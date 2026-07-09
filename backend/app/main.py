from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import settings
from . import service
from .db import get_job, init_db, list_jobs, update_job, update_segment
from .pipeline import Pipeline, PipelineError, fit_score, seed_demo_job


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
    # Đặt riêng engine cho job này, khác VIDEO_DUB_TTS_ENGINE toàn cục (vd job A dùng "vieneu"
    # local trong khi job B dùng "vbee" cloud). Gemini TTS đã bị loại bỏ.
    tts_engine: Literal["vieneu", "vbee"] | None = None


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "demo_mode": settings.effective_demo_mode,
        "ffmpeg": bool(settings.ffmpeg),
        "ffprobe": bool(settings.ffprobe),
        "cloud_ready": settings.cloud_ready,
        "tts_engine": settings.tts_engine,
        "gpu": detect_gpu(),
    }


@app.get("/api/voices")
def voices() -> dict[str, Any]:
    """Danh sách engine TTS (tĩnh, không gọi cloud). Chỉ VieNeu và Vbee; Gemini TTS đã bỏ."""
    vieneu_voice = settings.vieneu_voice or "default"
    return {
        "default_engine": settings.tts_engine,
        "engines": [
            {
                "id": "vieneu",
                "label": "VieNeu (chạy local)",
                "voices": [
                    {
                        "id": vieneu_voice,
                        "label": f"Giọng {vieneu_voice}",
                        "desc": "Đặt qua VIDEO_DUB_VIENEU_VOICE",
                    }
                ],
            },
            {
                "id": "vbee",
                "label": "Vbee (cloud)",
                "voices": [
                    {
                        "id": settings.vbee_voice,
                        "label": settings.vbee_voice,
                        "desc": "Đặt qua VIDEO_DUB_VBEE_VOICE",
                    }
                ],
            },
        ],
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
            metadata = service.probe_and_check(destination)
        except PipelineError as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(422, f"Video không hợp lệ: {exc}") from exc
    job = service.register_job(
        job_id, file.filename or destination.name, destination, metadata, voice, style
    )
    await work_queue.put(job_id)
    return job


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
def download(job_id: str, kind: Literal["video", "srt"] = "video") -> FileResponse:
    current = get_job(job_id, include_segments=False)
    if not current:
        raise HTTPException(404, "Không tìm thấy dự án.")
    if kind == "srt":
        # SRT do _write_srt ghi cạnh output, không lưu trong artifacts.
        path = settings.jobs_dir / job_id / "subtitles-vi.srt"
    else:
        path = Path(current.get("artifacts", {}).get("video", ""))
    if not path.is_file():
        raise HTTPException(404, "File xuất chưa sẵn sàng.")
    return FileResponse(path, filename=path.name)


@app.get("/api/jobs/{job_id}/source")
def source_media(job_id: str) -> FileResponse:
    """Serve video gốc cho player (FileResponse hỗ trợ Range nên tua được)."""
    current = get_job(job_id, include_segments=False)
    if not current:
        raise HTTPException(404, "Không tìm thấy dự án.")
    path = Path(current.get("source_path") or "")
    if not path.is_file():
        raise HTTPException(404, "Video gốc không còn trên đĩa.")
    return FileResponse(path, filename=path.name)


@app.get("/api/jobs/{job_id}/segments/{segment_id}/audio")
def segment_audio(job_id: str, segment_id: str) -> FileResponse:
    current = get_job(job_id)
    segment = next(
        (item for item in (current or {}).get("segments", []) if item["id"] == segment_id), None
    )
    if not segment:
        raise HTTPException(404, "Không tìm thấy phân đoạn.")
    path = Path(segment.get("audio_path") or "")
    if not path.is_file():
        raise HTTPException(404, "Chưa có audio cho câu này.")
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
