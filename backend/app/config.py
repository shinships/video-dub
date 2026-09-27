from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_type_hints


ROOT = Path(__file__).resolve().parents[2]

TRUE_VALUES = {"1", "true", "yes"}


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


@dataclass(frozen=True)
class Settings:
    """Ảnh chụp cấu hình tại một thời điểm — value object THUẦN, không tự đọc gì.

    Trước đây mỗi field mặc định là `os.getenv(...)`, mà default của dataclass được evaluate
    **lúc định nghĩa class** (tức lúc import). Hệ quả: đổi cấu hình lúc chạy là bất khả thi,
    và test phải set env trước cả dòng `import` đầu tiên. Nay mọi việc đọc nguồn cấu hình
    nằm ở `SettingsStore`; class này chỉ giữ giá trị đã được quyết.
    """

    data_dir: Path = ROOT / "data"
    gemini_api_key: str = ""
    google_project: str = ""
    google_region: str = "global"
    gcs_bucket: str = ""
    # Model Gemini cho bước DỊCH (Google AI Studio hoặc Vertex AI).
    gemini_model: str = "gemini-3.8-flash"
    # DeepSeek API (OpenAI-compatible) cho bước DỊCH.
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_base_url: str = "https://api.deepseek.com/chat/completions"
    # Thư mục xuất video thành phẩm mặc định (nếu để trống: xuất cạnh file nguồn hoặc cwd).
    output_dir: str = ""
    # Generic OpenAI-compatible API for local gateways/proxies.
    openai_compat_api_key: str = ""
    openai_compat_model: str = ""
    openai_compat_base_url: str = ""
    openai_compat_reasoning_effort: bool = False
    # Engine dịch: "auto" | "gemini" | "deepseek" | "openai_compat".
    translate_engine: str = "auto"
    # Tự động chuyển đổi dự phòng (Gemini <-> DeepSeek) khi provider chính chạm quota/rate-limit
    translate_fallback: bool = True
    # Engine tạo giọng: chỉ còn "vieneu" (local, miễn phí; cần requirements-tts-local.txt,
    # bước TTS không gọi cloud). Vbee và Gemini TTS đều đã gỡ — giữ lại biến env để cấu hình
    # cũ không làm app crash, nhưng giá trị khác "vieneu" bị bỏ qua ở resolve_tts_engine.
    tts_engine: str = "vieneu"
    # Giọng preset VieNeu (vd "Adam", "Minh Đức", "Trúc Ly", "Minh Quân"); để trống dùng giọng mặc định của model.
    vieneu_voice: str = "Minh Quân"
    # File wav 3-5 giây để nhân bản giọng; nếu đặt sẽ ưu tiên hơn vieneu_voice.
    vieneu_ref_audio: str = ""
    # --- Lồng tiếng 2 giọng (nam/nữ) ---
    # Bật tự dò giới tính người nói theo đoạn rồi gán giọng riêng. Mặc định tắt (giữ 1 giọng
    # như cũ); có thể bật riêng từng job qua cờ CLI --multi-speaker hoặc Form khi upload.
    multi_speaker: bool = False
    # Giọng theo giới tính khi bật multi_speaker. Giọng NAM mặc định kế thừa cấu hình 1-giọng
    # hiện có (vieneu_voice/ref_audio) để không phải khai lại; chỉ cần thêm giọng NỮ.
    vieneu_voice_male: str = ""
    vieneu_ref_audio_male: str = ""
    vieneu_voice_female: str = ""
    vieneu_ref_audio_female: str = ""
    # Mặc định "cpu" (đường ONNX torch-free, đã kiểm chứng ổn định): VieNeu tự dò thấy
    # CUDA của torch (cài cho Demucs) và cố chạy GPU sẽ đụng cuDNN thiếu symbol trên máy
    # đã test. Chỉ đổi "cuda" nếu đã xác nhận cuDNN tương thích với torch của dự án.
    vieneu_device: str = "cpu"
    stt_model: str = "latest_long"
    # Engine nhận dạng giọng nói: "whisper" (local, mặc định) | "google" (STT V2 batch).
    stt_engine: str = "whisper"
    whisper_model: str = "medium"
    whisper_compute: str = "int8"
    # Model tách thoại/nền: "htdemucs" (mặc định) | "htdemucs_ft" (sạch hơn, chậm hơn).
    demucs_model: str = "htdemucs"
    demucs_shifts: int = 0
    demo_mode: bool = False
    # Giới hạn thời lượng video nhận vào. Bốn chỗ trong dự án từng nói bốn con số khác nhau
    # (hằng 14400s nhưng báo lỗi "30 phút", pipeline báo "4 giờ", UI ghi "30 phút") — nay tất
    # cả đọc từ đây.
    max_duration_minutes: int = 240
    # --- Xác thực Google ---
    # `google_prefer_adc`: tạm ẩn GOOGLE_APPLICATION_CREDENTIALS lúc dựng client Google. Biến
    # này hay được set toàn hệ thống cho công cụ KHÁC (vd service account của Google Docs) và
    # sẽ âm thầm chiếm quyền xác thực, gọi nhầm sang project/quyền sai.
    # `google_gcloud_token`: chủ động lấy access token qua `gcloud auth print-access-token`
    # thay vì để thư viện tự dò ADC.
    # Hai cờ này trước đây cùng đọc VIDEO_DUB_USE_GCLOUD_AUTH nhưng với hai phép thử khác
    # nhau; tách tên ra để thấy rõ chúng là hai hành vi khác nhau (mặc định giữ nguyên như cũ).
    google_prefer_adc: bool = True
    google_gcloud_token: bool = False

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
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def ffmpeg(self) -> str | None:
        return shutil.which("ffmpeg")

    @property
    def ffprobe(self) -> str | None:
        return shutil.which("ffprobe")

    @property
    def gemini_api_keys(self) -> list[str]:
        """Danh sách API key của Gemini (hỗ trợ nhiều key phân tách bằng dấu phẩy, chấm phẩy hoặc xuống dòng)."""
        return _split_keys(self.gemini_api_key)

    @property
    def deepseek_api_keys(self) -> list[str]:
        """Danh sách API key của DeepSeek (hỗ trợ nhiều key phân tách bằng dấu phẩy, chấm phẩy hoặc xuống dòng)."""
        return _split_keys(self.deepseek_api_key)

    @property
    def openai_compat_api_keys(self) -> list[str]:
        return _split_keys(self.openai_compat_api_key)

    @property
    def effective_translate_engine(self) -> str:
        """Xác định engine dịch thực tế."""
        engine = (self.translate_engine or "auto").lower()
        if engine in {"deepseek", "gemini", "openai_compat"}:
            return engine
        if self.openai_compat_api_keys:
            return "openai_compat"
        if self.deepseek_api_keys and not (self.gemini_api_keys or self.google_project):
            return "deepseek"
        return "gemini"

    @property
    def active_translate_model(self) -> str:
        """Model dịch tương ứng với engine đang hoạt động."""
        if self.effective_translate_engine == "openai_compat":
            return self.openai_compat_model
        if self.effective_translate_engine == "deepseek":
            return self.deepseek_model
        return self.gemini_model

    @property
    def cloud_ready(self) -> bool:
        """Cấu hình cloud đã đủ chưa (không xét FFmpeg — đó là phụ thuộc local).
        Suy ra từ missing_requirements để hai chỗ không trôi lệch nhau."""
        local_only = {"ffmpeg", "ffprobe"}
        return not [item for item in self.missing_requirements if item["key"] not in local_only]

    @property
    def missing_requirements(self) -> list[dict[str, str]]:
        """Những thứ còn thiếu để chạy pipeline thật, kèm câu hướng dẫn cho UI.

        Trước đây thiếu bất cứ thứ gì cũng ÂM THẦM rơi vào demo mode: người dùng gõ sai API
        key sẽ nhận về transcript bịa sẵn mà không có lỗi nào. Trả danh sách tường minh để
        `/api/health` nói rõ thiếu gì và `POST /api/jobs` từ chối nhận video."""
        missing: list[dict[str, str]] = []
        if not self.ffmpeg:
            missing.append({"key": "ffmpeg", "message": "Chưa có FFmpeg trong PATH."})
        if not self.ffprobe:
            missing.append({"key": "ffprobe", "message": "Chưa có FFprobe trong PATH."})
        if not (self.gemini_api_keys or self.google_project or self.deepseek_api_keys or self.openai_compat_api_keys):
            missing.append(
                {
                    "key": "translator_key",
                    "message": "Chưa có API key để dịch. Mở Cài đặt để thêm key Gemini hoặc DeepSeek.",
                }
            )
        if self.stt_engine == "google" and not (self.google_project and self.gcs_bucket):
            missing.append(
                {
                    "key": "google_stt",
                    "message": "STT Google cần Google Cloud project và GCS bucket (đặt trong Cài đặt).",
                }
            )
        return missing

    @property
    def max_duration_seconds(self) -> int:
        return max(1, self.max_duration_minutes) * 60

    @property
    def duration_limit_label(self) -> str:
        """Nhãn tiếng Việt của giới hạn, dùng chung cho lỗi backend lẫn chữ trên UI."""
        minutes = max(1, self.max_duration_minutes)
        if minutes % 60 == 0:
            return f"{minutes // 60} giờ"
        if minutes > 60:
            return f"{minutes // 60} giờ {minutes % 60} phút"
        return f"{minutes} phút"

    @property
    def effective_demo_mode(self) -> bool:
        """CHỈ bật khi người dùng chủ động yêu cầu (VIDEO_DUB_DEMO_MODE=true).

        Thiếu cấu hình KHÔNG còn tự rơi vào demo: job bị từ chối kèm lý do rõ ràng, vì trả
        kết quả giả cho người tưởng mình đang dùng thật là hỏng nghiêm trọng hơn nhiều."""
        return self.demo_mode


