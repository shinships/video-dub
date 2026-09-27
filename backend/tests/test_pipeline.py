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
    TTS_ENGINE,
    segment_audio_suffix,
    segment_median_f0,
    segment_tempo,
    split_sentences,
    ensure_engine_voices_ready,
    parse_vieneu_voices,
    unknown_vieneu_voices,
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
    monkeypatch.setattr(pipe, "_build_context", lambda client, segs, meter=None: "ngữ cảnh")
    calls: list[list[int]] = []

    def fake_chunk(client, indices, all_segments, style, context, meter=None):
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
        vieneu_voice="vn_default",
        vieneu_ref_audio="",
        vieneu_voice_male="vn_male",
        vieneu_ref_audio_male="",
        vieneu_voice_female="vn_female",
        vieneu_ref_audio_female="",
    )




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
    # Engine lạ (dữ liệu cũ) không kiểm tra offline được -> không chặn.
    ensure_engine_voices_ready("khong-ton-tai")


def test_resolve_segment_voice_vieneu_by_gender():
    cfg = _voice_cfg()
    assert resolve_segment_voice("vieneu", "female", True, cfg) == {"voice": "vn_female"}
    assert resolve_segment_voice("vieneu", "male", True, cfg) == {"voice": "vn_male"}
    # Multi tắt -> infer_kwargs mặc định (giữ hành vi cũ).
    assert resolve_segment_voice("vieneu", "female", False, cfg) == {"voice": "vn_default"}


