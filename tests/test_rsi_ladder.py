from datetime import date, timedelta

from utils.rsi_ladder import (
    LadderStore,
    bars_with_today,
    count_low_episodes,
    evaluate_add_buy,
    evaluate_entry,
    evaluate_exit,
    execution_avg_price,
    gc_state,
    judge_plan,
    ladder_preview,
    merge_profile,
    next_trigger,
    step_qty,
    tick_floor,
)


def _bars(closes, start=date(2025, 1, 6)):
    out = []
    d = start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append({"date": d, "open": c, "high": c, "low": c, "close": float(c), "volume": 1000.0})
        d += timedelta(days=1)
    return out


def _plan(**kw):
    p = {
        "id": "p1", "code": "005930", "name": "삼성전자", "budget": 10_000_000,
        "profile": merge_profile({}), "status": "ACTIVE", "fills": [], "sells": [],
        "sell_flags": {}, "armed": False, "base_qty": 0, "peak_pnl_pct": 0.0,
        "pending_buy": None, "pending_sell": None, "last_buy_date": None,
    }
    p.update(kw)
    return p


# ── 호가 · 트리거 ──

def test_tick_floor_bands():
    assert tick_floor(1999.9) == 1999
    assert tick_floor(4999) == 4995
    assert tick_floor(19999) == 19990
    assert tick_floor(49990) == 49950
    assert tick_floor(199_950) == 199_900
    assert tick_floor(499_999) == 499_500
    assert tick_floor(712_345) == 712_000


def test_next_trigger_is_minus_step_floored():
    assert next_trigger(70_000, 5) == 66_500
    assert next_trigger(20_500, 5) == 19_470  # 19475 → 10원 단위 내림


def test_step_qty_uses_budget_over_steps():
    p = _plan(budget=10_000_000)
    assert step_qty(p, 70_000) == 14  # 1,000,000 / 70,000


def test_ladder_preview_steps_down():
    rows = ladder_preview(10_000, 10_000_000, 5, 10)
    assert len(rows) == 10
    assert rows[1]["price"] == 9_500
    assert all(rows[i]["price"] < rows[i - 1]["price"] for i in range(1, 10))
    assert rows[-1]["avg"] < rows[0]["price"]


# ── 진입 ──

def test_low_episodes_need_recovery_above_reset():
    rsi = [40, 29, 31, 28, 36, 29, 40, 25, 36]
    res = count_low_episodes(rsi, 120, 30, 35)
    assert res["count"] == 3  # 29→(31,28 같은 에피소드)→36 종료, 29, 25


def test_gc_today_detected():
    falling = [200 - i for i in range(80)]
    rising = falling + [150 + i * 6 for i in range(30)]
    states = [gc_state(rising[: n], 3, 2.0)["state"] for n in range(85, len(rising) + 1)]
    assert "today" in states
    first = states.index("today")
    assert states[first + 1] == "recent"


def _gc_series():
    # 장기 하락(일봉 RSI 저점 반복) → 반등으로 20/60 GC
    closes = []
    px = 100.0
    for k in range(6):
        for _ in range(12):
            px *= 0.97
            closes.append(px)
        for _ in range(6):
            px *= 1.035
            closes.append(px)
    n = len(closes)
    for _ in range(200):
        bars = _bars(closes)
        if gc_state(closes, 3, 2.0)["state"] == "today":
            return bars
        px *= 1.03
        closes.append(px)
        assert len(closes) < n + 200
    raise AssertionError("GC 미발생")


def test_entry_ok_with_daily_lows_and_gc():
    bars = _gc_series()
    ent = evaluate_entry(bars, {"daily_low_count": 3})
    assert ent["g"]
    assert ent["low_count"] >= 3
    assert ent["ok"]


def test_entry_blocked_without_gc():
    closes = [100 * (0.99 ** i) for i in range(200)]
    ent = evaluate_entry(_bars(closes), {})
    assert not ent["g"]
    assert not ent["ok"]


def test_bars_with_today_replaces_today():
    bars = _bars([10, 11, 12])
    today = bars[-1]["date"]
    out = bars_with_today(bars, today, 99)
    assert len(out) == 3 and out[-1]["close"] == 99.0


