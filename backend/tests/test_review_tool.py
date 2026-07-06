"""Test các hàm thuần của tools/run_video_dub_job.py (duyệt bản dịch trong chat).
Import module tool qua sys.path; module này không có side-effect lúc import (mọi import app
nằm trong main())."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import run_video_dub_job as tool  # noqa: E402


def _job() -> dict:
    return {
        "id": "J",
        "segments": [
            {"id": "a", "position": 1, "start": 0.0, "end": 4.0, "source_text": "Hi", "translated_text": "Chào"},
            {"id": "b", "position": 2, "start": 4.0, "end": 6.5, "source_text": "Bye", "translated_text": "Tạm biệt"},
        ],
    }


def test_build_review_rows_computes_seconds_and_chars():
    rows = tool.build_review_rows(_job())
    assert rows[0]["id"] == "a" and rows[0]["en"] == "Hi" and rows[0]["vi"] == "Chào"
    assert rows[0]["sec"] == 4.0 and rows[0]["chars"] == len("Chào")
    assert rows[1]["sec"] == 2.5


def test_dump_review_round_trips_through_parse():
    edits = tool.parse_review(tool.dump_review(_job()))
    assert edits == [{"id": "a", "vi": "Chào"}, {"id": "b", "vi": "Tạm biệt"}]


def test_parse_review_skips_missing_id_or_empty_vi():
    text = json.dumps(
        {
            "segments": [
                {"id": "a", "vi": "giữ lại"},
                {"id": "", "vi": "bỏ vì thiếu id"},
                {"id": "c", "vi": "   "},
                {"id": "d"},
            ]
        }
    )
    assert tool.parse_review(text) == [{"id": "a", "vi": "giữ lại"}]


def test_parse_review_accepts_bare_list():
    text = json.dumps([{"id": "x", "vi": "một"}, {"id": "y", "vi": "hai"}])
    assert tool.parse_review(text) == [{"id": "x", "vi": "một"}, {"id": "y", "vi": "hai"}]


def test_apply_review_only_changed_and_existing_ids():
    current = {"a": "cũ", "b": "giữ nguyên"}
    edits = [
        {"id": "a", "vi": "mới"},          # đổi -> áp
        {"id": "b", "vi": "giữ nguyên"},   # không đổi -> bỏ
        {"id": "z", "vi": "id lạ"},        # id không tồn tại -> bỏ
    ]
    calls: list[tuple[str, str]] = []
    applied = tool.apply_review(edits, current, lambda sid, vi: calls.append((sid, vi)))
    assert applied == 1
    assert calls == [("a", "mới")]