def test_resolve_segment_voice_falls_back_when_gender_unset():
    # Giọng nữ chưa cấu hình -> fallback về mặc định thay vì trả rỗng/hỏng.
    cfg = _voice_cfg()
    cfg.vieneu_voice_female = ""
    cfg.vieneu_ref_audio_female = ""
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
    monkeypatch.setattr(pipe, "_build_context", lambda client, segs, meter=None: "ngữ cảnh")
    monkeypatch.setattr(
        pipe,
        "_translate_chunk",
        lambda client, indices, all_segments, style, context, meter=None: {i: f"vi{i}" for i in indices},
    )
    monkeypatch.setattr(
        pipe,
        "_review_translations",
        lambda client, segments, translated, context, meter=None: {1: "vi1-đã-sửa"},
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
    monkeypatch.setattr(pipe, "_build_context", lambda client, segs, meter=None: "")
    calls: list[list[int]] = []

    def fake_chunk(client, indices, all_segments, style, context, meter=None):
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


def test_segment_audio_suffix_is_wav():
    # VieNeu save ra WAV; engine cũ trong DB cũng phải ra WAV vì đã bị ép về VieNeu.
    assert segment_audio_suffix() == ".wav"
    assert segment_audio_suffix("vbee") == ".wav"








def test_vieneu_infer_kwargs_prefers_ref_audio_over_preset():
    # ref_audio (nhân bản giọng) phải thắng preset; trống cả hai -> mặc định SDK.
    assert vieneu_infer_kwargs("Ngọc Lan", "my.wav") == {"ref_audio": "my.wav"}
    assert vieneu_infer_kwargs("Ngọc Lan", "") == {"voice": "Ngọc Lan"}
    assert vieneu_infer_kwargs("", "") == {}


def test_resolve_tts_engine_coerces_legacy_engines_to_vieneu():
    # Vbee đã gỡ nhưng job cũ trong DB vẫn còn tts_engine='vbee' và user không sửa được cột
    # đó từ UI nữa -> phải ép về VieNeu, không để job hỏng ở bước TTS.
    assert TTS_ENGINE == "vieneu"
    assert resolve_tts_engine({"tts_engine": "vbee"}) == "vieneu"
    assert resolve_tts_engine({"tts_engine": None}) == "vieneu"
    assert resolve_tts_engine({}) == "vieneu"


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


def test_settings_gemini_api_keys():
    from backend.app.config import Settings

    s1 = Settings(gemini_api_key="key1")
    assert s1.gemini_api_keys == ["key1"]

    s2 = Settings(gemini_api_key="key1, key2; key3\nkey4")
    assert s2.gemini_api_keys == ["key1", "key2", "key3", "key4"]

    s3 = Settings(gemini_api_key=' "keyA", "keyB" ')
    assert s3.gemini_api_keys == ["keyA", "keyB"]

    s4 = Settings(gemini_api_key="")
    assert s4.gemini_api_keys == []


def test_multikey_genai_client_round_robin():
    from backend.app.pipeline import _MultiKeyGenaiClient

    calls = []

    class FakeClient:
        def __init__(self, key):
            self.key = key
            self.models = self

        def generate_content(self, *args, **kwargs):
            calls.append(self.key)
            return f"result_from_{self.key}"

    client = _MultiKeyGenaiClient(["key1", "key2"], client_factory=FakeClient)
    r1 = client.models.generate_content("prompt1")
    r2 = client.models.generate_content("prompt2")
    r3 = client.models.generate_content("prompt3")

    assert r1 == "result_from_key1"
    assert r2 == "result_from_key2"
    assert r3 == "result_from_key1"
    assert calls == ["key1", "key2", "key1"]


def test_multikey_genai_client_failover_on_rate_limit():
    from backend.app.pipeline import _MultiKeyGenaiClient

    class FakeRateLimitError(Exception):
        pass

    calls = []

    class FakeClient:
        def __init__(self, key):
            self.key = key
            self.models = self

        def generate_content(self, *args, **kwargs):
            calls.append(self.key)
            if self.key == "key1":
                raise FakeRateLimitError("429 RESOURCE_EXHAUSTED")
            return "success_from_key2"

    client = _MultiKeyGenaiClient(["key1", "key2"], client_factory=FakeClient)
    result = client.models.generate_content("prompt")
    assert result == "success_from_key2"
    assert calls == ["key1", "key2"]


def test_multikey_genai_client_raises_when_all_fail():
    import pytest
    from backend.app.pipeline import _MultiKeyGenaiClient

    class FakeRateLimitError(Exception):
        pass

    calls = []

    class FakeClient:
        def __init__(self, key):
            self.key = key
            self.models = self

        def generate_content(self, *args, **kwargs):
            calls.append(self.key)
            raise FakeRateLimitError("429 RESOURCE_EXHAUSTED")

    client = _MultiKeyGenaiClient(["key1", "key2"], client_factory=FakeClient)
    with pytest.raises(FakeRateLimitError):
        client.models.generate_content("prompt")
    assert calls == ["key1", "key2"]


def test_multikey_genai_client_does_not_rotate_on_non_rate_limit_error():
    import pytest
    from backend.app.pipeline import _MultiKeyGenaiClient

    calls = []

    class FakeClient:
        def __init__(self, key):
            self.key = key
            self.models = self

        def generate_content(self, *args, **kwargs):
            calls.append(self.key)
            raise ValueError("bad argument")

    client = _MultiKeyGenaiClient(["key1", "key2"], client_factory=FakeClient)
    with pytest.raises(ValueError, match="bad argument"):
        client.models.generate_content("prompt")
    assert calls == ["key1"]


def test_settings_deepseek_config():
    from backend.app.config import Settings

    s1 = Settings(deepseek_api_key="ds-key1, ds-key2", translate_engine="deepseek")
    assert s1.deepseek_api_keys == ["ds-key1", "ds-key2"]
    assert s1.effective_translate_engine == "deepseek"
    assert s1.active_translate_model == "deepseek-v4-pro"
    assert s1.cloud_ready is True

    s2 = Settings(deepseek_api_key="ds-key1", gemini_api_key="", translate_engine="auto")
    assert s2.effective_translate_engine == "deepseek"

    s3 = Settings(deepseek_api_key="", gemini_api_key="gem-key", translate_engine="auto")
    assert s3.effective_translate_engine == "gemini"
    assert s3.active_translate_model == "gemini-3.8-flash"


def test_openai_compat_engine_and_payload_omit_deepseek_reasoning():
    from types import SimpleNamespace
    from backend.app.config import Settings
    from backend.app.pipeline import _MultiKeyDeepSeekClient

    settings = Settings(
        translate_engine="openai_compat",
        openai_compat_api_key="proxy-key",
        openai_compat_model="video-dub-gemini-flash",
        openai_compat_base_url="http://127.0.0.1:23334/v1",
    )
    assert settings.effective_translate_engine == "openai_compat"
    assert settings.active_translate_model == "video-dub-gemini-flash"
    assert not any(item["key"] == "translator_key" for item in settings.missing_requirements)

    captured = {}
    class FakeHttpClient:
        def post(self, url, headers, json, timeout):
            captured.update(url=url, headers=headers, payload=json)
            return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": "OK"}}]}, text="")

    client = _MultiKeyDeepSeekClient(
        api_keys=["proxy-key"], base_url=settings.openai_compat_base_url,
        model=settings.openai_compat_model, http_client=FakeHttpClient(),
        supports_reasoning_effort=False,
    )
    client.models.generate_content("hello", config=SimpleNamespace(temperature=0.2, thinking_config=SimpleNamespace(thinking_budget=0)))
    assert captured["url"] == "http://127.0.0.1:23334/v1/chat/completions"
    assert "reasoning_effort" not in captured["payload"]


def test_multikey_deepseek_client_round_robin():
    from types import SimpleNamespace
    from backend.app.pipeline import _MultiKeyDeepSeekClient

    calls = []

    class FakeHttpClient:
        def post(self, url, headers, json, timeout):
            key = headers["Authorization"].replace("Bearer ", "")
            calls.append(key)
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"choices": [{"message": {"content": f"translated_by_{key}"}}]},
                text="",
            )

    client = _MultiKeyDeepSeekClient(
        api_keys=["ds-1", "ds-2"],
        model="deepseek-v4-pro",
        http_client=FakeHttpClient(),
    )
    r1 = client.models.generate_content("hello")
    r2 = client.models.generate_content("world")
    r3 = client.models.generate_content("again")

    assert r1.text == "translated_by_ds-1"
    assert r2.text == "translated_by_ds-2"
    assert r3.text == "translated_by_ds-1"
    assert calls == ["ds-1", "ds-2", "ds-1"]