# ── 추가 매수 ──

def _flat_bars(px, n=80):
    return _bars([px] * n)


def test_add_buy_at_trigger():
    p = _plan(fills=[{"step": 1, "price": 70_000, "qty": 14}])
    bars = _flat_bars(66_500)
    r = judge_plan(p, price=66_500, bars=bars, weekly_bars=None, hold=14, avg=70_000, today=date(2025, 6, 2))
    assert r["trigger"] == 66_500
    assert r["buy"]["step"] == 2 and r["buy"]["qty"] == 15


def test_no_add_buy_above_trigger():
    p = _plan(fills=[{"step": 1, "price": 70_000, "qty": 14}])
    r = judge_plan(p, price=66_600, bars=_flat_bars(66_600), weekly_bars=None, hold=14, avg=70_000,
                   today=date(2025, 6, 2))
    assert r["buy"] is None


def test_no_add_buy_twice_same_day():
    today = date(2025, 6, 2)
    p = _plan(fills=[{"step": 1, "price": 70_000, "qty": 14}], last_buy_date=today.isoformat())
    r = judge_plan(p, price=60_000, bars=_flat_bars(60_000), weekly_bars=None, hold=14, avg=70_000, today=today)
    assert r["buy"] is None


def test_intraday_add_buy_uses_price_only():
    today = date(2025, 6, 2)
    p = _plan(fills=[{"step": 1, "price": 70_000, "qty": 14}])
    assert evaluate_add_buy(p, price=66_600, today=today)["buy"] is None
    r = evaluate_add_buy(p, price=66_000, today=today)
    assert r["trigger"] == 66_500 and r["buy"]["step"] == 2
    p["last_buy_date"] = today.isoformat()
    assert evaluate_add_buy(p, price=60_000, today=today)["buy"] is None
    assert evaluate_add_buy(_plan(fills=p["fills"], pending_sell={"status": "QUEUED"}),
                            price=60_000, today=today)["buy"] is None


def test_full_plan_does_not_buy():
    fills = [{"step": i, "price": 70_000, "qty": 1} for i in range(1, 11)]
    p = _plan(status="FULL", fills=fills)
    r = judge_plan(p, price=10_000, bars=_flat_bars(10_000), weekly_bars=None, hold=10, avg=70_000,
                   today=date(2025, 6, 2))
    assert r["buy"] is None


def test_extra_manual_fills_do_not_count_as_steps():
    fills = [{"step": i, "price": 70_000, "qty": 1} for i in range(1, 10)]
    fills.append({"step": 0, "price": 70_000, "qty": 1, "extra": True})
    p = _plan(fills=fills)
    r = judge_plan(p, price=60_000, bars=_flat_bars(60_000), weekly_bars=None, hold=10, avg=70_000,
                   today=date(2025, 6, 2))
    assert r["buy"]["step"] == 10


def test_execution_avg_price_weighted_by_order():
    rows = [
        {"ord_no": "0012345", "cntr_qty": "10", "cntr_pric": "+9,000"},
        {"ord_no": "0012345", "cntr_qty": "5", "cntr_pric": "9030"},
        {"ord_no": "0099999", "cntr_qty": "100", "cntr_pric": "1"},
        {"ord_no": "0012345", "cntr_qty": "0", "cntr_pric": "0"},
    ]
    assert execution_avg_price(rows, "12345") == 9_010
    assert execution_avg_price(rows, "777") is None
    assert execution_avg_price(rows, "") is None


def test_gap_down_fill_reanchors_next_trigger():
    p = _plan(fills=[{"step": 1, "price": 10_000, "qty": 100}, {"step": 2, "price": 8_800, "qty": 113}])
    r = judge_plan(p, price=8_400, bars=_flat_bars(8_400), weekly_bars=None, hold=213, avg=9_380,
                   today=date(2025, 6, 3))
    assert r["trigger"] == 8_360  # 9,500 이 아니라 실제 체결가 8,800 × 0.95
    assert r["buy"] is None


# ── 매도 ──

