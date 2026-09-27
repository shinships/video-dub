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
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
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
# Khe an toàn chừa lại trước câu kế tiếp khi cho câu dài tràn sang khoảng lặng phía sau.
SPILL_GUARD_SECONDS = 0.12
# Tốc độ output mặc định cho job mới: tua nhanh toàn bộ video (hình + nhạc nền + thoại)
# 10%, đồng bộ tuyệt đối — không chỉ riêng nhịp đọc giọng lồng tiếng.
DEFAULT_JOB_SPEED = 1.1
# Video giữ nguyên tốc độ (copy, nhanh) chỉ khi speed ~= 1.0; khác 1.0 phải re-encode để
# áp setpts nên cần codec/preset cho nhánh này.
VIDEO_CODEC = "libx264"
VIDEO_PRESET = "veryfast"
VIDEO_CRF = "18"

# --- Tham số Ngôn ngữ gốc ---
SOURCE_LANG_CODE = os.environ.get("VIDEO_DUB_SOURCE_LANG", "en")
SOURCE_LANG_GOOGLE = os.environ.get("VIDEO_DUB_SOURCE_LANG_GOOGLE", "en-US")
SOURCE_LANG_NAME = os.environ.get("VIDEO_DUB_SOURCE_LANG_NAME", "Anh")

# --- Đo chi phí thật ---
# Sau khi bỏ Vbee, DỊCH là bước duy nhất tốn tiền: STT (Whisper), tách nền (Demucs), tạo
# giọng (VieNeu) và render (FFmpeg) đều chạy local. Giá USD cho mỗi 1 TRIỆU token, tra theo
# tên model. Model không có trong bảng -> vẫn đếm token nhưng không quy ra tiền (thà không
# hiện số tiền còn hơn hiện một con số bịa).
# CẬP NHẬT KHI GIÁ ĐỔI: Gemini 3.8 Flash đang ở giá giới thiệu, tăng gấp đôi từ 2027-01-01.
# DeepSeek lấy giá GIỜ CAO ĐIỂM (off-peak rẻ hơn ~2x) để ước tính không bị thấp hơn thực tế.
MODEL_PRICING_USD_PER_M: dict[str, dict[str, float]] = {
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75, "cached_input": 0.075},
    "deepseek-v4-pro": {"input": 1.32, "output": 3.96, "cached_input": 0.044},
}
USD_TO_VND = float(os.getenv("VIDEO_DUB_USD_TO_VND", "26000"))

# --- Tham số dịch ---
TRANSLATE_BATCH = 40  # Số câu mỗi lời gọi Gemini (dịch theo lô).
TRANSLATE_WORKERS = 4  # Số lô dịch song song.
# Token gcloud sống ~1h; client cache quá hạn này sẽ gọi API bằng token chết giữa chừng.
CLIENT_TTL_SECONDS = 1800.0
VI_CHARS_PER_SEC = 15.0  # Ước lượng ký tự tiếng Việt đọc được mỗi giây (khống chế độ dài).
# --- Tham số gộp câu (ghép đoạn STT vụn thành câu trọn vẹn trước khi dịch/TTS) ---
# Whisper hay cắt giữa câu -> dịch từng mảnh thiếu ngữ cảnh + TTS ngắt nghỉ vô duyên + gọi
# TTS gấp đôi (mỗi call bị giãn cách). Gộp lại giúp cả 3: dịch đủ ý, giọng liền mạch, ít call.
# Mỗi đoạn TTS được đặt ĐÚNG mốc gốc lúc render -> phần giữa cuối-đoạn và đầu-đoạn-kế là im
# lặng (thoại Việt thường ngắn hơn khung Anh). Câu bị tách thành nhiều mảnh => khoảng lặng đó
# rơi vào GIỮA một câu -> nghe "ngắt quãng trong 1 câu". Gộp đủ mảnh của cùng câu để tránh.
MERGE_MAX_GAP = 1.0  # Khe lặng tối đa (giây) mặc định còn cho phép nối tiếp (breath pause).
# Người dẫn hay ngắt nhấn 1-1.5s GIỮA câu. Nếu mảnh kế bắt đầu bằng chữ thường thì gần như chắc
# chắn là phần nối của cùng câu (STT tiếng Anh viết hoa đầu câu) -> nới khe lặng cho phép để
# không tách giữa câu, kể cả khi người dẫn ngừng lâu để nhấn.
MERGE_CONTINUATION_GAP = 1.6  # Khe lặng tối đa khi mảnh kế mở đầu bằng chữ thường (nối câu).
# merge_transcripts chỉ nối khi mảnh trước CHƯA hết câu, nên trần dưới đây bị chạm nghĩa là
# đang buộc tách GIỮA một câu — chính là lỗi "ngắt quãng trong 1 câu". Đặt cao hơn độ dài
# câu nói thực tế (quan sát tới ~20s) để trần chỉ còn là chốt an toàn cho run-on không dấu câu.
MERGE_MAX_SECONDS = 24.0  # Trần độ dài một câu gộp (chốt an toàn, hiếm khi chạm).
MERGE_MAX_CHARS = 480  # Trần ký tự một câu gộp.
SENTENCE_END = ".?!…"  # Đoạn trước tận cùng bằng các ký tự này coi như hết câu -> không nối.
# --- Dò giới tính người nói theo cao độ (F0) để lồng tiếng 2 giọng nam/nữ ---
# Chạy local trên vocals.wav đã tách sẵn: mỗi đoạn đo trung vị F0 các khung hữu thanh, F0 dưới
# ngưỡng -> nam, trên -> nữ. Không cần model/token mới. Chỉ chạy khi job bật multi_speaker.
GENDER_F0_THRESHOLD = float(os.getenv("VIDEO_DUB_GENDER_F0_THRESHOLD", "165.0"))  # Hz phân nam/nữ.
GENDER_F0_MIN = 70.0  # Sàn dải F0 hợp lệ (giọng nam trầm) — dưới mức này coi như nhiễu.
GENDER_F0_MAX = 300.0  # Trần dải F0 hợp lệ (giọng nữ cao) — trên mức này coi như nhiễu.
GENDER_MIN_VOICED_SEC = 0.3  # Đoạn có ít tiếng hữu thanh hơn mức này thì không đủ tin -> kế thừa.
GENDER_FRAME_SEC = 0.04  # Khung phân tích ~40ms (đủ dài để chứa >=1 chu kỳ giọng nam trầm).
GENDER_HOP_SEC = 0.02  # Bước nhảy khung 20ms.
GENDER_DEFAULT = "male"  # Nhãn mặc định khi đoạn đầu chưa đủ tin để dò.
# --- Soát lại nhất quán sau dịch song song (1 lời gọi Gemini) ---
# Các lô dịch song song nên xưng hô/thuật ngữ có thể trôi giữa lô dù đã có glossary chung.
REVIEW_MAX_CHARS = 24000  # Vượt trần này thì bỏ soát (phản hồi cho danh sách quá dài kém tin cậy).
# --- Tham số khớp độ dài lồng tiếng ---
FIT_TOLERANCE = 1.15  # TTS dài hơn khung quá tỉ lệ này thì viết lại ngắn hơn.
FIT_MAX_RETRIES = 2
# Số đoạn TTS tạo song song khi export. Quota Gemini-TTS theo phút thường thấp (đặc biệt
# project promo/free tier) — để mặc định thấp, chỉnh qua VIDEO_DUB_TTS_WORKERS nếu quota cao hơn.
TTS_WORKERS = int(os.getenv("VIDEO_DUB_TTS_WORKERS", "2"))
BACKOFF_MAX_RETRIES = 6
BACKOFF_BASE_SECONDS = 8.0
# --- Cắt lặng đầu/đuôi audio TTS trước khi đo độ dài ---
# VieNeu hay đệm 0.1-0.4s im lặng hai đầu -> đo dài giả, kích hoạt viết-lại/tăng tốc
# oan và gây cảm giác vào câu trễ. Ngưỡng thấp + giữ chút lặng cho êm, tránh cụt phụ âm nhẹ.
TTS_TRIM_THRESHOLD = "-50dB"
TTS_TRIM_KEEP = 0.06  # Giây lặng giữ lại mỗi đầu.


# Job nào đang chạy trong ngữ cảnh hiện tại. ContextVar chứ không phải biến toàn cục vì
# `asyncio.to_thread` COPY context sang thread — nhờ đó `run()` gọi từ trong thread TTS/render
# vẫn biết mình thuộc job nào mà không phải thêm tham số vào cả chục chỗ gọi.
CURRENT_JOB: ContextVar[str | None] = ContextVar("video_dub_current_job", default=None)


