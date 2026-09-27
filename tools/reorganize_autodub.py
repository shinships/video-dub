"""tools/reorganize_autodub.py — Sắp xếp, phân loại và dọn dẹp các video trong ~/Movies/AutoDub:
1. Chuyển video thành phẩm tiếng Việt ra ~/Movies/<tên>_VN.mp4 (ưu tiên bản nén 720p)
2. Xoá các bản thô 1080p trùng lặp để giải phóng ổ cứng
3. Chuyển các file gốc tiếng Anh đã hoàn thành vào ~/Movies/AutoDub/Originals/
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

WATCH_DIR = Path.home() / "Movies" / "AutoDub"
OUTPUT_DIR = Path.home() / "Movies"
ORIGINALS_DIR = WATCH_DIR / "Originals"


def reorganize(dry_run: bool = False) -> None:
    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("🧹 BẮT ĐẦU SẮP XẾP VÀ DỌN DẸP THƯ MỤC AUTODUB")
    print(f"📁 Thư mục nguồn:       {WATCH_DIR}")
    print(f"🎬 Thư mục thành phẩm:  {OUTPUT_DIR}")
    print(f"📦 Thư mục lưu gốc:     {ORIGINALS_DIR}")
    print("=" * 60)

    handled_stems: set[str] = set()

    # 1. Tìm tất cả file 720p nén
    compressed_files = list(WATCH_DIR.glob("*_VN_720p.mp4"))
    for comp in compressed_files:
        base_stem = comp.stem.replace("_VN_720p", "")
        handled_stems.add(base_stem)
        final_dest = OUTPUT_DIR / f"{base_stem}_VN.mp4"
        raw_file = WATCH_DIR / f"{base_stem}_VN.mp4"
        orig_file = WATCH_DIR / f"{base_stem}.mp4"

        # Nếu tên file nén bị cắt ngắn, tìm file thô tương ứng
        if not raw_file.is_file():
            candidates = list(WATCH_DIR.glob(f"*{base_stem}*_VN.mp4"))
            if candidates:
                raw_file = candidates[0]
                base_stem = raw_file.stem.replace("_VN", "")
                handled_stems.add(base_stem)
                final_dest = OUTPUT_DIR / f"{base_stem}_VN.mp4"
                orig_file = WATCH_DIR / f"{base_stem}.mp4"

        if not orig_file.is_file():
            candidates = [f for f in WATCH_DIR.glob(f"*{base_stem}*.mp4") if "_vn" not in f.stem.lower()]
            if candidates:
                orig_file = candidates[0]

        # a. Di chuyển bản nén ra ~/Movies/<tên>_VN.mp4
        if dry_run:
            print(f"[Dry-run] Sẽ chuyển: {comp.name} ➔ {final_dest.name}")
        else:
            if final_dest.exists() and final_dest.resolve() != comp.resolve():
                final_dest.unlink()
            shutil.move(str(comp), str(final_dest))
            print(f"✅ Đã xuất thành phẩm: {final_dest.name}")

        # b. Xoá bản thô 1080p nếu còn trong AutoDub
        if raw_file.is_file():
            if dry_run:
                print(f"[Dry-run] Sẽ xoá bản thô: {raw_file.name} ({raw_file.stat().st_size / 1024 / 1024:.1f} MB)")
            else:
                raw_size = raw_file.stat().st_size / (1024 * 1024)
                raw_file.unlink()
                print(f"🗑️  Đã xoá bản thô: {raw_file.name} ({raw_size:.1f} MB)")

        # c. Chuyển file gốc tiếng Anh vào Originals/
        if orig_file.is_file():
            orig_dest = ORIGINALS_DIR / orig_file.name
            if dry_run:
                print(f"[Dry-run] Sẽ lưu gốc: {orig_file.name} ➔ Originals/")
            else:
                if orig_dest.exists() and orig_dest.resolve() != orig_file.resolve():
                    orig_dest.unlink()
                shutil.move(str(orig_file), str(orig_dest))
                print(f"📦 Đã lưu gốc tiếng Anh vào Originals: {orig_file.name}")

    # 2. Xử lý các video chỉ có bản _VN.mp4 mà chưa có _720p (chưa được xử lý ở bước 1)
    uncompressed_vn = list(WATCH_DIR.glob("*_VN.mp4"))
    for vn_file in uncompressed_vn:
        base_stem = vn_file.stem.replace("_VN", "")
        if base_stem in handled_stems:
            continue
        handled_stems.add(base_stem)
        final_dest = OUTPUT_DIR / f"{base_stem}_VN.mp4"
        orig_file = WATCH_DIR / f"{base_stem}.mp4"

        if dry_run:
            print(f"[Dry-run] Sẽ chuyển: {vn_file.name} ➔ {final_dest.name}")
        else:
            if final_dest.exists() and final_dest.resolve() != vn_file.resolve():
                final_dest.unlink()
            shutil.move(str(vn_file), str(final_dest))
            print(f"✅ Đã xuất thành phẩm: {final_dest.name}")

        if orig_file.is_file():
            orig_dest = ORIGINALS_DIR / orig_file.name
            if dry_run:
                print(f"[Dry-run] Sẽ lưu gốc: {orig_file.name} ➔ Originals/")
            else:
                if orig_dest.exists() and orig_dest.resolve() != orig_file.resolve():
                    orig_dest.unlink()
                shutil.move(str(orig_file), str(orig_dest))
                print(f"📦 Đã lưu gốc tiếng Anh vào Originals: {orig_file.name}")

    print("\n🎉 Hoàn tất sắp xếp thư mục!")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    reorganize(dry_run=dry)
