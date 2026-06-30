from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import settings
from .db import connect, get_job, init_db, list_jobs, now_iso, update_job, update_segment
from .pipeline import Pipeline, PipelineError, fit_score, probe, seed_demo_job


queues: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
work_queue: asyncio.Queue[str] = asyncio.Queue()


async def publish(job_id: str, event: dict[str, Any]) -> None:
    for queue in list(queues[job_id]):
        await queue.put(event)


pipeline = Pipeline(publish)


async def worker() -> None:
    while True:
        job_id = await work_queue.get()
        try:
            await pipeline.process(job_id)
        finally:
            work_queue.task_done()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    seed_demo_job()
    task = asyncio.create_task(worker())
    yield
    task.cancel()


app = FastAPI(title="Lồng Tiếng AI", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SegmentPatch(BaseModel):
    translated_text: str = Field(min_length=1, max_length=4000)


class JobSettingsPatch(BaseModel):
    voice: str | None = None
    style: str | None = None
    speed: float | None = Field(default=None, ge=0.5, le=2.0)
    pitch: float | None = Field(default=None, ge=-6, le=6)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "demo_mode": settings.effective_demo_mode,
        "ffmpeg": bool(settings.ffmpeg),
        "ffprobe": bool(settings.ffprobe),
        "cloud_ready": settings.cloud_ready,
        "gpu": detect_gpu(),
    }


@app.get("/api/jobs")
def jobs() -> list[dict[str, Any]]:
    return list_jobs()


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    value = get_job(job_id)
    if not value:
        raise HTTPException(404, "Không tìm thấy dự án.")
    return value


@app.post("/api/jobs", status_code=201)
async def create_job(
    file: UploadFile = File(...),
    voice: str = Form("Aoede"),
    style: str = Form("Tự nhiên"),
) -> dict[str, Any]:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in {".mp4", ".mkv", ".mov"}:
        raise HTTPException(415, "Chỉ hỗ trợ MP4, MKV hoặc MOV.")
    job_id = str(uuid.uuid4())
    destination = settings.uploads_dir / f"{job_id}{extension}"
    with destination.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            output.write(chunk)
    metadata = {"duration": 0, "width": 0, "height": 0}
    if settings.ffprobe:
        try:
            metadata = probe(destination)
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(422, f"Video không hợp lệ: {exc}") from exc
        if metadata["duration"] > 1800:
            destination.unlink(missing_ok=True)
            raise HTTPException(422, "Video vượt giới hạn 30 phút.")
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
                file.filename or destination.name,
                str(destination),
                metadata["duration"],
                metadata["width"],
                metadata["height"],
                voice,
                style,
                now,
                now,
            ),
        )
    await work_queue.put(job_id)
    return get_job(job_id) or {}


@app.patch("/api/jobs/{job_id}")
def patch_job(job_id: str, payload: JobSettingsPatch) -> dict[str, Any]:
    if not get_job(job_id, include_segments=False):
        raise HTTPException(404, "Không tìm thấy dự án.")
    update_job(job_id, **payload.model_dump(exclude_none=True))
    return get_job(job_id) or {}


@app.patch("/api/jobs/{job_id}/segments/{segment_id}")
def patch_segment(job_id: str, segment_id: str, payload: SegmentPatch) -> dict[str, Any]:
    current = get_job(job_id)
    segment = next((item for item in (current or {}).get("segments", []) if item["id"] == segment_id), None)
    if not segment:
        raise HTTPException(404, "Không tìm thấy phân đoạn.")
    score = fit_score(payload.translated_text, segment["end"] - segment["start"])
    update_segment(
        segment_id,
        translated_text=payload.translated_text,
        fit_score=score,
        audio_path=None,
        audio_duration=None,
    )
    return get_job(job_id) or {}


@app.post("/api/jobs/{job_id}/segments/{segment_id}/regenerate", status_code=202)
async def regenerate(job_id: str, segment_id: str, tasks: BackgroundTasks) -> dict[str, str]:
    if not get_job(job_id):
        raise HTTPException(404, "Không tìm thấy dự án.")
    tasks.add_task(pipeline.regenerate, job_id, segment_id)
    return {"status": "processing"}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str) -> dict[str, str]:
    if not get_job(job_id, include_segments=False):
        raise HTTPException(404, "Không tìm thấy dự án.")
    update_job(job_id, cancelled=1, status="cancelled")
    await publish(job_id, {"type": "cancelled"})
    return {"status": "cancelled"}


@app.post("/api/jobs/{job_id}/retry", status_code=202)
async def retry(job_id: str) -> dict[str, str]:
    if not get_job(job_id, include_segments=False):
        raise HTTPException(404, "Không tìm thấy dự án.")
    update_job(job_id, cancelled=0, status="queued", error=None)
    await work_queue.put(job_id)
    return {"status": "queued"}


@app.post("/api/jobs/{job_id}/export", status_code=202)
async def export(job_id: str, tasks: BackgroundTasks) -> dict[str, str]:
    if not get_job(job_id):
        raise HTTPException(404, "Không tìm thấy dự án.")
    tasks.add_task(pipeline.export, job_id)
    return {"status": "processing"}


@app.get("/api/jobs/{job_id}/events")
async def events(job_id: str) -> StreamingResponse:
    if not get_job(job_id, include_segments=False):
        raise HTTPException(404, "Không tìm thấy dự án.")

    async def stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        queues[job_id].add(queue)
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            queues[job_id].discard(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str) -> FileResponse:
    current = get_job(job_id, include_segments=False)
    path = Path((current or {}).get("artifacts", {}).get("video", ""))
    if not path.is_file():
        raise HTTPException(404, "File xuất chưa sẵn sàng.")
    return FileResponse(path, filename=path.name)


def detect_gpu() -> dict[str, Any]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {"available": False, "name": "Không phát hiện"}
    try:
        import subprocess

        output = subprocess.check_output(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            text=True,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0,
        ).strip()
        name, memory = [part.strip() for part in output.split(",", 1)]
        return {"available": True, "name": name, "memory": memory}
    except Exception:
        return {"available": False, "name": "Không đọc được NVIDIA GPU"}
