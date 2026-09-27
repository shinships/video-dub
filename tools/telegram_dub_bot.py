"""telegram_dub_bot.py — Dịch vụ Duby Bot chuyên biệt trên Telegram:
- Nhận link video (YouTube, Shorts, TikTok, X/Twitter, Facebook...) hoặc video đính kèm trực tiếp
- Tự động tải về máy
- Lồng tiếng tiếng Việt (VieNeu local, hỗ trợ tùy biến giọng và lồng tiếng 2 giọng nam/nữ)
- Nén 720p giữ nguyên tuyệt đối tỉ lệ khung hình (ngang 16:9, dọc Shorts 9:16, vuông 1:1)
- Gửi lại file video hoàn chỉnh trực tiếp vào Telegram chat/topic!
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
WATCH_DIR = Path.home() / "Movies" / "AutoDub"
OUTPUT_DIR = Path.home() / "Movies"
ORIGINALS_DIR = WATCH_DIR / "Originals"
STAGING_DIR = WATCH_DIR / ".staging"
LOCKS_DIR = WATCH_DIR / ".locks"
LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
WATCH_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
STAGING_DIR.mkdir(parents=True, exist_ok=True)
LOCKS_DIR.mkdir(parents=True, exist_ok=True)


def _load_dotenv(path: Path) -> None:
    """Nạp biến môi trường từ .env."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


_load_dotenv(ROOT / ".env")

# Nhập các hàm nén, khoá và gửi video từ watch_folder & file_lock
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from tools.file_lock import file_lock, is_file_locked, list_active_locks
    from tools.watch_folder import (
        compress_video,
        load_history,
        save_history,
        send_telegram_notification,
        send_telegram_video,
    )
except ModuleNotFoundError:
    from file_lock import file_lock, is_file_locked, list_active_locks
    from watch_folder import (
        compress_video,
        load_history,
        save_history,
        send_telegram_notification,
        send_telegram_video,
    )

TELEGRAM_API_BASE = "https://api.telegram.org"
URL_REGEX = re.compile(r"https?://[^\s]+", re.IGNORECASE)

# Danh sách giọng đọc phổ biến để gợi ý trong bot
POPULAR_VOICES = {
    "nam": [
        ("Minh Quân", "Bắc", "Tin tức, trang trọng (mặc định)"),
        ("Adam", "Nam", "Tự nhiên, trẻ trung"),
        ("Phạm Tuyên", "Bắc", "Tự nhiên, rõ ràng"),
        ("Thái Sơn", "Nam", "Kể chuyện, truyền cảm"),
        ("Xuân Vĩnh", "Bắc", "Tự nhiên, điềm đạm"),
        ("Quang Sơn", "Trung", "Tự nhiên, miền Trung"),
    ],
    "nu": [
        ("Ngọc Lan", "Bắc", "Truyền cảm, ấm áp"),
        ("Mai Anh", "Bắc", "Tin tức, năng động"),
        ("Trúc Ly", "Bắc", "Tự nhiên, nhẹ nhàng"),
        ("Thục Đoan", "Nam", "Kể chuyện, mượt mà"),
        ("Thùy Dung", "Nam", "Tin tức, hiện đại"),
        ("Ngọc Trân", "Trung", "Tự nhiên, miền Trung"),
    ],
}


def sanitize_video_title(
    title: str,
    channel: str = "",
    uploader: str = "",
    video_id: str = "",
    max_length: int = 80,
) -> str:
    """Làm sạch tiêu đề video từ YouTube/mạng xã hội để đặt tên file ngắn gọn, đẹp và an toàn:
    - Loại bỏ YouTube ID trong ngoặc vuông hoặc tròn (vd: [Ez-anO32D_s], (nBRZKG1kjBM))
    - Loại bỏ hậu tố kênh hoặc phân cách (vd: ' ｜ WSJ', ' | The Wall Street Journal', ' // BBC')
    - Loại bỏ các tag quảng bá / kỹ thuật thừa (vd: [Official Video], [HD], [4K], (1080p)...)
    - Loại bỏ emoji (giữ nguyên bảng mã tiếng Việt)
    - Thay thế ký tự cấm hệ điều hành (: -> ' -', xoá / \\ * ? " “ ” < > | ｜)
    - Giới hạn độ dài tối đa (mặc định 80 ký tự, cắt sạch tại ranh giới từ)
    """
    t = (title or "").strip()
    if not t:
        return f"video_{video_id}" if video_id else "video"

    # 1. Bỏ YouTube ID dạng 11 ký tự trong ngoặc vuông hoặc tròn
    t = re.sub(r"\[[a-zA-Z0-9_-]{11}\]", "", t)
    t = re.sub(r"\([a-zA-Z0-9_-]{11}\)", "", t)
    # Bỏ tag dạng [id] bất kỳ ở cuối chuỗi
    t = re.sub(r"\[[a-zA-Z0-9_-]+\]$", "", t).strip()

    # 2. Bỏ các tag phổ biến trong ngoặc vuông hoặc tròn
    tag_keywords = (
        r"(?i:official|music\s*video|lyrics?|audio|mv|hd|4k|1080p|60fps|"
        r"full\s*episode|vietsub|engsub|sub\s*eng|trailer|teaser|exclusive|remastered)"
    )
    t = re.sub(rf"\[\s*{tag_keywords}[^\]]*\]", "", t)
    t = re.sub(rf"\(\s*{tag_keywords}[^\)]*\)", "", t)

    # 3. Bỏ phần kênh/thương hiệu sau ký tự phân cách pipe (| hoặc ｜ hoặc //)
    # Ví dụ: " ｜ WSJ", " | The Wall Street Journal", " // Bloomberg"
    t = re.sub(r"\s*[|｜]\s*.*$", "", t)
    t = re.sub(r"\s*//\s*.*$", "", t)

    # 4. Nếu uploader/channel được truyền vào và xuất hiện ở cuối sau dấu gạch ngang (vd " - WSJ")
    for ch in [channel, uploader]:
        if ch and len(ch.strip()) >= 2:
            t = re.sub(rf"\s*[-–—]\s*{re.escape(ch.strip())}\s*$", "", t, flags=re.IGNORECASE)

    # Bỏ đuôi " - YouTube" nếu có
    t = re.sub(r"\s*[-–—]\s*YouTube\s*$", "", t, flags=re.IGNORECASE)

    # 5. Xoá emoji (giữ nguyên bảng mã BMP tiếng Việt)
    t = re.sub(r"[\U00010000-\U0010ffff]", "", t)

    # 6. Thay thế / loại bỏ các ký tự cấm hoặc gây rối hệ điều hành (: / \ * ? " “ ” < > | ｜)
    t = t.replace(":", " -")
    t = re.sub(r"[/\\*?\"“”<>|｜]", "", t)

    # 7. Chuẩn hoá khoảng trắng và ký tự thừa ở 2 đầu
    t = re.sub(r"\s+", " ", t).strip(" .-_#:")

    # 8. Giới hạn độ dài tối đa (cắt tại ranh giới từ)
    if len(t) > max_length:
        cut = t[:max_length]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        t = cut.strip(" .-_#:")

    if not t:
        return f"video_{video_id}" if video_id else "video"

    return t


