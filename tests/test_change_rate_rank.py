"""상따 유니버스 — ka10027 전일대비등락률상위 파싱·필터."""
from api.kiwoom_api import (
    KiwoomAPI,
    SANGTTA_CHANGE_RATE_RANK_FILTERS,
    SANGTTA_UNIVERSE_MIN_CHANGE_RATE,
)


def test_sangtta_universe_filter_defaults():
    assert SANGTTA_CHANGE_RATE_RANK_FILTERS["stk_cnd"] == "1"  # 관리종목제외
    assert SANGTTA_CHANGE_RATE_RANK_FILTERS["pric_cnd"] == "8"  # 1천원이상
    assert SANGTTA_CHANGE_RATE_RANK_FILTERS["trde_prica_cnd"] == "100"  # 10억이상
    assert SANGTTA_CHANGE_RATE_RANK_FILTERS["stex_tp"] == "1"  # KRX
    assert SANGTTA_UNIVERSE_MIN_CHANGE_RATE == 13.0


def test_parse_change_rate_rank_row():
    row = KiwoomAPI._parse_change_rate_rank_row({
        "stk_cd": "A005930",
        "stk_nm": "삼성전자",
        "cur_prc": "+70000",
        "pred_pre": "+5000",
        "flu_rt": "+14.50",
        "now_trde_qty": "123456",
        "stk_cls": "",
    })
    assert row["stock_code"] == "005930"
    assert row["stock_name"] == "삼성전자"
    assert row["current_price"] == 70000
    assert row["change_rate"] == 14.5
    assert row["volume"] == 123456
    assert row["product_type"] == "STOCK"


def test_parse_change_rate_rank_excludes_etf_name_family():
    etf = KiwoomAPI._parse_change_rate_rank_row({
        "stk_cd": "069500",
        "stk_nm": "KODEX 200",
        "cur_prc": "30000",
        "pred_pre": "1000",
        "flu_rt": "15.00",
        "now_trde_qty": "1000",
    })
    assert KiwoomAPI._is_etf_family_item(etf["stock_name"], etf["product_type"])
    assert not KiwoomAPI._is_screener_stock(etf["stock_name"], etf["product_type"])


def test_min_change_13_keep_rule():
    """유니버스 기본: 등락률≥13% 만 후보."""
    rows = [
        {"change_rate": 15.2},
        {"change_rate": 13.0},
        {"change_rate": 12.9},
        {"change_rate": -1.0},
    ]
    floor = SANGTTA_UNIVERSE_MIN_CHANGE_RATE
    kept = [r for r in rows if float(r.get("change_rate") or 0) >= floor]
    assert [r["change_rate"] for r in kept] == [15.2, 13.0]


def test_cap_by_trade_amount_prefers_trade_amount():
    items = [
        {"stock_code": "A", "trade_amount": 100, "volume": 9, "current_price": 1},
        {"stock_code": "B", "trade_amount": 500, "volume": 1, "current_price": 1},
        {"stock_code": "C", "trade_amount": 200, "volume": 1, "current_price": 1},
    ]
    capped = KiwoomAPI.cap_by_trade_amount(items, 2)
    assert [x["stock_code"] for x in capped] == ["B", "C"]


def test_cap_by_trade_amount_falls_back_to_volume_times_price():
    items = [
        {"stock_code": "A", "volume": 10, "current_price": 1000},
        {"stock_code": "B", "volume": 50, "current_price": 1000},
        {"stock_code": "C", "volume": 20, "current_price": 1000},
    ]
    capped = KiwoomAPI.cap_by_trade_amount(items, 2)
    assert [x["stock_code"] for x in capped] == ["B", "C"]
