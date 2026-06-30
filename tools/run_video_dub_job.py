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


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ[name.strip()] = value.strip()
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=os.getenv("VIDEO_DUB_SOURCE"))
    parser.add_argument("--job-id", default=os.getenv("VIDEO_DUB_JOB_ID") or str(uuid.uuid4()))
    parser.add_argument("--voice", default=os.getenv("VIDEO_DUB_VOICE", "Puck"))
    parser.add_argument("--style", default=os.getenv("VIDEO_DUB_STYLE", "Natural Vietnamese narration"))
    parser.add_argument("--no-copy", action="store_true")
    parser.add_argument("--fallback-silent-background", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if not args.source:
        raise ValueError("Missing --source or VIDEO_DUB_SOURCE")

    os.chdir(ROOT)
    load_env()
    sys.path.insert(0, str(BACKEND))

    from app.config import settings
    from app.db import connect, get_job, init_db, now_iso, update_job
    from app.pipeline import Pipeline, PipelineError, probe, run

    source = Path(args.source)
    job_id = args.job_id

    async def hook(current_job_id: str, event: dict) -> None:
        emit({"job_id": current_job_id, "event": event})

    emit({"job_id": job_id, "source": str(source), "voice": args.voice, "style": args.style})
    if not source.is_file():
        raise FileNotFoundError(source)

    init_db()
    metadata = probe(source)
    if metadata["duration"] > 1800:
        raise PipelineError("Video is longer than 30 minutes.")

    destination = source
    if not args.no_copy:
        destination = settings.uploads_dir / f"{job_id}{source.suffix.lower()}"
        emit({"stage": "copy", "destination": str(destination)})
        shutil.copy2(source, destination)

    now = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs
            (id, name, source_path, status, stage, progress, duration, width, height,
             voice, style, artifacts, cost, created_at, updated_at)
            VALUES (?, ?, ?, 'queued', 'upload', 3, ?, ?, ?, ?, ?, '{}', '{}', ?, ?)
            """,
            (
                job_id,
                source.name,
                str(destination),
                metadata["duration"],
                metadata["width"],
                metadata["height"],
                args.voice,
                args.style,
                now,
                now,
            ),
        )

    pipeline = Pipeline(hook)
    await pipeline.process(job_id)
    job = get_job(job_id)
    if not job or job.get("status") == "failed":
        error = (job or {}).get("error") or "Pipeline failed before export"
        if not args.fallback_silent_background or "Demucs" not in error:
            raise RuntimeError(error)

        work = settings.jobs_dir / job_id
        source_audio = work / "source.wav"
        if not source_audio.is_file():
            raise RuntimeError(error)

        silent_background = work / "silent-background.wav"
        emit({"stage": "fallback-silent-background", "reason": error})
        run(
            [
                settings.ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t",
                f"{metadata['duration']:.3f}",
                str(silent_background),
            ]
        )
        update_job(
            job_id,
            artifacts={
                "source_audio": str(source_audio),
                "background": str(silent_background),
                "vocals": str(source_audio),
                "fallback": "no_demucs_silent_background",
            },
            status="processing",
            stage="transcribe",
            progress=35,
            error=None,
        )
        transcripts = await asyncio.to_thread(pipeline._transcribe, source_audio, job_id)
        update_job(job_id, stage="translate", progress=45)
        translated = await asyncio.to_thread(pipeline._translate, transcripts, args.style)
        await asyncio.to_thread(
            pipeline._replace_segments,
            job_id,
            [(item["text"], item["translated"]) for item in translated],
            4.8,
            [(item["start"], item["end"]) for item in translated],
        )
        update_job(job_id, status="review", stage="translate", progress=52, error=None)
        job = get_job(job_id)

    emit({"stage": "export-start", "segments": len(job.get("segments", []))})
    output = await pipeline.export(job_id)
    emit({"stage": "done", "output": str(output), "job": get_job(job_id)})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        trace = traceback.format_exc()
        print(trace, file=sys.stderr, flush=True)
        try:
            load_env()
            sys.path.insert(0, str(BACKEND))
            from app.db import update_job

            job_id = os.getenv("VIDEO_DUB_JOB_ID")
            if job_id:
                update_job(job_id, status="failed", stage="failed", error=trace)
        except Exception:
            pass
        raise
