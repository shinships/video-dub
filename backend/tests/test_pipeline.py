import json
from types import SimpleNamespace

import pytest

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
    _strip_json,
    fit_score,
    merge_transcripts,
    assign_speakers,
    classify_gender,
    resolve_segment_voice,
    resolve_tts_engine,
    segment_audio_suffix,
    segment_median_f0,
    segment_tempo,
    split_sentences,
    ensure_engine_voices_ready,
    parse_vbee_voices,
    parse_vieneu_voices,
    unknown_vieneu_voices,
    vbee_read,
    vbee_request_payload,
    vbee_should_try_sync,
    vbee_sync_outcome,
    vbee_sync_payload,
    vieneu_infer_kwargs,
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


def test_atempo_chain_custom_bounds_skip_reclamp():
    # Tích (tempo đoạn × speed) đã kẹp sẵn từng thừa số; biên nới phải giữ nguyên giá trị
    # thay vì kẹp lần nữa về ATEMPO_MAX làm lệch đồng bộ với timeline đã tua nhanh.
    assert abs(_tempo_product(_atempo_chain(1.265, lo=0.5, hi=2.0)) - 1.265) < 1e-3


def test_segment_tempo_never_slows_short_audio():
    # Câu ngắn hơn khung phải đọc tốc độ tự nhiên (1.0), không bị kéo chậm để lấp khung —
    # kéo chậm là nguyên nhân chính khiến nhịp đọc lúc nhanh lúc chậm giữa các câu.
    assert segment_tempo(2.0, 4.0, 4.0) == 1.0
    assert segment_tempo(3.9, 4.0, 4.0) == 1.0


def test_segment_tempo_spills_into_gap_before_speeding_up():
    # Câu dài hơn khung nhưng còn khoảng lặng phía sau -> tràn sang, giữ tốc độ tự nhiên.
    assert segment_tempo(4.8, 4.0, 5.0) == 1.0
    # Hết chỗ trống mới tăng tốc đúng phần thiếu.
    assert abs(segment_tempo(4.4, 4.0, 4.0) - 1.1) < 1e-9


def test_segment_tempo_clamps_at_atempo_max():
    assert segment_tempo(8.0, 4.0, 4.0) == ATEMPO_MAX
    # Khe âm/khoảng chồng lấn không được làm chia cho số âm.
    assert segment_tempo(1.0, 0.0, -1.0) == ATEMPO_MAX


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


def test_merge_transcripts_joins_until_sentence_end():
    # Whisper cắt giữa câu -> nối các mảnh cho tới khi gặp dấu kết câu, rồi bắt đầu câu mới.
    segs = [
        {"text": "In this video", "start": 0.0, "end": 1.5},
        {"text": "I will show you", "start": 1.6, "end": 3.0},
        {"text": "three tips.", "start": 3.1, "end": 4.5},
        {"text": "Let's begin.", "start": 5.0, "end": 6.0},
    ]
    merged = merge_transcripts(segs)
    assert [m["text"] for m in merged] == [
        "In this video I will show you three tips.",
        "Let's begin.",
    ]
    # Câu gộp giữ mốc đầu của mảnh đầu và mốc cuối của mảnh cuối.
    assert merged[0]["start"] == 0.0 and merged[0]["end"] == 4.5


def test_merge_transcripts_respects_gap_and_caps():
    # Khe lặng vượt cả ngưỡng nối câu (chữ thường) -> không nối dù chưa hết câu.
    gapped = merge_transcripts(
        [
            {"text": "Hello there", "start": 0.0, "end": 1.0},
            {"text": "friend", "start": 3.0, "end": 3.5},
        ]
    )
    assert [m["text"] for m in gapped] == ["Hello there", "friend"]

    # Trần độ dài buộc tách dù khe nhỏ và chưa hết câu.
    capped = merge_transcripts(
        [
            {"text": "one", "start": 0.0, "end": 1.0},
            {"text": "two", "start": 1.1, "end": 2.5},
        ],
        max_seconds=2.0,
    )
    assert [m["text"] for m in capped] == ["one", "two"]