def _split_keys(raw: str) -> list[str]:
    cleaned = (raw or "").strip().strip('"').strip("'")
    normalized = cleaned.replace(";", ",").replace("\n", ",")
    return [k.strip().strip('"').strip("'") for k in normalized.split(",") if k.strip().strip('"').strip("'")]


# --- Mô tả từng field: tên biến môi trường, kiểu, và (nếu sửa được qua UI) nhãn tiếng Việt ---


@dataclass(frozen=True)
class FieldSpec:
    """Một field cấu hình: đọc từ đâu, kiểu gì, có cho sửa qua UI không."""

    env: tuple[str, ...] = ()
    secret: bool = False
    lower: bool = False
    # Chỉ field có `label` mới xuất hiện ở `GET/PUT /api/settings` — đây chính là whitelist.
    label: str = ""
    group: str = ""
    choices: tuple[str, ...] = ()
    help: str = ""


FIELD_SPECS: dict[str, FieldSpec] = {
    # data_dir CỐ TÌNH không sửa được qua API: settings.json nằm bên trong chính nó, đổi
    # đường dẫn lúc chạy sẽ thành bài toán con-gà-quả-trứng. Giữ ở env cho CLI/test.
    "data_dir": FieldSpec(env=("VIDEO_DUB_DATA_DIR",)),
    "gemini_api_key": FieldSpec(
        env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        secret=True,
        label="API key Gemini",
        group="translate",
        help="Nhiều key thì phân tách bằng dấu phẩy. Lưu trong keychain của hệ điều hành.",
    ),
    "deepseek_api_key": FieldSpec(
        env=("DEEPSEEK_API_KEY",),
        secret=True,
        label="API key DeepSeek",
        group="translate",
        help="Nhiều key thì phân tách bằng dấu phẩy. Lưu trong keychain của hệ điều hành.",
    ),
    "output_dir": FieldSpec(
        env=("VIDEO_DUB_OUTPUT_DIR", "AUTODUB_OUTPUT_DIR"),
        label="Thư mục xuất thành phẩm",
        group="storage",
        help="Thư mục mặc định để lưu video sau khi lồng tiếng xong (vd /Users/mktmda/Movies).",
    ),
    "openai_compat_api_key": FieldSpec(
        env=("VIDEO_DUB_OPENAI_COMPAT_API_KEY",), secret=True,
        label="API key OpenAI-compatible", group="translate",
        help="Dùng cho local proxy hoặc provider OpenAI-compatible; lưu trong keychain.",
    ),
    "openai_compat_model": FieldSpec(env=("VIDEO_DUB_OPENAI_COMPAT_MODEL",), label="Model OpenAI-compatible", group="translate"),
    "openai_compat_base_url": FieldSpec(env=("VIDEO_DUB_OPENAI_COMPAT_BASE_URL",), label="Base URL OpenAI-compatible", group="translate"),
    "openai_compat_reasoning_effort": FieldSpec(env=("VIDEO_DUB_OPENAI_COMPAT_REASONING_EFFORT",)),
    "translate_engine": FieldSpec(
        env=("VIDEO_DUB_TRANSLATE_ENGINE",),
        lower=True,
        label="Engine dịch",
        group="translate",
        choices=("auto", "gemini", "deepseek", "openai_compat"),
        help="openai_compat: local proxy hoặc OpenAI-compatible provider.",
    ),
    "gemini_model": FieldSpec(env=("VIDEO_DUB_GEMINI_MODEL",), label="Model Gemini", group="translate"),
    "deepseek_model": FieldSpec(env=("VIDEO_DUB_DEEPSEEK_MODEL",), label="Model DeepSeek", group="translate"),
    "deepseek_base_url": FieldSpec(env=("DEEPSEEK_BASE_URL",)),
    "translate_fallback": FieldSpec(
        env=("VIDEO_DUB_TRANSLATE_FALLBACK",),
        label="Tự chuyển provider khi chạm quota",
        group="translate",
    ),
    "google_project": FieldSpec(env=("GOOGLE_CLOUD_PROJECT",), label="Google Cloud project", group="google"),
    "google_region": FieldSpec(env=("GOOGLE_CLOUD_REGION",), label="Google Cloud region", group="google"),
    "gcs_bucket": FieldSpec(env=("VIDEO_DUB_GCS_BUCKET",), label="GCS bucket (cho STT Google)", group="google"),
    "google_prefer_adc": FieldSpec(env=("VIDEO_DUB_USE_GCLOUD_AUTH",)),
    "google_gcloud_token": FieldSpec(env=("VIDEO_DUB_USE_GCLOUD_AUTH",)),
    "tts_engine": FieldSpec(env=("VIDEO_DUB_TTS_ENGINE",), lower=True),
    "vieneu_voice": FieldSpec(env=("VIDEO_DUB_VIENEU_VOICE",), label="Giọng VieNeu", group="voice"),
    "vieneu_ref_audio": FieldSpec(
        env=("VIDEO_DUB_VIENEU_REF_AUDIO",),
        label="File giọng mẫu (clone)",
        group="voice",
        help="WAV 3-5 giây. Có file này thì nó được ưu tiên hơn giọng preset.",
    ),
    "multi_speaker": FieldSpec(
        env=("VIDEO_DUB_MULTI_SPEAKER",),
        label="Mặc định lồng tiếng 2 giọng",
        group="voice",
    ),
    "vieneu_voice_male": FieldSpec(env=("VIDEO_DUB_VIENEU_VOICE_MALE",), label="Giọng nam", group="voice"),
    "vieneu_ref_audio_male": FieldSpec(
        env=("VIDEO_DUB_VIENEU_REF_AUDIO_MALE",), label="File giọng mẫu (nam)", group="voice"
    ),
    "vieneu_voice_female": FieldSpec(env=("VIDEO_DUB_VIENEU_VOICE_FEMALE",), label="Giọng nữ", group="voice"),
    "vieneu_ref_audio_female": FieldSpec(
        env=("VIDEO_DUB_VIENEU_REF_AUDIO_FEMALE",), label="File giọng mẫu (nữ)", group="voice"
    ),
    "vieneu_device": FieldSpec(
        env=("VIDEO_DUB_VIENEU_DEVICE",),
        lower=True,
        label="Thiết bị chạy TTS",
        group="engine",
        choices=("cpu", "cuda", "mps"),
        help="cpu là đường đã kiểm chứng ổn định; chỉ đổi khi chắc chắn driver tương thích.",
    ),
    "stt_engine": FieldSpec(
        env=("VIDEO_DUB_STT_ENGINE",),
        lower=True,
        label="Engine nhận dạng lời thoại",
        group="engine",
        choices=("whisper", "google"),
        help="whisper chạy local, miễn phí. google cần project + bucket.",
    ),
    "stt_model": FieldSpec(env=("VIDEO_DUB_STT_MODEL",)),
    "whisper_model": FieldSpec(
        env=("VIDEO_DUB_WHISPER_MODEL",),
        label="Model Whisper",
        group="engine",
        choices=("tiny", "base", "small", "medium", "large-v3"),
    ),
    "whisper_compute": FieldSpec(
        env=("VIDEO_DUB_WHISPER_COMPUTE",),
        label="Độ chính xác Whisper",
        group="engine",
        choices=("int8", "int8_float16", "float16", "float32"),
    ),
    "demucs_model": FieldSpec(
        env=("VIDEO_DUB_DEMUCS_MODEL",),
        label="Model tách nền",
        group="engine",
        choices=("htdemucs", "htdemucs_ft"),
        help="htdemucs_ft sạch hơn nhưng chậm hơn đáng kể.",
    ),
    "demucs_shifts": FieldSpec(env=("VIDEO_DUB_DEMUCS_SHIFTS",)),
    "demo_mode": FieldSpec(env=("VIDEO_DUB_DEMO_MODE",)),
    "max_duration_minutes": FieldSpec(
        env=("VIDEO_DUB_MAX_DURATION_MINUTES",),
        label="Thời lượng video tối đa (phút)",
        group="engine",
        help="Video dài hơn mức này bị từ chối ngay lúc tải lên.",
    ),
}

