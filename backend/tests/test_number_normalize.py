from app.pipeline import apply_tts_pronunciations, normalize_numbers_for_tts as n


def test_english_names_use_native_multilingual_phonemizer():
    from vieneu_utils.phonemize_text import phonemize_text_with_emotions

    text = "Tôi dùng Higgsfield, Blender và Photoshop."
    assert apply_tts_pronunciations(text) == text
    phones = phonemize_text_with_emotions(text)
    assert "hˈɪɡz" in phones and "blˈɛnd" in phones and "ʃ" in phones


def test_pronunciation_overrides_do_not_change_vietnamese_ai():
    assert apply_tts_pronunciations("AI và Higgsfield, LEGO") == "ây ai và Higgsfield, lê gô"
    assert apply_tts_pronunciations("ai hỏi về higgsfield") == "ai hỏi về higgsfield"
    assert apply_tts_pronunciations("SAIGON và Higgsfields") == "SAIGON và Higgsfields"



def test_thousand_separators_and_currency():
    assert n("15000 USD.") == "mười lăm nghìn đô la."
    assert n("15.000 USD") == "mười lăm nghìn đô la"
    assert n("$15,000 mỗi tháng") == "mười lăm nghìn đô la mỗi tháng"
    assert n("1.250.000 đồng") == "một triệu hai trăm năm mươi nghìn đồng"


def test_decimal_percent_range():
    assert n("tăng 1,5 lần, 20%") == "tăng một phẩy năm lần, hai mươi phần trăm"
    assert n("3-4 giờ") == "ba đến bốn giờ"
    assert n("21, 101, 1005") == "hai mươi mốt, một trăm lẻ một, một nghìn không trăm lẻ năm"