def test_s0_only_in_profit_below_ma20():
    ma = {"ma10": 0, "ma15": 0, "ma20": 11_000, "ma60": 0}
    p = _plan()
    r = evaluate_exit(p, price=10_500, avg=10_000, hold=100, ma=ma)
    assert r["sells"] == [{"rule": "S0", "qty": 50}]
    r2 = evaluate_exit(p, price=9_500, avg=10_000, hold=100, ma=ma)
    assert r2["sells"] == []


def test_s0_once_per_cycle():
    ma = {"ma20": 11_000}
    p = _plan(sell_flags={"S0": True})
    assert evaluate_exit(p, price=10_500, avg=10_000, hold=50, ma=ma)["sells"] == []


def test_arm_then_staged_take_profit():
    p = _plan(peak_pnl_pct=16.0)
    ma = {"ma10": 11_600, "ma15": 11_000, "ma20": 10_800}
    r = evaluate_exit(p, price=11_500, avg=10_000, hold=100, ma=ma)
    assert r["armed"]
    assert r["sells"] == [{"rule": "S1", "qty": 50}]
    assert r["base_qty"] == 100

    p2 = _plan(armed=True, base_qty=100, peak_pnl_pct=16.0, sell_flags=r["flags"])
    r2 = evaluate_exit(p2, price=10_900, avg=10_000, hold=50, ma=ma)
    assert r2["sells"] == [{"rule": "S2", "qty": 25}]

    p3 = _plan(armed=True, base_qty=100, peak_pnl_pct=16.0, sell_flags=r2["flags"])
    r3 = evaluate_exit(p3, price=10_700, avg=10_000, hold=25, ma=ma)
    assert r3["sells"] == [{"rule": "S3", "qty": 25}]


def test_gap_down_fires_all_stages_same_day():
    p = _plan(armed=True, peak_pnl_pct=20.0)
    ma = {"ma10": 12_000, "ma15": 11_800, "ma20": 11_500}
    r = evaluate_exit(p, price=11_000, avg=10_000, hold=100, ma=ma)
    assert [s["rule"] for s in r["sells"]] == ["S1", "S2", "S3"]
    assert sum(s["qty"] for s in r["sells"]) == 100


def test_sell_day_blocks_buy():
    p = _plan(fills=[{"step": 1, "price": 70_000, "qty": 100}], sell_flags={}, armed=False)
    bars = _bars([80_000] * 79 + [66_000])  # MA20 ≈ 80k, 현재가 < 트리거 이지만 손실 → S0 아님
    r = judge_plan(p, price=66_000, bars=bars, weekly_bars=None, hold=100, avg=70_000, today=date(2025, 6, 2))
    assert r["sells"] == []
    assert r["buy"] is not None

    p2 = _plan(fills=[{"step": 1, "price": 60_000, "qty": 100}])
    bars2 = _bars([80_000] * 79 + [57_000])
    r2 = judge_plan(p2, price=57_000, bars=bars2, weekly_bars=None, hold=100, avg=50_000, today=date(2025, 6, 2))
    assert r2["sells"] and r2["sells"][0]["rule"] == "S0"
    assert r2["buy"] is None


# ── 연동 ──

def _store_with_plan(tmp_path, monkeypatch, *, enabled=True):
    import utils.rsi_ladder as mod

    s = LadderStore(tmp_path / "st.json")
    s.data["enabled"] = enabled
    monkeypatch.setattr(mod, "_STORE", s)
    return s