FIELD_TYPES: dict[str, Any] = get_type_hints(Settings)
SECRET_FIELDS = frozenset(name for name, spec in FIELD_SPECS.items() if spec.secret)
# Whitelist tường minh cho PUT /api/settings. KHÔNG suy ra từ dataclass: field mới thêm vào
# Settings phải được khai báo có chủ đích ở đây mới sửa được từ ngoài.
EDITABLE_FIELDS = tuple(name for name, spec in FIELD_SPECS.items() if spec.label)

KEYCHAIN_SERVICE = "video-dub"


def _is_true(raw: str) -> bool:
    return raw.strip().strip('"').strip("'").lower() in TRUE_VALUES


def _coerce(name: str, raw: str) -> Any:
    """Ép chuỗi (từ env hoặc settings.json) về đúng kiểu của field. Giá trị hỏng -> None
    để lớp gọi bỏ qua và lùi về nguồn kế tiếp, thay vì làm sập cả app vì một dòng gõ nhầm."""
    spec = FIELD_SPECS.get(name, FieldSpec())
    kind = FIELD_TYPES.get(name, str)
    if kind is bool:
        # Ngoại lệ: google_gcloud_token dùng phép thử nghiêm hơn (chỉ 1/true/yes) vì nó BẬT
        # một hành vi (gọi gcloud CLI), còn google_prefer_adc là tắt-khi-được-yêu-cầu.
        if name == "google_prefer_adc":
            return raw.strip().lower() not in {"0", "false", "no"}
        return _is_true(raw)
    if kind is int:
        try:
            return int(raw.strip())
        except (TypeError, ValueError):
            return None
    if kind is Path:
        text = raw.strip()
        return Path(text).expanduser() if text else None
    text = raw.strip()
    return text.lower() if spec.lower else text


