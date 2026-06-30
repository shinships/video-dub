from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("VIDEO_DUB_DATA_DIR", ROOT / "data"))
    google_project: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    google_region: str = os.getenv("GOOGLE_CLOUD_REGION", "global")
    gcs_bucket: str = os.getenv("VIDEO_DUB_GCS_BUCKET", "")
    gemini_model: str = os.getenv("VIDEO_DUB_GEMINI_MODEL", "gemini-2.5-flash")
    tts_model: str = os.getenv("VIDEO_DUB_TTS_MODEL", "gemini-2.5-flash-tts")
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
