"""Tự động theo dõi một thư mục (Watch Folder): khi có video mới được thả vào,
tự động lồng tiếng Việt và xuất ra file có định dạng: <tên-video>_VN.mp4.

Ví dụ chạy:
    ./.venv/bin/python tools/watch_folder.py
    ./.venv/bin/python tools/watch_folder.py --watch-dir "/Users/mktmda/Movies/AutoDub" --voice "Minh Quân"
    ./.venv/bin/python tools/watch_folder.py --once   # Quét 1 lượt rồi thoát (batch mode)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_dotenv(path: Path) -> None:
    """Nạp cấu hình từ .env nếu có."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


_load_dotenv(ROOT / ".env")

try:
    from tools.file_lock import (
        acquire_file_lock,
        file_lock,
        is_file_locked,
        list_active_locks,
        release_file_lock,
    )
    from tools.telegram_video import probe_video
except ModuleNotFoundError:
    from file_lock import (
        acquire_file_lock,
        file_lock,
        is_file_locked,
        list_active_locks,
        release_file_lock,
    )
    from telegram_video import probe_video

DEFAULT_WATCH_DIR = Path.home() / "Movies" / "AutoDub"
DEFAULT_OUTPUT_DIR = Path.home() / "Movies"
DEFAULT_ORIGINALS_DIR = DEFAULT_WATCH_DIR / "Originals"
DEFAULT_VOICE = os.getenv("VIDEO_DUB_VOICE", "Minh Quân")
SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}
IGNORE_SUFFIXES = {"_vn", ".vi"}
TEMP_EXTENSIONS = {".tmp", ".part", ".crdownload", ".download", ".downloading"}


DEFAULT_COMPRESS_SCRIPT = Path("/Users/mktmda/Projects/vibe-coding/video-compress/video_compress.py")


def send_telegram_notification(
    message: str,
    token: str | None = None,
    chat_id: str | None = None,
    topic_id: str | None = None,
) -> bool:
    """Gửi thông báo text qua Telegram Bot API (dùng urllib chuẩn, không cần thư viện ngoài)."""
    token = token or os.getenv("TELEGRAM_DUB_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    topic_id = topic_id or os.getenv("TELEGRAM_TOPIC_ID")

    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }
    if topic_id:
        try:
            payload["message_thread_id"] = int(topic_id)
        except (ValueError, TypeError):
            pass

    try:
        import urllib.request

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as exc:
        print(f"[Telegram] Không thể gửi thông báo: {exc}", file=sys.stderr)
        return False


def send_telegram_video(
    video_path: Path,
    caption: str,
    token: str | None = None,
    chat_id: str | None = None,
    topic_id: str | None = None,
) -> bool:
    """Gửi file video trực tiếp qua Telegram Bot API (sendVideo).
    Hỗ trợ stream video trong chat. Nếu file > 50MB (giới hạn của Telegram Bot API),
    sẽ tự động lùi về gửi thông báo text kèm đường dẫn file."""
    token = token or os.getenv("TELEGRAM_DUB_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    topic_id = topic_id or os.getenv("TELEGRAM_TOPIC_ID")

    if not token or not chat_id or not video_path.is_file():
        return False

    file_size_mb = video_path.stat().st_size / (1024 * 1024)
    if file_size_mb > 50.0:
        print(
            f"[Telegram] Video ({file_size_mb:.1f} MB) vượt trần 50MB của Bot API. Chuyển sang thông báo text.",
            file=sys.stderr,
        )
        oversize_msg = (
            f"{caption}\n\n"
            f"⚠️ <i>Lưu ý: File video ({file_size_mb:.1f} MB) vượt trần 50MB của Telegram Bot API. "
            f"Video đã lưu tại:</i>\n<code>{video_path}</code>"
        )
        return send_telegram_notification(oversize_msg, token=token, chat_id=chat_id, topic_id=topic_id)

    url = f"https://api.telegram.org/bot{token}/sendVideo"
    try:
        metadata = probe_video(video_path)
    except Exception as exc:
        print(f"[Telegram] Không thể probe metadata video trước khi tải lên: {exc}", file=sys.stderr)
        return False
    data: dict[str, Any] = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "HTML",
        "supports_streaming": "true",
        "width": str(metadata["width"]),
        "height": str(metadata["height"]),
        "duration": str(metadata["duration"]),
    }
    if topic_id:
        try:
            data["message_thread_id"] = str(int(topic_id))
        except (ValueError, TypeError):
            pass

    try:
        import httpx

        print(f"📤 Đang gửi video ({file_size_mb:.1f} MB) tới Telegram Topic...")
        with open(video_path, "rb") as f:
            files = {"video": (video_path.name, f, "video/mp4")}
            resp = httpx.post(url, data=data, files=files, timeout=300.0)
            if resp.status_code == 200 and resp.json().get("ok"):
                print("✅ Đã gửi video thành công vào Topic Telegram!")
                return True
            else:
                print(f"[Telegram] sendVideo thất bại ({resp.status_code}): {resp.text}", file=sys.stderr)
                return send_telegram_notification(caption, token=token, chat_id=chat_id, topic_id=topic_id)
    except Exception as exc:
        print(f"[Telegram] Lỗi khi tải video lên Telegram: {exc}", file=sys.stderr)
        return send_telegram_notification(caption, token=token, chat_id=chat_id, topic_id=topic_id)


