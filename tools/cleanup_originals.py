"""tools/cleanup_originals.py — Tự động dọn dẹp các video gốc trong thư mục Originals.
Được kích hoạt bởi LaunchAgent com.mktmda.videodub-cleanup vào 23:00 hàng ngày.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCH_DIR = Path.home() / "Movies" / "AutoDub"
DEFAULT_ORIGINALS_DIR = DEFAULT_WATCH_DIR / "Originals"


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv(ROOT / ".env")


def cleanup_originals(
    originals_dir: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Xoá toàn bộ các file video gốc trong thư mục Originals để giải phóng ổ cứng."""
    target_dir = originals_dir or Path(os.getenv("AUTODUB_ORIGINALS_DIR", str(DEFAULT_ORIGINALS_DIR)))
    if not target_dir.is_dir():
        print(f"[Cleanup] Thư mục không tồn tại: {target_dir}")
        return 0

    deleted_count = 0
    total_freed_bytes = 0

    print(f"🧹 Bắt đầu dọn dẹp thư mục: {target_dir} ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    for item in sorted(target_dir.iterdir()):
        if item.is_file() and not item.name.startswith("."):
            try:
                size = item.stat().st_size
                if dry_run:
                    print(f"  [Dry-run] Sẽ xoá: {item.name} ({size / 1024 / 1024:.1f} MB)")
                else:
                    item.unlink()
                    print(f"  🗑️ Đã xoá: {item.name} ({size / 1024 / 1024:.1f} MB)")
                deleted_count += 1
                total_freed_bytes += size
            except Exception as exc:
                print(f"  [Lỗi khi xoá {item.name}] {exc}", file=sys.stderr)

    freed_mb = total_freed_bytes / (1024 * 1024)
    status_prefix = "[Dry-run] " if dry_run else "✅ "
    print(f"{status_prefix}Đã dọn dẹp {deleted_count} file, giải phóng {freed_mb:.1f} MB dung lượng.")

    if not dry_run and deleted_count > 0:
        try:
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            from tools.watch_folder import send_telegram_notification
            msg = (
                f"🧹 <b>Tự động dọn dẹp hàng ngày (23:00)</b>\n\n"
                f"📁 <b>Thư mục:</b> <code>AutoDub/Originals/</code>\n"
                f"🗑️ <b>Đã xoá:</b> {deleted_count} video gốc tiếng Anh\n"
                f"💾 <b>Giải phóng:</b> {freed_mb:.1f} MB dung lượng ổ cứng\n\n"
                f"✨ <i>Thư mục Originals đã sạch sẽ.</i>"
            )
            send_telegram_notification(msg)
        except Exception as exc:
            print(f"[Telegram] Không thể gửi thông báo dọn dẹp: {exc}", file=sys.stderr)

    return deleted_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Dọn dẹp thư mục video gốc Originals.")
    parser.add_argument(
        "--dir",
        default=os.getenv("AUTODUB_ORIGINALS_DIR", str(DEFAULT_ORIGINALS_DIR)),
        help="Thư mục Originals cần dọn dẹp",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chạy thử nghiệm không thực sự xoá file",
    )
    args = parser.parse_args()
    cleanup_originals(Path(args.dir), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