class _JobProcesses:
    """Sổ theo dõi subprocess đang sống theo job, để HUỶ là giết được thật.

    Trước đây huỷ chỉ được kiểm ở ranh giới `_stage()`: Demucs/Whisper/FFmpeg đã chạy rồi thì
    vẫn chạy tiếp tới hết, ăn CPU và có khi cả chục phút sau job mới thật sự dừng. Người dùng
    bấm huỷ xong thấy quạt vẫn rú là mất lòng tin ngay.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: dict[str, set[subprocess.Popen[str]]] = {}
        self._cancelled: set[str] = set()

    def add(self, job_id: str, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            self._live.setdefault(job_id, set()).add(proc)

    def discard(self, job_id: str, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            live = self._live.get(job_id)
            if live is not None:
                live.discard(proc)
                if not live:
                    self._live.pop(job_id, None)

    def cancel(self, job_id: str) -> int:
        """Đánh dấu huỷ và kết liễu mọi tiến trình con của job. Trả số tiến trình đã giết."""
        with self._lock:
            self._cancelled.add(job_id)
            procs = list(self._live.get(job_id, ()))
        killed = 0
        for proc in procs:
            try:
                if proc.poll() is None:
                    proc.terminate()  # SIGTERM: ffmpeg/demucs thoát sạch và nhanh
                    killed += 1
            except OSError:
                pass  # tiến trình vừa tự kết thúc — không có gì để giết
        return killed

    def is_cancelled(self, job_id: str | None) -> bool:
        if job_id is None:
            return False
        with self._lock:
            return job_id in self._cancelled

    def forget(self, job_id: str) -> None:
        """Quên job sau khi chạy xong. BẮT BUỘC, nếu không cờ huỷ còn lại sẽ làm lần chạy
        sau (bấm Thử lại) bị coi là đã huỷ ngay từ lệnh subprocess đầu tiên."""
        with self._lock:
            self._cancelled.discard(job_id)
            self._live.pop(job_id, None)


_job_processes = _JobProcesses()


def cancel_job_processes(job_id: str) -> int:
    return _job_processes.cancel(job_id)


def can_resume_export(job: dict[str, Any]) -> bool:
    """Job có đủ thứ để chạy lại pha export không (nền đã tách + có phân đoạn để đọc).

    Chỉ nhìn cột `stage` là chưa đủ: có job mang stage="export" nhưng thư mục rỗng và không
    có phân đoạn nào (gặp thật trong dữ liệu — job chết trước khi kịp ghi gì). Xếp nó chạy
    lại thì vỡ ở `job["artifacts"]["background"]` với `KeyError: 'background'` — thông báo
    vô nghĩa với người dùng, lại còn giống hệt một lỗi thật.
    """
    background = (job.get("artifacts") or {}).get("background")
    if not background or not Path(background).is_file():
        return False
    with connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM segments WHERE job_id = ?", (job["id"],)
        ).fetchone()[0]
    return count > 0


@contextmanager
def job_context(job_id: str):
    """Gắn mọi subprocess sinh ra bên trong vào `job_id`, và dọn sổ khi xong."""
    token = CURRENT_JOB.set(job_id)
    try:
        yield
    finally:
        CURRENT_JOB.reset(token)
        _job_processes.forget(job_id)


def run(command: list[str], timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    """Chạy subprocess, có đăng ký để huỷ job là giết được ngay.

    Dùng Popen thay cho `subprocess.run` vì `run()` chỉ trả về khi tiến trình đã xong — không
    có handle nào để giết giữa chừng.
    """
    job_id = CURRENT_JOB.get()
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Không set encoding -> subprocess dùng locale mặc định (cp1252 trên Windows),
        # crash reader thread nếu subprocess (ffmpeg/demucs) in byte ngoài cp1252 và
        # nuốt mất log thật của lỗi gốc. Ép UTF-8 + thay thế ký tự lỗi thay vì crash.
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if job_id:
        _job_processes.add(job_id, proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except BaseException:
        # Quá giờ hoặc bị ngắt: giết rồi thu xác, đừng để tiến trình mồ côi chạy tiếp.
        proc.kill()
        proc.communicate()
        raise
    finally:
        if job_id:
            _job_processes.discard(job_id, proc)

    # Kiểm tra huỷ TRƯỚC khi xét returncode: tiến trình bị ta giết sẽ trả mã lỗi, báo thành
    # "FFmpeg thất bại" thì che mất nguyên nhân thật là người dùng vừa bấm huỷ.
    if _job_processes.is_cancelled(job_id):
        raise asyncio.CancelledError
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, command, stdout, stderr)
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


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


def check_duration(seconds: float) -> None:
    """Chốt chặn thời lượng DUY NHẤT của dự án.

    Trước đây bốn chỗ nói bốn con số: `service.MAX_DURATION_SECONDS` là 14400 nhưng câu báo
    lỗi ngay dưới nó ghi "30 phút", pipeline tự kiểm lại và báo "4 giờ", còn UI ghi "30 phút".
    Người dùng tải video 45 phút lên sẽ được nhận — rồi đọc thông báo nói giới hạn là 30 phút.
    """
    limit = settings.max_duration_seconds
    if seconds > limit:
        raise PipelineError(
            f"Video dài {seconds / 60:.0f} phút, vượt giới hạn {settings.duration_limit_label}. "
            "Đổi mức này trong Cài đặt nếu cần."
        )


def _seconds(duration: Any) -> float:
    if duration is None:
        return 0.0
    if hasattr(duration, "total_seconds"):
        return float(duration.total_seconds())
    return float(getattr(duration, "seconds", duration))


def _new_vertex_client(genai: Any) -> Any:
    with google_auth_scope():
        return genai.Client(
            vertexai=True,
            credentials=_active_gcloud_credentials(),
            project=settings.google_project,
            location=settings.google_region,
        )


@contextmanager
def google_auth_scope():
    """Tạm ẩn GOOGLE_APPLICATION_CREDENTIALS trong lúc DỰNG client Google, rồi trả lại nguyên trạng.

    Biến này hay được set toàn hệ thống cho một công cụ KHÁC (vd service account của Google
    Docs) và sẽ âm thầm chiếm quyền xác thực, khiến Speech/TTS bị từ chối dù project đích đã
    bật API. Bản cũ xử lý bằng cách `os.environ.pop(...)` ngay lúc import config — tức xoá
    vĩnh viễn khỏi cả tiến trình. Chấp nhận được với script cá nhân, KHÔNG chấp nhận được với
    phần mềm cài trên máy người khác: nó phá luôn mọi thư viện Google khác trong cùng tiến trình.

    Client Google phân giải credentials ngay lúc khởi tạo nên chỉ cần che trong phạm vi đó.
    """
    if not settings.google_prefer_adc:
        yield
        return
    saved = os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = saved


def _active_gcloud_credentials():
    if not settings.google_gcloud_token:
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


def segment_tempo(duration: float, slot: float, avail: float) -> float:
    """Tempo cho từng câu thoại. KHÔNG kéo chậm câu ngắn để lấp khung (mỗi câu một
    tốc độ nghe rất không đều); chỉ tăng tốc khi audio dài hơn cả chỗ trống thực tế
    (khung + khoảng lặng tới câu kế tiếp), và kẹp nhẹ để giọng không méo."""
    avail = max(slot, avail, 0.25)
    if duration <= avail:
        return 1.0
    return min(ATEMPO_MAX, duration / avail)


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


# Giọng preset VieNeu nằm sẵn trong package (assets/voices_v3_turbo.json của mode v3turbo —
# mode mặc định của factory Vieneu()). Đọc thẳng file JSON nên KHÔNG phải nạp model: /api/voices
# và bước kiểm tra cấu hình chạy được cả khi máy chưa từng load VieNeu.
VIENEU_VOICES_ASSET = "voices_v3_turbo.json"
_vieneu_voices_cache: list[dict[str, Any]] | None = None


def parse_vieneu_voices(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Bóc preset giọng từ voices_v3_turbo.json -> [{id, label, desc, gender}].
    Tên preset (khoá) chính là giá trị đặt cho VIDEO_DUB_VIENEU_VOICE."""
    presets = data.get("presets") if isinstance(data.get("presets"), dict) else {}
    voices: list[dict[str, Any]] = []
    for name, item in presets.items():
        if not name:
            continue
        info = item if isinstance(item, dict) else {}
        gender = info.get("gender") or ""
        description = info.get("description") or ""
        voices.append(
            {
                "id": name,
                "label": name,
                "desc": description or gender,
                "gender": gender,
            }
        )
    return voices


def vieneu_preset_voices() -> list[dict[str, Any]]:
    """Danh sách giọng preset VieNeu (cache theo tiến trình). Chưa cài vieneu / thiếu asset
    -> rỗng, nơi gọi tự lùi về giọng cấu hình trong env."""
    global _vieneu_voices_cache

    if _vieneu_voices_cache is not None:
        return list(_vieneu_voices_cache)
    try:
        import vieneu

        asset = Path(vieneu.__file__).parent / "assets" / VIENEU_VOICES_ASSET
        voices = parse_vieneu_voices(json.loads(asset.read_text(encoding="utf-8")))
    except Exception as exc:
        print(f"[vieneu] không đọc được danh sách giọng preset: {exc}", file=sys.stderr)
        voices = []
    _vieneu_voices_cache = voices
    return list(voices)


def unknown_vieneu_voices(cfg: Any = None, known: list[str] | None = None) -> list[str]:
    """Các giọng VieNeu cấu hình trong env nhưng không có trong preset. VieNeu ném
    ValueError('Voice ... not found') ngay lúc synth, nên bắt sớm ở đây để không vỡ sau khi
    đã tốn STT + dịch. Giọng nào có ref_audio (nhân bản) thì preset không được dùng -> bỏ qua."""
    cfg = settings if cfg is None else cfg  # đọc settings lúc gọi, không bind lúc định nghĩa
    if known is None:
        known = [voice["id"] for voice in vieneu_preset_voices()]
    if not known:  # không đọc được preset -> không kết luận gì, tránh báo lỗi oan
        return []
    candidates = [
        (cfg.vieneu_voice, cfg.vieneu_ref_audio),
        (cfg.vieneu_voice_male, cfg.vieneu_ref_audio_male),
        (cfg.vieneu_voice_female, cfg.vieneu_ref_audio_female),
    ]
    return [voice for voice, ref_audio in candidates if voice and not ref_audio and voice not in known]


def ensure_engine_voices_ready(engine: str) -> None:
    """Chặn sớm khi giọng cấu hình sai tên (chỉ VieNeu kiểm tra được offline)."""
    if engine != "vieneu":
        return
    unknown = unknown_vieneu_voices()
    if unknown:
        available = ", ".join(voice["id"] for voice in vieneu_preset_voices())
        raise PipelineError(
            f"Giọng VieNeu không tồn tại: {', '.join(unknown)}. Giọng hợp lệ: {available}."
        )


# Engine TTS duy nhất còn lại (Vbee và Gemini TTS đều đã bỏ). Giữ hằng số thay vì rải chuỗi
# "vieneu" khắp nơi để lần thêm engine sau chỉ phải sửa một chỗ.
TTS_ENGINE = "vieneu"


def segment_audio_suffix(engine: str = TTS_ENGINE) -> str:
    """Đuôi file audio đoạn. VieNeu save ra WAV."""
    return ".wav"


def resolve_tts_engine(job: dict[str, Any]) -> str:
    """Chỉ còn VieNeu. Job cũ trong DB có thể còn tts_engine='vbee' (engine đã gỡ) — ép về
    VieNeu thay vì để job hỏng ở bước TTS, vì user không sửa được cột đó từ UI nữa."""
    return TTS_ENGINE


# Giọng "chưa chọn" trong DB: "Aoede" là tên giọng của Vertex AI (engine TTS cũ, đã bỏ) và vẫn
# đang là mặc định của cột jobs.voice — không phải preset VieNeu nên phải bỏ qua, dùng giọng
# cấu hình trong env.
LEGACY_VOICE_PLACEHOLDERS = {None, "", "Aoede"}


def resolve_segment_voice(
    engine: str,
    speaker: str | None,
    multi_speaker: bool,
    cfg: Any = settings,
    job_voice: str | None = None,
    known_voices: list[str] | None = None,
) -> Any:
    """Chọn giọng cho một đoạn: trả infer_kwargs (dict) cho VieNeu.
    Multi-speaker TẮT (hoặc speaker không phải nam/nữ) -> giọng mặc định 1-giọng như cũ. BẬT:
    female -> giọng nữ, male -> giọng nam; giọng giới tính chưa cấu hình thì fallback về mặc
    định (giọng nam mặc định kế thừa cấu hình 1-giọng nên không phải khai lại).
    Giọng chọn trên UI (job_voice) thắng env ở chế độ 1-giọng; truyền known_voices để bỏ qua
    giọng của engine khác (job đổi engine sau khi đã chọn giọng thì jobs.voice không còn hợp lệ)."""
    if job_voice in LEGACY_VOICE_PLACEHOLDERS or (
        known_voices is not None and job_voice not in known_voices
    ):
        job_voice = None
    # VieNeu (và mọi engine local khác dùng infer_kwargs): giọng chọn trên UI là preset, nên
    # thắng cả ref_audio trong env (người dùng vừa chỉ định rõ giọng khác).
    default_kwargs = (
        {"voice": job_voice} if job_voice else vieneu_infer_kwargs(cfg.vieneu_voice, cfg.vieneu_ref_audio)
    )
    if not multi_speaker or speaker not in ("male", "female"):
        return default_kwargs
    if speaker == "female":
        return vieneu_infer_kwargs(cfg.vieneu_voice_female, cfg.vieneu_ref_audio_female) or default_kwargs
    return vieneu_infer_kwargs(cfg.vieneu_voice_male, cfg.vieneu_ref_audio_male) or default_kwargs


