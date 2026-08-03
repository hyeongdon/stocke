"""ka10004 주식호가 — mrkcond flat 필드 파싱."""
from api.kiwoom_api import KiwoomAPI


SAMPLE_KA10004 = {
    "bid_req_base_tm": "153843",
    "sel_fpr_bid": "+388000",
    "sel_fpr_req": "3077",
    "buy_fpr_bid": "+387500",
    "buy_fpr_req": "535",
    "sel_2th_pre_bid": "+388500",
    "sel_2th_pre_req": "1119",
    "buy_2th_pre_bid": "+387000",
    "buy_2th_pre_req": "3847",
    "sel_3th_pre_bid": "+389000",
    "sel_3th_pre_req": "1837",
    "buy_3th_pre_bid": "+386500",
    "buy_3th_pre_req": "1692",
    "return_code": 0,
    "return_msg": "정상적으로 처리되었습니다",
}


def test_parse_ka10004_flat_fields():
    book = KiwoomAPI.parse_ka10004_orderbook(SAMPLE_KA10004)
    assert len(book) >= 3
    lv1 = book[0]
    assert lv1["level"] == 1
    assert lv1["ask_price"] == 388000
    assert lv1["ask_qty"] == 3077
    assert lv1["bid_price"] == 387500
    assert lv1["bid_qty"] == 535
    lv2 = book[1]
    assert lv2["ask_price"] == 388500
    assert lv2["bid_qty"] == 3847


def test_parse_ka10004_legacy_askp_keys():
    book = KiwoomAPI.parse_ka10004_orderbook({
        "askp1": "1000",
        "askp_rsqn1": "10",
        "bidp1": "990",
        "bidp_rsqn1": "20",
    })
    assert len(book) == 1
    assert book[0]["ask_price"] == 1000
    assert book[0]["bid_qty"] == 20


def test_parse_ka10004_empty():
    assert KiwoomAPI.parse_ka10004_orderbook({}) == []
    assert KiwoomAPI.parse_ka10004_orderbook({"return_code": 0}) == []


def test_pig_verdict_from_parsed_book():
    from utils.jongga_engine import pig_orderbook_verdict

    book = KiwoomAPI.parse_ka10004_orderbook(SAMPLE_KA10004)
    # bid 535+3847+1692 vs ask 3077+1119+1837 — levels=3
    v, d = pig_orderbook_verdict(book, levels=3, min_ratio=1.5)
    assert d["bid_qty"] == 535 + 3847 + 1692
    assert d["ask_qty"] == 3077 + 1119 + 1837
    assert d["ratio"] is not None
    assert v in ("buy", "sell", "neutral")
