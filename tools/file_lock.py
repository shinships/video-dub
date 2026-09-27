"""tools/file_lock.py — Cơ chế khoá file nguyên tử (Atomic File Lock) dùng fcntl.flock trên POSIX/macOS.

Mục đích:
- Đảm bảo chỉ 1 tiến trình xử lý 1 video tại một thời điểm (tránh xung đột giữa
  Telegram Duby Bot, Watch Folder chạy nền hoặc double-click .command).
- Kernel OS tự giải phóng khoá nếu tiến trình bị tắt, crash hoặc nhận SIGKILL
  -> Không bao giờ lo xảy ra deadlock / khoá rác vĩnh viễn.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, IO, Generator


def get_lock_file(target_file: Path, locks_dir: Path | None = None) -> Path:
    """Xác định đường dẫn file lock trong thư mục .locks/."""
    ldir = locks_dir or (target_file.parent / ".locks")
    ldir.mkdir(parents=True, exist_ok=True)
    return ldir / f"{target_file.name}.lock"


def acquire_file_lock(
    target_file: Path,
    owner: str = "autodub",
    details: dict[str, Any] | None = None,
    locks_dir: Path | None = None,
) -> IO[Any] | None:
    """Cố gắng chiếm khoá độc quyền không chặn (non-blocking).
    Nếu thành công: ghi metadata (PID, owner, thời gian) và trả về file pointer đang mở.
    Nếu file đã bị tiến trình khác khoá: trả về None.
    """
    lock_path = get_lock_file(target_file, locks_dir=locks_dir)
    try:
        f = open(lock_path, "a+", encoding="utf-8")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Chiếm khoá thành công -> ghi thông tin chẩn đoán
        f.seek(0)
        f.truncate()
        payload = {
            "pid": os.getpid(),
            "owner": owner,
            "file": target_file.name,
            "acquired_at": time.time(),
            "details": details or {},
        }
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        return f
    except (BlockingIOError, OSError):
        # File đang bị tiến trình khác chiếm giữ khoá
        try:
            f.close()
        except Exception:
            pass
        return None


def release_file_lock(lock_fp: IO[Any] | None) -> None:
    """Giải phóng khoá và đóng file descriptor."""
    if lock_fp is None:
        return
    try:
        if not lock_fp.closed:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
            lock_fp.close()
    except Exception as exc:
        print(f"[FileLock] Lỗi khi giải phóng khoá: {exc}", file=sys.stderr)


def is_file_locked(
    target_file: Path,
    locks_dir: Path | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Kiểm tra xem target_file có đang bị khoá bởi tiến trình nào không.
    Trả về (True, metadata) nếu đang bị khoá, hoặc (False, {}) nếu không bị khoá.
    """
    lock_path = get_lock_file(target_file, locks_dir=locks_dir)
    if not lock_path.is_file():
        return False, {}

    try:
        with open(lock_path, "r+", encoding="utf-8") as f:
            try:
                # Thử chiếm khoá thử nghiệm
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Chiếm được -> chứng tỏ KHÔNG có tiến trình nào đang khoá
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                return False, {}
            except (BlockingIOError, OSError):
                # Không chiếm được -> ĐANG có tiến trình khoá tích cực
                info: dict[str, Any] = {}
                try:
                    f.seek(0)
                    content = f.read().strip()
                    if content:
                        info = json.loads(content)
                except Exception:
                    pass
                return True, info
    except Exception:
        return False, {}


@contextlib.contextmanager
def file_lock(
    target_file: Path,
    owner: str = "autodub",
    details: dict[str, Any] | None = None,
    locks_dir: Path | None = None,
) -> Generator[bool, None, None]:
    """Context manager hỗ trợ 'with file_lock(...) as acquired:'
    Nếu acquired=True: đã chiếm được khoá thành công.
    Nếu acquired=False: file đang bị chiếm giữ, cần bỏ qua tác vụ.
    """
    fp = acquire_file_lock(target_file, owner=owner, details=details, locks_dir=locks_dir)
    if fp is None:
        yield False
        return
    try:
        yield True
    finally:
        release_file_lock(fp)


def list_active_locks(locks_dir: Path) -> list[dict[str, Any]]:
    """Liệt kê tất cả các file đang bị khoá tích cực trong thư mục .locks/."""
    if not locks_dir.is_dir():
        return []

    active: list[dict[str, Any]] = []
    for lock_file in sorted(locks_dir.glob("*.lock")):
        original_name = lock_file.name[:-5]  # bỏ đuôi .lock
        locked, info = is_file_locked(Path(original_name), locks_dir=locks_dir)
        if locked:
            info.setdefault("file", original_name)
            active.append(info)
    return active
