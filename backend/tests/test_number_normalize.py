from app.pipeline import normalize_numbers_for_tts as n


def test_thousand_separators_and_currency():
    assert n("15000 USD.") == "mười lăm nghìn đô la."
    assert n("15.000 USD") == "mười lăm nghìn đô la"
    assert n("$15,000 mỗi tháng") == "mười lăm nghìn đô la mỗi tháng"
    assert n("1.250.000 đồng") == "một triệu hai trăm năm mươi nghìn đồng"


def test_decimal_percent_range():
    assert n("tăng 1,5 lần, 20%") == "tăng một phẩy năm lần, hai mươi phần trăm"
    assert n("3-4 giờ") == "ba đến bốn giờ"
    assert n("21, 101, 1005") == "hai mươi mốt, một trăm lẻ một, một nghìn không trăm lẻ năm"
