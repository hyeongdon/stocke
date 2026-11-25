from utils.rsi_ladder import (
    cancel_sell_hold,
    evaluate_exit,
    evaluate_sell_ladder,
    is_take_profit,
    merge_profile,
    normalize_sell_ladder,
    sell_reason_code,
    sell_rule_label,
)


def _plan(levels, **kw):
    p = {
        "id": "p1", "code": "005930", "name": "삼성전자", "budget": 10_000_000,
        "profile": merge_profile({}), "status": "ACTIVE", "fills": [], "sells": [],
        "sell_flags": {}, "sell_arm_flags": {}, "armed": False, "base_qty": 0, "peak_pnl_pct": 0.0,
        "pending_buy": None, "pending_sell": None,
        "sell_ladder": {"enabled": True, "arm_pct": 0, "levels": levels},
    }
    p.update(kw)
    return p


LEVELS = [
    {"type": "price", "price": 11000, "pct": 30},
    {"type": "price", "price": 12000, "pct": 30},
    {"type": "ma", "ma": 20, "offset": -1, "pct": 40},
]


def test_normalize_drops_invalid_levels():
    sl = normalize_sell_ladder({"enabled": True, "levels": [
        {"type": "price", "price": 0, "pct": 50},
        {"type": "ma", "ma": 7, "pct": 50},
        {"type": "ma", "ma": 60, "offset": 99, "pct": 150},
    ]})
    assert sl["levels"] == [{
        "type": "ma", "ma": 60, "offset": 30.0, "pct": 100.0, "side": "break", "arm": "none",
    }]
    assert sl["arm_pct"] == 0.0
    assert normalize_sell_ladder({"enabled": True, "levels": []})["enabled"] is False


def test_normalize_side_arm_and_arm_pct():
    sl = normalize_sell_ladder({"enabled": True, "arm_pct": 15, "levels": [
        {"type": "ma", "ma": 20, "pct": 50, "side": "break", "arm": "cross"},
        {"type": "price", "price": 12000, "pct": 50, "side": "break"},
    ]})
    assert sl["arm_pct"] == 15.0
    assert sl["levels"][0]["side"] == "break" and sl["levels"][0]["arm"] == "cross"
    assert sl["levels"][1]["side"] == "break" and sl["levels"][1]["arm"] == "none"


def test_price_levels_fire_without_arming():
    ex = evaluate_exit(_plan(LEVELS), price=11500, avg=10000, hold=100, ma={"ma20": 9000})
    assert ex["sells"] == [{"rule": "L1:P11000", "qty": 30}]
    assert ex["base_qty"] == 100
    assert ex["flags"] == {"L1:P11000": True}


def test_ma_level_breakdown_and_price_only_skips_ma():
    plan = _plan(LEVELS)
    ma = {"ma20": 10000}
    ex = evaluate_sell_ladder(plan, price=9800, hold=100, ma=ma)
    assert ex["sells"] == [{"rule": "L3:M20-1", "qty": 40}]
    assert evaluate_sell_ladder(plan, price=9800, hold=100, ma=ma, price_only=True)["sells"] == []
    assert evaluate_sell_ladder(plan, price=9950, hold=100, ma=ma)["sells"] == []


def test_last_remaining_level_sells_everything():
    plan = _plan(LEVELS, base_qty=100, sell_flags={"L1:P11000": True, "L3:M20-1": True})
    ex = evaluate_sell_ladder(plan, price=12500, hold=33, ma={"ma20": 10000})
    assert ex["sells"] == [{"rule": "L2:P12000", "qty": 33}]


def test_rule_labels_codes_and_grace():
    assert sell_rule_label("L1:P11000") == "매도사다리 1차 11,000원 도달"
    assert sell_rule_label("L1:P11000v") == "매도사다리 1차 11,000원 이탈"
    assert sell_rule_label("L3:M20-1") == "매도사다리 3차 MA20 -1% 이탈"
    assert sell_rule_label("L2:M60") == "매도사다리 2차 MA60 이탈"
    assert sell_rule_label("L2:M60^") == "매도사다리 2차 MA60 도달"
    assert sell_reason_code("L2:M60") == "LADDER_SELL"
    assert sell_reason_code("S1") == "LADDER_S1"
    assert is_take_profit(["L1:P11000"])


