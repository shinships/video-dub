import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import clip_video as cv  # noqa: E402

CHAPTERS = [
    {"start_time": 0.0, "end_time": 2.0, "title": "Intro"},
    {"start_time": 2.0, "end_time": 4.0, "title": "Setup Blender"},
    {"start_time": 4.0, "end_time": 6.0, "title": "Render"},
]


def test_parse_timestamp():
    assert cv.parse_timestamp("90") == 90
    assert cv.parse_timestamp("1:30") == 90
    assert cv.parse_timestamp("1:02:03") == 3723
    with pytest.raises(ValueError):
        cv.parse_timestamp("abc")


def test_parse_range():
    seg = cv.parse_range("1:20-5:00")
    assert (seg.start, seg.end, seg.label) == (80, 300, "120-500")
    assert cv.parse_range("10:00-").end == float("inf")
    with pytest.raises(ValueError):
        cv.parse_range("5:00-1:00")


def test_select_chapters():
    segs = cv.select_chapters(CHAPTERS, "1,3")
    assert [s.label for s in segs] == ["ch1", "ch3"]
    assert [s.label for s in cv.select_chapters(CHAPTERS, "2-3")] == ["ch2", "ch3"]
    assert cv.select_chapters(CHAPTERS, "blender")[0].label == "ch2"
    with pytest.raises(ValueError):
        cv.select_chapters(CHAPTERS, "9")
    with pytest.raises(ValueError):
        cv.select_chapters([], "1")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_build_clips_merge_and_split(tmp_path):
    src = tmp_path / "vid.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=6:s=160x120:r=25",
                    "-f", "lavfi", "-i", "sine=d=6", "-shortest", "-c:v", "libx264", "-c:a", "aac",
                    str(src)], check=True)
    segs = cv.select_chapters(CHAPTERS, "1,3")
    merged = cv.build_clips(src, segs, tmp_path / "out", split=False)
    assert len(merged) == 1 and merged[0].name == "vid_ch1_ch3.mp4"
    assert abs(cv.probe_duration(merged[0]) - 4.0) < 0.5

    split = cv.build_clips(src, cv.select_chapters(CHAPTERS, "1,3"), tmp_path / "out2", split=True)
    assert [p.name for p in split] == ["vid_ch1.mp4", "vid_ch3.mp4"]
    assert abs(cv.probe_duration(split[1]) - 2.0) < 0.5