def _keyring_module():
    """Import trong hàm: `keyring` có thể chưa cài, và trên Linux nó chạm D-Bus ngay lúc import.
    Tắt được qua VIDEO_DUB_USE_KEYCHAIN=false (test dùng cờ này để không đụng keychain thật)."""
    if os.getenv("VIDEO_DUB_USE_KEYCHAIN", "true").strip().lower() in {"0", "false", "no"}:
        return None
    try:
        import keyring

        return keyring
    except Exception:
        return None


def keychain_available() -> bool:
    module = _keyring_module()
    if module is None:
        return False
    try:
        return module.get_keyring() is not None
    except Exception:
        return False


def read_secret(name: str) -> str | None:
    module = _keyring_module()
    if module is None:
        return None
    try:
        return module.get_password(KEYCHAIN_SERVICE, name) or None
    except Exception:
        return None  # không có backend keychain -> coi như chưa lưu, lùi về env


def write_secret(name: str, value: str) -> None:
    """Lưu/xoá secret trong keychain OS. Ném lỗi nếu không có keychain — tuyệt đối KHÔNG
    lặng lẽ ghi key xuống settings.json (file đó không được chứa secret)."""
    module = _keyring_module()
    if module is None:
        raise SettingsError(
            "Máy này chưa dùng được keychain nên không lưu được API key. "
            "Hãy cài gói `keyring`, hoặc đặt key trong tệp .env."
        )
    try:
        if value:
            module.set_password(KEYCHAIN_SERVICE, name, value)
        else:
            module.delete_password(KEYCHAIN_SERVICE, name)
    except Exception as exc:
        if not value:
            return  # xoá thứ chưa từng lưu thì coi như xong
        raise SettingsError(f"Không ghi được vào keychain: {exc}") from exc


