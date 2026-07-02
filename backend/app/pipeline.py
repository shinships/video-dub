from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from .config import settings
from .db import connect, get_job, now_iso, update_job, update_segment


T = TypeVar("T")


EventHook = Callable[[str, dict[str, Any]], Awaitable[None]]


DEMO_SEGMENTS = [
    ("In this video, I’m going to share 5 simple productivity tips.", "Trong video này, tôi sẽ chia sẻ 5 mẹo tăng năng suất đơn giản."),
    ("These ideas have changed the way I work.", "Những ý tưởng này đã thay đổi cách tôi làm việc."),
    ("They help me get more done every day.", "Chúng giúp tôi hoàn thành nhiều việc hơn mỗi ngày."),
    ("Tip number one is to plan your day the night before.", "Mẹo đầu tiên là lập kế hoạch cho ngày hôm sau từ tối hôm trước."),
    ("A few minutes of planning can save hours of decision-making.", "Chỉ vài phút lên kế hoạch có thể giúp bạn tiết kiệm hàng giờ đắn đo."),
    ("Tip number two is to focus on one task at a time.", "Mẹo thứ hai là tập trung vào một việc tại một thời điểm."),
    ("Multitasking feels productive, but it usually slows you down.", "Đa nhiệm có vẻ hiệu quả, nhưng thường khiến bạn chậm lại."),
]


class PipelineError(RuntimeError):
    pass


# --- Tham số trộn âm thanh (giữ nền gốc) ---
AUDIO_FORMAT = "aformat=sample_rates=48000:channel_layouts=stereo"
NARRATION_LUFS = -16.0  # Chuẩn loudness cho bus thoại Việt.
BG_VOLUME = 1.0  # Giữ nguyên mức nền gốc; ducking lo phần nhường chỗ cho thoại.
DUCK_THRESHOLD = 0.04  # Ngưỡng (biên độ tuyến tính ~-28 dB) để nền bắt đầu giảm.
DUCK_RATIO = 6
DUCK_ATTACK = 15  # ms
DUCK_RELEASE = 300  # ms
MIX_LIMIT = 0.9  # Trần limiter mix cuối (~-0.9 dB) chống vỡ tiếng.
# Kẹp tốc độ atempo: chỉ tinh chỉnh nhẹ, phần còn lại do bước viết-lại lo.
ATEMPO_MIN = 0.9
ATEMPO_MAX = 1.15
# Tốc độ output mặc định cho job mới: tua nhanh toàn bộ video (hình + nhạc nền + thoại)
# 10%, đồng bộ tuyệt đối — không chỉ riêng nhịp đọc giọng lồng tiếng.
DEFAULT_JOB_SPEED = 1.1
# Video giữ nguyên tốc độ (copy, nhanh) chỉ khi speed ~= 1.0; khác 1.0 phải re-encode để
# áp setpts nên cần codec/preset cho nhánh này.
VIDEO_CODEC = "libx264"
VIDEO_PRESET = "veryfast"
VIDEO_CRF = "18"

# --- Tham số dịch ---
TRANSLATE_BATCH = 40  # Số câu mỗi lời gọi Gemini (dịch theo lô).
TRANSLATE_WORKERS = 4  # Số lô dịch song song.
# Token gcloud sống ~1h; client cache quá hạn này sẽ gọi API bằng token chết giữa chừng.
CLIENT_TTL_SECONDS = 1800.0
VI_CHARS_PER_SEC = 15.0  # Ước lượng ký tự tiếng Việt đọc được mỗi giây (khống chế độ dài).
# --- Tham số khớp độ dài lồng tiếng ---
FIT_TOLERANCE = 1.15  # TTS dài hơn khung quá tỉ lệ này thì viết lại ngắn hơn.
FIT_MAX_RETRIES = 2
# Số đoạn TTS tạo song song khi export. Quota Gemini-TTS theo phút thường thấp (đặc biệt
# project promo/free tier) — để mặc định thấp, chỉnh qua VIDEO_DUB_TTS_WORKERS nếu quota cao hơn.
TTS_WORKERS = int(os.getenv("VIDEO_DUB_TTS_WORKERS", "2"))
BACKOFF_MAX_RETRIES = 6
BACKOFF_BASE_SECONDS = 8.0
# Giãn cách chủ động giữa các lần gọi TTS (giây). Quota theo PHÚT bị vượt rất nhanh nếu gọi
# dồn dập rồi chỉ retry bị động — exponential backoff (tới >4 phút/lần) lãng phí cả cửa sổ
# quota đã hồi. Giãn cách trước giúp tránh 429 ngay từ đầu thay vì chờ bị từ chối rồi lùi.
TTS_MIN_INTERVAL_SECONDS = float(os.getenv("VIDEO_DUB_TTS_MIN_INTERVAL", "8"))