def test_multikey_deepseek_client_failover_on_429():
    from types import SimpleNamespace
    from backend.app.pipeline import _MultiKeyDeepSeekClient

    calls = []

    class FakeHttpClient:
        def post(self, url, headers, json, timeout):
            key = headers["Authorization"].replace("Bearer ", "")
            calls.append(key)
            if key == "ds-1":
                return SimpleNamespace(
                    status_code=429,
                    text="429 RateLimit/QuotaExceeded",
                )
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"choices": [{"message": {"content": "ok_from_ds-2"}}]},
                text="",
            )

    client = _MultiKeyDeepSeekClient(
        api_keys=["ds-1", "ds-2"],
        model="deepseek-v4-pro",
        http_client=FakeHttpClient(),
    )
    resp = client.models.generate_content("test prompt")
    assert resp.text == "ok_from_ds-2"
    assert calls == ["ds-1", "ds-2"]


def test_pipeline_selects_deepseek_client(monkeypatch):
    from types import SimpleNamespace
    from backend.app import pipeline as pipeline_module
    from backend.app.pipeline import Pipeline, _MultiKeyDeepSeekClient

    monkeypatch.setattr(
        pipeline_module,
        "settings",
        SimpleNamespace(
            effective_translate_engine="deepseek",
            deepseek_api_keys=["ds-test-key"],
            deepseek_model="deepseek-v4-pro",
            deepseek_base_url="https://api.deepseek.com/chat/completions",
        ),
    )
    pipe = Pipeline(hook=None)
    client = pipe._translation_client()
    assert isinstance(client, _MultiKeyDeepSeekClient)