def compress_video(
    src: Path,
    resolution: int = 720,
    crf: int = 20,
    preset: str = "medium",
    compress_script: Path | None = None,
) -> Path | None:
    """Nén video bằng công cụ video_compress.py (xuống 720p nét cao: CRF 20, preset medium)."""
    script = compress_script or Path(os.getenv("VIDEO_COMPRESS_SCRIPT", str(DEFAULT_COMPRESS_SCRIPT)))
    if not script.is_file():
        print(f"[Cảnh báo] Không tìm thấy công cụ nén video: {script}", file=sys.stderr)
        return None

    print(f"\n📦 Bắt đầu nén video sang {resolution}p bằng {script.name} (CRF {crf}, preset {preset})...")
    cmd = [
        sys.executable,
        str(script),
        str(src),
        "-r",
        str(resolution),
        "--crf",
        str(crf),
        "--preset",
        preset,
        "--overwrite",
    ]
    try:
        subprocess.run(cmd, check=True)
        # video_compress.py tạo file: <tên>_compressed.mp4
        compressed_file = src.parent / f"{src.stem}_compressed.mp4"
        if compressed_file.is_file():
            target_file = src.parent / f"{src.stem}_{resolution}p.mp4"
            compressed_file.replace(target_file)
            return target_file
    except Exception as exc:
        print(f"[Lỗi nén video] {exc}", file=sys.stderr)
    return None


def get_output_path(source_path: Path, output_dir: Path | None = None, suffix: str = "_VN") -> Path:
    """Tạo đường dẫn file kết quả theo định dạng yêu cầu: <tên-video>_VN.mp4.
    Mặc định xuất cùng thư mục với video nguồn nếu output_dir không được chỉ định."""
    target_dir = output_dir if output_dir is not None else source_path.parent
    return target_dir / f"{source_path.stem}{suffix}.mp4"


def is_candidate_file(path: Path, suffix: str = "_VN") -> bool:
    """Kiểm tra xem file có phải là video nguồn hợp lệ cần xử lý không:
    - Bỏ qua file ẩn (bắt đầu bằng dấu chấm)
    - Bỏ qua file đang tải dở dang (.crdownload, .part...)
    - Bỏ qua các file kết quả đã có hậu tố (_VN.mp4, .vi.mp4) để tránh vòng lặp vô tận
    - Phải có đuôi mở rộng video được hỗ trợ."""
    if not path.is_file():
        return False

    name = path.name
    # Bỏ qua file ẩn hoặc file hệ thống (vd .DS_Store, ._video.mp4)
    if name.startswith("."):
        return False

    # Bỏ qua các file nằm trong thư mục lưu trữ Originals
    if "Originals" in path.parts:
        return False

    suffix_lower = path.suffix.lower()
    if suffix_lower in TEMP_EXTENSIONS:
        return False
    if suffix_lower not in SUPPORTED_EXTENSIONS:
        return False

    stem_lower = path.stem.lower()
    norm_suffix = suffix.lower().lstrip("_")
    # Kiểm tra tránh lồng tiếng đè lên video đã là bản dịch hoặc video đã nén
    if (
        stem_lower.endswith(f"_{norm_suffix}")
        or stem_lower.endswith(".vi")
        or stem_lower.endswith("_compressed")
        or stem_lower.endswith("_720p")
        or stem_lower.endswith("_1080p")
    ):
        return False

    return True


