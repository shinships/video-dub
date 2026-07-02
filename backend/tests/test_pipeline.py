import json
import time
from types import SimpleNamespace

import app.pipeline as pipeline_module
from app.pipeline import (
    ATEMPO_MAX,
    ATEMPO_MIN,
    DEFAULT_JOB_SPEED,
    Pipeline,
    _atempo_chain,
    _is_rate_limited,
    _parse_translations,
    _pitch_chain,
    _RateLimiter,
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


def test_parse_translations_skips_bad_index_and_empty_text():
    payload = json.dumps(
        [
            {"index": "rác", "vi": "bị bỏ"},
            {"index": 2, "vi": "   "},
            {"index": 3, "vi": "hợp lệ"},
        ]
    )
    # Index không phải số và bản dịch rỗng phải bị loại để vòng dịch-lại xử lý.
    assert _parse_translations(payload) == {3: "hợp lệ"}


def _make_segments(count: int) -> list[dict]:
    return [
        {"text": f"sentence {i}", "start": float(i), "end": float(i + 1)} for i in range(count)
    ]


def test_translate_retries_missing_indices(monkeypatch):
    pipe = Pipeline(hook=None)
    monkeypatch.setattr(pipe, "_genai_client", lambda: object())
    monkeypatch.setattr(pipe, "_build_context", lambda client, segs: "ngữ cảnh")
    calls: list[list[int]] = []

    def fake_chunk(client, indices, all_segments, style, context):
        calls.append(list(indices))
        if len(calls) == 1:
            return {0: "không", 2: "hai"}  # bỏ sót index 1
        return {1: "một"}

    monkeypatch.setattr(pipe, "_translate_chunk", fake_chunk)
    translated, context = pipe._translate(_make_segments(3), "tự nhiên")
    assert context == "ngữ cảnh"
    assert [item["translated"] for item in translated] == ["không", "một", "hai"]
    # Lượt dịch-lại chỉ gửi đúng các index còn thiếu.
    assert calls[1] == [1]


def test_translate_falls_back_to_english_when_retry_fails(monkeypatch):
    pipe = Pipeline(hook=None)
    monkeypatch.setattr(pipe, "_genai_client", lambda: object())
    monkeypatch.setattr(pipe, "_build_context", lambda client, segs: "")
    calls: list[list[int]] = []

    def fake_chunk(client, indices, all_segments, style, context):
        calls.append(list(indices))
        if len(calls) == 1:
            return {0: "không"}
        raise RuntimeError("lỗi mạng")

    monkeypatch.setattr(pipe, "_translate_chunk", fake_chunk)
    translated, _context = pipe._translate(_make_segments(2), "tự nhiên")
    # Lượt dịch-lại lỗi -> giữ nguyên tiếng Anh thay vì làm hỏng cả job.
    assert [item["translated"] for item in translated] == ["không", "sentence 1"]


def test_fit_score_prefers_real_audio_duration():
    # Khớp hoàn hảo (audio bằng đúng khung) → điểm cao nhất.
    assert fit_score("bất kỳ", 4.0, audio_seconds=4.0) == 99
    # Audio dài gấp rưỡi khung → điểm thấp hơn rõ.
    assert fit_score("bất kỳ", 4.0, audio_seconds=6.0) < 90


def test_is_rate_limited_detects_429_variants():
    assert _is_rate_limited(Exception("429 Quota exceeded for ..."))
    assert _is_rate_limited(Exception("RESOURCE_EXHAUSTED: too many requests"))
    assert not _is_rate_limited(Exception("403 PermissionDenied: API disabled"))


def test_rate_limiter_enforces_minimum_spacing():
    limiter = _RateLimiter(min_interval=0.05)
    start = time.monotonic()
    limiter.wait()
    limiter.wait()
    limiter.wait()
    elapsed = time.monotonic() - start
    # 3 lần gọi liên tiếp phải cách nhau tối thiểu 2 * min_interval.
    assert elapsed >= 0.09


def test_default_job_speed_stays_within_atempo_range():
    # DEFAULT_JOB_SPEED phải nằm trong biên atempo, nếu không _render sẽ âm thầm kẹp
    # tốc độ mặc định xuống giá trị khác với những gì cấu hình.
    assert ATEMPO_MIN <= DEFAULT_JOB_SPEED <= ATEMPO_MAX


def test_write_srt_scales_timestamps_by_speed(tmp_path, monkeypatch):
    # Tua nhanh video (speed) thì mốc thời gian phụ đề cũng phải chia theo speed để
    # khớp đúng timeline đã bị nén lại của video xuất ra.
    # settings là dataclass frozen với jobs_dir là property -> không setattr trực tiếp
    # được; patch nguyên tên "settings" trong module bằng namespace giả gọn hơn.
    monkeypatch.setattr(pipeline_module, "settings", SimpleNamespace(jobs_dir=tmp_path))
    (tmp_path / "job-x").mkdir()
    fake_job = {
        "segments": [
            {"start": 0.0, "end": 2.0, "translated_text": "Xin chào"},
            {"start": 2.0, "end": 4.4, "translated_text": "Tạm biệt"},
        ]
    }
    monkeypatch.setattr(pipeline_module, "get_job", lambda job_id: fake_job)

    pipe = Pipeline(hook=None)
    srt_path = pipe._write_srt("job-x", speed=1.1)
    content = srt_path.read_text(encoding="utf-8")

    assert "00:00:00,000 --> 00:00:01,818" in content
    assert "00:00:01,818 --> 00:00:04,000" in content


def test_write_srt_defaults_to_speed_one(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "settings", SimpleNamespace(jobs_dir=tmp_path))
    (tmp_path / "job-y").mkdir()
    fake_job = {"segments": [{"start": 1.0, "end": 3.0, "translated_text": "Câu"}]}
    monkeypatch.setattr(pipeline_module, "get_job", lambda job_id: fake_job)

    pipe = Pipeline(hook=None)
    content = pipe._write_srt("job-y").read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:03,000" in content
