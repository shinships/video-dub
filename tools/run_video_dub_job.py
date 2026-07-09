"""Chạy lồng tiếng một video theo yêu cầu: đường dẫn (file hoặc URL) vào -> MP4 ra.

Ví dụ:
    python tools/run_video_dub_job.py --source "C:/clip.mp4" --voice Aoede
    python tools/run_video_dub_job.py --source "https://youtu.be/..." --output out.mp4

Duyệt bản dịch trong chat (dừng sau bước dịch, sửa rồi mới export):
    python tools/run_video_dub_job.py --source "C:/clip.mp4" --job-id J --review-out review.json
    # (sửa trường "vi" trong review.json cho từng câu)
    python tools/run_video_dub_job.py --resume --job-id J --review-in review.json --output out.mp4

Khôi phục sau lỗi giữa chừng (job đã dịch xong, chỉ export lại — bỏ qua STT/dịch):
    python tools/run_video_dub_job.py --resume --job-id J
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
_current_job_id: str | None = None  # cập nhật ngay khi job được tạo, để except ở __main__ dùng đúng id


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lồng tiếng Việt cho một video.")
    parser.add_argument("--source", default=os.getenv("VIDEO_DUB_SOURCE"), help="File local hoặc URL.")
    parser.add_argument("--job-id", default=os.getenv("VIDEO_DUB_JOB_ID") or str(uuid.uuid4()))
    parser.add_argument("--voice", default=os.getenv("VIDEO_DUB_VOICE", "Aoede"))
    parser.add_argument(
        "--tts-engine",
        choices=["vieneu", "vbee"],
        default=os.getenv("VIDEO_DUB_JOB_TTS_ENGINE"),
        help="Ghi đè engine TTS riêng cho job này (mặc định: dùng VIDEO_DUB_TTS_ENGINE toàn cục).",
    )
    parser.add_argument(
        "--multi-speaker",
        dest="multi_speaker",
        action="store_true",
        default=os.getenv("VIDEO_DUB_MULTI_SPEAKER", "false").lower() in {"1", "true", "yes"},
        help="Lồng tiếng 2 giọng: tự dò nam/nữ theo đoạn rồi gán giọng riêng.",
    )
    parser.add_argument("--speed", type=float, default=None, help="Toc do video output, vi du 1.1.")
    parser.add_argument("--style", default=os.getenv("VIDEO_DUB_STYLE", "Tự nhiên"))
    parser.add_argument("--output", default=os.getenv("VIDEO_DUB_OUTPUT"), help="Nơi lưu MP4 kết quả.")
    parser.add_argument("--no-copy", action="store_true", help="Không sao chép file nguồn vào data/uploads.")
    parser.add_argument("--silent-background", action="store_true", help="Xuat chi TTS, khong giu audio goc.")
    parser.add_argument("--skip-separation", action="store_true", help="Nhan dang truc tiep tu audio nguon.")
    parser.add_argument("--allow-long", action="store_true", help="Cho phep CLI xu ly video vuot gioi han web 30 phut.")
    parser.add_argument("--resume", action="store_true", help="Bo qua STT/dich; nap job co san (--job-id) va export tiep.")
    parser.add_argument("--review-out", default=None, help="Dung sau buoc dich, xuat bang duyet ra file JSON (khong export).")
    parser.add_argument("--review-in", default=None, help="Nap file duyet da sua (JSON) va ap vao ban dich truoc khi export.")
    return parser.parse_args()


def build_review_rows(job: dict) -> list[dict]:
    """Bảng duyệt bản dịch: mỗi câu kèm mốc thời gian, độ dài khung (giây) và số ký tự VI
    hiện tại — để soát nhanh câu nào dễ đọc dồn (nhiều ký tự trên ít giây)."""
    rows = []
    for seg in job.get("segments", []):
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or 0.0)
        vi = seg.get("translated_text") or ""
        rows.append(
            {
                "id": seg["id"],
                "pos": seg.get("position"),
                "start": round(start, 2),
                "end": round(end, 2),
                "sec": round(max(0.0, end - start), 2),
                "chars": len(vi),
                "en": seg.get("source_text") or "",
                "vi": vi,
            }
        )
    return rows


def dump_review(job: dict) -> str:
    """Xuất bảng duyệt ra JSON (sửa trường "vi" của từng câu rồi nạp lại bằng --review-in)."""
    return json.dumps(
        {"job_id": job.get("id"), "segments": build_review_rows(job)},
        ensure_ascii=False,
        indent=2,
    )


def parse_review(text: str) -> list[dict]:
    """Đọc file duyệt (JSON) -> danh sách {id, vi} hợp lệ (có id và vi không rỗng)."""
    data = json.loads(text)
    rows = data.get("segments", []) if isinstance(data, dict) else data
    edits = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        seg_id = row.get("id")
        vi = (row.get("vi") or "").strip()
        if seg_id and vi:
            edits.append({"id": str(seg_id), "vi": vi})
    return edits


def apply_review(edits: list[dict], current: dict[str, str], update_fn) -> int:
    """Áp bản sửa: chỉ ghi câu có id hợp lệ và VI thực sự đổi (update_fn tự xoá audio cũ để
    export synth lại đúng bản mới). Trả về số câu đã sửa."""
    applied = 0
    for edit in edits:
        seg_id = edit["id"]
        vi = edit["vi"]
        if seg_id in current and vi != current[seg_id]:
            update_fn(seg_id, vi)
            applied += 1
    return applied


def make_silent_background(job: dict, settings, run) -> Path:
    work = settings.jobs_dir / job["id"]
    work.mkdir(parents=True, exist_ok=True)
    duration = float(job.get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("Job duration is missing.")
    output = work / "silent-background.wav"
    if not output.is_file():
        run(
            [
                settings.ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t",
                f"{duration:.3f}",
                str(output),
            ]
        )
    return output


def extract_source_audio(job: dict, settings, run) -> Path:
    work = settings.jobs_dir / job["id"]
    work.mkdir(parents=True, exist_ok=True)
    output = work / "source.wav"
    if not output.is_file():
        run([settings.ffmpeg, "-y", "-i", job["source_path"], "-vn", "-ac", "2", "-ar", "44100", str(output)])
    return output


async def main() -> None:
    global _current_job_id
    args = parse_args()

    os.chdir(ROOT)
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)  # ưu tiên ADC/gcloud
    sys.path.insert(0, str(BACKEND))

    from app import service  # config tự nạp .env khi import
    from app.config import settings
    from app.db import get_job, init_db, update_job, update_segment
    from app.pipeline import Pipeline, run

    init_db()

    async def hook(current_job_id: str, event: dict) -> None:
        emit({"job_id": current_job_id, "event": event})

    pipeline = Pipeline(hook)

    def apply_review_file(job_id: str, path: str) -> None:
        """Nạp file duyệt đã sửa và ghi lại bản dịch; xoá audio_path để export synth lại câu đã đổi."""
        current_job = get_job(job_id) or {}
        current = {s["id"]: (s.get("translated_text") or "") for s in current_job.get("segments", [])}
        edits = parse_review(Path(path).read_text(encoding="utf-8"))
        applied = apply_review(
            edits,
            current,
            lambda sid, vi: update_segment(sid, translated_text=vi, audio_path=None),
        )
        emit({"stage": "review-applied", "job_id": job_id, "edits": applied, "total": len(edits)})

    async def finalize(job_id: str) -> None:
        """Export MP4 + copy ra --output. Dùng chung cho nhánh chuẩn lẫn --resume."""
        current_job = get_job(job_id, include_segments=False) or {}
        if current_job.get("status") == "failed":
            raise RuntimeError(current_job.get("error") or "Pipeline thất bại trước khi export.")
        if args.silent_background:
            silent_background = make_silent_background(current_job, settings, run)
            update_job(
                job_id,
                artifacts={
                    **(current_job.get("artifacts") or {}),
                    "background": str(silent_background),
                    "audio_mode": "tts_only",
                },
            )
            emit({"stage": "silent-background", "path": str(silent_background)})
        segments = len(get_job(job_id, include_segments=True).get("segments", []))
        emit({"stage": "export-start", "segments": segments})
        await pipeline.export(job_id)
        current_job = get_job(job_id, include_segments=False) or {}
        produced = Path(current_job.get("artifacts", {}).get("video", ""))
        result = {"stage": "done", "job_id": job_id, "rendered": str(produced)}
        if produced.suffix.lower() == ".mp4" and produced.is_file():
            original_source = args.source or (current_job.get("artifacts") or {}).get("original_source")
            if args.output:
                output = Path(args.output)
            elif original_source:
                output = service.default_output_path(original_source, current_job["name"])
            else:  # Job cũ chưa lưu original_source: đành xuất cạnh thư mục hiện tại.
                output = Path.cwd() / f"{Path(current_job['name']).stem}.vi.mp4"
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(produced, output)
            result["output"] = str(output)
        else:
            result["note"] = "Chưa có MP4 thật (có thể đang demo mode — cấu hình FFmpeg + Cloud)."
        emit(result)

    # --- Nhánh RESUME: job đã có sẵn, nhảy thẳng tới (áp duyệt →) export ---
    if args.resume:
        job = get_job(args.job_id, include_segments=False)
        if not job:
            raise ValueError(f"--resume cần --job-id trỏ tới job đã tồn tại: {args.job_id}")
        _current_job_id = job["id"]
        if job.get("status") == "failed":
            # Mục đích chính của --resume là khôi phục sau lỗi giữa chừng (vd hết tài nguyên
            # hệ thống lúc TTS) -> xoá trạng thái lỗi cũ để finalize() không tự chặn export
            # (finalize kiểm tra status=="failed" cho luồng chuẩn, nơi lỗi đó vẫn còn mới).
            update_job(job["id"], status="review", stage="translate", error=None)
        if args.speed is not None:
            update_job(job["id"], speed=args.speed)
        if args.tts_engine:
            update_job(job["id"], tts_engine=args.tts_engine)
        if args.review_in:
            apply_review_file(job["id"], args.review_in)
        await finalize(job["id"])
        return

    # --- Nhánh chuẩn: tạo job -> STT/dịch -> (duyệt) -> export ---
    if not args.source:
        raise ValueError("Thiếu --source (hoặc biến môi trường VIDEO_DUB_SOURCE).")
    emit({"job_id": args.job_id, "source": args.source, "voice": args.voice, "style": args.style})

    if args.allow_long:
        existing = get_job(args.job_id, include_segments=False)
        if existing:
            job = existing
        else:
            source_path, name = service.prepare_source(args.source, args.job_id, copy=not args.no_copy)
            metadata = service.probe(source_path)
            job = service.register_job(args.job_id, name, source_path, metadata, args.voice, args.style)
    else:
        job = service.create_job_from_source(
            args.source, voice=args.voice, style=args.style, job_id=args.job_id, copy=not args.no_copy
        )
    job_id = job["id"]
    _current_job_id = job_id
    if args.speed is not None:
        update_job(job_id, speed=args.speed)
    if args.tts_engine:
        update_job(job_id, tts_engine=args.tts_engine)
    # Ghi rõ 0/1 theo cờ + env để cả nhánh --allow-long lẫn thường đều tôn trọng cấu hình.
    update_job(job_id, multi_speaker=1 if args.multi_speaker else 0)
    # Lưu lại đường dẫn/URL nguồn gốc (job["source_path"] chỉ là bản copy nội bộ trong
    # data/uploads) để --resume sau này (không truyền lại --source) vẫn xuất đúng vị trí
    # cạnh file gốc thay vì rơi về thư mục hiện tại.
    if not (job.get("artifacts") or {}).get("original_source"):
        update_job(job_id, artifacts={**(job.get("artifacts") or {}), "original_source": args.source})
    emit({"stage": "queued", "job_id": job_id, "name": job["name"], "duration": job["duration"]})

    if args.skip_separation:
        if not args.silent_background:
            raise ValueError("--skip-separation chỉ dùng cùng --silent-background.")
        job = get_job(job_id, include_segments=False) or {}
        source_audio = extract_source_audio(job, settings, run)
        silent_background = make_silent_background(job, settings, run)
        artifacts = {
            **(job.get("artifacts") or {}),
            "source_audio": str(source_audio),
            "background": str(silent_background),
            "vocals": str(source_audio),
            "audio_mode": "tts_only_skip_separation",
        }
        update_job(job_id, artifacts=artifacts, status="processing", stage="transcribe", progress=35, error=None)
        emit({"stage": "transcribe", "job_id": job_id})
        transcripts = await asyncio.to_thread(pipeline._transcribe, source_audio, job_id)
        update_job(job_id, stage="translate", progress=45)
        emit({"stage": "translate", "segments": len(transcripts)})
        translated, context = await asyncio.to_thread(pipeline._translate, transcripts, job.get("style", "Tự nhiên"))
        if context:
            artifacts = {**artifacts, "translate_context": context}
            update_job(job_id, artifacts=artifacts)
        await asyncio.to_thread(
            pipeline._replace_segments,
            job_id,
            [(item["text"], item["translated"]) for item in translated],
            4.8,
            [(item["start"], item["end"]) for item in translated],
        )
        update_job(job_id, status="review", stage="translate", progress=52, error=None)
        await hook(job_id, {"type": "ready", "message": "Bản dịch đã sẵn sàng để duyệt."})
    else:
        await pipeline.process(job_id)

    job = get_job(job_id, include_segments=False) or {}
    if job.get("status") == "failed":
        raise RuntimeError(job.get("error") or "Pipeline thất bại trước khi duyệt/export.")

    # Áp bản duyệt đã sửa (nếu có) ngay sau khi dịch, trước khi TTS/export.
    if args.review_in:
        apply_review_file(job_id, args.review_in)

    # Dừng để duyệt: xuất bảng ra file rồi thoát trước export (chạy tiếp bằng --resume).
    if args.review_out:
        review_job = get_job(job_id) or {}
        Path(args.review_out).write_text(dump_review(review_job), encoding="utf-8")
        emit(
            {
                "stage": "review-ready",
                "job_id": job_id,
                "review_file": str(Path(args.review_out).resolve()),
                "segments": len(review_job.get("segments", [])),
            }
        )
        return

    await finalize(job_id)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        trace = traceback.format_exc()
        print(trace, file=sys.stderr, flush=True)
        job_id = _current_job_id or os.getenv("VIDEO_DUB_JOB_ID")
        if job_id:
            try:
                sys.path.insert(0, str(BACKEND))
                from app.db import update_job

                update_job(job_id, status="failed", stage="failed", error=trace)
            except Exception:
                pass
        raise