def test_missing_config_no_longer_silently_enables_demo_mode():
    """Demo mode chỉ bật khi người dùng chủ động yêu cầu.

    Bản cũ: `effective_demo_mode = demo_mode or not (cloud_ready and ffmpeg and ffprobe)`
    -> thiếu bất cứ thứ gì cũng trả kết quả giả mà không báo lỗi.
    """
    from backend.app.config import Settings

    broken = Settings(gemini_api_key="", deepseek_api_key="", google_project="")
    assert broken.effective_demo_mode is False
    assert broken.cloud_ready is False
    assert [item["key"] for item in broken.missing_requirements] == ["translator_key"]

    assert Settings(demo_mode=True).effective_demo_mode is True


def test_missing_requirements_flags_google_stt_without_bucket():
    from backend.app.config import Settings

    keys = [
        item["key"]
        for item in Settings(gemini_api_key="k", stt_engine="google", gcs_bucket="").missing_requirements
    ]
    assert keys == ["google_stt"]
    # Whisper chạy local nên không cần bucket.
    assert Settings(gemini_api_key="k", stt_engine="whisper").missing_requirements == []


def test_cleanup_job_intermediates_keeps_what_reexport_needs(tmp_path, monkeypatch):
    """Dọn rác nhưng phải giữ nền + giọng từng câu, nếu không sửa một câu là phải chạy lại
    tách nền và TTS toàn bộ video."""
    from backend.app import pipeline as pipe

    work = tmp_path / "jobs" / "job1"
    (work / "demucs" / "htdemucs" / "source").mkdir(parents=True)
    stem = work / "demucs" / "htdemucs" / "source"

    rac = {
        work / "narration-batch-0.wav": 1000,
        work / "narration-batch-30.wav": 2000,
        work / "filter-batch-0.txt": 10,
        work / "filter-complex.txt": 10,
        work / "source.wav": 500,
        stem / "vocals.wav": 300,
    }
    giu = [work / "dubbed-vi.mp4", work / "subtitles-vi.srt", work / "segment-0001.wav", stem / "no_vocals.wav"]
    for path, size in rac.items():
        path.write_bytes(b"x" * size)
    for path in giu:
        path.write_bytes(b"k")

    monkeypatch.setattr(pipe, "settings", SimpleNamespace(jobs_dir=tmp_path / "jobs"))

    freed = pipe.cleanup_job_intermediates("job1")
    assert freed == sum(rac.values())
    assert not any(path.exists() for path in rac)
    assert all(path.exists() for path in giu), "đã xoá nhầm file cần cho lần export sau"

    # Job không tồn tại -> không nổ.
    assert pipe.cleanup_job_intermediates("khong-co") == 0


def test_delete_job_files_removes_everything(tmp_path, monkeypatch):
    from backend.app import pipeline as pipe

    jobs = tmp_path / "jobs"
    uploads = tmp_path / "uploads"
    (jobs / "job1" / "demucs").mkdir(parents=True)
    uploads.mkdir()
    (jobs / "job1" / "dubbed-vi.mp4").write_bytes(b"x" * 70)
    (jobs / "job1" / "demucs" / "no_vocals.wav").write_bytes(b"y" * 30)
    (uploads / "job1.mp4").write_bytes(b"z" * 40)
    (uploads / "job2.mp4").write_bytes(b"w" * 999)  # job khác, KHÔNG được đụng

    monkeypatch.setattr(pipe, "settings", SimpleNamespace(jobs_dir=jobs, uploads_dir=uploads))
    assert pipe.delete_job_files("job1") == 140
    assert not (jobs / "job1").exists()
    assert not (uploads / "job1.mp4").exists()
    assert (uploads / "job2.mp4").exists()