class TelegramDubBot:
    def __init__(self) -> None:
        self.token = os.getenv("TELEGRAM_DUB_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.token:
            sys.exit("❌ Không tìm thấy TELEGRAM_DUB_BOT_TOKEN trong .env!")

        self.api_url = f"{TELEGRAM_API_BASE}/bot{self.token}"
        self.default_chat_id = os.getenv("TELEGRAM_DUB_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "-1003879100454")
        self.default_topic_id = os.getenv("TELEGRAM_DUB_TOPIC_ID") or os.getenv("TELEGRAM_TOPIC_ID", "3")

        allowed_str = os.getenv("TELEGRAM_ALLOWED_USERS", "1563046373")
        self.allowed_users = {int(u.strip()) for u in allowed_str.split(",") if u.strip().isdigit()}

        self.job_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.running = True
        self.offset = 0
        self.active_job: dict[str, Any] | None = None

    def register_commands(self) -> None:
        """Đăng ký danh sách menu lệnh với Telegram."""
        commands = [
            {"command": "start", "description": "Khởi động và xem menu hướng dẫn"},
            {"command": "dub", "description": "Lồng tiếng video từ link (YouTube, TikTok, X...)"},
            {"command": "voice", "description": "Xem danh sách các giọng đọc tiếng Việt"},
            {"command": "engine", "description": "Xem hoặc đổi engine dịch thuật (Gemini / DeepSeek)"},
            {"command": "status", "description": "Xem trạng thái hệ thống & hàng đợi"},
            {"command": "settings", "description": "Xem cấu hình mặc định hiện tại"},
            {"command": "help", "description": "Hướng dẫn sử dụng chi tiết"},
        ]
        try:
            resp = httpx.post(f"{self.api_url}/setMyCommands", json={"commands": commands}, timeout=10.0)
            if resp.status_code == 200 and resp.json().get("ok"):
                print("📋 Đã đồng bộ danh sách lệnh Menu với Telegram.")
        except Exception as exc:
            print(f"[Telegram] Lỗi đăng ký commands: {exc}", file=sys.stderr)

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_to_message_id: int | None = None,
        thread_id: int | str | None = None,
    ) -> dict[str, Any] | None:
        """Gửi tin nhắn phản hồi qua Telegram."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        if thread_id:
            try:
                payload["message_thread_id"] = int(thread_id)
            except (ValueError, TypeError):
                pass

        try:
            resp = httpx.post(f"{self.api_url}/sendMessage", json=payload, timeout=15.0)
            if resp.status_code == 200 and resp.json().get("ok"):
                return resp.json().get("result")
            else:
                print(f"[Telegram] Lỗi sendMessage ({resp.status_code}): {resp.text}", file=sys.stderr)
        except Exception as exc:
            print(f"[Telegram] Ngoại lệ sendMessage: {exc}", file=sys.stderr)
        return None

    def edit_message(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
    ) -> bool:
        """Cập nhật nội dung tin nhắn trạng thái thay vì gửi mới."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        try:
            resp = httpx.post(f"{self.api_url}/editMessageText", json=payload, timeout=10.0)
            return resp.status_code == 200 and resp.json().get("ok", False)
        except Exception:
            return False

    def is_authorized(self, from_user: dict[str, Any], chat: dict[str, Any]) -> bool:
        """Kiểm tra quyền của người gửi."""
        user_id = from_user.get("id")
        chat_id = str(chat.get("id"))
        if user_id in self.allowed_users:
            return True
        if self.default_chat_id and chat_id == str(self.default_chat_id):
            return True
        return False

    def download_video(self, url: str, dest_dir: Path) -> tuple[Path, str]:
        """Tải video bằng yt-dlp, đặt tên file ngắn gọn sạch sẽ, trả về (đường_dẫn_file, tiêu_đề)."""
        import yt_dlp

        dest_dir.mkdir(parents=True, exist_ok=True)
        # Sử dụng template trung gian có tiền tố dl_ và id để tránh ký tự đặc biệt khi tải
        outtmpl = str(dest_dir / "dl_%(id)s.%(ext)s")
        node_bin = shutil.which("node")
        if not node_bin:
            for candidate in (
                "/Users/mktmda/aicoworker/app/nodejs/bin/node",
                "/opt/homebrew/bin/node",
                "/usr/local/bin/node",
            ):
                if Path(candidate).is_file():
                    node_bin = candidate
                    break

        options = {
            "outtmpl": outtmpl,
            "format": "bestvideo[height<=1080]+bestaudio/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            # YouTube cần JS runtime để tạo PO token, không có sẽ bị 403
            "js_runtimes": {"node": {"path": node_bin}} if node_bin else {"node": {}},
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = str(info.get("id") or "video")
            raw_title = str(info.get("title") or "")
            channel = str(info.get("channel") or info.get("uploader") or "")
            uploader = str(info.get("uploader") or "")

            filename = ydl.prepare_filename(info)
            temp_path = Path(filename).with_suffix(".mp4")
            if not temp_path.is_file():
                temp_path = Path(filename)
            if not temp_path.is_file():
                candidates = list(dest_dir.glob(f"dl_{video_id}.*"))
                if candidates:
                    temp_path = candidates[0]
                else:
                    raise RuntimeError(f"Không tìm thấy file tải về cho {url}")

            # Làm sạch tiêu đề video (loại bỏ [id], tag [Official...], kênh | WSJ...)
            clean_title = sanitize_video_title(raw_title, channel=channel, uploader=uploader, video_id=video_id)
            target_path = dest_dir / f"{clean_title}.mp4"

            # Đảm bảo không trùng với file đang có sẵn khác
            if target_path.exists() and target_path.resolve() != temp_path.resolve():
                counter = 1
                while target_path.exists() and target_path.resolve() != temp_path.resolve():
                    target_path = dest_dir / f"{clean_title}_{counter}.mp4"
                    counter += 1
                clean_title = target_path.stem

            if temp_path.resolve() != target_path.resolve():
                if target_path.exists():
                    target_path.unlink()
                temp_path.rename(target_path)

            return target_path, clean_title

    def download_telegram_file(self, file_id: str, dest_path: Path) -> bool:
        """Tải file video đính kèm trực tiếp từ Telegram về máy."""
        try:
            resp = httpx.get(f"{self.api_url}/getFile", params={"file_id": file_id}, timeout=15.0)
            if resp.status_code != 200 or not resp.json().get("ok"):
                return False
            file_path = resp.json()["result"]["file_path"]
            download_url = f"{TELEGRAM_API_BASE}/file/bot{self.token}/{file_path}"
            with httpx.stream("GET", download_url, timeout=180.0) as r:
                r.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=65536):
                        f.write(chunk)
            return dest_path.is_file()
        except Exception as exc:
            print(f"[Telegram] Lỗi khi tải file đính kèm: {exc}", file=sys.stderr)
            return False

    def process_job(self, job: dict[str, Any]) -> None:
        """Hàm xử lý tuần tự một yêu cầu tải và lồng tiếng."""
        self.active_job = job
        job_type = job.get("type", "url")
        chat_id = job["chat_id"]
        thread_id = job.get("thread_id")
        msg_id = job["msg_id"]
        sender_name = job.get("sender_name", "Bạn")
        chosen_voice = job.get("voice") or os.getenv("VIDEO_DUB_VOICE", "Minh Quân")
        multi_speaker = job.get("multi_speaker", False)

        start_time = time.time()
        status_msg = None

        try:
            # 1. Tải video nguồn vào thư mục đệm .staging để watch_folder không quét phải khi tải dở
            if job_type == "url":
                url = job["url"]
                print(f"\n[Worker] Bắt đầu xử lý link từ {sender_name}: {url}")
                status_msg = self.send_message(
                    chat_id,
                    f"⏳ <b>[1/4] Đang tải video từ Internet...</b>\n🔗 Link: <code>{url}</code>",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                staging_file, title = self.download_video(url, STAGING_DIR)
            else:
                # File đính kèm
                file_name = job["file_name"]
                print(f"\n[Worker] Bắt đầu tải file đính kèm từ {sender_name}: {file_name}")
                status_msg = self.send_message(
                    chat_id,
                    f"⏳ <b>[1/4] Đang tải video đính kèm về máy...</b>\n📁 File: <code>{file_name}</code>",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                clean_stem = sanitize_video_title(Path(file_name).stem)
                clean_name = f"{clean_stem}{Path(file_name).suffix}"
                staging_file = STAGING_DIR / clean_name
                if not self.download_telegram_file(job["file_id"], staging_file):
                    raise RuntimeError("Không thể tải file từ Telegram.")
                title = clean_stem

            source_file = WATCH_DIR / staging_file.name
            raw_output = WATCH_DIR / f"{source_file.stem}_raw_VN.mp4"
            # Tạo tên file thành phẩm ngắn gọn bằng keywords
            try:
                from tools.keyword_renamer import get_keyword_output_path
                final_target = get_keyword_output_path(title or source_file.stem, OUTPUT_DIR, suffix="_VN")
            except Exception:
                final_target = OUTPUT_DIR / f"{source_file.stem}_VN.mp4"
            history_file = WATCH_DIR / ".autodub_history.json"

            # Chiếm khoá trước khi chuyển file vào WATCH_DIR để bảo vệ khỏi các tiến trình watch_folder khác
            with file_lock(source_file, owner="telegram_bot", details={"title": title, "sender": sender_name, "type": job_type}) as acquired:
                if not acquired:
                    msg = f"⚠️ Video <b>{title}</b> đang được tiến trình khác xử lý. Vui lòng thử lại sau!"
                    if status_msg:
                        self.edit_message(chat_id, status_msg["message_id"], msg)
                    else:
                        self.send_message(chat_id, msg, reply_to_message_id=msg_id, thread_id=thread_id)
                    return

                # Di chuyển an toàn từ .staging sang WATCH_DIR
                if staging_file.resolve() != source_file.resolve():
                    if source_file.exists():
                        source_file.unlink()
                    shutil.move(str(staging_file), str(source_file))

                print(f"[Worker] Đã chuyển video sang AutoDub: {source_file.name} ({source_file.stat().st_size / 1024 / 1024:.1f} MB)")

                # 2. Cập nhật trạng thái lồng tiếng
                tts_engine = os.getenv("VIDEO_DUB_JOB_TTS_ENGINE", "vieneu")
                mode_text = " (2 giọng nam/nữ)" if multi_speaker else ""
                chosen_trans_engine = job.get("translate_engine") or os.getenv("VIDEO_DUB_TRANSLATE_ENGINE", "auto").lower()
                has_ds = bool(os.getenv("DEEPSEEK_API_KEY"))
                has_gemini = bool(os.getenv("GEMINI_API_KEY"))
                fallback_on = os.getenv("VIDEO_DUB_TRANSLATE_FALLBACK", "true").lower() in {"1", "true", "yes"} and has_ds and has_gemini
                if chosen_trans_engine == "deepseek" or (chosen_trans_engine == "auto" and has_ds and not has_gemini):
                    trans_label = f"DeepSeek ({os.getenv('VIDEO_DUB_DEEPSEEK_MODEL', 'deepseek-v4-pro')})"
                    if fallback_on:
                        trans_label += " ↔ fallback Gemini"
                else:
                    trans_label = f"Gemini ({os.getenv('VIDEO_DUB_GEMINI_MODEL', 'gemini-3.8-flash')})"
                    if fallback_on:
                        trans_label += " ↔ fallback DeepSeek"
                update_text = (
                    f"🎬 <b>[2/4] Đang lồng tiếng Việt{mode_text}...</b>\n"
                    f"📁 Video: <i>{title}</i>\n"
                    f"🗣️ Giọng đọc: <b>{chosen_voice}</b> ({tts_engine})\n"
                    f"⏳ <i>Đang tách nhạc nền Demucs, nhận dạng Whisper & {trans_label}...</i>"
                )
                if status_msg:
                    self.edit_message(chat_id, status_msg["message_id"], update_text)

                # 3. Gọi công cụ lồng tiếng độc lập
                run_script = ROOT / "tools" / "run_video_dub_job.py"
                cmd = [
                    sys.executable,
                    str(run_script),
                    "--source",
                    str(source_file),
                    "--output",
                    str(raw_output),
                    "--voice",
                    chosen_voice,
                    "--tts-engine",
                    tts_engine,
                ]
                if multi_speaker:
                    cmd.append("--multi-speaker")
                if job.get("translate_engine"):
                    cmd.extend(["--translate-engine", job["translate_engine"]])

                env = os.environ.copy()
                env["VIDEO_DUB_SKIP_TELEGRAM_DELIVERY"] = "1"
                res = subprocess.run(cmd, cwd=str(ROOT), env=env)
                file_key = f"{source_file.name}:{source_file.stat().st_size}"
                history = load_history(history_file)
                dub_success = (
                    (res.returncode == 0 or (res.returncode == -6 and raw_output.is_file()))
                    and raw_output.is_file()
                    and raw_output.stat().st_size > 1000
                )

                if not dub_success:
                    # Đánh dấu vào lịch sử để không bị watch_folder thử lại vô tận nếu file lỗi/không có lời thoại
                    history.add(file_key)
                    save_history(history_file, history)

                    # Trích xuất lý do lỗi chi tiết từ database jobs nếu có
                    error_detail = ""
                    try:
                        from backend.app.db import connect
                        with connect() as conn:
                            row = conn.execute(
                                "SELECT error FROM jobs WHERE name = ? ORDER BY created_at DESC LIMIT 1",
                                (source_file.name,)
                            ).fetchone()
                            if row and row["error"]:
                                err = str(row["error"]).strip()
                                if "Không phát hiện được lời thoại" in err or "no speech" in err.lower():
                                    error_detail = "Quá trình nhận dạng giọng nói (Whisper) không phát hiện thấy lời thoại tiếng Anh nào trong video."
                                else:
                                    error_detail = err
                    except Exception:
                        pass

                    if not error_detail:
                        error_detail = f"Tiến trình kết thúc với mã thoát: {res.returncode}. Video có thể không có lời thoại tiếng Anh hoặc gặp sự cố xử lý."

                    file_mb = source_file.stat().st_size / (1024 * 1024) if source_file.is_file() else 0.0
                    err_msg = (
                        f"❌ <b>Không thể lồng tiếng cho video:</b>\n\n"
                        f"🎬 <b>Tên video:</b> <i>{title}</i>\n"
                        f"📁 <b>File nguồn:</b> <code>{source_file.name}</code> ({file_mb:.1f} MB)\n"
                        f"ℹ️ <b>Chi tiết:</b> {error_detail}\n\n"
                        f"💡 <i>Vui lòng kiểm tra lại video (đảm bảo có giọng nói tiếng Anh rõ ràng) hoặc gửi link khác!</i>"
                    )
                    if status_msg:
                        self.edit_message(chat_id, status_msg["message_id"], err_msg)
                    else:
                        self.send_message(chat_id, err_msg, reply_to_message_id=msg_id, thread_id=thread_id)
                    return

                # Ghi nhận vào lịch sử dùng chung để watch_folder không xử lý lặp
                history.add(file_key)
                save_history(history_file, history)

                # 4. Nén video xuống 720p bảo toàn tỉ lệ khung hình gốc
                if status_msg:
                    self.edit_message(
                        chat_id,
                        status_msg["message_id"],
                        f"📦 <b>[3/4] Đang nén video xuống 720p...</b>\n<i>Giữ nguyên tỉ lệ khung hình gốc & tối ưu dung lượng...</i>",
                    )

                final_video = final_target
                comp_info = ""
                c_path = compress_video(raw_output, resolution=720)
                if c_path and c_path.is_file():
                    orig_mb = raw_output.stat().st_size / (1024 * 1024) if raw_output.is_file() else 0.0
                    new_mb = c_path.stat().st_size / (1024 * 1024)
                    saved_pct = (1 - new_mb / max(0.001, orig_mb)) * 100 if orig_mb > 0 else 0.0
                    comp_info = f"\n📦 <b>Nén 720p:</b> {orig_mb:.1f} MB ➔ {new_mb:.1f} MB (giảm {saved_pct:.0f}%)"
                    # Xoá bản thô 1080p
                    try:
                        raw_output.unlink()
                    except Exception:
                        pass
                    # Di chuyển sang ~/Movies/<tên>_VN.mp4
                    if final_video.exists() and final_video.resolve() != c_path.resolve():
                        final_video.unlink()
                    shutil.move(str(c_path), str(final_video))
                else:
                    if final_video.exists() and final_video.resolve() != raw_output.resolve():
                        final_video.unlink()
                    shutil.move(str(raw_output), str(final_video))

                # Chuyển file gốc tiếng Anh sang Originals/
                orig_dest = ORIGINALS_DIR / source_file.name
                try:
                    if orig_dest.exists() and orig_dest.resolve() != source_file.resolve():
                        orig_dest.unlink()
                    shutil.move(str(source_file), str(orig_dest))
                    print(f"[Worker] Đã chuyển video gốc vào Originals: {orig_dest.name}")
                except Exception as exc:
                    print(f"[Worker] Không thể chuyển video gốc vào Originals: {exc}", file=sys.stderr)

                # 5. Gửi video thành phẩm
                if status_msg:
                    self.edit_message(
                        chat_id,
                        status_msg["message_id"],
                        f"📤 <b>[4/4] Đang gửi video thành phẩm lên Telegram...</b>",
                    )

                elapsed_sec = int(time.time() - start_time)
                elapsed_str = f"{elapsed_sec // 60}m {elapsed_sec % 60}s" if elapsed_sec >= 60 else f"{elapsed_sec}s"
                caption = (
                    f"🎉 <b>Lồng tiếng & Nén 720p hoàn tất!</b>\n\n"
                    f"🎬 <b>Tên video:</b> <i>{title}</i>\n"
                    f"🗣️ <b>Giọng đọc:</b> {chosen_voice} ({tts_engine}){mode_text}\n"
                    f"⏱️ <b>Thời gian xử lý:</b> {elapsed_str}"
                    f"{comp_info}\n"
                    f"📍 <b>Lưu tại máy:</b> <code>{final_video}</code>"
                )

                send_telegram_video(
                    final_video,
                    caption=caption,
                    token=self.token,
                    chat_id=str(chat_id),
                    topic_id=str(thread_id) if thread_id else None,
                )

        except Exception as exc:
            print(f"[Worker] Lỗi ngoại lệ khi xử lý video: {exc}", file=sys.stderr)
            err_text = f"❌ <b>Có lỗi xảy ra:</b> {exc}"
            if status_msg:
                self.edit_message(chat_id, status_msg["message_id"], err_text)
            else:
                self.send_message(chat_id, err_text, reply_to_message_id=msg_id, thread_id=thread_id)
        finally:
            self.active_job = None

    def worker_loop(self) -> None:
        """Vòng lặp hàng đợi xử lý tuần tự từng video."""
        while self.running:
            try:
                job = self.job_queue.get(timeout=1.0)
                try:
                    self.process_job(job)
                finally:
                    self.job_queue.task_done()
            except queue.Empty:
                continue

    def handle_message(self, message: dict[str, Any]) -> None:
        """Phân tích tin nhắn gửi đến bot."""
        from_user = message.get("from", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        msg_id = message.get("message_id")
        thread_id = message.get("message_thread_id")
        text = (message.get("text") or "").strip()
        sender_name = from_user.get("first_name", "Bạn")

        if not chat_id:
            return

        # ── 1. Lệnh /start hoặc /help ──
        if text.startswith(("/start", "/help")):
            welcome_msg = (
                f"👋 Xin chào <b>{sender_name}</b>! Tôi là <b>Duby</b> 🎬\n\n"
                "Tôi hỗ trợ tự động tải video YouTube và lồng tiếng Việt chuẩn ngữ cảnh.\n\n"
                "💡 <b>Các cách sử dụng:</b>\n"
                "1. <b>Gửi link trực tiếp</b>: Chỉ cần dán link YouTube (kể cả Shorts), TikTok, Twitter/X vào đây.\n"
                "2. <b>Lệnh /dub</b>: <code>/dub &lt;link&gt;</code>\n"
                "3. <b>Chọn giọng đọc</b>: <code>/dub &lt;link&gt; Mai Anh</code> hoặc <code>/dub &lt;link&gt; Adam</code>\n"
                "4. <b>Bật 2 giọng nam/nữ</b>: Thêm <code>--multi</code> (ví dụ: <code>/dub &lt;link&gt; --multi</code>)\n"
                "5. <b>Chọn AI dịch thuật</b>: Thêm <code>--deepseek</code> hoặc <code>--gemini</code>\n"
                "6. <b>Gửi file video</b>: Bạn cũng có thể gửi thẳng 1 file video (.mp4 &lt;20MB) vào đây!\n\n"
                "📋 <b>Danh sách lệnh nhanh:</b>\n"
                "• <code>/voice</code> — Xem danh sách các giọng đọc tiếng Việt\n"
                "• <code>/engine</code> — Xem hoặc đổi engine dịch thuật (Gemini / DeepSeek)\n"
                "• <code>/status</code> — Xem tình trạng máy tính & hàng đợi\n"
                "• <code>/settings</code> — Xem cấu hình mặc định hiện tại"
            )
            self.send_message(chat_id, welcome_msg, reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── 2. Lệnh /voice ──
        if text.startswith("/voice"):
            nam_lines = "\n".join([f"• <b>{name}</b> ({region}) — {desc}" for name, region, desc in POPULAR_VOICES["nam"]])
            nu_lines = "\n".join([f"• <b>{name}</b> ({region}) — {desc}" for name, region, desc in POPULAR_VOICES["nu"]])
            current_voice = os.getenv("VIDEO_DUB_VOICE", "Minh Quân")
            voice_msg = (
                f"🎙️ <b>Danh Sách Giọng Đọc Tiếng Việt</b>\n"
                f"Giọng mặc định hiện tại: <b>{current_voice}</b>\n\n"
                f"👨 <b>Giọng Nam:</b>\n{nam_lines}\n\n"
                f"👩 <b>Giọng Nữ:</b>\n{nu_lines}\n\n"
                "💡 <b>Cách chọn giọng khi lồng tiếng:</b>\n"
                "Gõ: <code>/dub &lt;link&gt; &lt;tên_giọng&gt;</code>\n"
                "Ví dụ: <code>/dub https://youtu.be/xyz Mai Anh</code>"
            )
            self.send_message(chat_id, voice_msg, reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── 3. Lệnh /status ──
        if text.startswith("/status"):
            q_size = self.job_queue.qsize()
            active_info = "Đang rảnh rỗi" if not self.active_job else f"Đang xử lý 1 video ({self.active_job.get('url', self.active_job.get('file_name'))})"
            video_count = len([f for f in WATCH_DIR.glob("*.mp4") if not f.name.startswith(".")])

            # Liệt kê các tiến trình đang khoá xử lý video (từ watch_folder hoặc bot)
            active_locks = list_active_locks(LOCKS_DIR)
            lock_info_text = ""
            if active_locks:
                lock_lines = [
                    f"• <b>{item.get('file')}</b> ({item.get('owner', 'tiến trình')}, PID {item.get('pid')})"
                    for item in active_locks
                ]
                lock_info_text = "\n\n🔒 <b>Tiến trình đang chạy:</b>\n" + "\n".join(lock_lines)

            status_msg = (
                "📊 <b>Trạng Thái Hệ Thống Duby</b>\n\n"
                f"⚙️ <b>Hoạt động:</b> {active_info}\n"
                f"⏳ <b>Hàng đợi đang chờ:</b> {q_size} video\n"
                f"📁 <b>Thư mục AutoDub:</b> {video_count} file\n"
                f"🖥️ <b>Môi trường:</b> macOS (LaunchAgent 24/7)\n"
                f"🎯 <b>Nén tự động:</b> 720p (Bảo toàn tỉ lệ)"
                f"{lock_info_text}"
            )
            self.send_message(chat_id, status_msg, reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── 4. Lệnh /settings ──
        if text.startswith("/settings"):
            tts_engine = os.getenv("VIDEO_DUB_JOB_TTS_ENGINE", "vieneu")
            voice = os.getenv("VIDEO_DUB_VOICE", "Minh Quân")
            multi = os.getenv("VIDEO_DUB_MULTI_SPEAKER", "false")
            from backend.app.config import settings
            cur_engine = settings.effective_translate_engine.upper()
            cur_model = settings.active_translate_model
            fb_str = "Bật (tự động chuyển đổi khi hết Quota)" if settings.translate_fallback else "Tắt"
            settings_msg = (
                "⚙️ <b>Cấu Hình Mặc Định Của Bot</b>\n\n"
                f"• <b>Engine TTS:</b> <code>{tts_engine}</code>\n"
                f"• <b>Giọng mặc định:</b> <b>{voice}</b>\n"
                f"• <b>Lồng tiếng 2 giọng:</b> <code>{multi}</code>\n"
                f"• <b>Dịch AI:</b> <code>{cur_engine}</code> (Model: <code>{cur_model}</code>)\n"
                f"• <b>Fallback song song:</b> {fb_str}\n"
                f"• <b>Độ phân giải nén:</b> <code>720p</code>\n"
                f"• <b>Khung hình:</b> Giữ nguyên tỉ lệ (ngang 16:9 / dọc Shorts 9:16)\n"
                f"• <b>Thư mục lưu trữ:</b> <code>~/Movies/AutoDub</code>"
            )
            self.send_message(chat_id, settings_msg, reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── 4b. Lệnh /engine [gemini|deepseek|auto] ──
        if text.startswith("/engine"):
            parts = text.split()
            from backend.app.config import settings
            if len(parts) > 1:
                target_engine = parts[1].lower().strip()
                if target_engine in ("gemini", "deepseek", "auto"):
                    os.environ["VIDEO_DUB_TRANSLATE_ENGINE"] = target_engine
                    settings.translate_engine = target_engine
                    engine_label = (
                        "Gemini (Chính) ↔ DeepSeek (Dự phòng)"
                        if target_engine == "gemini"
                        else (
                            "DeepSeek (Chính) ↔ Gemini (Dự phòng)"
                            if target_engine == "deepseek"
                            else "Tự động (theo key khả dụng)"
                        )
                    )
                    msg = (
                        f"✅ <b>Đã chuyển engine dịch thuật sang:</b> <code>{target_engine}</code>\n"
                        f"📌 Chế độ: <b>{engine_label}</b>\n"
                        f"🤖 Model hiện hành: <code>{settings.active_translate_model}</code>\n"
                        "💡 Khi engine chính hết quota hoặc gặp 429, hệ thống sẽ tự động fallback sang engine còn lại."
                    )
                else:
                    msg = "❌ Engine không hợp lệ! Vui lòng dùng: <code>/engine gemini</code>, <code>/engine deepseek</code>, hoặc <code>/engine auto</code>"
            else:
                cur = settings.effective_translate_engine
                gemini_count = len(settings.gemini_api_keys)
                ds_count = len(settings.deepseek_api_keys)
                fallback_status = "Bật (Tự động chuyển đổi khi hết Quota)" if settings.translate_fallback else "Tắt"
                msg = (
                    "🌐 <b>Cấu Hình Engine Dịch Thuật Đa Nền Tảng</b>\n\n"
                    f"• <b>Engine hiện tại:</b> <code>{cur.upper()}</code>\n"
                    f"• <b>Model đang dùng:</b> <code>{settings.active_translate_model}</code>\n"
                    f"• <b>Chế độ Fallback:</b> {fallback_status}\n"
                    f"• <b>Gemini Keys:</b> <code>{gemini_count} key</code> (Model: <code>{settings.gemini_model}</code>)\n"
                    f"• <b>DeepSeek Keys:</b> <code>{ds_count} key</code> (Model: <code>{settings.deepseek_model}</code>)\n\n"
                    "🔄 <b>Lệnh chuyển đổi nhanh:</b>\n"
                    "• <code>/engine deepseek</code> — Ưu tiên DeepSeek (deepseek-v4-pro)\n"
                    "• <code>/engine gemini</code> — Ưu tiên Google Gemini\n"
                    "• <code>/engine auto</code> — Tự động theo key có sẵn\n\n"
                    "💡 Bạn cũng có thể thêm cờ <code>--deepseek</code> hoặc <code>--gemini</code> ngay trong link video khi gửi!"
                )
            self.send_message(chat_id, msg, reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # ── 5. Xử lý video/document đính kèm trực tiếp ──
        doc_or_video = message.get("video") or message.get("document")
        if doc_or_video:
            caption = message.get("caption") or ""
            file_name = doc_or_video.get("file_name", f"telegram_video_{int(time.time())}.mp4")
            file_id = doc_or_video.get("file_id")
            file_size = doc_or_video.get("file_size", 0)
            ext = Path(file_name).suffix.lower()

            if ext in (".mp4", ".mov", ".mkv", ".webm"):
                if not self.is_authorized(from_user, chat):
                    self.send_message(chat_id, "🔒 Rất tiếc, bạn chưa có quyền sử dụng bot này.", reply_to_message_id=msg_id, thread_id=thread_id)
                    return

                if file_size > 20 * 1024 * 1024:
                    size_mb = file_size / (1024 * 1024)
                    self.send_message(
                        chat_id,
                        f"❌ File <b>{file_name}</b> quá lớn ({size_mb:.1f} MB).\n"
                        "Telegram Bot API giới hạn bot tải trực tiếp file dưới 20 MB. "
                        "Với file lớn hơn, hãy gửi link YouTube hoặc copy thẳng vào thư mục <code>~/Movies/AutoDub</code> trên máy Mac!",
                        reply_to_message_id=msg_id,
                        thread_id=thread_id,
                    )
                    return

                file_multi = "--multi" in caption.lower()
                file_engine = None
                if "--deepseek" in caption.lower():
                    file_engine = "deepseek"
                elif "--gemini" in caption.lower():
                    file_engine = "gemini"
                clean_cap = caption.replace("--multi", "").replace("--deepseek", "").replace("--gemini", "").strip()
                file_voice = clean_cap if clean_cap else None

                mode_str = " (2 giọng)" if file_multi else ""
                voice_str = f" | Giọng: {file_voice}" if file_voice else ""
                engine_str = f" | Dịch: {file_engine.upper()}" if file_engine else ""

                self.send_message(
                    chat_id,
                    f"📥 <b>Đã nhận file video trực tiếp:</b> <code>{file_name}</code>{mode_str}{voice_str}{engine_str}\n⏳ Đang đưa vào hàng đợi lồng tiếng...",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                self.job_queue.put({
                    "type": "file",
                    "file_id": file_id,
                    "file_name": file_name,
                    "chat_id": chat_id,
                    "thread_id": thread_id,
                    "msg_id": msg_id,
                    "sender_name": sender_name,
                    "voice": file_voice,
                    "multi_speaker": file_multi,
                    "translate_engine": file_engine,
                })
                return

        # ── 6. Tìm link video trong tin nhắn hoặc lệnh /dub ──
        urls = URL_REGEX.findall(text)
        if not urls:
            return

        target_url = urls[0]

        # Kiểm tra tùy biến giọng hoặc cờ --multi, cờ --deepseek / --gemini
        chosen_voice = None
        multi_speaker = "--multi" in text.lower()
        translate_engine = None
        if "--deepseek" in text.lower():
            translate_engine = "deepseek"
        elif "--gemini" in text.lower():
            translate_engine = "gemini"

        cleaned_text = (
            text.replace(target_url, "")
            .replace("/dub", "")
            .replace("--multi", "")
            .replace("--deepseek", "")
            .replace("--gemini", "")
            .strip()
        )
        if cleaned_text:
            # Người dùng gõ tên giọng, vd "Mai Anh"
            chosen_voice = cleaned_text

        # Kiểm tra quyền
        if not self.is_authorized(from_user, chat):
            self.send_message(
                chat_id,
                "🔒 Rất tiếc, bạn chưa có quyền sử dụng bot này.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return

        mode_str = " (Lồng tiếng 2 giọng nam/nữ)" if multi_speaker else ""
        voice_str = f" | Giọng: <b>{chosen_voice}</b>" if chosen_voice else ""
        engine_str = f" | Dịch: <b>{translate_engine.upper()}</b>" if translate_engine else ""

        self.send_message(
            chat_id,
            f"📥 <b>Đã tiếp nhận link video!</b>{mode_str}{voice_str}{engine_str}\n"
            f"🔗 <b>URL:</b> <code>{target_url}</code>\n"
            f"⏳ Đang xếp vào hàng đợi xử lý...",
            reply_to_message_id=msg_id,
            thread_id=thread_id,
        )
        self.job_queue.put({
            "type": "url",
            "url": target_url,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "msg_id": msg_id,
            "sender_name": sender_name,
            "voice": chosen_voice,
            "multi_speaker": multi_speaker,
            "translate_engine": translate_engine,
        })

    def run(self) -> None:
        """Khởi động long-polling lắng nghe Telegram."""
        print("=" * 60)
        print("🤖 DUBY TELEGRAM BOT ĐANG KHỞI ĐỘNG...")
        print(f"📁 Thư mục lưu trữ: {WATCH_DIR}")
        print("=" * 60)

        # Kiểm tra kết nối
        try:
            me_resp = httpx.get(f"{self.api_url}/getMe", timeout=10.0)
            if me_resp.status_code == 200 and me_resp.json().get("ok"):
                bot_info = me_resp.json().get("result", {})
                print(f"✅ Đã kết nối thành công: @{bot_info.get('username')} ({bot_info.get('first_name')})")
            else:
                sys.exit(f"❌ Token bot không hợp lệ: {me_resp.text}")
        except Exception as exc:
            sys.exit(f"❌ Không thể kết nối tới Telegram API: {exc}")

        # Tự động đăng ký danh sách lệnh menu
        self.register_commands()

        # Khởi chạy luồng xử lý nền
        worker_thread = threading.Thread(target=self.worker_loop, daemon=True)
        worker_thread.start()

        print("👂 Đang lắng nghe link video từ Telegram (Bấm Ctrl+C để dừng)...\n", flush=True)

        client = httpx.Client(timeout=30.0)
        while self.running:
            try:
                resp = client.get(
                    f"{self.api_url}/getUpdates",
                    params={"offset": self.offset, "timeout": 20},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        self.offset = update["update_id"] + 1
                        if "message" in update:
                            self.handle_message(update["message"])
                elif resp.status_code == 409:
                    print("[Telegram] Trùng lặp phiên lắng nghe (409 Conflict). Thử lại sau 5s...", file=sys.stderr)
                    time.sleep(5)
                else:
                    time.sleep(2)
            except httpx.TimeoutException:
                continue
            except Exception as exc:
                print(f"[Telegram] Lỗi vòng lặp getUpdates: {exc}", file=sys.stderr)
                time.sleep(3)


if __name__ == "__main__":
    bot = TelegramDubBot()
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n👋 Đã dừng Duby Bot.")
        bot.running = False