def test_merge_transcripts_joins_lowercase_continuation_across_wide_gap():
    # Người dẫn ngừng lâu (1.3s) để nhấn GIỮA câu; mảnh kế viết thường -> vẫn là cùng câu,
    # phải nối để tránh "ngắt quãng trong 1 câu" (khe lặng rơi vào giữa câu lúc render).
    merged = merge_transcripts(
        [
            {"text": "The most important thing", "start": 0.0, "end": 1.4},
            {"text": "is to stay consistent.", "start": 2.7, "end": 4.2},
        ]
    )
    assert [m["text"] for m in merged] == ["The most important thing is to stay consistent."]


def test_merge_transcripts_keeps_new_sentence_after_wide_gap():
    # Cùng khe lặng rộng nhưng mảnh kế VIẾT HOA đầu (câu mới) -> giữ tách, không nối nhầm.
    merged = merge_transcripts(
        [
            {"text": "The system works", "start": 0.0, "end": 1.4},
            {"text": "Next we configure it.", "start": 2.7, "end": 4.2},
        ]
    )
    assert [m["text"] for m in merged] == ["The system works", "Next we configure it."]


def test_split_sentences_cuts_fragment_at_sentence_boundary():
    # Mảnh Whisper chứa 2 câu (đứt giữa câu sau) -> tách đúng ranh giới theo mốc từng từ.
    frag = {
        "text": "I moved here. My whole plan",
        "start": 0.0,
        "end": 2.4,
        "words": [
            {"text": "I", "start": 0.0, "end": 0.2},
            {"text": "moved", "start": 0.2, "end": 0.6},
            {"text": "here.", "start": 0.6, "end": 1.0},
            {"text": "My", "start": 1.4, "end": 1.6},
            {"text": "whole", "start": 1.6, "end": 2.0},
            {"text": "plan", "start": 2.0, "end": 2.4},
        ],
    }
    pieces = split_sentences([frag])
    assert [p["text"] for p in pieces] == ["I moved here.", "My whole plan"]
    # Mốc thời gian lấy từ từ đầu/cuối của từng câu để render đặt đúng vị trí.
    assert pieces[0]["end"] == 1.0 and pieces[1]["start"] == 1.4


def test_split_sentences_keeps_abbreviation_before_lowercase():
    # Dấu chấm của viết tắt ("e.g.") theo sau bởi chữ thường -> chưa hết câu, không tách.
    frag = {
        "text": "e.g. we scaled.",
        "start": 0.0,
        "end": 1.2,
        "words": [
            {"text": "e.g.", "start": 0.0, "end": 0.4},
            {"text": "we", "start": 0.5, "end": 0.7},
            {"text": "scaled.", "start": 0.7, "end": 1.2},
        ],
    }
    assert [p["text"] for p in split_sentences([frag])] == ["e.g. we scaled."]


def test_split_sentences_passthrough_without_words():
    # Mảnh không có word timestamps (STT cũ/fallback) giữ nguyên, không đổi hành vi.
    frag = {"text": "Hello there", "start": 0.0, "end": 1.0}
    assert split_sentences([frag]) == [{"text": "Hello there", "start": 0.0, "end": 1.0}]


def test_split_then_merge_rebuilds_sentences_across_fragments():
    # Mảnh 1 đứt giữa câu, mảnh 2 chứa phần còn lại + một câu mới: sau tách + gộp phải ra
    # đúng 2 câu trọn vẹn — đây là lỗi "ngắt quãng giữa câu" khi mảnh ~10s chạm trần gộp.
    frags = [
        {
            "text": "It started why it",
            "start": 0.0,
            "end": 2.0,
            "words": [
                {"text": "It", "start": 0.0, "end": 0.3},
                {"text": "started", "start": 0.3, "end": 0.9},
                {"text": "why", "start": 0.9, "end": 1.3},
                {"text": "it", "start": 1.3, "end": 2.0},
            ],
        },
        {
            "text": "grew. So I built it.",
            "start": 2.1,
            "end": 5.0,
            "words": [
                {"text": "grew.", "start": 2.1, "end": 2.6},
                {"text": "So", "start": 3.0, "end": 3.2},
                {"text": "I", "start": 3.2, "end": 3.3},
                {"text": "built", "start": 3.3, "end": 3.7},
                {"text": "it.", "start": 3.7, "end": 5.0},
            ],
        },
    ]
    merged = pipeline_module.merge_transcripts(split_sentences(frags))
    assert [m["text"] for m in merged] == ["It started why it grew.", "So I built it."]


