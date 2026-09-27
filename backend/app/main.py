from __future__ import annotations

import asyncio
import json
import shutil
import sys
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .auth import load_or_create_token, require_token, token_path
from .config import (
    EDITABLE_FIELDS,
    FIELD_SPECS,
    SECRET_FIELDS,
    SettingsError,
    keychain_available,
    settings,
    store,
)
from . import service
from .db import delete_job, get_job, init_db, list_jobs, update_job, update_segment
from .pipeline import (
    Pipeline,
    PipelineError,
    TTS_ENGINE,
    can_resume_export,
    cancel_job_processes,
    delete_job_files,
    fit_score,
    seed_demo_job,
    unknown_vieneu_voices,
    vieneu_preset_voices,
)


queues: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)


@dataclass(frozen=True)
class Task:
    """Một việc nặng cần chạy tuần tự. Trước đây chỉ `process` đi qua hàng đợi, còn export và
    regenerate dùng `BackgroundTasks` nên ĐI VÒNG: N lần export song song sinh ra
    N×TTS_WORKERS tiến trình TTS cùng lúc, đủ làm nghẹt máy người dùng."""

    kind: Literal["process", "export", "regenerate"]
    job_id: str
    segment_id: str | None = None


work_queue: asyncio.Queue[Task] = asyncio.Queue()
# Việc đã xếp hàng hoặc đang chạy. Bấm Export hai lần không được thành hai lần render.
pending_tasks: set[Task] = set()

# Các stage thuộc pha export. Chạy lại pha này KHÔNG tốn tiền: `export` bỏ qua câu đã có
# audio, nên TTS đã làm được giữ nguyên. Pha trước đó (probe/separate/transcribe/translate)
# chạy lại là gọi lại API dịch và tính tiền tiếp.
RESUMABLE_STAGES = {"voice", "export"}


async def publish(job_id: str, event: dict[str, Any]) -> None:
    for queue in list(queues[job_id]):
        await queue.put(event)


pipeline = Pipeline(publish)


async def enqueue(task: Task) -> bool:
    """Xếp việc vào hàng đợi; trả False nếu đúng việc đó đã nằm sẵn trong hàng."""
    if task in pending_tasks:
        return False
    pending_tasks.add(task)
    await work_queue.put(task)
    return True


async def run_task(task: Task) -> None:
    """Chạy một việc, bắt mọi lỗi để job không bao giờ kẹt 'processing' vĩnh viễn."""
    try:
        if task.kind == "process":
            await pipeline.process(task.job_id)
        elif task.kind == "export":
            await pipeline.export(task.job_id)
        elif task.kind == "regenerate" and task.segment_id:
            await pipeline.regenerate(task.job_id, task.segment_id)
    except asyncio.CancelledError:
        job = get_job(task.job_id, include_segments=False) or {}
        if not job.get("cancelled"):
            raise  # backend đang tắt, không phải người dùng bấm huỷ -> để worker dừng hẳn
        update_job(task.job_id, status="cancelled", stage="cancelled")
        await publish(task.job_id, {"type": "cancelled"})
    except Exception as exc:  # noqa: BLE001 - lỗi nào cũng phải thành trạng thái thấy được
        message = str(exc)
        stderr = getattr(exc, "stderr", None)
        if stderr:
            message = f"{message}\n{stderr[-2000:]}"
        update_job(task.job_id, status="failed", stage="failed", error=message[:4000])
        await publish(task.job_id, {"type": "error", "message": message[:500]})


async def worker() -> None:
    while True:
        task = await work_queue.get()
        try:
            await run_task(task)
        finally:
            pending_tasks.discard(task)
            work_queue.task_done()


