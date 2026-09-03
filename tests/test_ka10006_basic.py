"""ka10006 주식시세 — mrkcond flat(close_pric/flu_rt) 파싱."""
from api.kiwoom_api import KiwoomAPI


# 실로그(동진쎄미켐 등)와 동일한 flat 키
SAMPLE_KA10006_FLAT = {
    "date": "20260804",
    "open_pric": "39000",
    "high_pric": "41000",
    "low_pric": "38500",
    "close_pric": "+40600",
    "pre": "+1800",
    "flu_rt": "+4.64",
    "trde_qty": "1234567",
    "trde_prica": "50000000000",
    "cntr_str": "120.5",
    "return_code": 0,
    "return_msg": "정상적으로 처리되었습니다",
}


def test_parse_ka10006_flat_close_pric_flu_rt():
    parsed = KiwoomAPI.parse_ka10006_basic(SAMPLE_KA10006_FLAT)
    assert parsed["current_price"] == 40600
    assert parsed["change_rate"] == "4.64"
    assert parsed["volume"] == 1234567
    assert parsed["price_diff"] == 1800
    assert parsed["raw_basic"].get("flu_rt") == "+4.64"


def test_parse_ka10006_flat_negative_rate():
    parsed = KiwoomAPI.parse_ka10006_basic({
        "close_pric": "20000",
        "pre": "-500",
        "flu_rt": "-2.44",
        "trde_qty": "100",
    })
    assert parsed["current_price"] == 20000
    assert parsed["change_rate"] == "-2.44"


def test_parse_ka10006_stk_mkprc_wrap():
    parsed = KiwoomAPI.parse_ka10006_basic({
        "stk_mkprc": [{"cur_prc": "1000", "flu_rt": "1.50", "trde_qty": "10"}],
    })
    assert parsed["current_price"] == 1000
    assert parsed["change_rate"] == "1.50"


def test_parse_ka10006_empty():
    parsed = KiwoomAPI.parse_ka10006_basic({})
    assert parsed["current_price"] == 0
    assert parsed["change_rate"] == "0.00" or parsed["change_rate"] == "0"


def test_extract_basic_row_prefers_flat_without_cur_prc():
    row = KiwoomAPI._extract_basic_row(SAMPLE_KA10006_FLAT)
    assert row.get("close_pric") == "+40600"
    assert row.get("flu_rt") == "+4.64"