def test_read_response_usage_handles_both_provider_shapes():
    """Gemini báo `usage_metadata`, DeepSeek báo `usage` kiểu OpenAI. Cả hai đều tính
    prompt ĐÃ GỒM token cache -> phải trừ ra, không thì tính tiền cache theo giá full."""
    from backend.app.pipeline import read_response_usage

    gemini = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=1000, candidates_token_count=200, cached_content_token_count=400
        )
    )
    usage = read_response_usage(gemini)
    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (600, 400, 200)

    deepseek = SimpleNamespace(
        usage={"prompt_tokens": 1000, "completion_tokens": 200, "prompt_cache_hit_tokens": 400}
    )
    assert read_response_usage(deepseek) == usage

    # Response không mang thông tin token -> None (để meter vẫn đếm được số lời gọi).
    assert read_response_usage(SimpleNamespace(text="xin chào")) is None


def test_estimate_usd_prices_cache_cheaper_and_skips_unknown_models():
    from backend.app.pipeline import TokenUsage, estimate_usd

    usage = TokenUsage(input_tokens=600, cached_input_tokens=400, output_tokens=200)
    # 600*0.75 + 400*0.075 + 200*3.75, chia 1 triệu.
    assert estimate_usd(usage, "gemini-3.8-flash") == pytest.approx(0.00123)
    # Cache rẻ hơn hẳn: dồn hết sang cache thì rẻ đi nhiều lần.
    cached_only = TokenUsage(cached_input_tokens=1000)
    full_price = TokenUsage(input_tokens=1000)
    assert estimate_usd(cached_only, "gemini-3.8-flash") < estimate_usd(full_price, "gemini-3.8-flash") / 9
    # Model lạ -> không bịa ra giá.
    assert estimate_usd(usage, "model-nao-do") is None


def test_usage_meter_accumulates_per_stage_and_is_thread_safe():
    from concurrent.futures import ThreadPoolExecutor
    from backend.app.pipeline import UsageMeter

    meter = UsageMeter(model="gemini-3.8-flash")
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=10, cached_content_token_count=0
        )
    )
    # Các lô dịch chạy song song -> cộng dồn phải an toàn với nhiều luồng.
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: meter.record("translate", response), range(200)))
    meter.record("review", response)

    snap = meter.snapshot()
    assert snap["stages"]["translate"] == {
        "input_tokens": 20000,
        "cached_input_tokens": 0,
        "output_tokens": 2000,
        "calls": 200,
    }
    assert snap["total"]["calls"] == 201
    assert snap["vnd"] > 0 and snap["model"] == "gemini-3.8-flash"


def test_merge_cost_adds_up_across_phases_instead_of_overwriting():
    """Job dịch một lần rồi có thể export NHIỀU lần (mỗi lần viết-lại câu đều gọi LLM).
    Ghi đè sẽ làm mất phần đã tiêu trước đó = tính thiếu tiền."""
    from backend.app.pipeline import merge_cost

    phase1 = {
        "model": "gemini-3.8-flash",
        "stages": {"translate": {"input_tokens": 1000, "output_tokens": 500, "calls": 3}},
    }
    phase2 = {
        "model": "gemini-3.8-flash",
        "stages": {
            "translate": {"input_tokens": 200, "output_tokens": 100, "calls": 1},
            "rewrite": {"input_tokens": 50, "output_tokens": 20, "calls": 2},
        },
    }
    merged = merge_cost(phase1, phase2)
    assert merged["stages"]["translate"]["input_tokens"] == 1200
    assert merged["stages"]["translate"]["calls"] == 4
    assert merged["stages"]["rewrite"]["calls"] == 2
    assert merged["total"] == {
        "input_tokens": 1250,
        "cached_input_tokens": 0,
        "output_tokens": 620,
        "calls": 6,
    }
    assert merged["vnd"] > 0

    # Chưa có gì trước đó / dữ liệu cũ sai định dạng -> không nổ.
    assert merge_cost(None, phase2)["stages"]["rewrite"]["calls"] == 2
    assert merge_cost({"stt": 1680, "total": 12180}, phase2)["total"]["calls"] == 3