def resume_interrupted() -> tuple[list[Task], list[str]]:
    """Dọn hậu quả của lần chạy trước lúc khởi động.

    Hàng đợi nằm trong RAM nên backend tắt giữa chừng là job kẹt `processing` VĨNH VIỄN —
    không chạy, không hỏng, không xoá được, không có đường quay lại. Đo trên dữ liệu thật:
    2/44 job kẹt như vậy, một cái đã dịch xong và dừng ở 88%.

    Quy tắc: `queued` thì xếp lại (chưa tiêu gì). `processing` ở pha export thì chạy tiếp
    (miễn phí, dùng lại TTS đã có). `processing` ở pha dịch thì CHỈ đánh dấu hỏng kèm lý do
    — tự động chạy lại sẽ âm thầm gọi lại API và tính tiền của người dùng mà họ không hề bấm gì.
    """
    resumed: list[Task] = []
    interrupted: list[str] = []
    for job in list_jobs():
        if job["status"] == "queued":
            resumed.append(Task("process", job["id"]))
        elif job["status"] == "processing":
            if job["stage"] in RESUMABLE_STAGES and can_resume_export(get_job(job["id"]) or job):
                resumed.append(Task("export", job["id"]))
            else:
                interrupted.append(job["id"])
    for job_id in interrupted:
        update_job(
            job_id,
            status="failed",
            stage="failed",
            error="Bị gián đoạn khi backend dừng. Bấm Thử lại để chạy lại "
            "(bước dịch sẽ gọi API và tính phí lần nữa).",
        )
    return resumed, interrupted


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    # Job demo là dữ liệu GIẢ. Chỉ seed khi người dùng chủ động bật demo mode, nếu không
    # mọi cài đặt mới đều có sẵn một dự án bịa trong thư viện.
    if settings.effective_demo_mode:
        seed_demo_job()
    # Cảnh báo (không chặn khởi động) khi VIDEO_DUB_VIENEU_VOICE* trỏ tới giọng không có thật:
    # job sẽ hỏng ngay khi bắt đầu xử lý, biết trước từ log vẫn hơn.
    unknown = unknown_vieneu_voices()
    if unknown:
        print(
            f"[cảnh báo] Giọng VieNeu không tồn tại trong preset: {', '.join(unknown)}.",
            file=sys.stderr,
        )
    task = asyncio.create_task(worker())
    # Nạp lại việc dang dở TRƯỚC khi nhận request mới, để job của lần chạy trước không nằm
    # kẹt ở "processing" trong khi người dùng tưởng nó vẫn đang chạy.
    resumed, interrupted = resume_interrupted()
    for item in resumed:
        await enqueue(item)
    if resumed or interrupted:
        print(
            f"[hàng đợi] chạy tiếp {len(resumed)} việc, đánh dấu gián đoạn {len(interrupted)} job.",
            file=sys.stderr,
        )
    # Sinh token NGAY lúc khởi động, đừng đợi request đầu tiên: dev proxy của Vite đọc file
    # này để gắn header, mà nó thường chạy trước cả request đầu tiên của trình duyệt.
    load_or_create_token()
    print(
        f"[bảo mật] API cần token cục bộ. Token nằm ở {token_path()}",
        file=sys.stderr,
    )
    yield
    task.cancel()


# Token cục bộ áp cho TOÀN BỘ route, không phải từng cái một: quên gắn vào một endpoint mới
# là hở đúng endpoint đó, mà lỗi kiểu này không ai phát hiện ra cho tới khi muộn.
app = FastAPI(
    title="Lồng Tiếng AI",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(require_token)],
)
app.add_middleware(
    CORSMiddleware,
    # Chỉ dev server Vite. `allow_credentials` bật để cookie token đi kèm được; đổi sang "*"
    # là trình duyệt từ chối gửi cookie, và cũng mở cho mọi trang web bất kỳ.
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ],
    allow_credentials=True,
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
    # Bật/tắt lồng tiếng 2 giọng (tự dò nam/nữ) cho riêng job này trước khi chạy.
    multi_speaker: bool | None = None


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "demo_mode": settings.effective_demo_mode,
        # Danh sách thiếu gì để chạy thật. UI dựa vào đây để dẫn người dùng sang màn hình
        # thiết lập thay vì để họ upload rồi mới biết hỏng.
        "missing": settings.missing_requirements,
        "ready": not settings.missing_requirements,
        "ffmpeg": bool(settings.ffmpeg),
        "ffprobe": bool(settings.ffprobe),
        "cloud_ready": settings.cloud_ready,
        "tts_engine": TTS_ENGINE,
        # UI lấy giới hạn thời lượng từ đây thay vì ghi cứng một con số khác với backend.
        "max_duration_minutes": settings.max_duration_minutes,
        "duration_limit": settings.duration_limit_label,
        "gpu": detect_gpu(),
    }