def test_merge_transcripts_skips_empty_segments():
    merged = merge_transcripts(
        [
            {"text": "  ", "start": 0.0, "end": 1.0},
            {"text": "hi", "start": 1.0, "end": 2.0},
        ]
    )
    assert [m["text"] for m in merged] == ["hi"]


def test_classify_gender_threshold():
    # F0 thấp -> nam, cao -> nữ; đúng biên 165Hz (bằng ngưỡng tính là nữ).
    assert classify_gender(120.0) == "male"
    assert classify_gender(210.0) == "female"
    assert classify_gender(165.0) == "female"
    assert classify_gender(164.9) == "male"


def test_assign_speakers_smoothing():
    # None kế thừa nhãn đoạn trước; đoạn đầu None -> mặc định "male".
    assert assign_speakers([120.0, None, 210.0, None]) == ["male", "male", "female", "female"]
    assert assign_speakers([None, 210.0]) == ["male", "female"]
    # Toàn bộ nữ và xen kẽ nam/nữ.
    assert assign_speakers([200.0, 220.0]) == ["female", "female"]
    assert assign_speakers([120.0, 210.0, 130.0]) == ["male", "female", "male"]


def test_assign_speakers_empty():
    assert assign_speakers([]) == []


def _voice_cfg():
    # Cfg giả lập giống Settings cho resolve_segment_voice (dataclass thật là frozen).
    return SimpleNamespace(
        vbee_voice="v_default",
        vbee_voice_male="v_male",
        vbee_voice_female="v_female",
        vieneu_voice="vn_default",
        vieneu_ref_audio="",
        vieneu_voice_male="vn_male",
        vieneu_ref_audio_male="",
        vieneu_voice_female="vn_female",
        vieneu_ref_audio_female="",
    )


def test_resolve_segment_voice_vbee_by_gender():
    cfg = _voice_cfg()
    assert resolve_segment_voice("vbee", "female", True, cfg) == "v_female"
    assert resolve_segment_voice("vbee", "male", True, cfg) == "v_male"
    # Multi tắt -> giọng mặc định 1-giọng như cũ, bất kể nhãn.
    assert resolve_segment_voice("vbee", "female", False, cfg) == "v_default"
    assert resolve_segment_voice("vbee", None, True, cfg) == "v_default"


def test_resolve_segment_voice_vbee_prefers_job_voice():
    # Giọng chọn trên UI (jobs.voice) thắng env ở chế độ 1-giọng...
    cfg = _voice_cfg()
    assert resolve_segment_voice("vbee", None, False, cfg, job_voice="sg_male_x") == "sg_male_x"
    # ...nhưng "Aoede" (giọng Vertex AI, mặc định cũ của cột jobs.voice) không phải voiceCode Vbee.
    assert resolve_segment_voice("vbee", None, False, cfg, job_voice="Aoede") == "v_default"
    assert resolve_segment_voice("vbee", None, False, cfg, job_voice="") == "v_default"
    assert resolve_segment_voice("vbee", None, False, cfg, job_voice=None) == "v_default"
    # Lồng tiếng 2 giọng: giọng nam/nữ theo env vẫn thắng lựa chọn trên UI.
    assert resolve_segment_voice("vbee", "female", True, cfg, job_voice="sg_male_x") == "v_female"


def test_resolve_segment_voice_vieneu_prefers_job_voice_over_ref_audio():
    # Giọng preset chọn trên UI là chỉ định rõ ràng -> thắng cả ref_audio (nhân bản) trong env.
    cfg = _voice_cfg()
    cfg.vieneu_ref_audio = "clone.wav"
    assert resolve_segment_voice("vieneu", None, False, cfg, job_voice="Trúc Ly") == {"voice": "Trúc Ly"}
    assert resolve_segment_voice("vieneu", None, False, cfg) == {"ref_audio": "clone.wav"}
    # Multi bật -> giọng theo giới tính trong env vẫn thắng lựa chọn trên UI.
    assert resolve_segment_voice("vieneu", "female", True, cfg, job_voice="Trúc Ly") == {
        "voice": "vn_female"
    }