_VI_DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
_VI_SCALES = ["", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ"]


def _vi_three(n: int, full: bool) -> str:
    """Đọc nhóm 3 chữ số (0-999). full=True: nhóm không đứng đầu -> đọc cả 'không trăm', 'lẻ'."""
    h, t, u = n // 100, (n // 10) % 10, n % 10
    words: list[str] = []
    if h or full:
        words += [_VI_DIGITS[h], "trăm"]
    if t == 0:
        if u and (h or full):
            words.append("lẻ")
    elif t == 1:
        words.append("mười")
    else:
        words += [_VI_DIGITS[t], "mươi"]
    if u:
        if u == 1 and t > 1:
            words.append("mốt")
        elif u == 5 and t > 0:
            words.append("lăm")
        elif u == 4 and t > 1:
            words.append("tư")
        else:
            words.append(_VI_DIGITS[u])
    return " ".join(words)


def vi_number_words(n: int) -> str:
    """Số nguyên -> chữ tiếng Việt: 15000 -> 'mười lăm nghìn', 1250000 -> 'một triệu hai trăm năm mươi nghìn'."""
    if n == 0:
        return "không"
    groups: list[int] = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    if len(groups) > len(_VI_SCALES):
        return " ".join(_VI_DIGITS[int(d)] for d in str(n))
    parts: list[str] = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        parts.append(_vi_three(g, full=i != len(groups) - 1))
        if _VI_SCALES[i]:
            parts.append(_VI_SCALES[i])
    return " ".join(parts)


_CURRENCY_WORDS = {"USD": "đô la", "US$": "đô la", "$": "đô la", "VND": "đồng", "VNĐ": "đồng", "đ": "đồng", "EUR": "ơ rô", "€": "ơ rô"}


def normalize_numbers_for_tts(text: str) -> str:
    """Chuẩn hoá số trước khi đưa TTS: bỏ dấu phân cách nghìn (15.000 / 15,000 -> 15000),
    đổi dấu thập phân về dạng đọc 'phẩy', đổi $/USD... thành chữ, % -> phần trăm."""
    import re

    def _group(m: "re.Match[str]") -> str:
        return m.group(0).replace(".", "").replace(",", "")

    # Số có nhóm nghìn chuẩn: 1.234.567 hoặc 1,234,567 (mỗi nhóm đúng 3 chữ số).
    text = re.sub(r"(?<![\d.,])\d{1,3}(?:([.,])\d{3})(?:\1\d{3})*(?![\d]|[.,]\d)", _group, text)
    # Số thập phân còn lại: 1,5 / 2.75 -> "1 phẩy 5".
    text = re.sub(r"(\d)[.,](\d)", r"\1 phẩy \2", text)
    text = re.sub(r"(\d)\s*%", r"\1 phần trăm", text)
    # Ký hiệu tiền đứng TRƯỚC số ($15000) -> sau số; đứng sau (15000 USD) -> chữ.
    text = re.sub(r"(US\$|\$|€)\s*(\d+(?: phẩy \d+)?)", lambda m: f"{m.group(2)} {_CURRENCY_WORDS[m.group(1)]}", text)
    text = re.sub(r"(\d)\s*(USD|VNĐ|VND|EUR|đ)\b", lambda m: f"{m.group(1)} {_CURRENCY_WORDS[m.group(2)]}", text)
    text = re.sub(r"(\d)\s*[-–]\s*(\d)", r"\1 đến \2", text)  # 3-4 giờ -> 3 đến 4 giờ
    # Cuối cùng đổi mọi số nguyên thành chữ (VieNeu không tự chuẩn hoá số -> đọc sai/bỏ sót).
    # Số dài >15 chữ số (mã, số điện thoại) đọc từng chữ số.
    text = re.sub(
        r"\d+",
        lambda m: vi_number_words(int(m.group(0))) if len(m.group(0)) <= 15 and not m.group(0).startswith("0") or m.group(0) == "0"
        else " ".join(_VI_DIGITS[int(d)] for d in m.group(0)),
        text,
    )
    return text


def _synth_vieneu(text: str, output: Path, infer_kwargs: dict[str, str] | None = None) -> None:
    """TTS local bằng VieNeu (không gọi cloud). Giữ lock xuyên suốt nạp + suy luận:
    model giữ trạng thái nội bộ nên không an toàn khi gọi song song, và suy luận vốn
    nghẽn CPU — chạy tuần tự không làm chậm thêm so với chạy chồng lên nhau."""
    global _vieneu_model
    import re
    import numpy as np
    with _vieneu_lock:
        if _vieneu_model is None:
            from vieneu import Vieneu

            _vieneu_model = Vieneu(device=settings.vieneu_device)
        if infer_kwargs is None:
            infer_kwargs = vieneu_infer_kwargs(settings.vieneu_voice, settings.vieneu_ref_audio)
        
        # Split text to avoid OOM on long segments
        max_len = 50
        text = normalize_numbers_for_tts(text)
        # Không tách tại dấu chấm/phẩy nằm GIỮA hai chữ số (15.000, 1,5) — trước đây tách thành
        # "15." + "000" nên đọc thành "mười lăm ... không không không".
        parts = re.split(r'((?:(?<!\d)[.,]|[.,](?!\d)|[:;?!])+)', text)
        chunks = []
        current = ""
        for part in parts:
            if len(current) + len(part) <= max_len:
                current += part
            else:
                if current:
                    chunks.append(current.strip())
                current = part
        if current:
            chunks.append(current.strip())
        
        final_chunks = []
        for c in chunks:
            while len(c) > max_len:
                idx = c.rfind(' ', 0, max_len)
                if idx == -1: idx = max_len
                final_chunks.append(c[:idx].strip())
                c = c[idx:].strip()
            if c:
                final_chunks.append(c)
        
        audios = []
        for chunk in final_chunks:
            if not chunk: continue
            try:
                a = _vieneu_model.infer(chunk, **infer_kwargs)
                audios.append(a)
            except Exception:
                # If ONNX memory error occurs on a chunk, split in half and retry
                mid = len(chunk) // 2
                idx = chunk.rfind(' ', 0, mid)
                if idx == -1: idx = mid
                sub1, sub2 = chunk[:idx].strip(), chunk[idx:].strip()
                if sub1:
                    audios.append(_vieneu_model.infer(sub1, **infer_kwargs))
                if sub2:
                    audios.append(_vieneu_model.infer(sub2, **infer_kwargs))
        
        if not audios:
            # Fallback for empty
            audios = [np.zeros(1, dtype=np.float32)]
            
        final_audio = np.concatenate(audios, axis=0)
        _vieneu_model.save(final_audio, str(output))
        import gc
        gc.collect()


def _is_rate_limited(exc: Exception) -> bool:
    """Phát hiện lỗi quota/rate-limit (429 / RESOURCE_EXHAUSTED), server tạm quá tải (503 / UNAVAILABLE), hoặc timeout mạng."""
    name = type(exc).__name__
    if any(k in name for k in ("ResourceExhausted", "TooManyRequests", "ServerError", "ServiceUnavailable", "Timeout", "TimeOut")):
        return True
    code = getattr(exc, "code", None)
    if callable(code) and any(k in str(code()) for k in ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED")):
        return True
    text = str(exc).upper()
    return any(k in text for k in ("RESOURCE_EXHAUSTED", "429", "503", "UNAVAILABLE", "HIGH DEMAND", "RATE_LIMIT", "QUOTA", "TIMED OUT", "TIMEOUT"))


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


_translation_list_schema: Any = None


def _translation_schema() -> Any:
    """Schema JSON cho response_schema (Gemini structured output). Không ép response_schema
    -> model thỉnh thoảng chèn dấu ngoặc kép " chưa escape vào nội dung dịch (vd trích một cụm
    từ), làm hỏng cú pháp JSON và hỏng NGUYÊN CẢ LÔ (rơi về fallback giữ tiếng Anh cho mọi câu
    trong lô). response_schema ép model tự tránh/escape đúng, giảm hẳn lỗi này."""
    global _translation_list_schema
    if _translation_list_schema is None:
        from pydantic import BaseModel

        class TranslationItem(BaseModel):
            index: int
            vi: str

        _translation_list_schema = list[TranslationItem]
    return _translation_list_schema


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


# Nháy/ngoặc hay bám sau dấu kết câu ("word." -> word.") khi xét ranh giới câu.
_TRAILING_QUOTES = "\"”’')"


def _sentence_piece(words: list[dict[str, Any]]) -> dict[str, Any]:
    text = " ".join(w["text"] for w in words)
    start = float(words[0]["start"])
    return {"text": text, "start": start, "end": max(float(words[-1]["end"]), start)}


def split_sentences(fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tách mảnh STT tại ranh giới câu theo timestamp từng từ. Whisper cắt mảnh theo cửa sổ
    âm thanh nên mảnh ~10s thường chứa vài câu và đứt GIỮA câu; nếu giữ nguyên,
    merge_transcripts phải nối cả mảnh (vượt trần độ dài) -> câu vẫn tách đôi, dịch rời rạc
    và lồng tiếng ngắt quãng giữa câu. Tách nhỏ theo câu trước để bước gộp chỉ còn phải nối
    các mẩu của cùng một câu. Mảnh không có word timestamps được giữ nguyên."""
    pieces: list[dict[str, Any]] = []
    for frag in fragments:
        words = [w for w in (frag.get("words") or []) if w.get("text")]
        if not words:
            pieces.append({"text": frag["text"], "start": frag["start"], "end": frag["end"]})
            continue
        buffer: list[dict[str, Any]] = []
        for index, word in enumerate(words):
            buffer.append(word)
            token = word["text"].rstrip(_TRAILING_QUOTES)
            next_char = words[index + 1]["text"][:1] if index + 1 < len(words) else ""
            # Chỉ tách khi từ kế không mở đầu bằng chữ thường: né viết tắt kiểu "e.g. we".
            if token[-1:] in SENTENCE_END and not next_char.islower():
                pieces.append(_sentence_piece(buffer))
                buffer = []
        if buffer:
            pieces.append(_sentence_piece(buffer))
    return pieces


def merge_transcripts(
    segments: list[dict[str, Any]],
    max_gap: float = MERGE_MAX_GAP,
    max_seconds: float = MERGE_MAX_SECONDS,
    max_chars: float = MERGE_MAX_CHARS,
    continuation_gap: float = MERGE_CONTINUATION_GAP,
) -> list[dict[str, Any]]:
    """Ghép các đoạn STT vụn (Whisper hay cắt giữa câu) thành câu trọn vẹn trước khi dịch/TTS.
    Nối đoạn kế khi đoạn trước CHƯA hết câu (không tận cùng bằng .?!…), khe lặng còn trong ngưỡng
    và câu gộp chưa vượt trần độ dài/ký tự — giúp dịch đủ ngữ cảnh và giọng đọc liền mạch, ít call
    TTS. Mảnh kế mở đầu bằng chữ thường => gần như chắc chắn nối tiếp cùng câu (STT tiếng Anh viết
    hoa đầu câu) nên nới khe lặng cho phép (continuation_gap) để không tách giữa câu khi người dẫn
    ngừng lâu để nhấn giọng."""
    merged: list[dict[str, Any]] = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg["start"])
        end = max(float(seg["end"]), start)
        if merged:
            prev = merged[-1]
            ends_sentence = prev["text"].rstrip()[-1:] in SENTENCE_END
            # Chữ thường đầu mảnh = tín hiệu mạnh "còn giữa câu" -> cho phép khe lặng rộng hơn.
            continues = text[:1].islower()
            gap_allowed = continuation_gap if continues else max_gap
            joined_len = len(prev["text"]) + 1 + len(text)
            fits = (
                not ends_sentence
                and (start - prev["end"]) <= gap_allowed
                and (end - prev["start"]) <= max_seconds
                and joined_len <= max_chars
            )
            if fits:
                prev["text"] = f"{prev['text']} {text}"
                prev["end"] = end
                continue
        merged.append({"text": text, "start": start, "end": end})
    return merged


def classify_gender(median_f0: float, threshold: float = GENDER_F0_THRESHOLD) -> str:
    """Phân giới tính người nói theo trung vị F0: dưới ngưỡng -> nam, bằng/trên -> nữ."""
    return "male" if median_f0 < threshold else "female"


def assign_speakers(
    f0_values: list[float | None],
    threshold: float = GENDER_F0_THRESHOLD,
    default: str = GENDER_DEFAULT,
) -> list[str]:
    """Gán nhãn 'male'/'female' cho từng đoạn từ danh sách trung vị F0. Đoạn None (không đủ
    tiếng hữu thanh để tin) KẾ THỪA nhãn đoạn liền trước cho mượt (im lặng/ậm ừ giữa lượt nói
    không nên đảo giọng); đoạn đầu mà None thì dùng nhãn mặc định."""
    labels: list[str] = []
    previous = default
    for value in f0_values:
        label = classify_gender(value, threshold) if value is not None else previous
        labels.append(label)
        previous = label
    return labels


def segment_median_f0(
    samples: Any,
    sr: int,
    start: float,
    end: float,
    f0_min: float = GENDER_F0_MIN,
    f0_max: float = GENDER_F0_MAX,
    min_voiced_sec: float = GENDER_MIN_VOICED_SEC,
) -> float | None:
    """Đo trung vị F0 các khung hữu thanh trong lát [start,end] của tín hiệu mono bằng tự
    tương quan (numpy). Trả None nếu tổng thời lượng khung hữu thanh < min_voiced_sec (không
    đủ tin). Chỉ nhận F0 rơi trong dải [f0_min,f0_max] để loại nhiễu/nhạc nền còn sót."""
    import numpy as np

    a = int(max(0.0, start) * sr)
    b = int(max(start, end) * sr)
    clip = np.asarray(samples[a:b], dtype=np.float64)
    if clip.size < int(GENDER_FRAME_SEC * sr):
        return None
    frame = int(GENDER_FRAME_SEC * sr)
    hop = max(1, int(GENDER_HOP_SEC * sr))
    lag_min = max(1, int(sr / f0_max))
    lag_max = min(frame - 1, int(sr / f0_min))
    if lag_max <= lag_min:
        return None
    # Ngưỡng năng lượng khung hữu thanh: theo RMS toàn lát (bỏ khung lặng/nhiễu nhỏ).
    rms_all = float(np.sqrt(np.mean(clip**2))) if clip.size else 0.0
    energy_floor = max(1e-4, rms_all * 0.5)
    pitches: list[float] = []
    for offset in range(0, clip.size - frame + 1, hop):
        window = clip[offset : offset + frame]
        rms = float(np.sqrt(np.mean(window**2)))
        if rms < energy_floor:
            continue
        window = window - window.mean()
        corr = np.correlate(window, window, mode="full")[frame - 1 :]
        if corr[0] <= 0:
            continue
        segment = corr[lag_min : lag_max + 1]
        if segment.size == 0:
            continue
        peak = int(np.argmax(segment)) + lag_min
        # Đỉnh tự tương quan phải đủ rõ so với năng lượng khung (corr[0]) mới coi là hữu thanh.
        if corr[peak] < 0.3 * corr[0]:
            continue
        pitches.append(sr / peak)
    if len(pitches) * hop / sr < min_voiced_sec:
        return None
    return float(np.median(pitches))


async def _stage(job_id: str, hook: EventHook, stage: str, progress: int, message: str) -> None:
    job = get_job(job_id, include_segments=False)
    if not job or job["cancelled"]:
        raise asyncio.CancelledError
    update_job(job_id, stage=stage, progress=progress, status="processing", error=None)
    await hook(job_id, {"type": "progress", "stage": stage, "progress": progress, "message": message})


def cleanup_job_intermediates(job_id: str) -> int:
    """Xoá file trung gian của một job sau khi xuất xong, trả số byte đã giải phóng.

    Chỗ phình đĩa lớn nhất là `narration-batch-*.wav`: mỗi batch dùng `adelay` theo mốc
    TUYỆT ĐỐI nên file nào cũng trải dài từ giây 0, tức dung lượng tăng dần theo O(n²) —
    đo thực tế một job 30 phút để lại ~4.2GB rác cho 379MB kết quả. Chúng được dựng lại
    ở mỗi lần render nên xoá là an toàn tuyệt đối.

    GIỮ LẠI những thứ cần cho lần export sau mà không phải chạy lại pipeline:
    `no_vocals.wav` (nền, dùng lại khi user sửa bản dịch rồi xuất lại) và `segment-*.wav`
    (khỏi phải TTS lại). Nếu xoá cả hai thì mỗi lần sửa một câu sẽ phải tách nền + đọc lại
    toàn bộ video.
    """
    work = settings.jobs_dir / job_id
    if not work.is_dir():
        return 0
    doomed: list[Path] = [
        *work.glob("narration-batch-*.wav"),
        *work.glob("filter-batch-*.txt"),
        *work.glob("filter-complex.txt"),
    ]
    source_audio = work / "source.wav"
    if source_audio.is_file():
        # Chỉ dùng cho tách nền + STT; cả hai đã xong. Cần lại thì trích từ video gốc (rẻ).
        doomed.append(source_audio)
    doomed.extend(work.glob("demucs/*/*/vocals.wav"))  # chỉ phục vụ STT + dò giới tính.

    freed = 0
    for path in doomed:
        try:
            freed += path.stat().st_size
            path.unlink()
        except OSError as exc:  # đĩa lỗi/quyền — không đáng làm hỏng job đã xuất xong
            print(f"[cleanup] bỏ qua {path.name}: {exc}", file=sys.stderr)
    return freed


def delete_job_files(job_id: str) -> int:
    """Xoá TOÀN BỘ file của một job (thư mục job + video gốc đã upload). Trả số byte đã xoá."""
    freed = 0
    work = settings.jobs_dir / job_id
    if work.is_dir():
        freed += sum(f.stat().st_size for f in work.rglob("*") if f.is_file())
        shutil.rmtree(work, ignore_errors=True)
    for upload in settings.uploads_dir.glob(f"{job_id}.*"):
        try:
            freed += upload.stat().st_size
            upload.unlink()
        except OSError:
            pass
    return freed


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


class _MultiKeyModelsProxy:
    def __init__(self, manager: _MultiKeyGenaiClient):
        self._manager = manager

    def generate_content(self, *args: Any, **kwargs: Any) -> Any:
        return self._manager.call_generate_content(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        _, client, _ = self._manager.get_active_client()
        return getattr(client.models, name)


class _MultiKeyGenaiClient:
    """Quản lý và xoay vòng (round-robin / failover) nhiều API key Gemini.
    - Phân bổ đều các lời gọi (lô song song) qua các key để nhân đôi/nhân ba hạn mức RPM.
    - Khi một key gặp lỗi quota (429 / RESOURCE_EXHAUSTED), tự động chuyển ngay sang key kế tiếp
      thay vì phải dừng chờ backoff luỹ thừa.
    """

    def __init__(
        self,
        api_keys: list[str],
        client_factory: Callable[[str], Any] | None = None,
    ):
        self._keys = [k.strip() for k in api_keys if k.strip()]
        if not self._keys:
            raise PipelineError("Không có Gemini API key nào hợp lệ.")
        if client_factory is not None:
            self._clients = [client_factory(k) for k in self._keys]
        else:
            from google import genai
            from google.genai import types

            self._clients = [
                genai.Client(api_key=k, http_options=types.HttpOptions(timeout=120000))
                for k in self._keys
            ]
        self._lock = threading.Lock()
        self._current_index = 0
        self.models = _MultiKeyModelsProxy(self)

    def get_active_client(self) -> tuple[int, Any, str]:
        with self._lock:
            idx = self._current_index % len(self._clients)
            return idx, self._clients[idx], self._keys[idx]

    def _next_client_index(self) -> int:
        with self._lock:
            idx = self._current_index % len(self._clients)
            self._current_index = (self._current_index + 1) % len(self._clients)
            return idx

    def _advance_past(self, bad_idx: int) -> None:
        with self._lock:
            if self._current_index % len(self._clients) == bad_idx:
                self._current_index = (bad_idx + 1) % len(self._clients)

    def call_generate_content(self, *args: Any, **kwargs: Any) -> Any:
        num_keys = len(self._clients)
        if num_keys == 1:
            return self._clients[0].models.generate_content(*args, **kwargs)

        last_exc: Exception | None = None
        start_idx = self._next_client_index()
        for offset in range(num_keys):
            idx = (start_idx + offset) % num_keys
            client = self._clients[idx]
            key = self._keys[idx]
            masked = f"{key[:6]}...{key[-4:]}" if len(key) > 10 else "***"
            try:
                return client.models.generate_content(*args, **kwargs)
            except Exception as exc:
                if not _is_rate_limited(exc):
                    raise
                last_exc = exc
                self._advance_past(idx)
                next_idx = (idx + 1) % num_keys
                next_key = self._keys[next_idx]
                next_masked = f"{next_key[:6]}...{next_key[-4:]}" if len(next_key) > 10 else "***"
                print(
                    f"[gemini-keys] Key {masked} chạm hạn mức quota/rate-limit; "
                    f"chuyển sang key {next_masked} ({offset + 1}/{num_keys})…",
                    file=sys.stderr,
                    flush=True,
                )
        if last_exc:
            raise last_exc

    def __getattr__(self, name: str) -> Any:
        _, client, _ = self.get_active_client()
        return getattr(client, name)


DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-pro"
DEEPSEEK_TIMEOUT_SECONDS = 90.0


class _DeepSeekResponse:
    # `usage` giữ nguyên payload OpenAI-compatible (prompt_tokens / completion_tokens /
    # prompt_cache_hit_tokens) để read_response_usage đo được chi phí thật.
    def __init__(self, text: str, usage: dict[str, Any] | None = None):
        self.text = text
        self.usage = usage or {}


class _DeepSeekModelsProxy:
    def __init__(self, manager: _MultiKeyDeepSeekClient):
        self._manager = manager

    def generate_content(self, *args: Any, **kwargs: Any) -> _DeepSeekResponse:
        return self._manager.call_generate_content(*args, **kwargs)


def deepseek_reasoning_payload(config: Any) -> dict[str, Any]:
    """Chuyển `thinking_config.thinking_budget=0` (cú pháp google-genai) sang tham số DeepSeek.

    Pipeline ĐÃ yêu cầu tắt thinking cho bước dịch/soát lại, nhưng client DeepSeek trước đây
    chỉ đọc mỗi `temperature` nên yêu cầu đó bị đánh rơi: đo thực tế thấy 83/102 token output
    là reasoning, mà output là phía đắt tiền ($3.96/M so với $1.32/M input).
    Đo A/B trên API thật: 89 -> 22 token completion khi thêm reasoning_effort="none".
    (Đừng dùng "minimal" — đo được 922 token, còn tệ hơn mặc định.)
    """
    thinking = getattr(config, "thinking_config", None) if config is not None else None
    if thinking is None:
        return {}
    budget = getattr(thinking, "thinking_budget", None)
    return {"reasoning_effort": "none"} if budget == 0 else {}


def generate_without_thinking(client: Any, prompt: str, **config_kwargs: Any) -> Any:
    """Gọi `generate_content` với thinking TẮT; tự lùi về config mặc định nếu model từ chối.

    Cả bốn lời gọi LLM của pipeline đều là việc bám sát chỉ dẫn (dịch, soát glossary, soạn
    hướng dẫn, rút gọn câu) chứ không phải suy luận nhiều bước, trong khi thinking token bị
    tính GIÁ OUTPUT — phía đắt gấp 3 lần input. Đo thật trước/sau khi tắt: 804đ -> 238đ cho
    cùng một đầu vào.

    Gom vào một chỗ vì nhánh lùi (model không hỗ trợ `thinking_budget=0` -> INVALID_ARGUMENT)
    trước đây được chép lại ở từng call site; chép thêm là sớm muộn cũng trôi lệch nhau.
    """
    from google.genai import types

    try:
        return client.models.generate_content(
            model=settings.active_translate_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=0), **config_kwargs
            ),
        )
    except Exception as exc:
        if "invalid_argument" in str(exc).lower() or "thinking" in str(exc).lower():
            return client.models.generate_content(
                model=settings.active_translate_model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        raise  # lỗi khác (quota/mạng) để _with_backoff lo, đừng nuốt mất


class _MultiKeyDeepSeekClient:
    """Quản lý và xoay vòng (round-robin / failover) nhiều API key DeepSeek.
    - Gọi endpoint chuẩn OpenAI-compatible (https://api.deepseek.com/chat/completions)
    - Tương thích 100% với giao diện client.models.generate_content(...) dùng trong pipeline.
    - Hỗ trợ model deepseek-v4-pro (hoặc deepseek-chat qua VIDEO_DUB_DEEPSEEK_MODEL).
    - Tự động chuyển đổi key kế tiếp ngay khi gặp lỗi quota / 429.
    """

    def __init__(
        self,
        api_keys: list[str],
        base_url: str | None = None,
        model: str | None = None,
        http_client: Any = None,
        supports_reasoning_effort: bool = True,
    ):
        self._keys = [k.strip() for k in api_keys if k.strip()]
        if not self._keys:
            raise PipelineError("Thiếu DEEPSEEK_API_KEY. Vui lòng cấu hình DEEPSEEK_API_KEY trong file .env.")
        self._base_url = (base_url or DEEPSEEK_DEFAULT_BASE_URL).strip()
        if not self._base_url.endswith("/chat/completions"):
            self._base_url = self._base_url.rstrip("/") + "/chat/completions"
        self._default_model = model or DEEPSEEK_DEFAULT_MODEL
        self._http_client = http_client
        self._supports_reasoning_effort = supports_reasoning_effort
        self._lock = threading.Lock()
        self._current_index = 0
        self.models = _DeepSeekModelsProxy(self)

    def get_active_key(self) -> tuple[int, str]:
        with self._lock:
            idx = self._current_index % len(self._keys)
            return idx, self._keys[idx]

    def _next_key(self) -> tuple[int, str]:
        with self._lock:
            idx = self._current_index % len(self._keys)
            self._current_index = (self._current_index + 1) % len(self._keys)
            return idx, self._keys[idx]

    def _advance_past(self, bad_idx: int) -> None:
        with self._lock:
            if self._current_index % len(self._keys) == bad_idx:
                self._current_index = (bad_idx + 1) % len(self._keys)

    def call_generate_content(
        self,
        contents: str | None = None,
        model: str | None = None,
        config: Any = None,
        **kwargs: Any,
    ) -> _DeepSeekResponse:
        import httpx

        prompt = contents
        if prompt is None and "contents" in kwargs:
            prompt = kwargs["contents"]
        if prompt is None:
            prompt = ""

        target_model = self._default_model
        if model and not str(model).startswith("gemini"):
            target_model = model

        temperature = 0.2
        if config is not None:
            temperature = getattr(config, "temperature", 0.2)

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": [
                {
                    "role": "system",
                    "content": "Bạn là chuyên gia dịch thuật và lồng tiếng phim song ngữ. Tuân thủ tuyệt đối cấu trúc JSON và hướng dẫn được yêu cầu, không thêm văn bản giải thích hay lời mở đầu thừa.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        # Chỉ gửi temperature nếu không phải Claude 3.7+ hoặc Sonnet-5/Opus-5 (nơi temperature bị deprecated trên proxy)
        if "claude" not in target_model.lower():
            payload["temperature"] = float(temperature)
        
        # reasoning_effort is a DeepSeek-specific capability. Generic providers must opt in
        # explicitly; model-name matching is unreliable for aliases and proxy routing.
        if self._supports_reasoning_effort:
            payload.update(deepseek_reasoning_payload(config))

        num_keys = len(self._keys)
        last_exc: Exception | None = None
        start_idx, _ = self._next_key()

        for offset in range(num_keys):
            idx = (start_idx + offset) % num_keys
            key = self._keys[idx]
            masked = f"{key[:6]}...{key[-4:]}" if len(key) > 10 else "***"
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
            try:
                if self._http_client is not None:
                    resp = self._http_client.post(
                        self._base_url,
                        headers=headers,
                        json=payload,
                        timeout=DEEPSEEK_TIMEOUT_SECONDS,
                    )
                else:
                    with httpx.Client(timeout=DEEPSEEK_TIMEOUT_SECONDS) as client:
                        resp = client.post(
                            self._base_url,
                            headers=headers,
                            json=payload,
                        )

                if resp.status_code == 429:
                    raise PipelineError(f"DeepSeek 429 RateLimit/QuotaExceeded: {resp.text}")
                if resp.status_code >= 500:
                    raise PipelineError(f"DeepSeek {resp.status_code} ServerError: {resp.text}")
                if resp.status_code != 200:
                    raise PipelineError(f"DeepSeek HTTP {resp.status_code}: {resp.text}")

                data = resp.json()
                choices = data.get("choices") or []
                if not choices:
                    raise PipelineError(f"DeepSeek API trả về rỗng: {data}")
                text = choices[0].get("message", {}).get("content", "")
                return _DeepSeekResponse(text=text, usage=data.get("usage") or {})
            except Exception as exc:
                if not _is_rate_limited(exc):
                    raise
                last_exc = exc
                self._advance_past(idx)
                next_idx = (idx + 1) % num_keys
                next_key = self._keys[next_idx]
                next_masked = f"{next_key[:6]}...{next_key[-4:]}" if len(next_key) > 10 else "***"
                print(
                    f"[deepseek-keys] Key {masked} chạm hạn mức quota/rate-limit; "
                    f"chuyển sang key {next_masked} ({offset + 1}/{num_keys})…",
                    file=sys.stderr,
                    flush=True,
                )

        if last_exc:
            raise last_exc
        raise PipelineError("Không thể kết nối đến DeepSeek API.")


class _FallbackTranslationClient:
    """Quản lý failover giữa hai nhà cung cấp dịch thuật (Gemini và DeepSeek).
    - Ưu tiên primary client.
    - Khi primary client chạm quota/rate-limit trên mọi key,
      tự động failover ngay sang secondary client để hoàn thành job!
    """

    def __init__(self, primary_client: Any, secondary_client: Any, primary_name: str, secondary_name: str):
        self._primary = primary_client
        self._secondary = secondary_client
        self._primary_name = primary_name
        self._secondary_name = secondary_name
        self.models = self

    def generate_content(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._primary.models.generate_content(*args, **kwargs)
        except Exception as exc:
            if not _is_rate_limited(exc) and "quota" not in str(exc).lower():
                raise
            print(
                f"[provider-fallback] {self._primary_name} chạm quota/rate-limit; "
                f"tự động chuyển sang {self._secondary_name}…",
                file=sys.stderr,
                flush=True,
            )
            return self._secondary.models.generate_content(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._primary, name)


@dataclass
class TokenUsage:
    """Token của một (hoặc nhiều) lời gọi LLM.

    `input_tokens` là phần input PHẢI TRẢ GIÁ ĐẦY ĐỦ; phần đọc từ cache tách riêng vì rẻ hơn
    10-30 lần. Cả Gemini lẫn DeepSeek đều báo tổng prompt ĐÃ GỒM cache, nên phải trừ ra —
    cộng thẳng sẽ tính tiền cache theo giá full.
    """

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, other: "TokenUsage") -> None:
        self.input_tokens += other.input_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "calls": self.calls,
        }


def read_response_usage(response: Any) -> TokenUsage | None:
    """Đọc số token thật từ response, chịu cả hai dạng: `usage_metadata` (google-genai) và
    `usage` kiểu OpenAI (DeepSeek). Không có thông tin -> None (đếm lời gọi, bỏ qua token)."""
    meta = getattr(response, "usage_metadata", None)
    if meta is not None:
        cached = int(getattr(meta, "cached_content_token_count", 0) or 0)
        prompt = int(getattr(meta, "prompt_token_count", 0) or 0)
        # `thoughts_token_count` KHÔNG nằm trong candidates_token_count nhưng vẫn bị tính
        # theo GIÁ OUTPUT. Bỏ qua là tính thiếu tiền ở đúng những lời gọi không tắt thinking
        # (_build_context, _rewrite_shorter).
        thoughts = int(getattr(meta, "thoughts_token_count", 0) or 0)
        return TokenUsage(
            input_tokens=max(0, prompt - cached),
            cached_input_tokens=cached,
            output_tokens=int(getattr(meta, "candidates_token_count", 0) or 0) + thoughts,
            calls=1,
        )
    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):
        cached = int(usage.get("prompt_cache_hit_tokens") or 0)
        prompt = int(usage.get("prompt_tokens") or 0)
        return TokenUsage(
            input_tokens=max(0, prompt - cached),
            cached_input_tokens=cached,
            output_tokens=int(usage.get("completion_tokens") or 0),
            calls=1,
        )
    return None


def estimate_usd(usage: TokenUsage, model: str) -> float | None:
    """Quy token ra USD theo bảng giá. Model lạ -> None (không đoán giá)."""
    price = MODEL_PRICING_USD_PER_M.get((model or "").strip().lower())
    if not price:
        return None
    return (
        usage.input_tokens * price["input"]
        + usage.cached_input_tokens * price.get("cached_input", price["input"])
        + usage.output_tokens * price["output"]
    ) / 1_000_000


class UsageMeter:
    """Gom token thật của mọi lời gọi LLM trong một job, tách theo bước.

    Cột `jobs.cost` trước đây CHỈ nhánh demo ghi (một con số cứng), nên không có cách nào
    biết một video thật tốn bao nhiêu — không đo được thì không bán credit được.
    Các lô dịch chạy song song trong ThreadPoolExecutor nên phải khoá khi cộng dồn.
    """

    def __init__(self, model: str = ""):
        self.model = model or settings.active_translate_model
        self._lock = threading.Lock()
        self._stages: dict[str, TokenUsage] = {}

    def record(self, stage: str, response: Any) -> None:
        usage = read_response_usage(response)
        if usage is None:
            # Vẫn đếm lời gọi: biết "có gọi mà không đọc được token" khác hẳn "không gọi".
            usage = TokenUsage(calls=1)
        with self._lock:
            self._stages.setdefault(stage, TokenUsage()).add(usage)

    def snapshot(self) -> dict[str, Any]:
        """Bản tóm tắt để ghi vào jobs.cost và hiển thị trên UI."""
        with self._lock:
            stages = {name: usage.as_dict() for name, usage in self._stages.items()}
            total = TokenUsage()
            for usage in self._stages.values():
                total.add(usage)
        usd = estimate_usd(total, self.model)
        return {
            "model": self.model,
            "stages": stages,
            "total": total.as_dict(),
            "usd": round(usd, 6) if usd is not None else None,
            "vnd": round(usd * USD_TO_VND) if usd is not None else None,
            # Nói rõ để UI không phải đoán: hai bước này chạy local nên không tốn tiền API.
            "free_local": ["stt", "tts", "separate", "render"],
        }


def merge_cost(old: Any, new: dict[str, Any]) -> dict[str, Any]:
    """Cộng dồn hai bản ghi chi phí theo từng bước.

    Job đi qua hai pha tách rời (`process` dịch, rồi `export` có thể viết-lại câu), và user
    có thể export NHIỀU LẦN sau khi sửa bản dịch. Ghi đè sẽ làm mất phần đã tiêu trước đó,
    tức tính thiếu tiền — nên luôn cộng vào.
    """
    stages: dict[str, TokenUsage] = {}
    for source in (old, new):
        if not isinstance(source, dict):
            continue
        for name, raw in (source.get("stages") or {}).items():
            if not isinstance(raw, dict):
                continue
            stages.setdefault(name, TokenUsage()).add(
                TokenUsage(
                    input_tokens=int(raw.get("input_tokens") or 0),
                    cached_input_tokens=int(raw.get("cached_input_tokens") or 0),
                    output_tokens=int(raw.get("output_tokens") or 0),
                    calls=int(raw.get("calls") or 0),
                )
            )
    total = TokenUsage()
    for usage in stages.values():
        total.add(usage)
    model = new.get("model") or (old or {}).get("model") or ""
    usd = estimate_usd(total, model)
    return {
        "model": model,
        "stages": {name: usage.as_dict() for name, usage in stages.items()},
        "total": total.as_dict(),
        "usd": round(usd, 6) if usd is not None else None,
        "vnd": round(usd * USD_TO_VND) if usd is not None else None,
        "free_local": ["stt", "tts", "separate", "render"],
    }


def _record_usage(meter: "UsageMeter | None", stage: str, response: Any) -> Any:
    """Ghi nhận usage nếu có meter, rồi trả lại response để gọi kiểu `return _record_usage(...)`."""
    if meter is not None:
        meter.record(stage, response)
    return response


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
            with job_context(job_id):
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
            # Demo không gọi API nào -> chi phí bằng 0, đúng theo định dạng thật để UI
            # không phải xử lý hai dạng khác nhau.
            cost=merge_cost(None, {"model": "(demo)", "stages": {}}),
        )

    def _real_process_sync(self, job_id: str) -> None:
        job = get_job(job_id, include_segments=False)
        if not job or not job["source_path"]:
            raise PipelineError("Không tìm thấy video nguồn.")
        # Kiểm tra cấu hình giọng ngay từ đầu: sai tên giọng mà để tới bước TTS mới vỡ thì đã
        # tốn cả STT lẫn tiền dịch.
        ensure_engine_voices_ready(resolve_tts_engine(job))
        source = Path(job["source_path"])
        work = settings.jobs_dir / job_id
        work.mkdir(parents=True, exist_ok=True)
        metadata = probe(source)
        check_duration(metadata["duration"])
        update_job(job_id, **metadata, stage="separate", progress=20)

        audio = work / "source.wav"
        run([settings.ffmpeg, "-y", "-i", str(source), "-vn", "-ac", "2", "-ar", "44100", str(audio)])
        background, vocals = self._separate(audio, work)
        artifacts = {"source_audio": str(audio), "background": str(background), "vocals": str(vocals)}
        update_job(job_id, artifacts=artifacts, stage="transcribe", progress=35)
        speech_path = vocals
        try:
            transcripts = self._transcribe(vocals, job_id)
        except PipelineError as exc:
            if "Không phát hiện" not in str(exc):
                raise
            speech_path = audio
            transcripts = self._transcribe(audio, job_id)
        # Lồng tiếng 2 giọng: dò giới tính từng đoạn trên đúng file đã sinh transcript, gán
        # 'speaker' để _translate spread giữ lại và _replace_segments lưu vào DB.
        if job.get("multi_speaker"):
            transcripts = self._detect_speakers(speech_path, transcripts)
        meter = UsageMeter()
        translated, context = self._translate(transcripts, job.get("style", "tự nhiên"), meter)
        # Ghi chi phí THẬT vào jobs.cost. Trước đây cột này chỉ nhánh demo ghi một con số
        # cứng, nên không có cách nào biết một video thật tốn bao nhiêu tiền API.
        update_job(job_id, cost=merge_cost(job.get("cost"), meter.snapshot()))
        if context:
            # Lưu hướng dẫn dịch để bước viết-lại lúc export giữ đúng glossary/xưng hô.
            update_job(job_id, artifacts={**artifacts, "translate_context": context})
        self._replace_segments(
            job_id,
            [(item["text"], item["translated"]) for item in translated],
            default_length=4.8,
            timings=[(item["start"], item["end"]) for item in translated],
            speakers=[item.get("speaker") for item in translated],
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
            raw = self._transcribe_whisper(vocals)
        else:
            raw = self._transcribe_google(vocals, job_id)
        # Tách theo ranh giới câu (word timestamps) rồi gộp đoạn vụn thành câu trọn vẹn ngay
        # tại đây để mọi luồng gọi _transcribe (pipeline chuẩn lẫn skip-separation của CLI)
        # đều nhận câu đầy đủ trước khi dịch/TTS.
        return merge_transcripts(split_sentences(raw))

    def _detect_speakers(
        self, speech_path: Path, transcripts: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Gán nhãn 'male'/'female' cho từng transcript theo trung vị F0 của đúng lát audio
        (đọc file speech MỘT lần). Dùng cho lồng tiếng 2 giọng. Lỗi dò giọng KHÔNG được làm
        hỏng job -> nuốt exception, để nguyên (không speaker) và tiếp tục 1 giọng."""
        if not transcripts:
            return transcripts
        try:
            import soundfile as sf

            samples, sr = sf.read(str(speech_path), dtype="float32", always_2d=False)
            if getattr(samples, "ndim", 1) > 1:  # stereo -> trộn về mono cho phân tích F0.
                samples = samples.mean(axis=1)
            f0_values = [
                segment_median_f0(samples, sr, item["start"], item["end"]) for item in transcripts
            ]
            for item, label in zip(transcripts, assign_speakers(f0_values)):
                item["speaker"] = label
        except Exception as exc:  # noqa: BLE001 - dò giọng chỉ là tăng cường, không chặn job.
            print(f"[multi-speaker] bỏ qua dò giới tính ({type(exc).__name__}): {exc}", file=sys.stderr)
        return transcripts

    def _transcribe_whisper(self, vocals: Path) -> list[dict[str, Any]]:
        """STT local bằng faster-whisper (nhanh, miễn phí, không cần GCS)."""
        model = _get_whisper_model(settings.whisper_model)
        # word_timestamps=True tốn thêm pass căn chỉnh từng từ nhưng bắt buộc: mảnh Whisper
        # cắt theo cửa sổ âm thanh, đứt giữa câu -> cần mốc từng từ để split_sentences tách
        # đúng ranh giới câu, tránh lồng tiếng bị ngắt quãng giữa một câu.
        segments, _info = model.transcribe(
            str(vocals), language=SOURCE_LANG_CODE, vad_filter=True, word_timestamps=True
        )
        output: list[dict[str, Any]] = []
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            start = float(seg.start)
            end = max(float(seg.end), start + 0.5)
            words = [
                {"text": w.word.strip(), "start": float(w.start), "end": float(w.end)}
                for w in (seg.words or [])
                if (w.word or "").strip()
            ]
            output.append({"text": text, "start": start, "end": end, "words": words})
        if not output:
            raise PipelineError("Không phát hiện được lời thoại.")
        return output

    def _transcribe_google(self, vocals: Path, job_id: str) -> list[dict[str, Any]]:
        from google.cloud import storage
        from google.cloud.speech_v2 import SpeechClient
        from google.cloud.speech_v2.types import cloud_speech

        with google_auth_scope():
            storage_client = storage.Client(project=settings.google_project)
        bucket = storage_client.bucket(settings.gcs_bucket)
        object_name = f"video-dub/{job_id}/vocals.wav"
        bucket.blob(object_name).upload_from_filename(vocals)
        uri = f"gs://{settings.gcs_bucket}/{object_name}"
        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=[SOURCE_LANG_GOOGLE],
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
        with google_auth_scope():
            client = SpeechClient()
        response = client.batch_recognize(request=request).result(timeout=3600)
        output: list[dict[str, Any]] = []
        previous_end = 0.0
        for result in response.results[uri].transcript.results:
            if not result.alternatives:
                continue
            alt = result.alternatives[0]
            words = list(alt.words)
            start = _seconds(words[0].start_offset) if words else previous_end
            end = _seconds(words[-1].end_offset) if words else _seconds(result.result_end_offset)
            word_marks = [
                {"text": (w.word or "").strip(), "start": _seconds(w.start_offset), "end": _seconds(w.end_offset)}
                for w in words
                if (w.word or "").strip()
            ]
            output.append(
                {"text": alt.transcript.strip(), "start": start, "end": max(end, start + 0.5), "words": word_marks}
            )
            previous_end = end
        if not output:
            raise PipelineError("Không phát hiện được lời thoại.")
        return output

    def _create_deepseek_client(self) -> _MultiKeyDeepSeekClient:
        keys = settings.deepseek_api_keys
        if not keys:
            raise PipelineError("Thiếu DEEPSEEK_API_KEY. Vui lòng cấu hình DEEPSEEK_API_KEY trong file .env.")
        cache_key = f"deepseek_{settings.deepseek_model}_" + ",".join(keys)
        return self._cached_client(
            cache_key,
            lambda: _MultiKeyDeepSeekClient(
                api_keys=keys,
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_model,
            ),
        )

    def _create_openai_compat_client(self) -> _MultiKeyDeepSeekClient:
        keys = settings.openai_compat_api_keys
        if not (keys and settings.openai_compat_base_url and settings.openai_compat_model):
            raise PipelineError("Thiếu cấu hình OpenAI-compatible provider.")
        cache_key = f"openai_compat_{settings.openai_compat_model}_" + ",".join(keys)
        return self._cached_client(
            cache_key,
            lambda: _MultiKeyDeepSeekClient(
                api_keys=keys,
                base_url=settings.openai_compat_base_url,
                model=settings.openai_compat_model,
                supports_reasoning_effort=settings.openai_compat_reasoning_effort,
            ),
        )

    def _create_gemini_client(self) -> Any:
        keys = settings.gemini_api_keys
        if keys:
            cache_key = "genai_" + ",".join(keys)
            return self._cached_client(
                cache_key,
                lambda: _MultiKeyGenaiClient(keys),
            )

        if settings.google_project:
            from google import genai

            return self._cached_client(
                f"genai_vertex_{settings.google_project}_{settings.google_region}",
                lambda: _new_vertex_client(genai),
            )

        raise PipelineError("Thiếu GEMINI_API_KEY. Vui lòng cấu hình GEMINI_API_KEY trong file .env.")

    def _translation_client(self, engine: str | None = None):
        target_engine = engine or settings.effective_translate_engine
        if target_engine == "openai_compat":
            return self._create_openai_compat_client()
        has_gemini = bool(getattr(settings, "gemini_api_keys", None) or getattr(settings, "google_project", None))
        has_deepseek = bool(getattr(settings, "deepseek_api_keys", None))
        has_openai_compat = bool(getattr(settings, "openai_compat_api_keys", None) and getattr(settings, "openai_compat_base_url", None))
        fallback_enabled = bool(getattr(settings, "translate_fallback", True))

        # Chế độ Fallback: ưu tiên Gemini, nếu lỗi hoặc hết quota chuyển sang Claude/OpenAI-compat
        if fallback_enabled and has_gemini and has_openai_compat:
            primary = self._create_gemini_client()
            secondary = self._create_openai_compat_client()
            return _FallbackTranslationClient(primary, secondary, "Gemini", getattr(settings, "openai_compat_model", "openai_compat"))

        if fallback_enabled and has_gemini and has_deepseek:
            if target_engine == "deepseek":
                primary = self._create_deepseek_client()
                secondary = self._create_gemini_client()
                return _FallbackTranslationClient(primary, secondary, "DeepSeek", "Gemini")
            else:
                primary = self._create_gemini_client()
                secondary = self._create_deepseek_client()
                return _FallbackTranslationClient(primary, secondary, "Gemini", "DeepSeek")

        # Chế độ đơn provider
        if target_engine == "deepseek":
            return self._create_deepseek_client()
        return self._create_gemini_client()

    def _genai_client(self):
        """Client dịch thuật (tương thích ngược với các test/mock cũ)."""
        return self._translation_client()

    def _build_context(self, client, segments: list[dict[str, Any]], meter: UsageMeter | None = None) -> str:
        """Pass 1 lần: tóm tắt chủ đề + glossary để dịch nhất quán, sát nghĩa."""
        transcript = " ".join(item["text"] for item in segments)[:12000]
        prompt = (
            f"Đọc transcript {SOURCE_LANG_NAME} và soạn NGẮN GỌN bằng tiếng Việt bản HƯỚNG DẪN DỊCH "
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
                lambda: generate_without_thinking(client, prompt, temperature=0.2),
                label="lấy ngữ cảnh dịch",
            )
            _record_usage(meter, "context", response)
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
        meter: UsageMeter | None = None,
    ) -> dict[int, str]:
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
                    "prev_context_src": all_segments[gi - 1]["text"] if gi > 0 else "",
                    "next_context_src": all_segments[gi + 1]["text"] if gi + 1 < len(all_segments) else "",
                }
            )
        prompt = (
            f"Bạn là chuyên gia lồng tiếng {SOURCE_LANG_NAME}→Việt. Dịch SÁT NGHĨA, tự nhiên, dễ đọc thành tiếng.\n"
            f"Phong cách: {style}.\n"
            "Quan trọng: mỗi câu dịch phải đọc VỪA trong 'max_seconds' (cố gắng không quá 'max_chars' "
            "ký tự) mà vẫn giữ đủ ý — ưu tiên câu gọn, lược từ đệm thừa thay vì cắt nội dung.\n"
            "Dùng prev/next context để giữ mạch, đại từ và thuật ngữ nhất quán.\n"
            "BẮT BUỘC tuân theo hướng dẫn dịch bên dưới: dùng đúng glossary và đúng cặp xưng hô "
            "đã chọn cho MỌI câu.\n"
            "TUYỆT ĐỐI KHÔNG dùng dấu ngoặc kép \" trong nội dung dịch (cần trích dẫn một cụm từ "
            "thì dùng dấu nháy đơn ' hoặc bỏ dấu trích dẫn) vì sẽ làm hỏng cú pháp JSON.\n\n"
            f"--- Hướng dẫn dịch (bối cảnh, glossary, xưng hô) ---\n{context}\n\n"
            f"--- Câu cần dịch (JSON) ---\n{json.dumps(lines, ensure_ascii=False)}\n\n"
            'Trả về DUY NHẤT một JSON array, mỗi phần tử {"index": <int>, "vi": "<bản dịch>"}.'
        )
        response = _with_backoff(
            lambda: generate_without_thinking(
                client,
                prompt,
                # Nhiệt thấp để cùng thuật ngữ cho ra cùng bản dịch giữa các lô song song.
                temperature=0.2,
                response_mime_type="application/json",
                response_schema=_translation_schema(),
            ),
            label="dịch theo lô",
        )
        _record_usage(meter, "translate", response)
        return _parse_translations(response.text)

    def _translate(
        self,
        segments: list[dict[str, Any]],
        style: str = "tự nhiên",
        meter: UsageMeter | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """Dịch toàn bộ segments; trả (kết quả, hướng dẫn dịch) để tái dùng khi viết-lại lúc export.
        `meter` (nếu có) gom token thật của mọi lời gọi để ghi vào jobs.cost."""
        if not segments:
            return [], ""
        client = self._genai_client()
        context = self._build_context(client, segments, meter)
        offsets = list(range(0, len(segments), TRANSLATE_BATCH))
        results: dict[int, str] = {}

        def work(offset: int) -> dict[int, str]:
            indices = list(range(offset, min(offset + TRANSLATE_BATCH, len(segments))))
            return self._translate_chunk(client, indices, segments, style, context, meter)

        with ThreadPoolExecutor(max_workers=min(TRANSLATE_WORKERS, len(offsets))) as pool:
            for mapping in pool.map(work, offsets):
                results.update(mapping)

        # Dịch lại một lượt các câu model bỏ sót (JSON hỏng/thiếu index) trước khi chấp nhận
        # fallback — giữ nguyên tiếng Anh giữa video lộ rõ hơn nhiều so với một lời gọi thêm.
        missing = [index for index in range(len(segments)) if not results.get(index)]
        for offset in range(0, len(missing), TRANSLATE_BATCH):
            batch = missing[offset : offset + TRANSLATE_BATCH]
            try:
                results.update(self._translate_chunk(client, batch, segments, style, context, meter))
            except Exception:
                break  # phần còn thiếu rơi xuống fallback tiếng Anh bên dưới

        # Soát lại 1 lượt để dọn lệch nhất quán giữa các lô song song (xưng hô/glossary).
        results.update(self._review_translations(client, segments, results, context, meter))

        # Fallback cuối: câu nào vẫn thiếu thì giữ nguyên tiếng Anh để không mất đoạn.
        return [
            {**item, "translated": results.get(index) or item["text"]}
            for index, item in enumerate(segments)
        ], context

    def _review_translations(
        self,
        client,
        segments: list[dict[str, Any]],
        translated: dict[int, str],
        context: str,
        meter: UsageMeter | None = None,
    ) -> dict[int, str]:
        """Pass soát lại 1 lời gọi: dịch song song nên xưng hô/thuật ngữ có thể trôi giữa các
        lô dù đã có glossary chung. Gửi toàn bộ bản dịch + hướng dẫn, yêu cầu CHỈ sửa câu lệch
        nhất quán. Trả map {index: bản sửa}; rỗng nếu không cần sửa / call lỗi / bỏ qua."""
        if not context:
            return {}  # Không có glossary/xưng hô chuẩn thì không có mốc để soát.
        rows = [
            {"index": index, "vi": translated[index]}
            for index in range(len(segments))
            if translated.get(index)
        ]
        payload = json.dumps(rows, ensure_ascii=False)
        if not rows or len(payload) > REVIEW_MAX_CHARS:
            return {}  # Quá dài -> phản hồi soát kém tin cậy, bỏ qua để không làm hỏng bản tốt.
        try:
            prompt = (
                "Dưới đây là toàn bộ bản dịch tiếng Việt của một video, dịch theo nhiều lô "
                "song song nên có thể LỆCH NHẤT QUÁN về xưng hô hoặc thuật ngữ giữa các câu.\n"
                "Dựa vào HƯỚNG DẪN DỊCH, chỉ tìm và sửa những câu dùng SAI cặp xưng hô đã chọn "
                "hoặc SAI glossary. Giữ nguyên nghĩa, KHÔNG viết lại câu đã đúng.\n\n"
                "TUYỆT ĐỐI KHÔNG dùng dấu ngoặc kép \" trong nội dung sửa (cần trích dẫn một cụm "
                "từ thì dùng dấu nháy đơn ' hoặc bỏ dấu trích dẫn) vì sẽ làm hỏng cú pháp JSON.\n\n"
                f"--- Hướng dẫn dịch ---\n{context}\n\n"
                f"--- Bản dịch (JSON) ---\n{payload}\n\n"
                'Trả về DUY NHẤT một JSON array chỉ gồm các câu CẦN sửa, mỗi phần tử '
                '{"index": <int>, "vi": "<bản sửa>"}. Không câu nào cần sửa thì trả về [].'
            )
            response = _with_backoff(
                lambda: generate_without_thinking(
                    client,
                    prompt,
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=_translation_schema(),
                ),
                label="soát lại bản dịch",
            )
            _record_usage(meter, "review", response)
        except Exception:
            return {}  # Soát lại là bước tinh chỉnh; lỗi thì giữ nguyên bản dịch, không làm hỏng job.
        fixes = _parse_translations(response.text)
        # Chỉ nhận sửa cho index hợp lệ và thực sự khác bản cũ.
        return {
            index: vi
            for index, vi in fixes.items()
            if 0 <= index < len(segments) and vi != translated.get(index)
        }

    def _replace_segments(
        self,
        job_id: str,
        rows: list[tuple[str, str]],
        default_length: float,
        timings: list[tuple[float, float]] | None = None,
        speakers: list[str | None] | None = None,
    ) -> None:
        with connect() as conn:
            conn.execute("DELETE FROM segments WHERE job_id = ?", (job_id,))
            cursor = 0.0
            for index, (source, translated) in enumerate(rows, 1):
                start, end = timings[index - 1] if timings else (cursor, cursor + default_length)
                speaker = speakers[index - 1] if speakers else None
                score = fit_score(translated, end - start)
                conn.execute(
                    """
                    INSERT INTO segments
                    (id, job_id, position, start, end, source_text, translated_text,
                     fit_score, status, speaker, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
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
                        speaker,
                        now_iso(),
                    ),
                )
                cursor = end

    async def regenerate(self, job_id: str, segment_id: str) -> None:
        with job_context(job_id):
            await self._regenerate(job_id, segment_id)

    async def _regenerate(self, job_id: str, segment_id: str) -> None:
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

    def _rewrite_shorter(
        self,
        source_en: str,
        current_vi: str,
        seconds: float,
        context: str = "",
        meter: UsageMeter | None = None,
    ) -> str | None:
        """Nhờ Gemini viết lại câu Việt ngắn hơn để đọc vừa khung giờ, giữ đủ ý."""
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
                lambda: generate_without_thinking(client, prompt, temperature=0.3),
                label="viết lại câu ngắn hơn",
            )
            _record_usage(meter, "rewrite", response)
            text = (response.text or "").strip()
            return text or None
        except Exception:
            return None

    def _synthesize_segment(self, job_id: str, segment: dict[str, Any], meter: UsageMeter | None = None) -> Path:
        job = get_job(job_id, include_segments=False) or {}
        engine = resolve_tts_engine(job)
        suffix = segment_audio_suffix(engine)
        output = settings.jobs_dir / job_id / f"segment-{segment['position']:04d}{suffix}"
        seconds = max(0.5, segment["end"] - segment["start"])
        text = segment["translated_text"]
        # Lồng tiếng 2 giọng: chọn giọng theo nhãn nam/nữ đã dò; multi tắt -> giọng chọn trên UI
        # (job.voice) hoặc giọng mặc định trong env. Lọc theo preset offline để bỏ qua giọng
        # còn sót của engine cũ (job cũ có thể còn voiceCode Vbee trong cột jobs.voice).
        known = [voice["id"] for voice in vieneu_preset_voices()]
        voice = resolve_segment_voice(
            engine,
            segment.get("speaker"),
            bool(job.get("multi_speaker")),
            job_voice=job.get("voice"),
            known_voices=known or None,
        )

        # Vòng khớp độ dài: nếu TTS dài hơn khung quá ngưỡng thì viết lại ngắn hơn rồi synth lại.
        # Đo thật + viết lại + atempo lúc render vẫn khống chế được độ dài cho cả hai engine.
        context = (job.get("artifacts") or {}).get("translate_context", "")
        duration = 0.0
        for attempt in range(FIT_MAX_RETRIES + 1):
            _synth_vieneu(text, output, voice)
            _trim_silence(output)
            duration = probe_audio(output)
            if duration <= seconds * FIT_TOLERANCE or attempt == FIT_MAX_RETRIES:
                break
            shorter = self._rewrite_shorter(segment["source_text"], text, seconds, context, meter)
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
        with job_context(job_id):
            return await self._export(job_id)

    async def _export(self, job_id: str) -> Path:
        job = get_job(job_id)
        if not job:
            raise PipelineError("Không tìm thấy dự án.")
        if not settings.effective_demo_mode and not can_resume_export(job):
            raise PipelineError(
                "Dự án này thiếu file nền đã tách hoặc chưa có phân đoạn nào, không xuất được. "
                "Hãy chạy lại từ đầu (Thử lại) để tách nền và dịch lại."
            )
        if settings.effective_demo_mode:
            output = settings.jobs_dir / job_id / "demo-export.txt"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("Demo mode: cấu hình Google Cloud và FFmpeg để render MP4 thật.", encoding="utf-8")
            update_job(job_id, status="completed", stage="export", progress=100, artifacts={**job["artifacts"], "video": str(output)})
            return output
        # Job đã duyệt xong mới export -> kiểm tra lại giọng (env có thể đổi từ lúc xử lý).
        ensure_engine_voices_ready(resolve_tts_engine(job))
        await _stage(job_id, self.hook, "voice", 68, "Đang tạo giọng Việt…")
        pending = [s for s in job["segments"] if s["status"] != "ready" or not s["audio_path"]]
        if pending:
            semaphore = asyncio.Semaphore(TTS_WORKERS)

            # Vòng khớp độ dài có thể gọi LLM (_rewrite_shorter) cho từng câu -> phải đo,
            # nếu không phần chi phí này biến mất khỏi hoá đơn.
            export_meter = UsageMeter()

            async def synth(segment: dict[str, Any]) -> None:
                async with semaphore:
                    await asyncio.to_thread(self._synthesize_segment, job_id, segment, export_meter)

            await asyncio.gather(*(synth(segment) for segment in pending))
            current = get_job(job_id, include_segments=False) or {}
            update_job(job_id, cost=merge_cost(current.get("cost"), export_meter.snapshot()))
        await _stage(job_id, self.hook, "export", 88, "Đang mix và kết xuất MP4…")
        output = await asyncio.to_thread(self._render, job_id)
        update_job(job_id, status="completed", stage="export", progress=100, artifacts={**job["artifacts"], "video": str(output)})
        # Dọn file trung gian NGAY sau khi có MP4: giữ lại thì mỗi job để lại hàng GB rác
        # (đo thực tế: 12.5GB trên đĩa cho 1.7GB kết quả). Chạy sau khi đã update_job nên
        # lỗi dọn dẹp không thể làm job đang "completed" hoá thành lỗi.
        freed = await asyncio.to_thread(cleanup_job_intermediates, job_id)
        if freed:
            print(f"[cleanup] job {job_id}: giải phóng {freed / 1e9:.2f} GB", file=sys.stderr)
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
        segments = job["segments"]
        bg_seconds = probe_audio(Path(job["artifacts"]["background"]))
        
        if not segments:
            inputs.extend(["-i", job["source_path"]])
            if speed_changed:
                filters.append(f"[0:v]setpts=PTS/{speed:.6f}[vout];[0:a]atempo={speed}[aout]")
                filter_script = work / "filter-complex.txt"
                filter_script.write_text(";".join(filters), encoding="utf-8")
                run([
                    settings.ffmpeg, "-y", *inputs, "-/filter_complex", str(filter_script),
                    "-map", "[vout]", "-c:v", VIDEO_CODEC, "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF, "-pix_fmt", "yuv420p",
                    "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", str(output)
                ])
            else:
                run([
                    settings.ffmpeg, "-y", *inputs, "-c:v", "copy", "-c:a", "copy", str(output)
                ])
            return output
            
        if len(segments) > 30:
            batch_size = 30
            for b_idx in range(0, len(segments), batch_size):
                b_segs = segments[b_idx : b_idx + batch_size]
                b_inputs: list[str] = []
                b_filters: list[str] = []
                b_labels: list[str] = []
                for i, seg in enumerate(b_segs):
                    g_idx = b_idx + i
                    audio_path = Path(seg["audio_path"])
                    duration = float(seg.get("audio_duration") or 0.0)
                    if duration <= 0:
                        duration = probe_audio(audio_path)
                    target = max(0.25, seg["end"] - seg["start"])
                    next_start = segments[g_idx + 1]["start"] if g_idx + 1 < len(segments) else bg_seconds
                    avail = next_start - seg["start"] - SPILL_GUARD_SECONDS
                    ratio = segment_tempo(duration, target, avail) * speed
                    delay = int(seg["start"] / speed * 1000)
                    b_inputs.extend(["-i", str(audio_path)])
                    label = f"s{i}"
                    b_filters.append(
                        f"[{i}:a]{AUDIO_FORMAT},{_atempo_chain(ratio, lo=0.5, hi=2.0)},"
                        f"adelay={delay}|{delay}[{label}]"
                    )
                    b_labels.append(f"[{label}]")
                b_filters.append(f"{''.join(b_labels)}amix=inputs={len(b_labels)}:normalize=0,{AUDIO_FORMAT}[outa]")
                b_script = work / f"filter-batch-{b_idx}.txt"
                b_script.write_text(";".join(b_filters), encoding="utf-8")
                b_wav = work / f"narration-batch-{b_idx}.wav"
                run([
                    settings.ffmpeg, "-y", *b_inputs,
                    "-/filter_complex", str(b_script),
                    "-map", "[outa]", str(b_wav)
                ])
                b_input_idx = len(inputs) // 2
                inputs.extend(["-i", str(b_wav)])
                label = f"b{b_idx}"
                filters.append(f"[{b_input_idx}:a]{AUDIO_FORMAT}[{label}]")
                labels.append(f"[{label}]")
        else:
            for index, segment in enumerate(segments):
                audio_path = Path(segment["audio_path"])
                # Dùng độ dài đã đo lúc TTS; chỉ probe lại khi thiếu (mp3 tạo bởi bản cũ).
                duration = float(segment.get("audio_duration") or 0.0)
                if duration <= 0:
                    duration = probe_audio(audio_path)
                target = max(0.25, segment["end"] - segment["start"])
                # Chỗ trống thực tế kéo dài tới lúc câu kế tiếp bắt đầu (hoặc hết nền nếu là
                # câu cuối): câu hơi dài được tràn sang khoảng lặng thay vì bị tua nhanh.
                next_start = segments[index + 1]["start"] if index + 1 < len(segments) else bg_seconds
                avail = next_start - segment["start"] - SPILL_GUARD_SECONDS
                # Khớp trong khung gốc rồi nhân thêm "speed" để theo kịp timeline đã bị nén lại.
                # Hai thừa số đã kẹp sẵn (tempo ≤ ATEMPO_MAX, speed trong biên) nên nới lo/hi
                # để tích của chúng không bị kẹp lần nữa làm lệch đồng bộ.
                ratio = segment_tempo(duration, target, avail) * speed
                # Mốc bắt đầu cũng phải chia cho speed để khớp đúng vị trí trên timeline đã tua nhanh.
                delay = int(segment["start"] / speed * 1000)
                inputs.extend(["-i", str(audio_path)])
                label = f"s{index}"
                filters.append(
                    f"[{index}:a]{AUDIO_FORMAT},{_atempo_chain(ratio, lo=0.5, hi=2.0)},"
                    f"adelay={delay}|{delay}[{label}]"
                )
                labels.append(f"[{label}]")
        # Bus thoại: cộng dồn KHÔNG chuẩn-hoá (tránh bug amix chia đôi âm lượng),
        # rồi chuẩn loudness về -16 LUFS và tách 2 nhánh: 1 để nghe, 1 làm khoá sidechain.
        filters.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,"
            f"loudnorm=I={NARRATION_LUFS}:TP=-1.5:LRA=11{pitch_chain},{AUDIO_FORMAT},"
            "asplit=2[narr_mix][narr_key0]"
        )
        background = job["artifacts"]["background"]
        bg_index = len(inputs) // 2
        source_index = bg_index + 1
        inputs.extend(["-i", background, "-i", job["source_path"]])
        # sidechaincompress cắt output theo độ dài nhánh KHOÁ (sidechain), không phải nhánh
        # chính — nếu câu thoại cuối kết thúc trước khi video hết (im lặng/outro cuối), khoá
        # ngắn hơn nền sẽ cắt cụt luôn đoạn nền+hình còn lại. Đệm khoá bằng im lặng cho dài ít
        # nhất bằng nền (đã theo "speed") để tránh mất đuôi video.
        bg_target_seconds = bg_seconds / speed
        filters.append(f"[narr_key0]apad=whole_dur={bg_target_seconds:.3f}[narr_key]")
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
        filter_script = work / "filter-complex.txt"
        filter_script.write_text(";".join(filters), encoding="utf-8")
        run(
            [
                settings.ffmpeg,
                "-y",
                *inputs,
                "-/filter_complex",
                str(filter_script),
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


def _trim_silence(path: Path) -> None:
    """Cắt lặng đầu/đuôi audio TTS (Gemini/VieNeu hay đệm 0.1-0.4s) để đo độ dài chính xác,
    tránh kích hoạt viết-lại/tăng tốc oan và bớt cảm giác vào câu trễ. Giữ lại chút lặng hai
    đầu cho êm. Đuôi trim bằng mẹo areverse (silenceremove chỉ cắt được từ đầu). Lỗi -> giữ
    nguyên file gốc (bước tinh chỉnh, không được làm hỏng audio đã có)."""
    if not settings.ffmpeg:
        return
    trim = (
        f"silenceremove=start_periods=1:start_silence={TTS_TRIM_KEEP}:"
        f"start_threshold={TTS_TRIM_THRESHOLD}:detection=peak,areverse,"
        f"silenceremove=start_periods=1:start_silence={TTS_TRIM_KEEP}:"
        f"start_threshold={TTS_TRIM_THRESHOLD}:detection=peak,areverse"
    )
    tmp = path.with_name(f"{path.stem}-trim{path.suffix}")
    try:
        run([settings.ffmpeg, "-y", "-i", str(path), "-af", trim, str(tmp)])
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)


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