def _field_type(value: Any) -> str:
    """Kiểu ô nhập cho UI. Phải tách số ra khỏi text: gửi "240" (chuỗi) cho một field int sẽ bị
    `_validate` từ chối, form trông như hỏng dù người dùng gõ đúng."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "number"
    return "text"


def _settings_payload() -> dict[str, Any]:
    """Giá trị hiện tại + mô tả field cho UI dựng form. Secret KHÔNG bao giờ trả về giá trị,
    chỉ trả cờ đã-đặt-hay-chưa — API key đã lưu thì không có lý do gì để nó chạy ngược ra
    ngoài qua HTTP nữa."""
    current = store.current
    sources = store.sources()
    values: dict[str, Any] = {}
    for name in EDITABLE_FIELDS:
        values[name] = "" if name in SECRET_FIELDS else getattr(current, name)
    return {
        "values": values,
        "secrets_set": {name: bool(getattr(current, name)) for name in sorted(SECRET_FIELDS)},
        "sources": sources,
        "keychain": keychain_available(),
        "fields": [
            {
                "name": name,
                "label": FIELD_SPECS[name].label,
                "group": FIELD_SPECS[name].group,
                "secret": FIELD_SPECS[name].secret,
                "choices": list(FIELD_SPECS[name].choices),
                "help": FIELD_SPECS[name].help,
                "type": _field_type(getattr(current, name)),
            }
            for name in EDITABLE_FIELDS
        ],
        "missing": current.missing_requirements,
        "ready": not current.missing_requirements,
    }


@app.get("/api/settings")
def read_settings() -> dict[str, Any]:
    return _settings_payload()


@app.put("/api/settings")
def write_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Lưu cấu hình rồi nạp lại NGAY (không cần khởi động lại backend).

    Nhận dict tự do nhưng `store.update` chỉ chấp nhận field trong whitelist `EDITABLE_FIELDS`
    và validate từng giá trị — không để tên field do client gửi chảy thẳng vào chỗ ghi dữ liệu.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Dữ liệu cài đặt không hợp lệ.")
    try:
        store.update(payload)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _settings_payload()


def _vieneu_voice_options() -> list[dict[str, Any]]:
    """Giọng VieNeu cho UI: preset đóng gói sẵn trong package (không phải nạp model). Không đọc
    được (chưa cài vieneu) -> lùi về đúng giọng cấu hình trong env như trước."""
    voices = vieneu_preset_voices()
    if not voices:
        name = settings.vieneu_voice or "Minh Quân"
        return [{"id": name, "label": f"Giọng {name}", "desc": "Đặt qua VIDEO_DUB_VIENEU_VOICE"}]
    return voices


@app.get("/api/voices")
def voices() -> dict[str, Any]:
    """Danh sách giọng. Chỉ còn engine VieNeu (Vbee và Gemini TTS đã gỡ): giọng lấy từ preset
    đóng gói trong package. Kèm giọng nam/nữ dùng khi bật lồng tiếng 2 giọng (multi_speaker).
    Vẫn trả mảng `engines` một phần tử để frontend cũ không vỡ."""
    vieneu_voice = settings.vieneu_voice or "Minh Quân"
    vieneu_male = settings.vieneu_voice_male or settings.vieneu_ref_audio_male or vieneu_voice
    vieneu_female = settings.vieneu_voice_female or settings.vieneu_ref_audio_female or "(chưa đặt)"
    return {
        "default_engine": TTS_ENGINE,
        "default_voice": vieneu_voice,
        "multi_speaker_default": settings.multi_speaker,
        "engines": [
            {
                "id": TTS_ENGINE,
                "label": "VieNeu (chạy local)",
                "default_voice": vieneu_voice,
                "voices": _vieneu_voice_options(),
                "gendered_voices": {"male": vieneu_male, "female": vieneu_female},
            }
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
    voice: str = Form("Minh Quân"),
    style: str = Form("Tự nhiên"),
    # Bật lồng tiếng 2 giọng (tự dò nam/nữ). Không gửi -> theo mặc định env VIDEO_DUB_MULTI_SPEAKER.
    multi_speaker: bool = Form(settings.multi_speaker),
) -> dict[str, Any]:
    if not voice or voice == "Aoede":
        voice = settings.vieneu_voice or "Minh Quân"
    # Chặn NGAY tại cửa: thiếu cấu hình mà vẫn nhận video thì người dùng chờ xong pipeline
    # mới phát hiện kết quả là giả (hoặc job hỏng giữa chừng).
    missing = settings.missing_requirements
    if missing and not settings.effective_demo_mode:
        raise HTTPException(
            422,
            "Chưa đủ cấu hình để lồng tiếng: " + " ".join(item["message"] for item in missing),
        )
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
        job_id, file.filename or destination.name, destination, metadata, voice, style, multi_speaker
    )
    await enqueue(Task("process", job_id))
    return job


@app.patch("/api/jobs/{job_id}")
def patch_job(job_id: str, payload: JobSettingsPatch) -> dict[str, Any]:
    if not get_job(job_id, include_segments=False):
        raise HTTPException(404, "Không tìm thấy dự án.")
    update_job(job_id, **payload.model_dump(exclude_none=True))
    return get_job(job_id) or {}


@app.delete("/api/jobs/{job_id}")
def remove_job(job_id: str) -> dict[str, Any]:
    """Xoá hẳn một dự án: bản ghi DB + toàn bộ file trên đĩa (stems, audio từng câu, MP4,
    video gốc đã upload). Trước đây không có đường xoá nào nên `data/` chỉ có phình ra."""
    if not get_job(job_id, include_segments=False):
        raise HTTPException(404, "Không tìm thấy dự án.")
    # Xoá file trước: DB mất trước mà file còn lại thì thành rác mồ côi, không ai dọn nữa.
    freed = delete_job_files(job_id)
    delete_job(job_id)
    return {"status": "deleted", "freed_bytes": freed}


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
async def regenerate(job_id: str, segment_id: str) -> dict[str, str]:
    if not get_job(job_id):
        raise HTTPException(404, "Không tìm thấy dự án.")
    await enqueue(Task("regenerate", job_id, segment_id))
    return {"status": "processing"}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str) -> dict[str, Any]:
    if not get_job(job_id, include_segments=False):
        raise HTTPException(404, "Không tìm thấy dự án.")
    update_job(job_id, cancelled=1, status="cancelled")
    # Đặt cờ thôi là chưa đủ: cờ chỉ được đọc ở ranh giới `_stage()`, nên Demucs/Whisper/FFmpeg
    # đang chạy vẫn chạy tới hết. Giết thẳng tiến trình con để huỷ có hiệu lực ngay.
    killed = cancel_job_processes(job_id)
    await publish(job_id, {"type": "cancelled"})
    return {"status": "cancelled", "killed": killed}


@app.post("/api/jobs/{job_id}/retry", status_code=202)
async def retry(job_id: str) -> dict[str, str]:
    if not get_job(job_id, include_segments=False):
        raise HTTPException(404, "Không tìm thấy dự án.")
    # Job hỏng ở pha export thì chạy lại đúng pha đó: dịch lại từ đầu vừa mất thời gian vừa
    # tính tiền API một lần nữa cho phần đã làm xong.
    job = get_job(job_id) or {}
    kind = (
        "export"
        if job.get("stage") in RESUMABLE_STAGES and can_resume_export(job)
        else "process"
    )
    update_job(job_id, cancelled=0, status="queued", error=None)
    await enqueue(Task(kind, job_id))
    return {"status": "queued", "kind": kind}


@app.post("/api/jobs/{job_id}/export", status_code=202)
async def export(job_id: str) -> dict[str, str]:
    if not get_job(job_id):
        raise HTTPException(404, "Không tìm thấy dự án.")
    queued = await enqueue(Task("export", job_id))
    # Bấm hai lần không xếp hàng hai lần; báo lại để UI không hiện như vừa nhận việc mới.
    return {"status": "processing" if queued else "already_queued"}


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