def test_read_response_usage_counts_thinking_tokens_as_output():
    """thoughts_token_count nằm NGOÀI candidates_token_count nhưng vẫn bị tính giá output.
    Pipeline đã tắt thinking ở cả 4 call site, nhưng model vẫn có thể trả thoughts (nhánh lùi
    khi không hỗ trợ thinking_budget=0) — bỏ qua trường này là tính thiếu tiền."""
    from backend.app.pipeline import read_response_usage

    usage = read_response_usage(
        SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=500,
                candidates_token_count=100,
                cached_content_token_count=0,
                thoughts_token_count=800,
            )
        )
    )
    assert usage.output_tokens == 900
    assert usage.input_tokens == 500


def test_deepseek_reasoning_payload_forwards_thinking_budget_zero():
    """Pipeline tắt thinking qua thinking_config (cú pháp google-genai); client DeepSeek
    trước đây chỉ đọc temperature nên yêu cầu đó bị đánh rơi và mỗi lời gọi vẫn trả tiền cho
    reasoning. Đo A/B trên API thật: 89 -> 22 token completion."""
    from backend.app.pipeline import deepseek_reasoning_payload

    off = SimpleNamespace(temperature=0.2, thinking_config=SimpleNamespace(thinking_budget=0))
    assert deepseek_reasoning_payload(off) == {"reasoning_effort": "none"}

    # Không khai báo thinking_config -> giữ nguyên hành vi mặc định của model.
    assert deepseek_reasoning_payload(SimpleNamespace(temperature=0.3)) == {}
    assert deepseek_reasoning_payload(None) == {}
    # Có ngân sách thinking > 0 -> tôn trọng, không tự ý tắt.
    assert deepseek_reasoning_payload(SimpleNamespace(thinking_config=SimpleNamespace(thinking_budget=512))) == {}


def test_generate_without_thinking_turns_thinking_off_and_keeps_config():
    """Bốn lời gọi LLM đều là bám chỉ dẫn, không phải suy luận nhiều bước, mà thinking token
    bị tính GIÁ OUTPUT. Trước đây _build_context và _rewrite_shorter bỏ sót: sau khi sửa lỗi
    DeepSeek, riêng _build_context đã chiếm 1.763/1.843 token output của cả job."""
    from backend.app.pipeline import generate_without_thinking

    seen = []

    class _Models:
        def generate_content(self, *, model, contents, config):
            seen.append(config)
            return SimpleNamespace(text="ok")

    client = SimpleNamespace(models=_Models())
    generate_without_thinking(client, "prompt", temperature=0.3)

    assert len(seen) == 1
    assert seen[0].thinking_config.thinking_budget == 0
    assert seen[0].temperature == 0.3  # config của call site không bị helper nuốt mất


def test_generate_without_thinking_falls_back_when_model_rejects_it():
    """Model không hỗ trợ thinking_budget=0 thì phải chạy được với config mặc định, chứ không
    làm hỏng cả bước dịch."""
    from backend.app.pipeline import generate_without_thinking

    seen = []

    class _Models:
        def generate_content(self, *, model, contents, config):
            seen.append(config)
            if getattr(config, "thinking_config", None) is not None:
                raise RuntimeError("400 INVALID_ARGUMENT: thinking_budget not supported")
            return SimpleNamespace(text="ok")

    client = SimpleNamespace(models=_Models())
    assert generate_without_thinking(client, "prompt", temperature=0.2).text == "ok"

    assert len(seen) == 2  # thử tắt thinking -> bị từ chối -> chạy lại không có thinking_config
    assert getattr(seen[1], "thinking_config", None) is None
    assert seen[1].temperature == 0.2


def test_generate_without_thinking_reraises_other_errors_for_backoff():
    """Lỗi quota/mạng phải nổi lên cho _with_backoff xử lý, không được nuốt thành lời gọi thứ hai."""
    from backend.app.pipeline import generate_without_thinking

    calls = []

    class _Models:
        def generate_content(self, *, model, contents, config):
            calls.append(config)
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    with pytest.raises(RuntimeError, match="429"):
        generate_without_thinking(SimpleNamespace(models=_Models()), "prompt", temperature=0.2)
    assert len(calls) == 1