def test_cancel_hold_restores_ladder_flag():
    plan = _plan(LEVELS, status="EXITING", sell_flags={"L1:P11000": True},
                 pending_sell={"status": "HOLD", "rules": ["L1:P11000"], "status_before": "ACTIVE"})
    assert cancel_sell_hold(plan)
    assert plan["sell_flags"] == {}
    assert plan["status"] == "ACTIVE"


def test_disabled_ladder_falls_back_to_s_rules():
    plan = _plan(LEVELS)
    plan["sell_ladder"]["enabled"] = False
    ex = evaluate_exit(plan, price=11500, avg=10000, hold=100, ma={"ma10": 9000, "ma15": 9000, "ma20": 9000})
    assert ex["sells"] == [] and "ladder" not in ex


def test_ma_break_cross_arm_blocks_reverse_array():
    """역배열(이미 MA 아래)에서는 선접촉 무장 전에는 이탈 매도하지 않는다."""
    levels = [{"type": "ma", "ma": 20, "pct": 100, "side": "break", "arm": "cross"}]
    plan = _plan(levels)
    ma = {"ma20": 10000}
    ex = evaluate_sell_ladder(plan, price=9000, hold=100, ma=ma, avg=8000)
    assert ex["sells"] == []
    assert ex["arm_flags"] == {}

    # 먼저 선 위로 올라가면 무장만
    plan2 = _plan(levels)
    armed = evaluate_sell_ladder(plan2, price=10500, hold=100, ma=ma, avg=8000)
    assert armed["sells"] == []
    assert armed["arm_flags"] == {"L1:M20": True}

    # 무장 후 이탈하면 매도
    plan3 = _plan(levels, sell_arm_flags={"L1:M20": True})
    fired = evaluate_sell_ladder(plan3, price=9000, hold=100, ma=ma, avg=8000)
    assert fired["sells"] == [{"rule": "L1:M20", "qty": 100}]


def test_arm_pct_gates_break_but_not_reach():
    levels = [
        {"type": "price", "price": 11000, "pct": 40, "side": "reach"},
        {"type": "ma", "ma": 20, "pct": 60, "side": "break", "arm": "none"},
    ]
    plan = _plan(levels, sell_ladder={"enabled": True, "arm_pct": 15, "levels": levels})
    ma = {"ma20": 10000}
    # +10% — 도달은 되고 이탈(MA)은 무장 전
    ex = evaluate_sell_ladder(plan, price=11000, hold=100, ma=ma, avg=10000)
    assert round(ex["peak_pct"], 4) == 10.0
    assert ex["sells"] == [{"rule": "L1:P11000", "qty": 40}]
    assert ex["armed"] is False

    # +15% peak — MA 이탈 가능
    plan2 = _plan(levels, sell_ladder={"enabled": True, "arm_pct": 15, "levels": levels},
                  peak_pnl_pct=16.0, sell_flags={"L1:P11000": True}, base_qty=100)
    ex2 = evaluate_sell_ladder(plan2, price=9800, hold=60, ma=ma, avg=10000)
    assert ex2["armed"] is True
    assert ex2["sells"] == [{"rule": "L2:M20", "qty": 60}]


def test_price_break_and_ma_reach_sides():
    levels = [
        {"type": "price", "price": 9000, "pct": 50, "side": "break", "arm": "none"},
        {"type": "ma", "ma": 20, "pct": 50, "side": "reach", "arm": "none"},
    ]
    plan = _plan(levels)
    ma = {"ma20": 10000}
    assert evaluate_sell_ladder(plan, price=9500, hold=100, ma=ma)["sells"] == []
    ex = evaluate_sell_ladder(plan, price=8800, hold=100, ma=ma)
    assert ex["sells"] == [{"rule": "L1:P9000v", "qty": 50}]
    ex2 = evaluate_sell_ladder(
        _plan(levels, sell_flags={"L1:P9000v": True}, base_qty=100),
        price=10100, hold=50, ma=ma,
    )
    assert ex2["sells"] == [{"rule": "L2:M20^", "qty": 50}]