def test_allows_new_buy_window_and_liquidation_exempt(tmp_path, monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace

    from utils.auto_trade_engine import allows_strategy_new_buy
    from utils.datetime_kst import KST

    _store_with_plan(tmp_path, monkeypatch)
    s = SimpleNamespace(liquidate_before_close=True, liquidate_time="15:00")
    day = datetime(2026, 10, 26, tzinfo=KST)  # 월요일
    assert allows_strategy_new_buy(s, "rsi_ladder", now=day.replace(hour=15, minute=5))[0]
    assert not allows_strategy_new_buy(s, "rsi_ladder", now=day.replace(hour=15, minute=25))[0]
    assert not allows_strategy_new_buy(s, "rsi_ladder", now=day.replace(hour=8, minute=59))[0]


def test_allows_new_buy_blocked_when_disabled(tmp_path, monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace

    from utils.auto_trade_engine import allows_strategy_new_buy
    from utils.datetime_kst import KST

    _store_with_plan(tmp_path, monkeypatch, enabled=False)
    s = SimpleNamespace(liquidate_before_close=False)
    ok, reason = allows_strategy_new_buy(s, "rsi_ladder", now=datetime(2026, 10, 26, 10, 0, tzinfo=KST))
    assert not ok and "OFF" in reason


def test_ladder_pre_sell_qty_only_for_matching_sell(tmp_path, monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace

    from managers.stop_loss_manager import StopLossManager

    s = _store_with_plan(tmp_path, monkeypatch)
    plan = s.create_plan(code="005930", name="삼성전자", budget=1_000_000, profile={})
    plan["status"] = "ACTIVE"
    plan["pending_sell"] = {"status": "SENT", "sent_utc": "2026-10-26T06:05:00", "pre_hold": 100, "qty": 50}
    pos = SimpleNamespace(strategy_key="rsi_ladder", stock_code="005930")
    new_sell = SimpleNamespace(created_at=datetime(2026, 10, 26, 6, 5, 2))
    old_sell = SimpleNamespace(created_at=datetime(2026, 10, 25, 6, 5, 0))
    assert StopLossManager._ladder_pre_sell_qty(pos, new_sell) == 100
    assert StopLossManager._ladder_pre_sell_qty(pos, old_sell) == 0
    other = SimpleNamespace(strategy_key="legacy", stock_code="005930")
    assert StopLossManager._ladder_pre_sell_qty(other, new_sell) == 0


def test_risk_gate_and_overnight_treat_ladder_as_swing():
    from utils.market_risk_gate import normalize_strategy_key, strategy_limited_when_bad
    from utils.overnight_keep import OvernightCandidate, select_overnight_keep

    assert normalize_strategy_key("rsi_ladder") == "rsi_ladder"
    assert strategy_limited_when_bad(None, "rsi_ladder") is False
    keep, _, liq = select_overnight_keep(
        [OvernightCandidate(position_id=1, strategy_key="rsi_ladder", pnl_rate=-12.0)], keep_slots=0,
    )
    assert keep == {1} and not liq


def test_ladder_exit_levels_show_no_stop():
    from types import SimpleNamespace

    from managers.stop_loss_manager import StopLossManager

    settings = SimpleNamespace(stop_loss_rate=5.0, take_profit_rate=10.0)
    assert StopLossManager.strategy_stop_loss_rate(settings, "rsi_ladder") is None
    assert StopLossManager.strategy_stop_loss_rate(settings, "legacy") == 5.0

    ex = {"effective_stop_price": 95_000, "stop_loss_price_pct": 95_000, "trailing_start_price": 110_000,
          "levels": [{"reason": "STOP_LOSS", "price": 95_000, "method": "PCT"}], "current_price": 98_000}
    out = StopLossManager.overlay_global_exit_settings(ex, 100_000, settings, "rsi_ladder")
    assert out["levels"] == []
    assert out["effective_stop_price"] is None and out["stop_loss_price_pct"] is None
    assert out["trailing_start_price"] is None
    assert out["stop_policy"].startswith("손절 없음")
    assert out["current_price"] == 98_000

    legacy = StopLossManager.overlay_global_exit_settings({"levels": []}, 100_000, settings, "legacy")
    assert legacy["effective_stop_price"] == 95_000
    assert "stop_policy" not in legacy


# ── 저장소 ──

def test_store_roundtrip(tmp_path):
    s = LadderStore(tmp_path / "st.json")
    plan = s.create_plan(code="005930", name="삼성전자", budget=1_000_000, profile={"step_pct": 4})
    s.set_enabled(True)
    s2 = LadderStore(tmp_path / "st.json")
    assert s2.enabled
    assert s2.get(plan["id"])["profile"]["step_pct"] == 4.0
    assert s2.open_plan_for("005930")["id"] == plan["id"]
    assert len(s2.data["events"]) == 2
