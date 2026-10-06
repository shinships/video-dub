import pytest

from app.pipeline import apply_tts_pronunciations, normalize_numbers_for_tts as n


def test_pronunciation_overrides_keep_native_ai_and_vietnamese_ai():
    # AI không còn override: phonemizer gốc đã đọc /eɪ aɪ/ đúng hơn "ây ai" qua G2P Việt.
    assert apply_tts_pronunciations("AI và Higgsfield, LEGO") == "AI và Higgsfield, lê gô"
    assert apply_tts_pronunciations("ai hỏi về higgsfield") == "ai hỏi về Higgsfield"
    assert apply_tts_pronunciations("SAIGON và Higgsfields") == "SAIGON và Higgsfields"


def test_shout_normalisation_keeps_acronyms_and_vietnamese_places():
    assert apply_tts_pronunciations("NVIDIA và OPENAI") == "Nvidia và OpenAI"
    text = "HTTPS, WWDC, GPU, NAION, ASCII, SAIGON, HANOI, DANANG, VIETNAM"
    assert apply_tts_pronunciations(text) == text


@pytest.mark.parametrize("variant", ["HiggsField", "Higgsfield", "HIGGSFIELD", "higgsfield"])
def test_higgsfield_casing_and_punctuation(variant):
    assert apply_tts_pronunciations(f'AI: "{variant}", nhé!') == 'AI: "Higgsfield", nhé!'


@pytest.mark.parametrize("variant", ["iPhone", "IPHONE", "iphone", "Iphone"])
def test_iphone_with_model_number(variant):
    assert apply_tts_pronunciations(f"Dùng {variant} 17 Pro.") == "Dùng iPhone 17 Pro."


def test_pronunciation_matches_whole_words_and_is_idempotent():
    untouched = "Higgsfields myiphone iPhone17 tiếnghiggsfield higgsfieldViệt SAIGON ai Ai"
    assert apply_tts_pronunciations(untouched) == untouched
    text = "AI dùng HIGGSFIELD, IPHONE và CLAUDE; ai dùng NVIDIA?"
    spoken = "AI dùng Higgsfield, iPhone và Claude; ai dùng Nvidia?"
    assert apply_tts_pronunciations(text) == spoken
    assert apply_tts_pronunciations(spoken) == spoken


def test_numbers_attached_to_latin_letters_are_left_for_native_normalizer():
    # Đổi "16" -> "mười sáu" trước sẽ ra "mười sáuGB" rồi bị đọc từng chữ cái.
    assert n("Cần 16GB RAM") == "Cần 16GB RAM"
    assert n("Mạng 5G, video 1080p, 60fps, tăng 3x") == "Mạng 5G, video 1080p, 60fps, tăng 3x"
    assert n("Chạy 10km, 5kg") == "Chạy 10km, 5kg"
    assert n("Dùng GPT-4o và GPT-4") == "Dùng GPT-4o và GPT-bốn"
    assert n("Bản v2.0") == "Bản v2.0"


def test_time_and_date_left_for_native_normalizer():
    assert n("Lúc 3:30 PM") == "Lúc 3:30 PM"
    assert n("Mở 24/7") == "Mở 24/7"


def test_magnitude_decades_and_ordinals():
    assert n("$5B và $300M") == "năm tỷ đô la và ba trăm triệu đô la"
    assert n("300M người dùng") == "ba trăm triệu người dùng"
    assert n("Video 4K") == "Video 4K"  # K không đổi: có thể là độ phân giải
    assert n("thập niên 90s, năm 1990s") == "thập niên chín mươi, năm chín mươi"
    assert n("1st và 2nd") == "một và hai"


def test_thousand_separators_and_currency():
    assert n("15000 USD.") == "mười lăm nghìn đô la."
    assert n("15.000 USD") == "mười lăm nghìn đô la"
    assert n("$15,000 mỗi tháng") == "mười lăm nghìn đô la mỗi tháng"
    assert n("1.250.000 đồng") == "một triệu hai trăm năm mươi nghìn đồng"


def test_decimal_percent_range():
    assert n("tăng 1,5 lần, 20%") == "tăng một phẩy năm lần, hai mươi phần trăm"
    assert n("3-4 giờ") == "ba đến bốn giờ"
    assert n("21, 101, 1005") == "hai mươi mốt, một trăm lẻ một, một nghìn không trăm lẻ năm"
