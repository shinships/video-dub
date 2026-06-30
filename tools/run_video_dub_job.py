"""Chạy lồng tiếng một video theo yêu cầu: đường dẫn (file hoặc URL) vào -> MP4 ra.

Ví dụ:
    python tools/run_video_dub_job.py --source "C:/clip.mp4" --voice Aoede
    python tools/run_video_dub_job.py --source "https://youtu.be/..." --output out.mp4
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
    parser.add_argument("--style", default=os.getenv("VIDEO_DUB_STYLE", "Tự nhiên"))
    parser.add_argument("--output", default=os.getenv("VIDEO_DUB_OUTPUT"), help="Nơi lưu MP4 kết quả.")
    parser.add_argument("--no-copy", action="store_true", help="Không sao chép file nguồn vào data/uploads.")
    return parser.parse_args()


async def main() -> None:
    global _current_job_id
    args = parse_args()
    if not args.source:
        raise ValueError("Thiếu --source (hoặc biến môi trường VIDEO_DUB_SOURCE).")

    os.chdir(ROOT)
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)  # ưu tiên ADC/gcloud
    sys.path.insert(0, str(BACKEND))

    from app import service  # config tự nạp .env khi import
    from app.db import get_job, init_db
    from app.pipeline import Pipeline

    init_db()
    emit({"job_id": args.job_id, "source": args.source, "voice": args.voice, "style": args.style})

    job = service.create_job_from_source(
        args.source, voice=args.voice, style=args.style, job_id=args.job_id, copy=not args.no_copy
    )
    job_id = job["id"]
    _current_job_id = job_id
    emit({"stage": "queued", "job_id": job_id, "name": job["name"], "duration": job["duration"]})

    async def hook(current_job_id: str, event: dict) -> None:
        emit({"job_id": current_job_id, "event": event})

    pipeline = Pipeline(hook)
    await pipeline.process(job_id)
    job = get_job(job_id, include_segments=False) or {}
    if job.get("status") == "failed":
        raise RuntimeError(job.get("error") or "Pipeline thất bại trước khi export.")

    segments = len(get_job(job_id, include_segments=True).get("segments", []))
    emit({"stage": "export-start", "segments": segments})
    await pipeline.export(job_id)

    job = get_job(job_id, include_segments=False) or {}
    produced = Path(job.get("artifacts", {}).get("video", ""))
    result = {"stage": "done", "job_id": job_id, "rendered": str(produced)}
    if produced.suffix.lower() == ".mp4" and produced.is_file():
        output = Path(args.output) if args.output else service.default_output_path(args.source, job["name"])
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, output)
        result["output"] = str(output)
    else:
        result["note"] = "Chưa có MP4 thật (có thể đang demo mode — cấu hình FFmpeg + Cloud)."
    emit(result)


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
