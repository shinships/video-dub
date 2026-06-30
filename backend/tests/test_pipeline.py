import json

from app.pipeline import (
    ATEMPO_MAX,
    ATEMPO_MIN,
    _atempo_chain,
    _parse_translations,
    _pitch_chain,
    _strip_json,
    fit_score,
)


def _tempo_product(chain: str) -> float:
    product = 1.0
    for part in chain.split(","):
        product *= float(part.split("=")[1])
    return product


def test_atempo_chain_clamps_to_safe_range():
    # Tỉ lệ quá lớn/nhỏ phải bị kẹp về biên, không còn ép tới 4x như trước.
    fast = _tempo_product(_atempo_chain(3.0))
    slow = _tempo_product(_atempo_chain(0.2))
    assert abs(fast - ATEMPO_MAX) < 1e-3
    assert abs(slow - ATEMPO_MIN) < 1e-3


def test_atempo_chain_keeps_value_in_range():
    assert _atempo_chain(1.05) == "atempo=1.05000"


def test_pitch_chain_empty_when_no_shift():
    assert _pitch_chain(0) == ""
    assert _pitch_chain(0.0) == ""


def test_pitch_chain_builds_filters_when_shifted():
    chain = _pitch_chain(2, sample_rate=48000)
    assert "asetrate=" in chain and "aresample=48000" in chain and "atempo=" in chain


def test_strip_json_removes_code_fence():
    fenced = "```json\n[{\"index\": 0, \"vi\": \"xin chào\"}]\n```"
    assert _strip_json(fenced) == '[{"index": 0, "vi": "xin chào"}]'


def test_parse_translations_handles_array_and_wrapped():
    direct = json.dumps([{"index": 0, "vi": "câu một"}, {"index": 1, "vi": "câu hai"}])
    assert _parse_translations(direct) == {0: "câu một", 1: "câu hai"}

    wrapped = json.dumps({"translations": [{"index": 5, "vi": "năm"}]})
    assert _parse_translations(wrapped) == {5: "năm"}

    assert _parse_translations("not json") == {}


def test_fit_score_prefers_real_audio_duration():
    # Khớp hoàn hảo (audio bằng đúng khung) → điểm cao nhất.
    assert fit_score("bất kỳ", 4.0, audio_seconds=4.0) == 99
    # Audio dài gấp rưỡi khung → điểm thấp hơn rõ.
    assert fit_score("bất kỳ", 4.0, audio_seconds=6.0) < 90
