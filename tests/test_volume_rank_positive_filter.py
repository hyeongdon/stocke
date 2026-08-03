"""거래대금순 스크리너 — 등락률 하한 필터."""
from api.kiwoom_api import KiwoomAPI


def test_parse_volume_rank_change_rate_sign():
    up = KiwoomAPI._parse_volume_rank_row({
        "stk_cd": "005930", "stk_nm": "삼성전자",
        "cur_prc": "70000", "pred_pre": "1000", "flu_rt": "1.45",
        "trde_qty": "1000", "trde_amt": "100",
    })
    down = KiwoomAPI._parse_volume_rank_row({
        "stk_cd": "000660", "stk_nm": "SK하이닉스",
        "cur_prc": "200000", "pred_pre": "-3000", "flu_rt": "-1.50",
        "trde_qty": "1000", "trde_amt": "100",
    })
    flat = KiwoomAPI._parse_volume_rank_row({
        "stk_cd": "035420", "stk_nm": "NAVER",
        "cur_prc": "200000", "pred_pre": "0", "flu_rt": "0.00",
        "trde_qty": "1000", "trde_amt": "100",
    })
    assert up["change_rate"] > 0
    assert down["change_rate"] < 0
    assert flat["change_rate"] == 0


def test_positive_change_keep_rule():
    """스크리너 폴백: 등락률>0 만 후보."""
    rows = [
        {"change_rate": 2.1},
        {"change_rate": 0.0},
        {"change_rate": -3.4},
        {"change_rate": 0.01},
    ]
    kept = [r for r in rows if float(r.get("change_rate") or 0) > 0]
    assert [r["change_rate"] for r in kept] == [2.1, 0.01]


def test_min_change_rate_33_keep_rule():
    """스크리너 기본: 등락률≥3.3% 만 후보 (매수 3.5% 전 1차 축소)."""
    rows = [
        {"change_rate": 3.5},
        {"change_rate": 3.3},
        {"change_rate": 3.29},
        {"change_rate": 1.0},
        {"change_rate": -0.5},
        {"change_rate": 0.0},
    ]
    floor = 3.3
    kept = [r for r in rows if float(r.get("change_rate") or 0) >= floor]
    assert [r["change_rate"] for r in kept] == [3.5, 3.3]


def test_max_change_rate_15_overheat_cut():
    """스크리너 과열컷: 등락률≥15% 제외."""
    rows = [
        {"change_rate": 14.99},
        {"change_rate": 15.0},
        {"change_rate": 18.2},
        {"change_rate": 3.5},
    ]
    floor, ceil = 3.3, 15.0
    kept = [
        r for r in rows
        if floor <= float(r.get("change_rate") or 0) < ceil
    ]
    assert [r["change_rate"] for r in kept] == [14.99, 3.5]


def test_min_trade_amount_20eok_keep_rule():
    """스크리너 기본: 당일 거래대금 ≥20억만 후보 (trde_amt 백만원 단위)."""
    rows = [
        {"trade_amount": 2500},   # 25억
        {"trade_amount": 2000},   # 20억
        {"trade_amount": 1999},   # 19.99억
        {"trade_amount": 100},    # 1억
        {"trade_amount": 0},
    ]
    floor_m = 20.0 * 100.0  # 억원 → 백만원
    kept = [r for r in rows if float(r.get("trade_amount") or 0) >= floor_m]
    assert [r["trade_amount"] for r in kept] == [2500, 2000]
