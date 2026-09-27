"""tools/keyword_renamer.py — Tự động trích xuất keywords ngắn gọn, súc tích cho tên file video xuất.
Sử dụng Gemini AI khi sẵn sàng (kết hợp với Pipeline) và có bộ lọc heuristic quy tắc dự phòng khi offline.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STOP_WORDS = {
    "a", "an", "the", "this", "that", "these", "those", "is", "are", "was", "were",
    "be", "been", "being", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "about", "into", "through", "during", "before", "after", "above", "below", "so",
    "and", "or", "but", "if", "because", "as", "until", "while", "i", "you", "he",
    "she", "it", "we", "they", "me", "him", "her", "us", "them", "my", "your", "his",
    "its", "our", "their", "what", "which", "who", "whom", "whose", "where", "when",
    "why", "how", "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "than", "too", "very",
    "can", "will", "just", "dont", "should", "now", "re", "ve", "ll", "d", "m"
}

_GENAI_CLIENT = None


def _get_client():
    global _GENAI_CLIENT
    if _GENAI_CLIENT is not None:
        return _GENAI_CLIENT
    try:
        from backend.app.pipeline import Pipeline
        p = Pipeline(hook=lambda *args: None)
        _GENAI_CLIENT = p._genai_client()
        return _GENAI_CLIENT
    except Exception:
        return None


def rule_based_keywords(title: str, max_words: int = 4) -> str:
    """Heuristic dự phòng khi không có mạng/LLM: loại bỏ stopwords và giữ 2-4 từ chính."""
    # Bỏ các đuôi _VN hoặc tiền tố thừa
    t = re.sub(r"_VN(\.mp4)?$", "", title, flags=re.IGNORECASE).strip()
    t = re.sub(r"[^\w\s-]", " ", t)
    words = [w for w in re.split(r"[\s_-]+", t) if w]

    filtered = [w.capitalize() for w in words if w.lower() not in STOP_WORDS]
    if len(filtered) < 2:
        filtered = [w.capitalize() for w in words]

    selected = filtered[:max_words]
    return " ".join(selected) if selected else (title.strip() or "Video")


def _normalize_casing(text: str) -> str:
    SPECIAL_CASING = {
        "chatgpt": "ChatGPT",
        "ai": "AI",
        "wsj": "WSJ",
        "ev": "EV",
        "hd": "HD",
        "api": "API",
        "stt": "STT",
        "tts": "TTS",
        "it": "IT",
    }
    words = text.split()
    fixed = []
    for w in words:
        lower = w.lower()
        if lower in SPECIAL_CASING:
            fixed.append(SPECIAL_CASING[lower])
        else:
            fixed.append(w.capitalize())
    return " ".join(fixed)


def generate_concise_keywords(title: str, context: str = "", max_words: int = 4) -> str:
    """Tạo 2 đến 4 keywords tiếng Anh ngắn gọn, đại diện cho video để đặt tên file:
    1. Ưu tiên gọi Gemini AI để trích xuất từ khoá chuẩn xác, ngữ nghĩa cao.
    2. Tự động lùi về quy tắc heuristic nếu Gemini không phản hồi hoặc offline.
    """
    clean_input = re.sub(r"_VN(\.mp4)?$", "", title, flags=re.IGNORECASE).strip()
    if not clean_input:
        return "Video"

    # Thử gọi Gemini AI
    client = _get_client()
    if client is not None:
        try:
            from backend.app.config import settings
            prompt = (
                "You are a video file naming assistant. Generate 2 to 4 concise, high-impact English keywords "
                "that best represent this video for a short output filename.\n"
                "Rules:\n"
                f"- Length: Exactly 2 to {max_words} words.\n"
                "- Format: Title Case (e.g. 'Google Offline Translator', 'Bill Gates AI Questions', 'North Korean Smartphone').\n"
                "- Clean: Letters, numbers, spaces only. No punctuation, no quotes, no file extensions, no filler words.\n"
                "- Do not use acronyms for countries or standard terms (e.g. use 'North Korean' not 'NK').\n"
                "- Prioritize key subject, person, technology, brand, or main action.\n"
                "- If the title is meaningless or an opaque code (e.g. 'predesj', 'predesk'), extract the main topic from the context snippet.\n"
                "- Return ONLY the keywords, nothing else.\n\n"
                f"Title: {clean_input}\n"
                f"Context: {context[:600]}\n"
                "Keywords:"
            )
            resp = client.models.generate_content(
                model=settings.active_translate_model,
                contents=prompt,
            )
            raw = (resp.text or "").strip()
            # Làm sạch kết quả từ LLM
            cleaned = re.sub(r"[^\w\s-]", "", raw)
            words = [w for w in cleaned.split() if w]
            if 1 <= len(words) <= max_words + 1:
                return _normalize_casing(" ".join(words[:max_words]))
        except Exception:
            pass

    # Dự phòng bằng Heuristic
    return _normalize_casing(rule_based_keywords(clean_input, max_words=max_words))


def get_keyword_output_path(
    source_stem: str,
    output_dir: Path,
    context: str = "",
    suffix: str = "_VN",
    max_words: int = 4,
) -> Path:
    """Trả về đường dẫn file kết quả với keywords ngắn gọn: output_dir / <keywords>_VN.mp4."""
    output_dir.mkdir(parents=True, exist_ok=True)
    keywords = generate_concise_keywords(source_stem, context=context, max_words=max_words)
    target = output_dir / f"{keywords}{suffix}.mp4"

    # Tránh trùng lặp nếu đã tồn tại file khác có cùng tên
    if target.exists():
        counter = 2
        while target.exists():
            target = output_dir / f"{keywords} {counter}{suffix}.mp4"
            counter += 1

    return target


def rename_existing_movies(movies_dir: Path, dry_run: bool = False) -> list[tuple[Path, Path]]:
    """Quét thư mục ~/Movies và đổi tên các file *_VN.mp4 theo keywords ngắn gọn."""
    candidates = list(movies_dir.glob("*_VN.mp4"))
    renamed = []

    for vid in candidates:
        stem = vid.stem.replace("_VN", "")
        # Lấy context từ SQLite nếu có
        context = ""
        try:
            from backend.app.db import connect
            with connect() as conn:
                row = conn.execute(
                    "SELECT id FROM jobs WHERE name LIKE ? ORDER BY created_at DESC LIMIT 1",
                    (f"%{stem}%",)
                ).fetchone()
                if not row and len(stem) >= 5:
                    prefix = stem[:5]
                    row = conn.execute(
                        "SELECT id FROM jobs WHERE name LIKE ? ORDER BY created_at DESC LIMIT 1",
                        (f"%{prefix}%",)
                    ).fetchone()
                if row:
                    segs = conn.execute(
                        "SELECT source_text FROM segments WHERE job_id = ? ORDER BY position LIMIT 6",
                        (row["id"],)
                    ).fetchall()
                    context = " ".join(s["source_text"] for s in segs)
        except Exception:
            pass

        keywords = generate_concise_keywords(stem, context=context)
        new_name = f"{keywords}_VN.mp4"
        dest = movies_dir / new_name

        if dest.resolve() == vid.resolve():
            continue

        if dest.exists() and not dry_run:
            counter = 2
            while dest.exists() and dest.resolve() != vid.resolve():
                dest = movies_dir / f"{keywords} {counter}_VN.mp4"
                counter += 1

        renamed.append((vid, dest))
        if dry_run:
            print(f"[Dry-run] Sẽ đổi: {vid.name}\n      ➔ {dest.name}\n")
        else:
            vid.rename(dest)
            print(f"✅ Đã đổi: {vid.name}\n   ➔ {dest.name}\n")

    return renamed


def main() -> None:
    parser = argparse.ArgumentParser(description="Trích xuất keywords ngắn gọn cho tên file video.")
    parser.add_argument("--rename-existing", action="store_true", help="Đổi tên các video hiện có trong ~/Movies")
    parser.add_argument("--dry-run", action="store_true", help="Chạy thử nghiệm không đổi tên thật")
    parser.add_argument("--movies-dir", default=str(Path.home() / "Movies"), help="Thư mục chứa video thành phẩm")
    args = parser.parse_args()

    if args.rename_existing:
        movies_dir = Path(args.movies_dir)
        print(f"🎬 Bắt đầu quét và tối ưu tên video trong: {movies_dir}")
        renamed = rename_existing_movies(movies_dir, dry_run=args.dry_run)
        print(f"Hoàn thành: Đã xử lý {len(renamed)} video.")


if __name__ == "__main__":
    main()