def is_file_stable(path: Path, wait_seconds: float = 2.0) -> bool:
    """Đảm bảo file đã được chép/tải xong hoàn toàn trước khi bắt đầu xử lý.
    Nếu kích thước file vẫn đang tăng hoặc file chưa mở đọc được thì coi như chưa sẵn sàng."""
    try:
        size1 = path.stat().st_size
        if size1 == 0:
            return False
        time.sleep(wait_seconds)
        size2 = path.stat().st_size
        if size1 != size2:
            return False

        # Thử mở file đọc 1 byte để chắc chắn không bị tiến trình khác khoá ghi
        with open(path, "rb") as f:
            f.read(1)
        return True
    except (OSError, PermissionError):
        return False


def load_history(history_file: Path) -> set[str]:
    """Đọc lịch sử các file đã xử lý thành công để không chạy lại."""
    if not history_file.is_file():
        return set()
    try:
        data = json.loads(history_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(data)
    except Exception:
        pass
    return set()


def save_history(history_file: Path, history: set[str]) -> None:
    """Lưu lịch sử các file đã xử lý."""
    try:
        history_file.write_text(json.dumps(sorted(history), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[Cảnh báo] Không thể lưu file lịch sử: {exc}", file=sys.stderr)


def process_video(
    source_path: Path,
    output_path: Path,
    voice: str,
    tts_engine: str | None = None,
    multi_speaker: bool = False,
) -> tuple[bool, str]:
    """Gọi công cụ run_video_dub_job.py qua tiến trình con độc lập.
    Trả về (thành_công, lý_do_lỗi_nếu_thất_bại)."""
    if not source_path.is_file():
        return False, f"File nguồn không tồn tại: {source_path}"

    if source_path.stat().st_size < 1000:
        return False, f"File nguồn quá nhỏ ({source_path.stat().st_size} bytes), có thể bị hỏng."

    run_script = ROOT / "tools" / "run_video_dub_job.py"
    cmd = [
        sys.executable,
        str(run_script),
        "--source",
        str(source_path),
        "--output",
        str(output_path),
        "--voice",
        voice,
    ]
    if tts_engine:
        cmd.extend(["--tts-engine", tts_engine])
    if multi_speaker:
        cmd.append("--multi-speaker")

    print(f"\n=======================================================")
    print(f"🎬 Bắt đầu lồng tiếng: {source_path.name}")
    print(f"   Đích xuất:         {output_path.name}")
    print(f"   Giọng đọc:         {voice}")
    print(f"=======================================================\n", flush=True)

    env = os.environ.copy()
    env["VIDEO_DUB_SKIP_TELEGRAM_DELIVERY"] = "1"
    result = subprocess.run(cmd, cwd=str(ROOT), env=env)
    success = (
        (result.returncode == 0 or (result.returncode == -6 and output_path.is_file()))
        and output_path.is_file()
        and output_path.stat().st_size > 1000
    )
    if success:
        print(f"\n✅ Hoàn thành xuất sắc: {output_path}")
        return True, ""

    # Trích xuất lý do lỗi chi tiết từ cơ sở dữ liệu jobs nếu có
    error_detail = ""
    try:
        from backend.app.db import connect
        with connect() as conn:
            row = conn.execute(
                "SELECT error, status, stage FROM jobs WHERE name = ? ORDER BY created_at DESC LIMIT 1",
                (source_path.name,)
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
        if result.returncode != 0:
            error_detail = f"Tiến trình kết thúc với mã lỗi {result.returncode}."
        elif not output_path.is_file() or output_path.stat().st_size <= 1000:
            error_detail = "Không tạo được file video đầu ra (file rỗng hoặc bị thiếu)."
        else:
            error_detail = "Video không có lời thoại tiếng Anh hoặc gặp sự cố xử lý."

    print(f"\n❌ Lỗi khi xử lý video: {source_path.name} ({error_detail})", file=sys.stderr)
    return False, error_detail


def scan_and_process(
    watch_dir: Path,
    output_dir: Path | None = None,
    voice: str = "Minh Quân",
    suffix: str = "_VN",
    history_file: Path | None = None,
    tts_engine: str | None = None,
    multi_speaker: bool = False,
    move_done_dir: Path | None = None,
    notify_telegram: bool = True,
    compress_720: bool = True,
) -> int:
    """Quét thư mục và xử lý các video mới."""
    processed_count = 0
    history = load_history(history_file) if history_file else set()
    out_dir = output_dir or Path(os.getenv("AUTODUB_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)))
    done_dir = move_done_dir or Path(os.getenv("AUTODUB_MOVE_DONE", str(DEFAULT_ORIGINALS_DIR)))

    out_dir.mkdir(parents=True, exist_ok=True)
    if done_dir:
        done_dir.mkdir(parents=True, exist_ok=True)

    for item in sorted(watch_dir.iterdir()):
        if not is_candidate_file(item, suffix=suffix):
            continue

        file_key = f"{item.name}:{item.stat().st_size}"
        if file_key in history:
            continue

        # Kiểm tra xem file kết quả cuối cùng đã có trong out_dir hoặc ngay trong watch_dir chưa
        final_target = out_dir / f"{item.stem}{suffix}.mp4"
        local_target = watch_dir / f"{item.stem}{suffix}.mp4"
        if final_target.is_file() or local_target.is_file():
            # Đã có file kết quả tương ứng -> ghi nhận và chuyển file gốc sang Originals/
            history.add(file_key)
            if history_file:
                save_history(history_file, history)
            if done_dir and item.is_file():
                dest = done_dir / item.name
                if item.resolve() != dest.resolve():
                    try:
                        shutil.move(str(item), str(dest))
                        print(f"  📦 Đã chuyển file gốc vào Originals: {dest.name}")
                    except Exception:
                        pass
            continue

        # Kiểm tra khoá để tránh trùng lặp nếu Telegram Bot hoặc tiến trình khác đang xử lý
        locked, lock_info = is_file_locked(item)
        if locked:
            owner = lock_info.get("owner", "tiến trình khác")
            pid = lock_info.get("pid", "?")
            print(f"  ⏳ [Đang xử lý] File {item.name} đang được xử lý bởi {owner} (PID {pid}), bỏ qua.")
            continue

        print(f"[Phát hiện file mới] {item.name} - đang kiểm tra độ ổn định...")
        if not is_file_stable(item):
            print(f"  ⏳ File chưa chép xong hoặc đang được ghi, sẽ kiểm tra lại ở lượt sau.")
            continue

        with file_lock(item, owner="watch_folder") as acquired:
            if not acquired:
                print(f"  ⏳ Không thể chiếm khoá cho file {item.name} (tiến trình khác vừa chiếm), bỏ qua.")
                continue

            start_time = time.time()
            # Xuất bản thô 1080p tạm thời vào watch_dir
            raw_output = watch_dir / f"{item.stem}_raw{suffix}.mp4"
            proc_res = process_video(
                source_path=item,
                output_path=raw_output,
                voice=voice,
                tts_engine=tts_engine,
                multi_speaker=multi_speaker,
            )
            if isinstance(proc_res, tuple):
                success, error_reason = proc_res
            else:
                success = bool(proc_res)
                error_reason = "Video không có lời thoại tiếng Anh hoặc gặp sự cố xử lý." if not success else ""

            elapsed_sec = int(time.time() - start_time)
            elapsed_str = f"{elapsed_sec // 60}m {elapsed_sec % 60}s" if elapsed_sec >= 60 else f"{elapsed_sec}s"

            if success:
                processed_count += 1
                history.add(file_key)
                if history_file:
                    save_history(history_file, history)

                comp_info = ""
                # Sinh tên file thành phẩm bằng keywords ngắn gọn
                try:
                    from tools.keyword_renamer import get_keyword_output_path
                    final_video = get_keyword_output_path(item.stem, out_dir, suffix=suffix)
                except Exception:
                    final_video = final_target

                if compress_720 and raw_output.is_file():
                    c_path = compress_video(raw_output, resolution=720)
                    if c_path and c_path.is_file():
                        orig_mb = raw_output.stat().st_size / (1024 * 1024) if raw_output.is_file() else 0.0
                        new_mb = c_path.stat().st_size / (1024 * 1024)
                        saved_pct = (1 - new_mb / max(0.001, orig_mb)) * 100 if orig_mb > 0 else 0.0
                        comp_info = f"\n📦 <b>Nén 720p:</b> {orig_mb:.1f} MB ➔ {new_mb:.1f} MB (giảm {saved_pct:.0f}%)"
                        # Xoá file thô 1080p để tiết kiệm dung lượng đĩa
                        try:
                            raw_output.unlink()
                        except Exception:
                            pass
                        # Di chuyển bản nén ra thư mục xuất ~/Movies/<tên>_VN.mp4
                        if final_video.exists() and final_video.resolve() != c_path.resolve():
                            final_video.unlink()
                        shutil.move(str(c_path), str(final_video))
                    elif raw_output.is_file():
                        if final_video.exists() and final_video.resolve() != raw_output.resolve():
                            final_video.unlink()
                        shutil.move(str(raw_output), str(final_video))
                elif raw_output.is_file():
                    if final_video.exists() and final_video.resolve() != raw_output.resolve():
                        final_video.unlink()
                    shutil.move(str(raw_output), str(final_video))

                # Hashtag do run_video_dub_job.py ghi sidecar cạnh raw_output
                tags_file = raw_output.with_suffix(".tags.txt")
                tags = tags_file.read_text(encoding="utf-8").strip() if tags_file.is_file() else ""
                tags_file.unlink(missing_ok=True)
                if notify_telegram:
                    import html as _html

                    title = final_video.stem.removesuffix("_VN")
                    msg = f"✅ {_html.escape(title)}" + (f"\n{tags}" if tags else "")
                    send_telegram_video(final_video, caption=msg)

                # Chuyển file gốc tiếng Anh sang thư mục Originals/
                if done_dir is not None and item.is_file():
                    dest = done_dir / item.name
                    try:
                        if dest.exists() and dest.resolve() != item.resolve():
                            dest.unlink()
                        shutil.move(str(item), str(dest))
                        print(f"  📦 Đã chuyển file gốc vào Originals: {dest}")
                    except Exception as exc:
                        print(f"  [Cảnh báo] Không thể di chuyển file nguồn: {exc}", file=sys.stderr)
            else:
                # Ghi nhận vào lịch sử để không lặp lại vô tận (vd: video không có lời thoại)
                history.add(file_key)
                if history_file:
                    save_history(history_file, history)
                print(f"  ⚠️ Đã đánh dấu bỏ qua file lỗi/không có thoại: {item.name}", file=sys.stderr)

                if notify_telegram:
                    file_mb = item.stat().st_size / (1024 * 1024) if item.is_file() else 0.0
                    fail_msg = (
                        f"⚠️ <b>Lồng tiếng thất bại hoặc bỏ qua</b>\n\n"
                        f"🎬 <b>Tên video:</b> <i>{item.stem}</i>\n"
                        f"📁 <b>File nguồn:</b> <code>{item.name}</code> ({file_mb:.1f} MB)\n"
                        f"📍 <b>Vị trí:</b> <code>{item}</code>\n"
                        f"❌ <b>Chi tiết lý do:</b> {error_reason}\n"
                        f"⏱️ <b>Thời gian:</b> {elapsed_str}\n\n"
                        f"💡 <i>Hệ thống đã đánh dấu vào lịch sử để không gửi cảnh báo lặp lại.</i>"
                    )
                    send_telegram_notification(fail_msg)

    return processed_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tự động theo dõi thư mục và lồng tiếng video ra dạng <tên>_VN.mp4."
    )
    parser.add_argument(
        "--watch-dir",
        default=os.getenv("AUTODUB_WATCH_DIR", str(DEFAULT_WATCH_DIR)),
        help=f"Thư mục cần theo dõi (mặc định: {DEFAULT_WATCH_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("AUTODUB_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)),
        help=f"Thư mục lưu video kết quả (mặc định: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--voice",
        default=DEFAULT_VOICE,
        help=f"Tên giọng đọc (mặc định: {DEFAULT_VOICE})",
    )
    parser.add_argument(
        "--tts-engine",
        choices=["vieneu"],
        default=os.getenv("VIDEO_DUB_JOB_TTS_ENGINE", "vieneu"),
        help="Engine giọng đọc. Chỉ còn 'vieneu' (Vbee đã gỡ).",
    )
    parser.add_argument(
        "--multi-speaker",
        action="store_true",
        default=os.getenv("VIDEO_DUB_MULTI_SPEAKER", "false").lower() in {"1", "true", "yes"},
        help="Bật tự nhận diện nhiều người nói (nam/nữ)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.getenv("AUTODUB_POLL_INTERVAL", "3.0")),
        help="Khoảng thời gian nghỉ giữa các lần quét (giây, mặc định: 3.0)",
    )
    parser.add_argument(
        "--move-done",
        default=os.getenv("AUTODUB_MOVE_DONE", str(DEFAULT_ORIGINALS_DIR)),
        help=f"Thư mục chuyển video gốc vào sau khi xử lý xong (mặc định: {DEFAULT_ORIGINALS_DIR})",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Chỉ quét 1 lần rồi thoát (dành cho chế độ batch / cron)",
    )
    parser.add_argument(
        "--no-compress",
        action="store_true",
        default=os.getenv("AUTODUB_NO_COMPRESS", "false").lower() in {"1", "true", "yes"},
        help="Tắt nén video xuống 720p sau khi lồng tiếng",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Tắt gửi thông báo qua Telegram",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    watch_dir = Path(args.watch_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else None
    move_done_dir = Path(args.move_done).expanduser().resolve() if args.move_done else None

    # Tự động tạo thư mục theo dõi nếu chưa có
    watch_dir.mkdir(parents=True, exist_ok=True)
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    history_file = watch_dir / ".autodub_history.json"

    print("=" * 60)
    print("🚀 DỊCH VỤ TỰ ĐỘNG LỒNG TIẾNG VIDEO (WATCH FOLDER)")
    print(f"📁 Thư mục theo dõi: {watch_dir}")
    print(f"🎯 Tên file kết quả: <tên-video>_VN.mp4")
    print(f"🗣️  Giọng đọc:        {args.voice} ({args.tts_engine})")
    if not args.no_compress:
        print("📦 Nén kết quả:     720p (qua video-compress)")
    bot_token = os.getenv("TELEGRAM_DUB_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not args.no_telegram and bot_token and os.getenv("TELEGRAM_CHAT_ID"):
        topic_info = f" (Topic ID: {os.getenv('TELEGRAM_TOPIC_ID')})" if os.getenv("TELEGRAM_TOPIC_ID") else ""
        print(f"📬 Thông báo:       Duby Bot{topic_info}")
    print("=" * 60)
    print("Chỉ cần kéo thả file video (.mp4, .mkv, .mov...) vào thư mục trên.")
    print("Bấm Ctrl+C để dừng.\n", flush=True)

    if args.once:
        count = scan_and_process(
            watch_dir=watch_dir,
            output_dir=output_dir,
            voice=args.voice,
            history_file=history_file,
            tts_engine=args.tts_engine,
            multi_speaker=args.multi_speaker,
            move_done_dir=move_done_dir,
            notify_telegram=not args.no_telegram,
            compress_720=not args.no_compress,
        )
        print(f"Đã xử lý xong {count} video.")
        return

    try:
        while True:
            scan_and_process(
                watch_dir=watch_dir,
                output_dir=output_dir,
                voice=args.voice,
                history_file=history_file,
                tts_engine=args.tts_engine,
                multi_speaker=args.multi_speaker,
                move_done_dir=move_done_dir,
                notify_telegram=not args.no_telegram,
                compress_720=not args.no_compress,
            )
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\n👋 Đã dừng dịch vụ tự động theo dõi.")


if __name__ == "__main__":
    main()