def test_resolve_segment_voice_ignores_voice_from_other_engine():
    # Job chọn giọng Vbee rồi đổi engine sang VieNeu: voiceCode Vbee không phải preset VieNeu,
    # dùng bừa sẽ ném ValueError lúc synth -> phải lùi về giọng cấu hình trong env.
    cfg = _voice_cfg()
    known = ["Trúc Ly", "Đức Trí"]
    assert resolve_segment_voice(
        "vieneu", None, False, cfg, job_voice="hn_female_ngochuyen_full_48k-fhg", known_voices=known
    ) == {"voice": "vn_default"}
    assert resolve_segment_voice(
        "vieneu", None, False, cfg, job_voice="Trúc Ly", known_voices=known
    ) == {"voice": "Trúc Ly"}


def test_parse_vieneu_voices_reads_presets():
    voices = parse_vieneu_voices(
        {
            "default_voice": "Ngọc Linh",
            "presets": {
                "Ngọc Lan": {"description": "nữ, giọng dịu dàng", "gender": "nữ", "codes": [1, 2]},
                "Gia Bảo": {"description": "nam, giọng mượt mà", "gender": "nam", "codes": [3]},
            },
        }
    )
    # Tên preset chính là giá trị đặt cho VIDEO_DUB_VIENEU_VOICE.
    assert [voice["id"] for voice in voices] == ["Ngọc Lan", "Gia Bảo"]
    assert voices[0]["desc"] == "nữ, giọng dịu dàng"
    assert voices[1]["gender"] == "nam"
    # Thân JSON lạ/rỗng -> rỗng chứ không ném lỗi.
    assert parse_vieneu_voices({}) == []


def test_unknown_vieneu_voices_flags_only_presets_without_ref_audio():
    cfg = _voice_cfg()
    cfg.vieneu_voice = "Đức Trí"
    cfg.vieneu_voice_male = "Không Có Thật"
    cfg.vieneu_voice_female = "Cũng Không"
    # Giọng nữ có ref_audio -> preset bị bỏ qua khi synth nên không tính là sai.
    cfg.vieneu_ref_audio_female = "female.wav"
    assert unknown_vieneu_voices(cfg, known=["Đức Trí", "Ngọc Lan"]) == ["Không Có Thật"]
    # Không đọc được preset -> không kết luận, tránh báo lỗi oan.
    assert unknown_vieneu_voices(cfg, known=[]) == []


def test_ensure_engine_voices_ready_raises_only_for_bad_vieneu_config(monkeypatch):
    monkeypatch.setattr(pipeline_module, "vieneu_preset_voices", lambda: [{"id": "Đức Trí"}])
    monkeypatch.setattr(
        pipeline_module,
        "settings",
        SimpleNamespace(
            vieneu_voice="Sai Tên",
            vieneu_ref_audio="",
            vieneu_voice_male="",
            vieneu_ref_audio_male="",
            vieneu_voice_female="",
            vieneu_ref_audio_female="",
        ),
    )
    with pytest.raises(pipeline_module.PipelineError) as err:
        ensure_engine_voices_ready("vieneu")
    assert "Sai Tên" in str(err.value) and "Đức Trí" in str(err.value)
    # Vbee không kiểm tra offline được -> không chặn.
    ensure_engine_voices_ready("vbee")


def test_resolve_segment_voice_vieneu_by_gender():
    cfg = _voice_cfg()
    assert resolve_segment_voice("vieneu", "female", True, cfg) == {"voice": "vn_female"}
    assert resolve_segment_voice("vieneu", "male", True, cfg) == {"voice": "vn_male"}
    # Multi tắt -> infer_kwargs mặc định (giữ hành vi cũ).
    assert resolve_segment_voice("vieneu", "female", False, cfg) == {"voice": "vn_default"}


def test_resolve_segment_voice_falls_back_when_gender_unset():
    # Giọng nữ chưa cấu hình -> fallback về mặc định thay vì trả rỗng/hỏng.
    cfg = _voice_cfg()
    cfg.vbee_voice_female = ""
    cfg.vieneu_voice_female = ""
    cfg.vieneu_ref_audio_female = ""
    assert resolve_segment_voice("vbee", "female", True, cfg) == "v_default"
    assert resolve_segment_voice("vieneu", "female", True, cfg) == {"voice": "vn_default"}


