import pytest

from app.pipeline import apply_tts_pronunciations, normalize_numbers_for_tts as n

# Logic thuần vẫn chạy ở demo mode khi chưa cài dependency TTS local.
pytest.importorskip("vieneu_utils.phonemize_text")


def test_english_names_use_native_multilingual_phonemizer():
    from vieneu_utils.phonemize_text import phonemize_text_with_emotions

    text = "Tôi dùng Higgsfield, Blender và Photoshop."
    assert apply_tts_pronunciations(text) == text
    phones = phonemize_text_with_emotions(text)
    assert "hˈɪɡz" in phones and "blˈɛnd" in phones and "ʃ" in phones


def test_native_ai_phonemes_are_english():
    from vieneu_utils.phonemize_text import phonemize_text_with_emotions

    assert "ˈeɪ ˈaɪ" in phonemize_text_with_emotions("Tôi dùng AI để tạo ảnh")


def test_english_names_read_english_whatever_the_casing():
    from vieneu_utils.phonemize_text import phonemize_to_chunks

    def phones(text: str) -> str:
        return phonemize_to_chunks(apply_tts_pronunciations(text), max_chars=256)[0].text

    for variant in ("Claude", "CLAUDE", "claude"):
        assert "klˈɔːd" in phones(f"Dùng {variant} nhé"), variant
    for variant in ("HiggsField", "Higgsfield", "HIGGSFIELD", "higgsfield"):
        assert "hˈɪɡz fˈiːld" in phones(f"Dùng {variant} nhé"), variant
    for variant in ("ChatGPT", "CHATGPT", "chatgpt"):
        assert "tʃˈæt dʒˈiː pˈiː tˈiː" in phones(f"Dùng {variant} nhé"), variant
    assert "ˈeɪ ˈaɪ" in phones("Tôi dùng AI")
    for variant in ("iPhone", "IPHONE", "iphone", "Iphone"):
        assert "ˈaɪ fˈoʊn" in phones(f"Dùng {variant} 17 Pro nhé"), variant


def test_mixed_sentence_keeps_vietnamese_ai_and_english_names():
    from vieneu_utils.phonemize_text import phonemize_text_with_emotions

    phones = phonemize_text_with_emotions(
        apply_tts_pronunciations("Ai biết dùng AI, HiggsField và iPhone không?")
    )
    assert "aːj" in phones
    assert "ˈeɪ ˈaɪ" in phones
    assert "hˈɪɡz fˈiːld" in phones
    assert "ˈaɪ fˈoʊn" in phones


def test_native_normalizer_reads_attached_units_correctly():
    from vieneu_utils.phonemize_text import PuncNormalizer

    norm = PuncNormalizer()
    assert norm.normalize(n("Cần 16GB RAM")).rstrip(".") == "cần mười sáu gigabyte ram"
    assert norm.normalize(n("Chạy 10km")).rstrip(".") == "chạy mười ki lô mét"


def test_synthesis_and_fit_retry_only_normalize_spoken_text(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock

    import app.pipeline as pipeline

    np = pytest.importorskip("numpy")
    model = Mock(sample_rate=24000)
    model.infer.return_value = np.zeros(2400, dtype=np.float32)
    monkeypatch.setattr(pipeline, "_vieneu_model", model)
    monkeypatch.setattr(pipeline, "settings", SimpleNamespace(jobs_dir=tmp_path))
    monkeypatch.setattr(pipeline, "get_job", lambda *a, **kw: {"voice": "Minh Quân"})
    monkeypatch.setattr(pipeline, "vieneu_preset_voices", lambda: [{"id": "Minh Quân"}])
    monkeypatch.setattr(pipeline, "resolve_segment_voice", lambda *a, **kw: {"voice": "Minh Quân"})
    monkeypatch.setattr(pipeline, "_trim_silence", lambda path: None)
    durations = iter([10.0, 1.0])
    monkeypatch.setattr(pipeline, "probe_audio", lambda path: next(durations))
    updates = Mock()
    monkeypatch.setattr(pipeline, "update_segment", updates)
    p = pipeline.Pipeline(Mock())
    original = "AI tạo ảnh với HIGGSFIELD và IPHONE."
    shorter = "AI dùng HIGGSFIELD trên IPHONE."
    rewrite = Mock(return_value=shorter)
    monkeypatch.setattr(p, "_rewrite_shorter", rewrite)
    segment = {"id": "seg", "position": 1, "start": 0.0, "end": 3.0,
               "translated_text": original, "source_text": "AI uses Higgsfield on iPhone."}

    p._synthesize_segment("sample", segment)

    assert [call.args[0] for call in model.infer.call_args_list] == [
        "AI tạo ảnh với Higgsfield và iPhone.", "AI dùng Higgsfield trên iPhone."
    ]
    assert rewrite.call_args.args[1] == original
    assert segment["translated_text"] == original
    assert updates.call_args.kwargs["translated_text"] == shorter
