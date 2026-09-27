"""Sinh hashtag chủ đề cho caption Telegram: 2 tag tự động (LLM) + 1 tag tuỳ chỉnh."""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

MAX_TRANSCRIPT_CHARS = 4000


def normalize_tag(raw: str) -> str:
    """'Đầu tư BĐS' -> '#DauTuBDS'. Bỏ dấu, bỏ ký tự lạ để Telegram bấm được."""
    text = raw.strip().lstrip("#").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    parts = re.split(r"[^A-Za-z0-9]+", text)
    tag = "".join(p[:1].upper() + p[1:] for p in parts if p)
    return f"#{tag}" if tag else ""


def _llm_tags(title: str, transcript: str) -> list[str]:
    import httpx

    try:
        from app.config import settings
    except ModuleNotFoundError:
        from backend.app.config import settings

    keys = settings.openai_compat_api_keys
    base = settings.openai_compat_base_url
    model = settings.openai_compat_model
    if not (keys and base and model):
        return []
    url = base if base.endswith("/chat/completions") else base.rstrip("/") + "/chat/completions"
    prompt = (
        "Đọc tiêu đề và nội dung video, trả về JSON {\"tags\": [a, b]} gồm ĐÚNG 2 hashtag chủ đề "
        "tiếng Anh, 1-2 từ, dạng PascalCase, không dấu #. Tag 1 = lĩnh vực rộng "
        "(vd AI, Business, Investing, Design, Productivity, Marketing). Tag 2 = chủ đề cụ thể hơn "
        "(vd PromptEngineering, Animation, InteriorDesign, Leverage). Chỉ trả JSON.\n\n"
        f"Tiêu đề: {title}\n\nNội dung:\n{transcript[:MAX_TRANSCRIPT_CHARS]}"
    )
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {keys[0]}"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", content, re.S)
    tags = json.loads(match.group(0)).get("tags", []) if match else []
    return [t for t in (normalize_tag(str(x)) for x in tags[:2]) if t]


def build_hashtags(title: str, segments: list[dict[str, Any]] | None, custom: str | None = None) -> str:
    """Trả về chuỗi '#AI #PromptEngineering #custom'. Lỗi LLM thì vẫn giữ tag tuỳ chỉnh."""
    transcript = " ".join(
        str(s.get("translated_text") or s.get("source_text") or "") for s in (segments or [])
    )
    tags: list[str] = []
    try:
        tags = _llm_tags(title, transcript)
    except Exception as exc:  # hashtag không được làm hỏng delivery
        print(f"[hashtags] {exc}", flush=True)
    if custom:
        extra = normalize_tag(custom)
        if extra and extra.lower() not in {t.lower() for t in tags}:
            tags.append(extra)
    return " ".join(tags)