class SettingsError(Exception):
    """Cấu hình không hợp lệ / không lưu được. Lớp API dịch thành 422 kèm câu tiếng Việt."""


class SettingsStore:
    """Nguồn sự thật cho cấu hình, đọc theo thứ tự: keychain -> settings.json -> env -> mặc định.

    Vì sao thứ tự này: giá trị người dùng bấm lưu trong app phải thắng biến môi trường, nếu
    không thì sửa trong Cài đặt sẽ "không ăn" mà chẳng rõ vì sao. Env vẫn là nguồn hợp lệ để
    CLI, script và test chạy được mà không cần keychain hay file.
    """

    def __init__(self) -> None:
        self._current = self._build()

    @property
    def current(self) -> Settings:
        return self._current

    def _file_values(self, data_dir: Path) -> dict[str, Any]:
        path = data_dir / "settings.json"
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}  # file hỏng không được làm app không khởi động nổi
        if not isinstance(raw, dict):
            return {}
        # Loại secret và data_dir: settings.json là file thường, KHÔNG được là nơi chứa API
        # key (secret chỉ đi qua keychain), còn data_dir thì chính file này nằm bên trong.
        return {
            k: v
            for k, v in raw.items()
            if k in FIELD_SPECS and k != "data_dir" and k not in SECRET_FIELDS
        }

    def _env_value(self, name: str) -> Any:
        for env_name in FIELD_SPECS.get(name, FieldSpec()).env:
            raw = os.getenv(env_name)
            if raw not in (None, ""):
                return _coerce(name, raw)
        return None

    def _build(self) -> Settings:
        # data_dir phải chốt trước vì settings.json nằm trong nó.
        data_dir = self._env_value("data_dir") or Settings.data_dir
        values: dict[str, Any] = {"data_dir": Path(data_dir)}
        file_values = self._file_values(Path(data_dir))

        for name in FIELD_TYPES:
            if name == "data_dir":
                continue
            resolved: Any = None
            if name in SECRET_FIELDS:
                secret = read_secret(name)
                if secret:
                    resolved = secret
            if resolved is None and name in file_values:
                raw = file_values[name]
                resolved = raw if not isinstance(raw, str) else _coerce(name, raw)
            if resolved is None:
                resolved = self._env_value(name)
            if resolved is not None:
                values[name] = resolved

        settings_obj = Settings(**values)
        for directory in (settings_obj.data_dir, settings_obj.uploads_dir, settings_obj.jobs_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return settings_obj

    def reload(self) -> Settings:
        self._current = self._build()
        return self._current

    def sources(self) -> dict[str, str]:
        """Mỗi field đang lấy giá trị từ đâu ("keychain" | "file" | "env" | "default").
        UI cần biết để giải thích vì sao một ô đang hiện giá trị mà họ không đặt."""
        file_values = self._file_values(self._current.data_dir)
        out: dict[str, str] = {}
        for name in EDITABLE_FIELDS:
            if name in SECRET_FIELDS and read_secret(name):
                out[name] = "keychain"
            elif name in file_values:
                out[name] = "file"
            elif self._env_value(name) is not None:
                out[name] = "env"
            else:
                out[name] = "default"
        return out

    def update(self, changes: dict[str, Any]) -> Settings:
        """Ghi các field trong whitelist rồi nạp lại. Secret vào keychain, phần còn lại vào
        settings.json. Validate trước khi ghi bất cứ thứ gì để không lưu nửa vời."""
        cleaned: dict[str, Any] = {}
        for name, value in changes.items():
            if name not in EDITABLE_FIELDS:
                raise SettingsError(f"Không có mục cấu hình '{name}' hoặc mục này không cho sửa.")
            cleaned[name] = _validate(name, value)

        secrets = {k: v for k, v in cleaned.items() if k in SECRET_FIELDS}
        plain = {k: v for k, v in cleaned.items() if k not in SECRET_FIELDS}

        if plain:
            path = self._current.settings_file
            current = self._file_values(self._current.data_dir)
            current.update(plain)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)  # ghi nguyên tử: mất điện giữa chừng không để lại file rỗng
        for name, value in secrets.items():
            write_secret(name, value)

        return self.reload()


def _validate(name: str, value: Any) -> Any:
    spec = FIELD_SPECS[name]
    kind = FIELD_TYPES[name]
    if kind is bool:
        if not isinstance(value, bool):
            raise SettingsError(f"'{spec.label}' phải là true/false.")
        return value
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"'{spec.label}' phải là số nguyên.")
        return value
    if not isinstance(value, str):
        raise SettingsError(f"'{spec.label}' phải là chuỗi.")
    text = value.strip()
    if spec.lower:
        text = text.lower()
    if spec.choices and text and text not in spec.choices:
        raise SettingsError(f"'{spec.label}' chỉ nhận: {', '.join(spec.choices)}.")
    return text


class _SettingsProxy:
    """Đứng tên `settings` để ~90 chỗ gọi `settings.x` không phải sửa, mà vẫn thấy giá trị
    mới ngay sau `reload()`. Mọi truy cập đều rơi xuống ảnh chụp hiện tại của store."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(store.current, name)

    def __repr__(self) -> str:
        return repr(store.current)


store = SettingsStore()
settings: Any = _SettingsProxy()
