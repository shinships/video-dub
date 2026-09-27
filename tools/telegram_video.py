"""Probe video metadata required by Telegram's sendVideo endpoint."""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any


class VideoProbeError(RuntimeError):
    """Raised when ffprobe cannot provide usable Telegram video metadata."""


def _positive_int(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise VideoProbeError(f"ffprobe returned invalid {field}: {value!r}") from exc
    if number <= 0:
        raise VideoProbeError(f"ffprobe returned invalid {field}: {number}")
    return number


def _sample_aspect_ratio(value: Any) -> tuple[int, int]:
    if value in (None, "", "N/A"):
        return 1, 1
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", str(value))
    if not match:
        raise VideoProbeError(f"ffprobe returned invalid sample_aspect_ratio: {value!r}")
    numerator, denominator = (int(part) for part in match.groups())
    # ffprobe uses a zero numerator for an unspecified SAR (including 0:0).
    if numerator == 0:
        return 1, 1
    if denominator <= 0:
        raise VideoProbeError(f"ffprobe returned invalid sample_aspect_ratio: {value!r}")
    return numerator, denominator


def _quarter_turns(value: Any, source: str) -> int:
    try:
        degrees = float(value)
    except (TypeError, ValueError) as exc:
        raise VideoProbeError(f"ffprobe returned invalid {source} rotation: {value!r}") from exc
    if not math.isfinite(degrees):
        raise VideoProbeError(f"ffprobe returned invalid {source} rotation: {value!r}")
    turns = round(degrees / 90)
    if not math.isclose(degrees, turns * 90, abs_tol=0.5):
        raise VideoProbeError(f"ffprobe returned unsupported {source} rotation: {degrees}")
    return turns % 4


def _matrix_rotation(matrix: Any) -> int | None:
    """Infer whether a display matrix is quarter-turn rotated when no angle is exposed."""
    if not isinstance(matrix, str):
        return None
    rows = re.findall(r"(?m)^\s*[0-9A-Fa-f]+:\s*([^\n]+)", matrix)
    values: list[int] = []
    for row in rows[:2]:
        for token in re.findall(r"[+-]?(?:0x)?[0-9A-Fa-f]+", row)[:2]:
            try:
                values.append(int(token, 0) if token.lower().startswith("0x") else int(token, 16))
            except ValueError:
                continue
    if len(values) < 4:
        return None
    # The first 2x2 matrix entries determine the orientation; 16.16 scaling
    # does not matter because only the dominant axis is needed here.
    a, b, c, d = values[:4]
    if abs(b) > abs(a) and abs(c) > abs(d):
        return 1
    if abs(a) >= abs(b) and abs(d) >= abs(c):
        return 0
    return None


def _rotation_quarters(stream: dict[str, Any]) -> int:
    side_data_list = stream.get("side_data_list") or []
    if not isinstance(side_data_list, list):
        raise VideoProbeError("ffprobe returned invalid side_data_list")
    for side_data in side_data_list:
        if not isinstance(side_data, dict):
            continue
        side_type = str(side_data.get("side_data_type") or "").lower()
        if "display matrix" not in side_type and "displaymatrix" not in side_data:
            continue
        if side_data.get("rotation") is not None:
            return _quarter_turns(side_data["rotation"], "display-matrix")
        inferred = _matrix_rotation(side_data.get("displaymatrix"))
        if inferred is not None:
            return inferred

    tags = stream.get("tags") or {}
    if not isinstance(tags, dict):
        raise VideoProbeError("ffprobe returned invalid stream tags")
    for key, value in tags.items():
        if str(key).lower() == "rotate" and value not in (None, ""):
            return _quarter_turns(value, "legacy")
    return 0


def probe_video(path: Path) -> dict[str, int]:
    """Return display dimensions and duration from the exact file sent to Telegram."""
    if not path.is_file():
        raise FileNotFoundError(path)

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise VideoProbeError("Không tìm thấy ffprobe để đọc metadata video") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()[:500]
        suffix = f": {detail}" if detail else ""
        raise VideoProbeError(f"ffprobe không đọc được video{suffix}") from exc
    except OSError as exc:
        raise VideoProbeError(f"Không thể chạy ffprobe: {exc}") from exc

    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise VideoProbeError("ffprobe trả về JSON không hợp lệ") from exc
    if not isinstance(payload, dict):
        raise VideoProbeError("ffprobe trả về payload không hợp lệ")

    streams = payload.get("streams") or []
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise VideoProbeError("ffprobe không tìm thấy video stream")
    stream = streams[0]
    width = _positive_int(stream.get("width"), "width")
    height = _positive_int(stream.get("height"), "height")
    sar_num, sar_den = _sample_aspect_ratio(stream.get("sample_aspect_ratio"))
    display_width = round(width * sar_num / sar_den)
    display_height = height
    if display_width <= 0:
        raise VideoProbeError(f"ffprobe tạo ra display width không hợp lệ: {display_width}")

    if _rotation_quarters(stream) % 2:
        display_width, display_height = display_height, display_width

    format_data = payload.get("format") or {}
    if not isinstance(format_data, dict):
        raise VideoProbeError("ffprobe returned invalid format metadata")
    duration_value = format_data.get("duration")
    if duration_value in (None, "", "N/A"):
        duration_value = stream.get("duration")
    try:
        duration_seconds = float(duration_value)
    except (TypeError, ValueError) as exc:
        raise VideoProbeError(f"ffprobe returned invalid duration: {duration_value!r}") from exc
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise VideoProbeError(f"ffprobe returned invalid duration: {duration_value!r}")

    return {
        "width": int(display_width),
        "height": int(display_height),
        "duration": max(1, round(duration_seconds)),
    }