def test_segment_median_f0_on_synthetic_tone():
    # Sóng sin 120Hz -> trung vị F0 rơi quanh 120 (biên nam), 220Hz -> quanh 220 (biên nữ).
    import numpy as np

    sr = 16000
    t = np.arange(sr * 2) / sr  # 2 giây đủ nhiều khung hữu thanh.
    male = 0.5 * np.sin(2 * np.pi * 120.0 * t)
    female = 0.5 * np.sin(2 * np.pi * 220.0 * t)
    f0_male = segment_median_f0(male, sr, 0.0, 2.0)
    f0_female = segment_median_f0(female, sr, 0.0, 2.0)
    assert f0_male is not None and abs(f0_male - 120.0) < 8.0
    assert f0_female is not None and abs(f0_female - 220.0) < 12.0
    assert classify_gender(f0_male) == "male"
    assert classify_gender(f0_female) == "female"


def test_segment_median_f0_returns_none_on_silence():
    import numpy as np

    sr = 16000
    silence = np.zeros(sr)  # 1 giây im lặng -> không đủ khung hữu thanh.
    assert segment_median_f0(silence, sr, 0.0, 1.0) is None


def test_translate_applies_review_fixes(monkeypatch):
    # Sau khi dịch, pass soát lại được gọi và bản sửa của nó ghi đè đúng index.
    pipe = Pipeline(hook=None)
    monkeypatch.setattr(pipe, "_genai_client", lambda: object())
    monkeypatch.setattr(pipe, "_build_context", lambda client, segs: "ngữ cảnh")
    monkeypatch.setattr(
        pipe,
        "_translate_chunk",
        lambda client, indices, all_segments, style, context: {i: f"vi{i}" for i in indices},
    )
    monkeypatch.setattr(
        pipe,
        "_review_translations",
        lambda client, segments, translated, context: {1: "vi1-đã-sửa"},
    )
    translated, _context = pipe._translate(_make_segments(3), "tự nhiên")
    assert [item["translated"] for item in translated] == ["vi0", "vi1-đã-sửa", "vi2"]


def test_review_translations_skips_without_context():
    # Không có hướng dẫn dịch (glossary/xưng hô) thì không có mốc để soát -> bỏ qua, không gọi API.
    pipe = Pipeline(hook=None)
    assert pipe._review_translations(object(), _make_segments(2), {0: "a", 1: "b"}, "") == {}


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


def test_segment_audio_suffix_follows_tts_engine():
    assert segment_audio_suffix("vieneu") == ".wav"
    # Vbee trả MP3 -> dùng chung nhánh mặc định.
    assert segment_audio_suffix("vbee") == ".mp3"


def test_vbee_request_payload_uses_async_mode():
    # Đường Batch: mode async, có webhookUrl bắt buộc dù ta chỉ poll.
    payload = vbee_request_payload("Xin chào", "hn_female_ngochuyen_full_48k-fhg")
    assert payload["mode"] == "async"
    assert payload["text"] == "Xin chào"
    assert payload["voiceCode"] == "hn_female_ngochuyen_full_48k-fhg"
    assert payload["outputFormat"] == "mp3"
    assert payload["webhookUrl"]


def test_vbee_read_handles_post_poll_and_error_shapes():
    # POST trả requestId + PROCESSING (chưa có audio).
    post = vbee_read({"requestId": "abc", "status": "PROCESSING"})
    assert post == {"request_id": "abc", "status": "PROCESSING", "audio_link": None, "error": None}
    # GET tới COMPLETED kèm audioLink; status chuẩn hoá in hoa.
    done = vbee_read({"requestId": "abc", "status": "completed", "audioLink": "https://x/y"})
    assert done["status"] == "COMPLETED" and done["audio_link"] == "https://x/y"
    # Bọc trong "result" (như API voices) vẫn bóc được.
    wrapped = vbee_read({"result": {"requestId": "z", "status": "PROCESSING"}})
    assert wrapped["request_id"] == "z"
    # Lỗi -> lấy message, không có request_id.
    err = vbee_read({"error": {"code": "BAD_REQUEST", "message": "thiếu webhookUrl"}})
    assert err["request_id"] is None and err["error"] == "thiếu webhookUrl"


def test_vbee_sync_payload_has_no_webhook():
    # Realtime API trả thẳng audio -> không requestId/webhookUrl như đường Batch.
    payload = vbee_sync_payload("Xin chào", "hn_female_ngochuyen_full_48k-fhg")
    assert payload["mode"] == "sync"
    assert payload["voiceCode"] == "hn_female_ngochuyen_full_48k-fhg"
    assert payload["outputFormat"] == "mp3"
    assert "webhookUrl" not in payload


