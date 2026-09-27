#!/usr/bin/env python3
"""Cắt một phần video (theo thời gian hoặc chapter) trước khi lồng tiếng.

Ví dụ:
  clip_video.py video.mp4 --list-chapters
  clip_video.py video.mp4 --range 1:20-5:00
  clip_video.py video.mp4 --range 0:00-2:00 --range 10:00-12:30
  clip_video.py video.mp4 --chapter 2,4-5            # gộp thành 1 file
  clip_video.py video.mp4 --chapter "Intro" --split  # mỗi chapter 1 file
  clip_video.py video.mp4 --chapter 3 --url https://youtu.be/xxx  # lấy chapter từ YouTube

Mặc định xuất vào thư mục AutoDub để pipeline dub tự nhận.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_WATCH_DIR = Path(os.getenv("AUTODUB_WATCH_DIR", str(Path.home() / "Movies" / "AutoDub")))


@dataclass
class Segment:
    start: float
    end: float
    label: str
    title: str = ""


def parse_timestamp(value: str) -> float:
    """'90' | '1:30' | '1:02:03' | '1:30.5' -> giây."""
    value = value.strip()
    if not re.fullmatch(r"\d+(:\d{1,2}){0,2}(\.\d+)?", value):
        raise ValueError(f"Mốc thời gian không hợp lệ: {value!r}")
    total = 0.0
    for part in value.split(":"):
        total = total * 60 + float(part)
    return total


def fmt_ts(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _label_ts(seconds: float) -> str:
    return fmt_ts(seconds).replace(":", "")


def parse_range(value: str) -> Segment:
    if "-" not in value:
        raise ValueError(f"Khoảng thời gian phải dạng START-END, nhận: {value!r}")
    start_s, end_s = value.split("-", 1)
    start = parse_timestamp(start_s)
    end = parse_timestamp(end_s) if end_s.strip() else float("inf")
    if end <= start:
        raise ValueError(f"END phải lớn hơn START: {value!r}")
    label = f"{_label_ts(start)}-{'end' if end == float('inf') else _label_ts(end)}"
    return Segment(start, end, label)


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def chapters_from_file(path: Path) -> list[dict]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    chapters = []
    for ch in json.loads(out).get("chapters", []):
        chapters.append({
            "start_time": float(ch["start_time"]),
            "end_time": float(ch["end_time"]),
            "title": (ch.get("tags") or {}).get("title", ""),
        })
    return chapters


def chapters_from_url(url: str) -> list[dict]:
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "no_warnings": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    return [
        {"start_time": float(c["start_time"]), "end_time": float(c["end_time"]), "title": c.get("title", "")}
        for c in (info.get("chapters") or [])
    ]


def select_chapters(chapters: list[dict], spec: str) -> list[Segment]:
    """spec: '2', '2,4', '3-5', 'Intro' (khớp một phần tên, không phân biệt hoa thường). Số đếm từ 1."""
    if not chapters:
        raise ValueError("Video không có chapter. Dùng --url để lấy chapter từ YouTube, hoặc dùng --range.")
    picked: list[int] = []
    for token in [t.strip() for t in spec.split(",") if t.strip()]:
        if re.fullmatch(r"\d+", token):
            picked.append(int(token))
        elif re.fullmatch(r"\d+-\d+", token):
            a, b = map(int, token.split("-"))
            picked.extend(range(a, b + 1))
        else:
            matches = [i + 1 for i, c in enumerate(chapters) if token.lower() in c["title"].lower()]
            if not matches:
                raise ValueError(f"Không tìm thấy chapter có tên chứa {token!r}")
            picked.extend(matches)
    segments = []
    for idx in dict.fromkeys(picked):  # bỏ trùng, giữ thứ tự
        if not 1 <= idx <= len(chapters):
            raise ValueError(f"Chapter {idx} không tồn tại (video có {len(chapters)} chapter)")
        c = chapters[idx - 1]
        segments.append(Segment(c["start_time"], c["end_time"], f"ch{idx}", c["title"]))
    return segments


def cut_segment(src: Path, seg: Segment, dst: Path) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{seg.start:.3f}", "-i", str(src)]
    if seg.end != float("inf"):
        cmd += ["-t", f"{seg.end - seg.start:.3f}"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
            "-map_chapters", "-1", str(dst)]
    subprocess.run(cmd, check=True)


def concat_files(parts: list[Path], dst: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in parts:
            f.write(f"file '{p.as_posix()}'\n")
        list_file = f.name
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_file,
                        "-c", "copy", str(dst)], check=True)
    finally:
        os.unlink(list_file)


def build_clips(src: Path, segments: list[Segment], out_dir: Path, split: bool) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(src)
    for seg in segments:
        if seg.start >= duration:
            raise ValueError(f"Đoạn {seg.label} bắt đầu sau khi video kết thúc ({fmt_ts(duration)})")
        seg.end = min(seg.end, duration)

    stem = src.stem
    if split:
        outputs = []
        for seg in segments:
            dst = out_dir / f"{stem}_{seg.label}.mp4"
            cut_segment(src, seg, dst)
            outputs.append(dst)
        return outputs

    label = "_".join(s.label for s in segments)
    if len(label) > 60:
        label = f"{segments[0].label}_plus{len(segments) - 1}"
    dst = out_dir / f"{stem}_{label}.mp4"
    if len(segments) == 1:
        cut_segment(src, segments[0], dst)
        return [dst]
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, seg in enumerate(segments):
            part = Path(tmp) / f"part{i:03d}.mp4"
            cut_segment(src, seg, part)
            parts.append(part)
        concat_files(parts, dst)
    return [dst]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Cắt đoạn video theo thời gian hoặc chapter trước khi dub.")
    p.add_argument("source", help="File video nguồn")
    p.add_argument("--url", help="Link YouTube gốc để lấy chapter (khi file không nhúng chapter)")
    p.add_argument("--list-chapters", action="store_true", help="Chỉ liệt kê chapter")
    p.add_argument("--range", action="append", default=[], help="START-END, vd 1:20-5:00 (lặp được; END trống = tới hết)")
    p.add_argument("--chapter", help="Chapter cần lấy: 2 | 2,4 | 3-5 | tên chapter")
    p.add_argument("--split", action="store_true", help="Mỗi đoạn/chapter xuất 1 file riêng (mặc định gộp 1 file)")
    p.add_argument("--out-dir", default=str(DEFAULT_WATCH_DIR), help=f"Thư mục xuất (mặc định {DEFAULT_WATCH_DIR})")
    args = p.parse_args(argv)

    is_url = bool(re.match(r"https?://", args.source))
    src = None if is_url else Path(args.source).expanduser().resolve()
    if is_url and not args.list_chapters:
        print("❌ Nguồn là link YouTube: chỉ dùng được với --list-chapters. Tải video trước khi cắt.", file=sys.stderr)
        return 1
    if src is not None and not src.is_file():
        print(f"❌ Không thấy file: {src}", file=sys.stderr)
        return 1
    if not is_url and not shutil.which("ffmpeg"):
        print("❌ Thiếu ffmpeg", file=sys.stderr)
        return 1

    try:
        need_chapters = args.list_chapters or args.chapter
        chapters = []
        if need_chapters:
            url = args.url or (args.source if is_url else None)
            if not url and src is not None:  # file tải bằng yt-dlp thường có dạng "... [VIDEO_ID].mp4"
                m = re.search(r"\[([A-Za-z0-9_-]{11})\]$", src.stem)
                url = f"https://www.youtube.com/watch?v={m.group(1)}" if m else None
            chapters = (chapters_from_file(src) if src is not None else []) or (chapters_from_url(url) if url else [])

        if args.list_chapters:
            if not chapters:
                print("Video không có chapter (thử thêm --url <link YouTube>).")
                return 0
            for i, c in enumerate(chapters, 1):
                print(f"{i:>2}. {fmt_ts(c['start_time'])}-{fmt_ts(c['end_time'])}  {c['title']}")
            return 0

        segments = [parse_range(r) for r in args.range]
        if args.chapter:
            segments += select_chapters(chapters, args.chapter)
        if not segments:
            p.error("Cần --range hoặc --chapter (hoặc --list-chapters)")

        outputs = build_clips(src, segments, Path(args.out_dir).expanduser(), args.split)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    for seg in segments:
        extra = f"  {seg.title}" if seg.title else ""
        print(f"✂️  {seg.label}: {fmt_ts(seg.start)}-{fmt_ts(seg.end)}{extra}")
    for out in outputs:
        print(f"✅ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
