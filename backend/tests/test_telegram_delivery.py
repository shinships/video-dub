from pathlib import Path
from types import SimpleNamespace

import pytest


def test_delivery_config_requires_all_values(monkeypatch):
    from tools.telegram_delivery import delivery_config

    monkeypatch.delenv("TELEGRAM_DUB_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("VIDEO_DUB_TELEGRAM_CHAT_ID", "-1001")
    monkeypatch.setenv("VIDEO_DUB_TELEGRAM_TOPIC_ID", "3")
    assert delivery_config() is None


def test_send_completed_video_targets_topic(monkeypatch, tmp_path: Path):
    from tools import telegram_delivery

    video = tmp_path / "done.mp4"
    video.write_bytes(b"fake-video")
    monkeypatch.setenv("TELEGRAM_DUB_BOT_TOKEN", "secret")
    monkeypatch.setenv("VIDEO_DUB_TELEGRAM_CHAT_ID", "-1003879100454")
    monkeypatch.setenv("VIDEO_DUB_TELEGRAM_TOPIC_ID", "3")
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"ok": True, "result": {"message_id": 42}},
            text="",
        )

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(
        telegram_delivery,
        "probe_video",
        lambda _path: {"width": 1280, "height": 720, "duration": 79},
    )
    result = telegram_delivery.send_completed_video(video, "done")

    assert captured["data"]["chat_id"] == "-1003879100454"
    assert captured["data"]["message_thread_id"] == "3"
    assert captured["data"]["width"] == "1280"
    assert captured["data"]["height"] == "720"
    assert captured["data"]["duration"] == "79"
    assert captured["files"]["video"][0] == "done.mp4"
    assert result == {"message_id": 42, "chat_id": "-1003879100454", "topic_id": 3}


def test_send_completed_video_keeps_probe_failure_strict(monkeypatch, tmp_path: Path):
    from tools import telegram_delivery
    from tools.telegram_video import VideoProbeError

    video = tmp_path / "unreadable.mp4"
    video.write_bytes(b"fake-video")
    monkeypatch.setenv("TELEGRAM_DUB_BOT_TOKEN", "secret")
    monkeypatch.setenv("VIDEO_DUB_TELEGRAM_CHAT_ID", "-1003879100454")
    monkeypatch.setenv("VIDEO_DUB_TELEGRAM_TOPIC_ID", "3")

    def fail_probe(_path):
        raise VideoProbeError("ffprobe không đọc được video")

    monkeypatch.setattr(telegram_delivery, "probe_video", fail_probe)

    with pytest.raises(VideoProbeError, match="ffprobe không đọc được video"):
        telegram_delivery.send_completed_video(video, "unreadable")
