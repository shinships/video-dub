"""Upload completed dubbed videos to one configured Telegram topic."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from tools.telegram_video import probe_video
except ModuleNotFoundError:
    from telegram_video import probe_video


def delivery_config() -> tuple[str, str, int] | None:
    token = os.getenv("TELEGRAM_DUB_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("VIDEO_DUB_TELEGRAM_CHAT_ID")
    topic_raw = os.getenv("VIDEO_DUB_TELEGRAM_TOPIC_ID")
    if not (token and chat_id and topic_raw):
        return None
    return token, chat_id, int(topic_raw)


def send_completed_video(path: Path, caption: str = "") -> dict[str, Any]:
    """Upload an MP4 to the configured topic and return Telegram's message metadata."""
    import httpx

    cfg = delivery_config()
    if cfg is None:
        return {"skipped": True, "reason": "telegram delivery is not configured"}
    token, chat_id, topic_id = cfg
    if not path.is_file():
        raise FileNotFoundError(path)

    metadata = probe_video(path)
    data = {
        "chat_id": chat_id,
        "message_thread_id": str(topic_id),
        "supports_streaming": "true",
        "width": str(metadata["width"]),
        "height": str(metadata["height"]),
        "duration": str(metadata["duration"]),
    }
    if caption:
        data["caption"] = caption[:1024]
    with path.open("rb") as video:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendVideo",
            data=data,
            files={"video": (path.name, video, "video/mp4")},
            timeout=600,
        )
    if response.status_code != 200:
        raise RuntimeError(f"Telegram sendVideo HTTP {response.status_code}: {response.text[:500]}")
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram sendVideo failed: {payload}")
    result = payload.get("result") or {}
    return {"message_id": result.get("message_id"), "chat_id": chat_id, "topic_id": topic_id}
