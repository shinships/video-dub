"""Lớp dùng chung cho mọi điểm vào (web API và CLI): chuẩn bị nguồn, tạo job, đường ra."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from .config import settings
from .db import connect, get_job, now_iso
from .pipeline import DEFAULT_JOB_SPEED, PipelineError, probe

MAX_DURATION_SECONDS = 1800  # 30 phút


def is_url(source: str) -> bool:
    return source.lower().startswith(("http://", "https://"))


def download_source(url: str, dest_dir: Path) -> Path:
    """Tải video từ URL bằng yt-dlp (cài qua requirements-audio)."""
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - phụ thuộc tuỳ chọn
        raise PipelineError("Cần cài yt-dlp để tải video từ URL (pip install yt-dlp).") from exc

    dest_dir.mkdir(parents=True, exist_ok=True)
    options = {
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "format": "mp4/bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "noprogress": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
    candidates = sorted(dest_dir.glob(f"{info['id']}.*"))
    video = next((p for p in candidates if p.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm"}), None)
    if not video:
        raise PipelineError(f"Tải xong nhưng không tìm thấy file video cho {url}.")
    return video


def prepare_source(source: str, job_id: str, copy: bool = True) -> tuple[Path, str]:
    """Trả về (đường dẫn dùng cho pipeline, tên hiển thị). Hỗ trợ cả file local lẫn URL."""
    if is_url(source):
        downloaded = download_source(source, settings.uploads_dir)
        return downloaded, downloaded.name
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(source)
    if not copy:
        return path, path.name
    destination = settings.uploads_dir / f"{job_id}{path.suffix.lower()}"
    shutil.copy2(path, destination)
    return destination, path.name


def probe_and_check(path: Path) -> dict[str, Any]:
    metadata = probe(path)
    if metadata["duration"] > MAX_DURATION_SECONDS:
        raise PipelineError("Video vượt giới hạn 30 phút.")
    return metadata


def register_job(
    job_id: str,
    name: str,
    source_path: Path,
    metadata: dict[str, Any],
    voice: str,
    style: str,
    multi_speaker: bool = False,
) -> dict[str, Any]:
    """Ghi một job 'queued' vào DB. Dùng chung cho web upload và CLI."""
    now = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs
            (id, name, source_path, status, stage, progress, duration, width, height,
             voice, style, speed, multi_speaker, artifacts, cost, created_at, updated_at)
            VALUES (?, ?, ?, 'queued', 'upload', 3, ?, ?, ?, ?, ?, ?, ?, '{}', '{}', ?, ?)
            """,
            (
                job_id,
                name,
                str(source_path),
                metadata["duration"],
                metadata["width"],
                metadata["height"],
                voice,
                style,
                DEFAULT_JOB_SPEED,
                1 if multi_speaker else 0,
                now,
                now,
            ),
        )
    return get_job(job_id) or {}


def create_job_from_source(
    source: str,
    voice: str = "Aoede",
    style: str = "Tự nhiên",
    job_id: str | None = None,
    copy: bool = True,
) -> dict[str, Any]:
    """Tiện ích một bước cho CLI: chuẩn bị nguồn → probe → đăng ký job.
    Idempotent: nếu --job-id trỏ tới job đã tồn tại (vd retry sau lỗi), trả về job đó
    nguyên trạng thay vì insert trùng (sẽ vi phạm UNIQUE constraint trên jobs.id)."""
    if job_id:
        existing = get_job(job_id, include_segments=False)
        if existing:
            return existing
    job_id = job_id or str(uuid.uuid4())
    source_path, name = prepare_source(source, job_id, copy=copy)
    metadata = probe_and_check(source_path)
    return register_job(job_id, name, source_path, metadata, voice, style)


def default_output_path(source: str, name: str) -> Path:
    """Nơi xuất mặc định: cạnh video gốc (file local) hoặc thư mục hiện tại (URL)."""
    stem = Path(name).stem
    if is_url(source):
        return Path.cwd() / f"{stem}.vi.mp4"
    return Path(source).resolve().with_name(f"{stem}.vi.mp4")
