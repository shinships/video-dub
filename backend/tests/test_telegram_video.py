import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import telegram_video


def _probe_payload(**stream):
    return {
        "streams": [
            {
                "width": 1920,
                "height": 1080,
                "sample_aspect_ratio": "1:1",
                "duration": "12.4",
                **stream,
            }
        ],
        "format": {"duration": "12.4"},
    }


def _mock_probe_json(monkeypatch, payload):
    monkeypatch.setattr(
        telegram_video.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps(payload), stderr=""
        ),
    )


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        ({}, {"width": 1920, "height": 1080, "duration": 12}),
        (
            {"width": 1080, "height": 1920},
            {"width": 1080, "height": 1920, "duration": 12},
        ),
        (
            {"width": 1000, "height": 1000},
            {"width": 1000, "height": 1000, "duration": 12},
        ),
    ],
)
def test_probe_video_returns_basic_geometry(monkeypatch, tmp_path, stream, expected):
    video = tmp_path / "sample.mp4"
    video.touch()
    _mock_probe_json(monkeypatch, _probe_payload(**stream))

    assert telegram_video.probe_video(video) == expected


def test_probe_video_applies_anamorphic_sample_aspect_ratio(monkeypatch, tmp_path):
    video = tmp_path / "anamorphic.mp4"
    video.touch()
    _mock_probe_json(
        monkeypatch,
        _probe_payload(width=720, height=576, sample_aspect_ratio="16:15"),
    )

    metadata = telegram_video.probe_video(video)

    assert metadata == {"width": 768, "height": 576, "duration": 12}


@pytest.mark.parametrize("sample_aspect_ratio", ["0:1", "0:0"])
def test_probe_video_treats_unspecified_sample_aspect_ratio_as_square_pixels(
    monkeypatch, tmp_path, sample_aspect_ratio
):
    video = tmp_path / "unspecified-sar.mp4"
    video.touch()
    _mock_probe_json(
        monkeypatch,
        _probe_payload(width=720, height=576, sample_aspect_ratio=sample_aspect_ratio),
    )

    assert telegram_video.probe_video(video) == {
        "width": 720,
        "height": 576,
        "duration": 12,
    }


@pytest.mark.parametrize("sample_aspect_ratio", ["-1:1", "1:-1", "-1:-1"])
def test_probe_video_rejects_negative_sample_aspect_ratio(
    monkeypatch, tmp_path, sample_aspect_ratio
):
    video = tmp_path / "negative-sar.mp4"
    video.touch()
    _mock_probe_json(
        monkeypatch,
        _probe_payload(sample_aspect_ratio=sample_aspect_ratio),
    )

    with pytest.raises(telegram_video.VideoProbeError, match="invalid sample_aspect_ratio"):
        telegram_video.probe_video(video)


def test_probe_video_applies_display_matrix_rotation(monkeypatch, tmp_path):
    video = tmp_path / "rotated.mp4"
    video.touch()
    _mock_probe_json(
        monkeypatch,
        _probe_payload(
            width=1920,
            height=1080,
            side_data_list=[
                {"side_data_type": "Display Matrix", "rotation": 90}
            ],
        ),
    )

    metadata = telegram_video.probe_video(video)

    assert metadata == {"width": 1080, "height": 1920, "duration": 12}


def test_probe_video_applies_legacy_rotate_tag(monkeypatch, tmp_path):
    video = tmp_path / "legacy-rotated.mp4"
    video.touch()
    _mock_probe_json(
        monkeypatch,
        _probe_payload(width=640, height=360, tags={"rotate": "270"}),
    )

    metadata = telegram_video.probe_video(video)

    assert metadata == {"width": 360, "height": 640, "duration": 12}


@pytest.mark.parametrize(
    "stream",
    [
        {"width": None},
        {"height": None},
        {"width": 0},
        {"height": -1},
        {"width": "not-a-number"},
    ],
)
def test_probe_video_rejects_missing_or_invalid_dimensions(
    monkeypatch, tmp_path, stream
):
    video = tmp_path / "bad-dimensions.mp4"
    video.touch()
    _mock_probe_json(monkeypatch, _probe_payload(**stream))

    with pytest.raises(telegram_video.VideoProbeError, match="invalid (width|height)"):
        telegram_video.probe_video(video)


def test_probe_video_rejects_invalid_json(monkeypatch, tmp_path):
    video = tmp_path / "invalid-json.mp4"
    video.touch()
    monkeypatch.setattr(
        telegram_video.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="{not json", stderr=""),
    )

    with pytest.raises(telegram_video.VideoProbeError, match="JSON không hợp lệ"):
        telegram_video.probe_video(video)


def test_probe_video_wraps_ffprobe_failure(monkeypatch, tmp_path):
    video = tmp_path / "probe-failure.mp4"
    video.touch()
    error = subprocess.CalledProcessError(
        1, ["ffprobe"], stderr="Invalid data found"
    )
    monkeypatch.setattr(
        telegram_video.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(telegram_video.VideoProbeError, match="ffprobe không đọc được video"):
        telegram_video.probe_video(video)


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg và ffprobe là bắt buộc cho fixture video thật",
)
def test_probe_video_with_real_ffmpeg_fixture(tmp_path):
    video = tmp_path / "real-fixture.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x32:d=1",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
    )

    assert telegram_video.probe_video(video) == {
        "width": 64,
        "height": 32,
        "duration": 1,
    }
