from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip())
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)


def emit(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--source-audio")
    parser.add_argument("--export-existing", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    os.chdir(ROOT)
    load_env()
    sys.path.insert(0, str(BACKEND))

    from app.config import settings
    from app.db import get_job, update_job
    from app.pipeline import Pipeline, run

    job = get_job(args.job_id, include_segments=False)
    if not job:
        raise RuntimeError(f"Job not found: {args.job_id}")

    work = settings.jobs_dir / args.job_id
    work.mkdir(parents=True, exist_ok=True)
    source_audio = Path(args.source_audio) if args.source_audio else work / "source.wav"
    if not source_audio.is_file():
        raise FileNotFoundError(source_audio)

    duration = float(job.get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("Job duration is missing.")

    silent_background = work / "silent-background.wav"
    if not silent_background.is_file():
        emit({"stage": "make-silent-background", "output": str(silent_background)})
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
                str(silent_background),
            ]
        )

    artifacts = {
        **job.get("artifacts", {}),
        "source_audio": str(source_audio),
        "background": str(silent_background),
        "vocals": str(source_audio),
        "fallback": "no_demucs_silent_background",
    }
    update_job(
        args.job_id,
        artifacts=artifacts,
        status="processing",
        stage="transcribe",
        progress=35,
        error=None,
    )

    async def hook(current_job_id: str, event: dict) -> None:
        emit({"job_id": current_job_id, "event": event})

    pipeline = Pipeline(hook)
    if args.export_existing:
        update_job(args.job_id, status="review", stage="translate", progress=52, error=None)
        current = get_job(args.job_id) or {}
        emit({"stage": "export-start", "segments": len(current.get("segments", []))})
        output = await pipeline.export(args.job_id)
        emit({"stage": "done", "output": str(output), "job": get_job(args.job_id, include_segments=False)})
        return

    emit({"stage": "transcribe", "job_id": args.job_id})
    transcripts = await asyncio.to_thread(pipeline._transcribe, source_audio, args.job_id)
    emit({"stage": "translate", "segments": len(transcripts)})
    update_job(args.job_id, stage="translate", progress=45)
    translated, context = await asyncio.to_thread(
        pipeline._translate, transcripts, job.get("style", "natural")
    )
    if context:
        current_artifacts = (get_job(args.job_id, include_segments=False) or {}).get("artifacts", {})
        update_job(args.job_id, artifacts={**current_artifacts, "translate_context": context})
    await asyncio.to_thread(
        pipeline._replace_segments,
        args.job_id,
        [(item["text"], item["translated"]) for item in translated],
        4.8,
        [(item["start"], item["end"]) for item in translated],
    )
    update_job(args.job_id, status="review", stage="translate", progress=52, error=None)

    current = get_job(args.job_id) or {}
    emit({"stage": "export-start", "segments": len(current.get("segments", []))})
    output = await pipeline.export(args.job_id)
    emit({"stage": "done", "output": str(output), "job": get_job(args.job_id, include_segments=False)})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        raise