def test_vbee_should_try_sync_respects_limit_mode_and_block():
    short = "Xin chào các bạn"
    long_text = "a" * (pipeline_module.VBEE_SYNC_MAX_CHARS + 1)
    assert vbee_should_try_sync(short, mode="auto", blocked=False)
    # Trần 300 ký tự áp cả khi ép sync — câu dài buộc đi Batch.
    assert not vbee_should_try_sync(long_text, mode="sync", blocked=False)
    assert not vbee_should_try_sync(short, mode="async", blocked=False)
    # Đã biết gói không mở sync -> không thử lại ở các đoạn sau.
    assert not vbee_should_try_sync(short, mode="auto", blocked=True)


def test_vbee_sync_outcome_classifies_audio_blocked_and_fallback():
    assert vbee_sync_outcome(200, "audio/mpeg", b"ID3data") == ("audio", "")
    # Body rỗng dù status 200 -> không ghi file rỗng, lùi về async.
    assert vbee_sync_outcome(200, "audio/mpeg", b"")[0] == "fallback"
    blocked = vbee_sync_outcome(
        400,
        "application/json",
        json.dumps(
            {"error": {"code": "BAD_REQUEST", "message": "This feature is not supported in user package"}}
        ).encode(),
    )
    assert blocked[0] == "blocked" and "user package" in blocked[1]
    # Vượt giới hạn đồng thời -> chỉ đoạn này lùi về async, không tắt sync cả tiến trình.
    busy = vbee_sync_outcome(
        429,
        "application/json",
        json.dumps({"error": {"code": "TTS_CCR_MAX_LIMIT_REACHED", "message": "quá tải"}}).encode(),
    )
    assert busy == ("fallback", "quá tải")
    # Thân không phải JSON hợp lệ vẫn phải ra fallback kèm mã HTTP.
    assert vbee_sync_outcome(500, "text/html", b"<html>")[0] == "fallback"


def test_parse_vbee_voices_reads_codes_and_cursor():
    voices, cursor = parse_vbee_voices(
        {
            "result": {
                "voices": [
                    {
                        "code": "hn_female_ngochuyen_full_48k-fhg",
                        "name": "HN - Ngọc Huyền",
                        "gender": "female",
                        "language_code": "vi-VN",
                    },
                    {"name": "thiếu code"},
                ],
                "pagination": {"next_cursor": "abc", "has_next_page": True},
            }
        }
    )
    assert cursor == "abc"
    # Bản ghi thiếu code bị bỏ (không dùng làm voiceCode được).
    assert len(voices) == 1
    assert voices[0]["id"] == "hn_female_ngochuyen_full_48k-fhg"
    assert voices[0]["label"] == "HN - Ngọc Huyền"
    assert voices[0]["gender"] == "female"
    assert "female" in voices[0]["desc"]
    # Thân JSON lạ/rỗng -> rỗng chứ không ném lỗi (UI vẫn chạy).
    assert parse_vbee_voices({}) == ([], None)


def test_vieneu_infer_kwargs_prefers_ref_audio_over_preset():
    # ref_audio (nhân bản giọng) phải thắng preset; trống cả hai -> mặc định SDK.
    assert vieneu_infer_kwargs("Ngọc Lan", "my.wav") == {"ref_audio": "my.wav"}
    assert vieneu_infer_kwargs("Ngọc Lan", "") == {"voice": "Ngọc Lan"}
    assert vieneu_infer_kwargs("", "") == {}


def test_resolve_tts_engine_prefers_job_over_global(monkeypatch):
    # Job đặt riêng engine (qua PATCH /api/jobs, không qua UI) phải thắng cấu hình toàn cục.
    # settings là dataclass frozen -> patch nguyên tên "settings" trong module thay vì field.
    monkeypatch.setattr(pipeline_module, "settings", SimpleNamespace(tts_engine="vbee"))
    assert resolve_tts_engine({"tts_engine": "vieneu"}) == "vieneu"
    assert resolve_tts_engine({"tts_engine": None}) == "vbee"
    assert resolve_tts_engine({}) == "vbee"


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
