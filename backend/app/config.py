from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _force_utf8_console() -> None:
    """Console Windows mặc định dùng cp1252, không in được tiếng Việt -> crash khi print/log.
    Ép UTF-8 ngay từ điểm vào chung (CLI lẫn uvicorn) để chuỗi tiếng Việt luôn in được."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_console()


def _load_dotenv(path: Path) -> None:
    """Nạp .env một lần cho MỌI điểm vào (uvicorn, CLI, test). Env đã set sẵn được giữ."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


_load_dotenv(ROOT / ".env")


def _prefer_adc_over_stray_credentials() -> None:
    """GOOGLE_APPLICATION_CREDENTIALS có thể bị set toàn hệ thống cho công cụ KHÁC (vd Google
    Docs service account) và sẽ âm thầm chiếm quyền xác thực, gọi nhầm sang project/quyền sai
    (TTS/Speech bị từ chối dù project đích đã bật API). Mặc định ưu tiên ADC qua gcloud login;
    chỉ giữ GOOGLE_APPLICATION_CREDENTIALS khi người dùng chủ động khai báo cho project này
    qua VIDEO_DUB_USE_GCLOUD_AUTH=false (tức không dùng gcloud auth)."""
    if os.getenv("VIDEO_DUB_USE_GCLOUD_AUTH", "true").lower() not in {"0", "false", "no"}:
        os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)


_prefer_adc_over_stray_credentials()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("VIDEO_DUB_DATA_DIR", ROOT / "data"))
    google_project: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    google_region: str = os.getenv("GOOGLE_CLOUD_REGION", "global")
    gcs_bucket: str = os.getenv("VIDEO_DUB_GCS_BUCKET", "")
    gemini_model: str = os.getenv("VIDEO_DUB_GEMINI_MODEL", "gemini-2.5-flash")
    tts_model: str = os.getenv("VIDEO_DUB_TTS_MODEL", "gemini-2.5-flash-tts")
    # Engine tạo giọng: "gemini" (cloud Vertex AI, mặc định) | "vieneu" (local, miễn phí,
    # cần cài requirements-tts-local.txt; bước TTS không gọi cloud nữa) | "vbee" (cloud VN,
    # giọng tiếng Việt tự nhiên, gọi HTTP bất đồng bộ; cần App ID + token studio Vbee).
    tts_engine: str = os.getenv("VIDEO_DUB_TTS_ENGINE", "gemini").lower()
    # Vbee TTS — App ID và token lấy trong studio Vbee (không hard-code, đọc từ env).
    vbee_app_id: str = os.getenv("VIDEO_DUB_VBEE_APP_ID", "")
    vbee_token: str = os.getenv("VIDEO_DUB_VBEE_TOKEN", "")
    # voiceCode Vbee (vd "hn_female_ngochuyen_full_48k-fhg"); xem danh sách qua API voices.
    vbee_voice: str = os.getenv("VIDEO_DUB_VBEE_VOICE", "hn_female_ngochuyen_full_48k-fhg")
    # Giọng preset VieNeu (vd "Ngọc Lan"); để trống dùng giọng mặc định của model.
    vieneu_voice: str = os.getenv("VIDEO_DUB_VIENEU_VOICE", "")
    # File wav 3-5 giây để nhân bản giọng; nếu đặt sẽ ưu tiên hơn vieneu_voice.
    vieneu_ref_audio: str = os.getenv("VIDEO_DUB_VIENEU_REF_AUDIO", "")
    # Mặc định "cpu" (đường ONNX torch-free, đã kiểm chứng ổn định): VieNeu tự dò thấy
    # CUDA của torch (cài cho Demucs) và cố chạy GPU sẽ đụng cuDNN thiếu symbol trên máy
    # đã test. Chỉ đổi "cuda" nếu đã xác nhận cuDNN tương thích với torch của dự án.
    vieneu_device: str = os.getenv("VIDEO_DUB_VIENEU_DEVICE", "cpu")
    stt_model: str = os.getenv("VIDEO_DUB_STT_MODEL", "latest_long")
    # Engine nhận dạng giọng nói: "whisper" (local, mặc định) | "google" (STT V2 batch).
    stt_engine: str = os.getenv("VIDEO_DUB_STT_ENGINE", "whisper").lower()
    whisper_model: str = os.getenv("VIDEO_DUB_WHISPER_MODEL", "medium")
    whisper_compute: str = os.getenv("VIDEO_DUB_WHISPER_COMPUTE", "int8")
    # Model tách thoại/nền: "htdemucs" (mặc định) | "htdemucs_ft" (sạch hơn, chậm hơn).
    demucs_model: str = os.getenv("VIDEO_DUB_DEMUCS_MODEL", "htdemucs")
    demucs_shifts: int = int(os.getenv("VIDEO_DUB_DEMUCS_SHIFTS", "0"))
    demo_mode: bool = os.getenv("VIDEO_DUB_DEMO_MODE", "auto").lower() in {"1", "true", "yes"}

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "video_dub.sqlite3"

    @property
    def ffmpeg(self) -> str | None:
        return shutil.which("ffmpeg")

    @property
    def ffprobe(self) -> str | None:
        return shutil.which("ffprobe")

    @property
    def cloud_ready(self) -> bool:
        # Translate (Vertex) + TTS cần project. GCS chỉ cần khi STT chạy bằng Google batch.
        if not self.google_project:
            return False
        if self.stt_engine == "google" and not self.gcs_bucket:
            return False
        return True

    @property
    def effective_demo_mode(self) -> bool:
        return self.demo_mode or not (self.cloud_ready and self.ffmpeg and self.ffprobe)


settings = Settings()
for directory in (settings.data_dir, settings.uploads_dir, settings.jobs_dir):
    directory.mkdir(parents=True, exist_ok=True)