def run(command: list[str], timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        # Không set encoding -> subprocess dùng locale mặc định (cp1252 trên Windows),
        # crash reader thread nếu subprocess (ffmpeg/demucs) in byte ngoài cp1252 và
        # nuốt mất log thật của lỗi gốc. Ép UTF-8 + thay thế ký tự lỗi thay vì crash.
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def probe(path: Path) -> dict[str, Any]:
    if not settings.ffprobe:
        raise PipelineError("Chưa cài FFmpeg/ffprobe.")
    result = run(
        [
            settings.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ]
    )
    payload = json.loads(result.stdout)
    video = next((s for s in payload.get("streams", []) if s.get("codec_type") == "video"), {})
    duration = float(payload.get("format", {}).get("duration", 0))
    return {"duration": duration, "width": video.get("width", 0), "height": video.get("height", 0)}


def _seconds(duration: Any) -> float:
    if duration is None:
        return 0.0
    if hasattr(duration, "total_seconds"):
        return float(duration.total_seconds())
    return float(getattr(duration, "seconds", duration))


def _active_gcloud_credentials():
    if os.getenv("VIDEO_DUB_USE_GCLOUD_AUTH", "").lower() not in {"1", "true", "yes"}:
        return None
    from google.oauth2.credentials import Credentials

    gcloud = shutil.which("gcloud.cmd") or shutil.which("gcloud") or "gcloud"
    token = run([gcloud, "auth", "print-access-token"], timeout=60).stdout.strip()
    credentials = Credentials(token=token)
    if settings.google_project:
        credentials = credentials.with_quota_project(settings.google_project)
    return credentials


def _atempo_chain(ratio: float, lo: float = ATEMPO_MIN, hi: float = ATEMPO_MAX) -> str:
    # Chỉ tinh chỉnh tốc độ trong biên hẹp để giọng không bị méo; độ dài đã được
    # khống chế ở bước dịch + vòng viết-lại nên atempo không phải gánh nặng.
    ratio = max(lo, min(hi, ratio))
    parts: list[float] = []
    while ratio > 2.0:
        parts.append(2.0)
        ratio /= 2.0
    while ratio < 0.5:
        parts.append(0.5)
        ratio /= 0.5
    parts.append(ratio)
    return ",".join(f"atempo={value:.5f}" for value in parts)


def _pitch_chain(semitones: float, sample_rate: int = 48000) -> str:
    """Dịch cao độ giữ nguyên tốc độ (best-effort). Trả về '' nếu không đổi."""
    semitones = max(-6.0, min(6.0, semitones))
    if abs(semitones) < 1e-3:
        return ""
    factor = 2 ** (semitones / 12.0)
    return (
        f",asetrate={int(sample_rate * factor)},aresample={sample_rate},"
        f"{_atempo_chain(1.0 / factor, lo=0.5, hi=2.0)}"
    )


class _RateLimiter:
    """Giãn cách tối thiểu giữa các lần gọi, dùng chung qua nhiều luồng (thread-safe).
    Bổ sung cho _with_backoff: backoff chỉ phản ứng SAU khi bị 429, còn limiter này chủ động
    né trước để không lãng phí thời gian chờ vào những lần gọi chắc chắn bị từ chối."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.min_interval
        if delay:
            time.sleep(delay)


_tts_limiter = _RateLimiter(TTS_MIN_INTERVAL_SECONDS)


_whisper_lock = threading.Lock()
_whisper_cache: dict[str, Any] = {}


def _load_whisper_model(model_name: str):
    from faster_whisper import WhisperModel

    try:
        return WhisperModel(model_name, device="cuda", compute_type=settings.whisper_compute)
    except Exception:
        return WhisperModel(model_name, device="cpu", compute_type="int8")


def _get_whisper_model(model_name: str):
    """Cache model Whisper giữa các job trong cùng tiến trình. Nạp model 'medium' mất
    hàng chục giây (đọc file model + khởi tạo CUDA/CPU context); job chạy tuần tự qua
    1 worker (xem main.py work_queue) nên job kế tiếp có thể tái dùng thay vì nạp lại."""
    with _whisper_lock:
        model = _whisper_cache.get(model_name)
        if model is None:
            model = _load_whisper_model(model_name)
            _whisper_cache[model_name] = model
        return model


_vieneu_lock = threading.Lock()
_vieneu_model: Any = None


def vieneu_infer_kwargs(voice: str, ref_audio: str) -> dict[str, str]:
    """Chọn giọng VieNeu: ưu tiên nhân bản từ ref_audio, rồi preset, trống thì mặc định SDK."""
    if ref_audio:
        return {"ref_audio": ref_audio}
    if voice:
        return {"voice": voice}
    return {}


def segment_audio_suffix(engine: str) -> str:
    """Đuôi file audio đoạn theo engine: VieNeu save ra WAV, Gemini trả bytes MP3."""
    return ".wav" if engine == "vieneu" else ".mp3"


def resolve_tts_engine(job: dict[str, Any]) -> str:
    """Engine của riêng job (đặt qua PATCH /api/jobs) thắng; job không đặt thì dùng
    VIDEO_DUB_TTS_ENGINE toàn cục -> nhiều job chạy song song có thể khác engine nhau."""
    return job.get("tts_engine") or settings.tts_engine


def _synth_vieneu(text: str, output: Path) -> None:
    """TTS local bằng VieNeu (không gọi cloud). Giữ lock xuyên suốt nạp + suy luận:
    model giữ trạng thái nội bộ nên không an toàn khi gọi song song, và suy luận vốn
    nghẽn CPU — chạy tuần tự không làm chậm thêm so với chạy chồng lên nhau."""
    global _vieneu_model
    with _vieneu_lock:
        if _vieneu_model is None:
            from vieneu import Vieneu

            _vieneu_model = Vieneu(device=settings.vieneu_device)
        kwargs = vieneu_infer_kwargs(settings.vieneu_voice, settings.vieneu_ref_audio)
        audio = _vieneu_model.infer(text, **kwargs)
        _vieneu_model.save(audio, str(output))


def _is_rate_limited(exc: Exception) -> bool:
    """Phát hiện lỗi quota/rate-limit (429 / RESOURCE_EXHAUSTED) từ Google API."""
    name = type(exc).__name__
    if "ResourceExhausted" in name or "TooManyRequests" in name:
        return True
    code = getattr(exc, "code", None)
    if callable(code) and "RESOURCE_EXHAUSTED" in str(code()):
        return True
    text = str(exc)
    return "RESOURCE_EXHAUSTED" in text or text.startswith("429") or " 429 " in text


def _with_backoff(fn: Callable[[], T], label: str = "call") -> T:
    """Gọi fn(), tự retry với backoff luỹ thừa khi gặp lỗi quota/rate-limit.
    Quota theo phút của Gemini (đặc biệt project promo/free tier) dễ bị vượt khi gọi dồn dập
    -> không retry sẽ làm cả job thất bại giữa chừng dù phần lớn request vẫn ổn."""
    last_exc: Exception | None = None
    for attempt in range(BACKOFF_MAX_RETRIES):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - cần bắt mọi lỗi SDK Google để quyết định retry
            if not _is_rate_limited(exc):
                raise
            last_exc = exc
            delay = BACKOFF_BASE_SECONDS * (2**attempt)
            print(
                f"[backoff] {label}: vượt quota (lần {attempt + 1}/{BACKOFF_MAX_RETRIES}), "
                f"chờ {delay:.0f}s…",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
    raise PipelineError(f"Vượt quota khi {label} sau {BACKOFF_MAX_RETRIES} lần thử lại.") from last_exc


def _strip_json(text: str) -> str:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def _parse_translations(text: str) -> dict[int, str]:
    """Parse JSON do Gemini trả về thành map {index: bản dịch}."""
    try:
        data = json.loads(_strip_json(text))
    except (json.JSONDecodeError, TypeError):
        return {}
    if isinstance(data, dict):
        data = data.get("translations") or data.get("items") or data.get("results") or []
    mapping: dict[int, str] = {}
    for row in data if isinstance(data, list) else []:
        if isinstance(row, dict) and "index" in row and ("vi" in row or "translated" in row):
            value = row.get("vi", row.get("translated", ""))
            try:
                index = int(row["index"])
            except (TypeError, ValueError):
                continue  # index rác từ model -> bỏ, để vòng dịch-lại xử lý câu thiếu
            text = str(value).strip()
            if text:
                mapping[index] = text
    return mapping


def fit_score(translated: str, seconds: float, audio_seconds: float | None = None) -> int:
    """Điểm khớp: ưu tiên độ dài audio thật, nếu chưa có thì ước theo ký tự."""
    seconds = max(0.3, seconds)
    if audio_seconds and audio_seconds > 0:
        ratio = audio_seconds / seconds
        penalty = abs(ratio - 1.0) * 120
    else:
        target_chars = max(12, seconds * VI_CHARS_PER_SEC)
        penalty = abs(len(translated) - target_chars) / target_chars * 70
    return max(55, min(99, round(100 - penalty)))


async def _stage(job_id: str, hook: EventHook, stage: str, progress: int, message: str) -> None:
    job = get_job(job_id, include_segments=False)
    if not job or job["cancelled"]:
        raise asyncio.CancelledError
    update_job(job_id, stage=stage, progress=progress, status="processing", error=None)
    await hook(job_id, {"type": "progress", "stage": stage, "progress": progress, "message": message})


def seed_demo_job(job_id: str = "demo") -> dict[str, Any]:
    existing = get_job(job_id)
    if existing:
        return existing
    with connect() as conn:
        now = now_iso()
        conn.execute(
            """
            INSERT INTO jobs
            (id, name, status, stage, progress, duration, width, height, voice, style,
             artifacts, cost, created_at, updated_at)
            VALUES (?, ?, 'review', 'translate', 52, 84, 1920, 1080, 'Aoede', 'Tự nhiên',
                    '{}', ?, ?, ?)
            """,
            (
                job_id,
                "Productivity Tips.mp4",
                json.dumps({"stt": 1680, "translation": 2100, "tts": 8400, "total": 12180}),
                now,
                now,
            ),
        )
        cursor = 0.0
        for position, (source, translated) in enumerate(DEMO_SEGMENTS, 1):
            length = 4.8 if position < 7 else 4.5
            conn.execute(
                """
                INSERT INTO segments
                (id, job_id, position, start, end, source_text, translated_text,
                 fit_score, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)
                """,
                (
                    f"demo-{position}",
                    job_id,
                    position,
                    cursor,
                    cursor + length,
                    source,
                    translated,
                    [92, 85, 96, 90, 88, 93, 79][position - 1],
                    now,
                ),
            )
            cursor += length
    return get_job(job_id) or {}


class Pipeline:
    def __init__(self, hook: EventHook):
        self.hook = hook
        # Cache client Google theo instance: tạo client mỗi lần gọi vừa chậm vừa tốn 1 lần
        # `gcloud auth print-access-token` (subprocess ~1-2s) khi dùng gcloud auth.
        self._client_lock = threading.Lock()
        self._cached_clients: dict[str, tuple[Any, float]] = {}

    def _cached_client(self, key: str, factory: Callable[[], Any]) -> Any:
        with self._client_lock:
            cached = self._cached_clients.get(key)
            if cached and time.monotonic() - cached[1] < CLIENT_TTL_SECONDS:
                return cached[0]
            client = factory()
            self._cached_clients[key] = (client, time.monotonic())
            return client

    async def process(self, job_id: str) -> None:
        try:
            if settings.effective_demo_mode:
                await self._demo_process(job_id)
            else:
                await asyncio.to_thread(self._real_process_sync, job_id)
            update_job(job_id, status="review", stage="translate", progress=52)
            await self.hook(job_id, {"type": "ready", "message": "Bản dịch đã sẵn sàng để duyệt."})
        except asyncio.CancelledError:
            update_job(job_id, status="cancelled", stage="cancelled")
            await self.hook(job_id, {"type": "cancelled"})
        except Exception as exc:
            update_job(job_id, status="failed", stage="failed", error=str(exc))
            await self.hook(job_id, {"type": "error", "message": str(exc)})

    async def _demo_process(self, job_id: str) -> None:
        stages = [
            ("probe", 8, "Đang kiểm tra video…"),
            ("separate", 22, "Đang tách thoại và nhạc nền…"),
            ("transcribe", 38, "Đang nhận dạng tiếng Anh…"),
            ("translate", 52, "Đang dịch tự nhiên sang tiếng Việt…"),
        ]
        for stage, progress, message in stages:
            await _stage(job_id, self.hook, stage, progress, message)
            await asyncio.sleep(0.45)
        self._replace_segments(job_id, DEMO_SEGMENTS, 4.8)
        update_job(
            job_id,
            duration=84,
            width=1920,
            height=1080,
            cost={"stt": 1680, "translation": 2100, "tts": 8400, "total": 12180},
        )

    def _real_process_sync(self, job_id: str) -> None:
        job = get_job(job_id, include_segments=False)
        if not job or not job["source_path"]:
            raise PipelineError("Không tìm thấy video nguồn.")
        source = Path(job["source_path"])
        work = settings.jobs_dir / job_id
        work.mkdir(parents=True, exist_ok=True)
        metadata = probe(source)
        if metadata["duration"] > 1800:
            raise PipelineError("Video vượt giới hạn 30 phút.")
        update_job(job_id, **metadata, stage="separate", progress=20)

        audio = work / "source.wav"
        run([settings.ffmpeg, "-y", "-i", str(source), "-vn", "-ac", "2", "-ar", "44100", str(audio)])
        background, vocals = self._separate(audio, work)
        artifacts = {"source_audio": str(audio), "background": str(background), "vocals": str(vocals)}
        update_job(job_id, artifacts=artifacts, stage="transcribe", progress=35)
        try:
            transcripts = self._transcribe(vocals, job_id)
        except PipelineError as exc:
            if "Không phát hiện" not in str(exc):
                raise
            transcripts = self._transcribe(audio, job_id)
        translated, context = self._translate(transcripts, job.get("style", "tự nhiên"))
        if context:
            # Lưu hướng dẫn dịch để bước viết-lại lúc export giữ đúng glossary/xưng hô.
            update_job(job_id, artifacts={**artifacts, "translate_context": context})
        self._replace_segments(
            job_id,
            [(item["text"], item["translated"]) for item in translated],
            default_length=4.8,
            timings=[(item["start"], item["end"]) for item in translated],
        )

    def _separate(self, audio: Path, work: Path) -> tuple[Path, Path]:
        output = work / "demucs"
        model = settings.demucs_model
        for device in ("cuda", "cpu"):
            try:
                command = [
                    sys.executable,
                    "-m",
                    "demucs",
                    "-n",
                    model,
                    "--two-stems",
                    "vocals",
                    "-d",
                    device,
                    "-o",
                    str(output),
                ]
                if settings.demucs_shifts > 0:
                    command += ["--shifts", str(settings.demucs_shifts)]
                command.append(str(audio))
                run(command, timeout=7200)
                stem = output / model / audio.stem
                return stem / "no_vocals.wav", stem / "vocals.wav"
            except Exception as exc:
                # Không nuốt lỗi: in lý do thật ra stderr để còn chẩn đoán (vd thiếu backend
                # ghi audio, OOM, thiếu cài) thay vì âm thầm rơi xuống fallback/device tiếp theo.
                detail = getattr(exc, "stderr", None) or str(exc)
                print(f"[demucs:{device}] thất bại, thử tiếp: {detail}", file=sys.stderr, flush=True)
                continue
        # Demucs lỗi (thiếu cài/OOM…). KHÔNG dùng nền im lặng — ưu tiên giữ nhạc nền gốc.
        return self._fallback_separation(audio, work)

    def _fallback_separation(self, audio: Path, work: Path) -> tuple[Path, Path]:
        """Tách dự phòng giữ nền: khử thoại bằng triệt kênh center; nếu lỗi dùng nguyên audio."""
        background = work / "background-fallback.wav"
        try:
            # Karaoke trick: trừ kênh trái-phải để loại giọng nằm giữa, giữ nhạc 2 bên.
            run(
                [
                    settings.ffmpeg,
                    "-y",
                    "-i",
                    str(audio),
                    "-af",
                    "pan=stereo|c0=c0-c1|c1=c1-c0",
                    str(background),
                ]
            )
        except Exception:
            background = audio
        # vocals = nguyên audio gốc để STT vẫn nhận được lời thoại.
        return background, audio

    def _transcribe(self, vocals: Path, job_id: str) -> list[dict[str, Any]]:
        if settings.stt_engine == "whisper":
            return self._transcribe_whisper(vocals)
        return self._transcribe_google(vocals, job_id)

    def _transcribe_whisper(self, vocals: Path) -> list[dict[str, Any]]:
        """STT local bằng faster-whisper (nhanh, miễn phí, không cần GCS)."""
        model = _get_whisper_model(settings.whisper_model)
        # word_timestamps=False: chỉ dùng start/end cấp đoạn, không đọc seg.words -> tắt để
        # bỏ hẳn pass căn chỉnh từng từ (DTW trên cross-attention), giảm thời gian STT.
        segments, _info = model.transcribe(
            str(vocals), language="en", vad_filter=True, word_timestamps=False
        )
        output: list[dict[str, Any]] = []
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            start = float(seg.start)
            end = max(float(seg.end), start + 0.5)
            output.append({"text": text, "start": start, "end": end})
        if not output:
            raise PipelineError("Không phát hiện được lời thoại.")
        return output

    def _transcribe_google(self, vocals: Path, job_id: str) -> list[dict[str, Any]]:
        from google.cloud import storage
        from google.cloud.speech_v2 import SpeechClient
        from google.cloud.speech_v2.types import cloud_speech

        storage_client = storage.Client(project=settings.google_project)
        bucket = storage_client.bucket(settings.gcs_bucket)
        object_name = f"video-dub/{job_id}/vocals.wav"
        bucket.blob(object_name).upload_from_filename(vocals)
        uri = f"gs://{settings.gcs_bucket}/{object_name}"
        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=["en-US"],
            model=settings.stt_model,
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=True,
                enable_word_time_offsets=True,
            ),
        )
        request = cloud_speech.BatchRecognizeRequest(
            recognizer=f"projects/{settings.google_project}/locations/global/recognizers/_",
            config=config,
            files=[cloud_speech.BatchRecognizeFileMetadata(uri=uri)],
            recognition_output_config=cloud_speech.RecognitionOutputConfig(
                inline_response_config=cloud_speech.InlineOutputConfig()
            ),
        )
        response = SpeechClient().batch_recognize(request=request).result(timeout=3600)
        output: list[dict[str, Any]] = []
        previous_end = 0.0
        for result in response.results[uri].transcript.results:
            if not result.alternatives:
                continue
            alt = result.alternatives[0]
            words = list(alt.words)
            start = _seconds(words[0].start_offset) if words else previous_end
            end = _seconds(words[-1].end_offset) if words else _seconds(result.result_end_offset)
            output.append({"text": alt.transcript.strip(), "start": start, "end": max(end, start + 0.5)})
            previous_end = end
        if not output:
            raise PipelineError("Không phát hiện được lời thoại.")
        return output

    def _genai_client(self):
        from google import genai

        return self._cached_client(
            "genai",
            lambda: genai.Client(
                vertexai=True,
                credentials=_active_gcloud_credentials(),
                project=settings.google_project,
                location=settings.google_region,
            ),
        )

    def _tts_client(self):
        from google.cloud import texttospeech

        return self._cached_client(
            "tts",
            lambda: texttospeech.TextToSpeechClient(credentials=_active_gcloud_credentials()),
        )

    def _build_context(self, client, segments: list[dict[str, Any]]) -> str:
        """Pass 1 lần: tóm tắt chủ đề + glossary để dịch nhất quán, sát nghĩa."""
        from google.genai import types

        transcript = " ".join(item["text"] for item in segments)[:12000]
        prompt = (
            "Đọc transcript tiếng Anh và soạn NGẮN GỌN bằng tiếng Việt bản HƯỚNG DẪN DỊCH "
            "dùng chung cho mọi phần của video (các phần được dịch song song nên hướng dẫn "
            "phải đủ để giữ nhất quán):\n"
            "- Chủ đề & bối cảnh (1-2 câu).\n"
            "- Glossary: mỗi dòng một mục dạng `EN => VI` cho thuật ngữ/tên riêng xuất hiện "
            "nhiều lần; ghi `EN => giữ nguyên` nếu không nên dịch.\n"
            "- Xưng hô: chọn DUY NHẤT một cặp đại từ (vd `tôi – bạn`) dùng xuyên suốt.\n"
            "- Văn phong nên dùng (1 câu).\n\n"
            f"Transcript:\n{transcript}"
        )
        try:
            response = _with_backoff(
                lambda: client.models.generate_content(
                    model=settings.gemini_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.2),
                ),
                label="lấy ngữ cảnh dịch",
            )
            return (response.text or "").strip()
        except Exception:
            return ""

    def _translate_chunk(
        self,
        client,
        indices: list[int],
        all_segments: list[dict[str, Any]],
        style: str,
        context: str,
    ) -> dict[int, str]:
        from google.genai import types

        lines = []
        for gi in indices:
            item = all_segments[gi]
            seconds = max(0.5, item["end"] - item["start"])
            lines.append(
                {
                    "index": gi,
                    "english": item["text"],
                    "max_seconds": round(seconds, 1),
                    "max_chars": max(12, int(seconds * VI_CHARS_PER_SEC)),
                    "prev_context_en": all_segments[gi - 1]["text"] if gi > 0 else "",
                    "next_context_en": all_segments[gi + 1]["text"] if gi + 1 < len(all_segments) else "",
                }
            )
        prompt = (
            "Bạn là chuyên gia lồng tiếng Anh→Việt. Dịch SÁT NGHĨA, tự nhiên, dễ đọc thành tiếng.\n"
            f"Phong cách: {style}.\n"
            "Quan trọng: mỗi câu dịch phải đọc VỪA trong 'max_seconds' (cố gắng không quá 'max_chars' "
            "ký tự) mà vẫn giữ đủ ý — ưu tiên câu gọn, lược từ đệm thừa thay vì cắt nội dung.\n"
            "Dùng prev/next context để giữ mạch, đại từ và thuật ngữ nhất quán.\n"
            "BẮT BUỘC tuân theo hướng dẫn dịch bên dưới: dùng đúng glossary và đúng cặp xưng hô "
            "đã chọn cho MỌI câu.\n\n"
            f"--- Hướng dẫn dịch (bối cảnh, glossary, xưng hô) ---\n{context}\n\n"
            f"--- Câu cần dịch (JSON) ---\n{json.dumps(lines, ensure_ascii=False)}\n\n"
            'Trả về DUY NHẤT một JSON array, mỗi phần tử {"index": <int>, "vi": "<bản dịch>"}.'
        )
        response = _with_backoff(
            lambda: client.models.generate_content(
                model=settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    # Nhiệt thấp để cùng thuật ngữ cho ra cùng bản dịch giữa các lô song song.
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            ),
            label="dịch theo lô",
        )
        return _parse_translations(response.text)

    def _translate(
        self, segments: list[dict[str, Any]], style: str = "tự nhiên"
    ) -> tuple[list[dict[str, Any]], str]:
        """Dịch toàn bộ segments; trả (kết quả, hướng dẫn dịch) để tái dùng khi viết-lại lúc export."""
        if not segments:
            return [], ""
        client = self._genai_client()
        context = self._build_context(client, segments)
        offsets = list(range(0, len(segments), TRANSLATE_BATCH))
        results: dict[int, str] = {}

        def work(offset: int) -> dict[int, str]:
            indices = list(range(offset, min(offset + TRANSLATE_BATCH, len(segments))))
            return self._translate_chunk(client, indices, segments, style, context)

        with ThreadPoolExecutor(max_workers=min(TRANSLATE_WORKERS, len(offsets))) as pool:
            for mapping in pool.map(work, offsets):
                results.update(mapping)

        # Dịch lại một lượt các câu model bỏ sót (JSON hỏng/thiếu index) trước khi chấp nhận
        # fallback — giữ nguyên tiếng Anh giữa video lộ rõ hơn nhiều so với một lời gọi thêm.
        missing = [index for index in range(len(segments)) if not results.get(index)]
        for offset in range(0, len(missing), TRANSLATE_BATCH):
            batch = missing[offset : offset + TRANSLATE_BATCH]
            try:
                results.update(self._translate_chunk(client, batch, segments, style, context))
            except Exception:
                break  # phần còn thiếu rơi xuống fallback tiếng Anh bên dưới

        # Fallback cuối: câu nào vẫn thiếu thì giữ nguyên tiếng Anh để không mất đoạn.
        return [
            {**item, "translated": results.get(index) or item["text"]}
            for index, item in enumerate(segments)
        ], context

    def _replace_segments(
        self,
        job_id: str,
        rows: list[tuple[str, str]],
        default_length: float,
        timings: list[tuple[float, float]] | None = None,
    ) -> None:
        with connect() as conn:
            conn.execute("DELETE FROM segments WHERE job_id = ?", (job_id,))
            cursor = 0.0
            for index, (source, translated) in enumerate(rows, 1):
                start, end = timings[index - 1] if timings else (cursor, cursor + default_length)
                score = fit_score(translated, end - start)
                conn.execute(
                    """
                    INSERT INTO segments
                    (id, job_id, position, start, end, source_text, translated_text,
                     fit_score, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        job_id,
                        index,
                        start,
                        end,
                        source,
                        translated,
                        score,
                        now_iso(),
                    ),
                )
                cursor = end

    async def regenerate(self, job_id: str, segment_id: str) -> None:
        segment = next(
            (item for item in (get_job(job_id) or {}).get("segments", []) if item["id"] == segment_id),
            None,
        )
        if not segment:
            raise PipelineError("Không tìm thấy phân đoạn.")
        update_segment(segment_id, status="processing")
        await self.hook(job_id, {"type": "segment", "segment_id": segment_id, "status": "processing"})
        if settings.effective_demo_mode:
            await asyncio.sleep(0.7)
            update_segment(segment_id, status="ready", fit_score=min(99, segment["fit_score"] + 3))
        else:
            await asyncio.to_thread(self._synthesize_segment, job_id, segment)
        await self.hook(job_id, {"type": "segment", "segment_id": segment_id, "status": "ready"})

    def _tts_bytes(self, text: str, job: dict[str, Any], seconds: float) -> bytes:
        from google.cloud import texttospeech

        prompt = (
            f"Đọc bằng tiếng Việt, phong cách {job.get('style', 'tự nhiên')}, rõ ràng, "
            f"vừa đúng {seconds:.1f} giây."
        )
        client = self._tts_client()
        _tts_limiter.wait()
        response = _with_backoff(
            lambda: client.synthesize_speech(
                input=texttospeech.SynthesisInput(text=text, prompt=prompt),
                voice=texttospeech.VoiceSelectionParams(
                    language_code="vi-VN",
                    name=job.get("voice", "Aoede"),
                    model_name=settings.tts_model,
                ),
                audio_config=texttospeech.AudioConfig(
                    audio_encoding=texttospeech.AudioEncoding.MP3,
                    sample_rate_hertz=24000,
                ),
            ),
            label="tạo giọng (TTS)",
        )
        return response.audio_content

    def _rewrite_shorter(
        self, source_en: str, current_vi: str, seconds: float, context: str = ""
    ) -> str | None:
        """Nhờ Gemini viết lại câu Việt ngắn hơn để đọc vừa khung giờ, giữ đủ ý."""
        from google.genai import types

        try:
            client = self._genai_client()
            guide = (
                f"\n--- Hướng dẫn dịch (BẮT BUỘC giữ đúng glossary và cặp xưng hô) ---\n{context}\n"
                if context
                else ""
            )
            prompt = (
                "Câu lồng tiếng tiếng Việt sau đọc bị DÀI hơn khung thời gian cho phép. "
                f"Hãy viết lại NGẮN GỌN hơn để đọc vừa khoảng {seconds:.1f} giây, "
                "vẫn giữ đủ ý chính, tự nhiên, không đổi cách xưng hô hay thuật ngữ. "
                "Chỉ trả về câu tiếng Việt mới.\n"
                f"{guide}"
                f"Câu gốc (English): {source_en}\n"
                f"Bản dịch hiện tại: {current_vi}"
            )
            response = _with_backoff(
                lambda: client.models.generate_content(
                    model=settings.gemini_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.3),
                ),
                label="viết lại câu ngắn hơn",
            )
            text = (response.text or "").strip()
            return text or None
        except Exception:
            return None

    def _synthesize_segment(self, job_id: str, segment: dict[str, Any]) -> Path:
        job = get_job(job_id, include_segments=False) or {}
        engine = resolve_tts_engine(job)
        suffix = segment_audio_suffix(engine)
        output = settings.jobs_dir / job_id / f"segment-{segment['position']:04d}{suffix}"
        seconds = max(0.5, segment["end"] - segment["start"])
        text = segment["translated_text"]

        # Vòng khớp độ dài: nếu TTS dài hơn khung quá ngưỡng thì viết lại ngắn hơn rồi synth lại.
        # VieNeu không nhận gợi ý thời lượng như Gemini nhưng vòng này (đo thật + viết lại
        # + atempo lúc render) vẫn khống chế được độ dài cho cả hai engine.
        context = (job.get("artifacts") or {}).get("translate_context", "")
        duration = 0.0
        for attempt in range(FIT_MAX_RETRIES + 1):
            if engine == "vieneu":
                _synth_vieneu(text, output)
            else:
                output.write_bytes(self._tts_bytes(text, job, seconds))
            duration = probe_audio(output)
            if duration <= seconds * FIT_TOLERANCE or attempt == FIT_MAX_RETRIES:
                break
            shorter = self._rewrite_shorter(segment["source_text"], text, seconds, context)
            if not shorter or shorter == text:
                break
            text = shorter

        update_segment(
            segment["id"],
            translated_text=text,
            audio_path=str(output),
            audio_duration=duration,
            fit_score=fit_score(text, seconds, duration),
            status="ready",
        )
        return output

    async def export(self, job_id: str) -> Path:
        job = get_job(job_id)
        if not job:
            raise PipelineError("Không tìm thấy dự án.")
        if settings.effective_demo_mode:
            output = settings.jobs_dir / job_id / "demo-export.txt"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("Demo mode: cấu hình Google Cloud và FFmpeg để render MP4 thật.", encoding="utf-8")
            update_job(job_id, status="completed", stage="export", progress=100, artifacts={**job["artifacts"], "video": str(output)})
            return output
        await _stage(job_id, self.hook, "voice", 68, "Đang tạo giọng Việt…")
        pending = [s for s in job["segments"] if s["status"] != "ready" or not s["audio_path"]]
        if pending:
            semaphore = asyncio.Semaphore(TTS_WORKERS)

            async def synth(segment: dict[str, Any]) -> None:
                async with semaphore:
                    await asyncio.to_thread(self._synthesize_segment, job_id, segment)

            await asyncio.gather(*(synth(segment) for segment in pending))
        await _stage(job_id, self.hook, "export", 88, "Đang mix và kết xuất MP4…")
        output = await asyncio.to_thread(self._render, job_id)
        update_job(job_id, status="completed", stage="export", progress=100, artifacts={**job["artifacts"], "video": str(output)})
        await self.hook(job_id, {"type": "completed", "url": f"/api/jobs/{job_id}/download"})
        return output

    def _render(self, job_id: str) -> Path:
        job = get_job(job_id) or {}
        work = settings.jobs_dir / job_id
        output = work / "dubbed-vi.mp4"
        inputs: list[str] = []
        filters: list[str] = []
        labels: list[str] = []
        # Kẹp cùng biên với atempo: "speed" tua nhanh CẢ video (hình + nền + thoại) nên
        # phải khớp dải mà atempo còn xử lý mượt, tránh méo tiếng nếu lỡ nhận giá trị lớn.
        speed = max(ATEMPO_MIN, min(ATEMPO_MAX, float(job.get("speed") or 1.0)))
        speed_changed = abs(speed - 1.0) > 1e-3
        pitch = float(job.get("pitch") or 0.0)
        pitch_chain = _pitch_chain(pitch)
        for index, segment in enumerate(job["segments"]):
            audio_path = Path(segment["audio_path"])
            # Dùng độ dài đã đo lúc TTS; chỉ probe lại khi thiếu (mp3 tạo bởi bản cũ).
            duration = float(segment.get("audio_duration") or 0.0)
            if duration <= 0:
                duration = probe_audio(audio_path)
            target = max(0.25, segment["end"] - segment["start"])
            # Khớp trong khung gốc rồi nhân thêm "speed" để theo kịp timeline đã bị nén lại.
            ratio = duration / target * speed
            # Mốc bắt đầu cũng phải chia cho speed để khớp đúng vị trí trên timeline đã tua nhanh.
            delay = int(segment["start"] / speed * 1000)
            inputs.extend(["-i", str(audio_path)])
            label = f"s{index}"
            filters.append(
                f"[{index}:a]{AUDIO_FORMAT},{_atempo_chain(ratio)},"
                f"adelay={delay}|{delay}[{label}]"
            )
            labels.append(f"[{label}]")
        # Bus thoại: cộng dồn KHÔNG chuẩn-hoá (tránh bug amix chia đôi âm lượng),
        # rồi chuẩn loudness về -16 LUFS và tách 2 nhánh: 1 để nghe, 1 làm khoá sidechain.
        filters.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,"
            f"loudnorm=I={NARRATION_LUFS}:TP=-1.5:LRA=11{pitch_chain},{AUDIO_FORMAT},"
            "asplit=2[narr_mix][narr_key]"
        )
        background = job["artifacts"]["background"]
        bg_index = len(job["segments"])
        source_index = bg_index + 1
        inputs.extend(["-i", background, "-i", job["source_path"]])
        # Nhạc nền tua nhanh cùng tỉ lệ "speed" để đồng bộ với hình + thoại (không "khớp
        # khung" như thoại vì nền là track liên tục, không có target riêng từng đoạn).
        bg_speed_chain = f",{_atempo_chain(speed)}" if speed_changed else ""
        # Giữ nguyên nền gốc; chỉ ducking (giảm nhẹ) khi có thoại Việt, trả lại đầy đủ khi im.
        filters.append(
            f"[{bg_index}:a]{AUDIO_FORMAT},volume={BG_VOLUME}{bg_speed_chain}[bg0];"
            f"[bg0][narr_key]sidechaincompress=threshold={DUCK_THRESHOLD}:ratio={DUCK_RATIO}:"
            f"attack={DUCK_ATTACK}:release={DUCK_RELEASE}[bg_ducked];"
            f"[bg_ducked][narr_mix]amix=inputs=2:normalize=0,"
            f"alimiter=limit={MIX_LIMIT}[mix]"
        )
        video_args: list[str]
        if speed_changed:
            # setpts nén timeline hình theo đúng "speed" -> phải re-encode, không copy được.
            filters.append(f"[{source_index}:v]setpts=PTS/{speed:.6f}[vout]")
            video_args = [
                "-map", "[vout]",
                "-c:v", VIDEO_CODEC,
                "-preset", VIDEO_PRESET,
                "-crf", VIDEO_CRF,
                "-pix_fmt", "yuv420p",
            ]
        else:
            video_args = ["-map", f"{source_index}:v:0", "-c:v", "copy"]
        run(
            [
                settings.ffmpeg,
                "-y",
                *inputs,
                "-filter_complex",
                ";".join(filters),
                *video_args,
                "-map",
                "[mix]",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(output),
            ],
            timeout=7200,
        )
        self._write_srt(job_id, speed)
        return output

    def _write_srt(self, job_id: str, speed: float = 1.0) -> Path:
        job = get_job(job_id) or {}
        output = settings.jobs_dir / job_id / "subtitles-vi.srt"
        chunks = []
        for index, segment in enumerate(job["segments"], 1):
            # Chia cho speed để phụ đề khớp đúng timeline đã tua nhanh của video xuất ra.
            start = segment["start"] / speed
            end = segment["end"] / speed
            chunks.append(
                f"{index}\n{format_srt(start)} --> {format_srt(end)}\n"
                f"{segment['translated_text']}\n"
            )
        output.write_text("\n".join(chunks), encoding="utf-8")
        return output


def probe_audio(path: Path) -> float:
    result = run(
        [
            settings.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return max(0.1, float(result.stdout.strip()))


def format_srt(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
