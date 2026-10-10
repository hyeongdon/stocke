"""RWM 밥그릇 순수 로직 유닛테스트 (PRD_RWM_BOWL §12)."""
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from utils.rwm_bowl import (
    RwmStateStore,
    WeekBar,
    compute_indicators,
    entry_cap,
    evaluate_daily_exit,
    evaluate_pullback,
    evaluate_signal,
    find_daily_pair,
    find_williams_turn,
    hard_stop_price,
    is_week_closed,
    ma_state_at,
    manual_status_line,
    merge_params,
    normalize_daily_bars,
    normalize_minute_bars,
    params_from_settings,
    parse_code_list,
    resample_weekly,
    signal_priority_key,
    size_position,
    drop_unconfirmed_daily,
)


def _bdays(n, start=date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bowl_bars(base=40, dip=4, dipr=-0.02, ur=0.006, dec=25, dr=-0.012, rec=80, flat=250):
    """평탄 → 하락(주봉·일봉 과매도) → 바닥 횡보 → 2차 투매 → 회복 (이중 바닥 밥그릇)."""
    px = [10000.0]
    for i in range(flat - 1):
        px.append(px[-1] * (1.001 if i % 2 == 0 else 0.999))
    for _ in range(dec):
        px.append(px[-1] * (1 + dr))
    for i in range(base):
        px.append(px[-1] * (1.005 if i % 2 == 0 else 0.995))
    for _ in range(dip):
        px.append(px[-1] * (1 + dipr))
    for _ in range(rec):
        px.append(px[-1] * (1 + ur))
    bars, prev = [], px[0]
    for d, c in zip(_bdays(len(px)), px):
        bars.append({
            "date": d, "open": prev, "high": max(prev, c) * 1.003,
            "low": min(prev, c) * 0.997, "close": c, "volume": 1_000_000,
        })
        prev = c
    return bars


def _first_idx(bars, p, pred, start=251):
    ind = compute_indicators(bars, p, last_week_closed=True)
    for j in range(start, ind.n):
        if pred(ind, j):
            return j
    raise AssertionError("index not found")


def _gc_idx(bars, p):
    return _first_idx(
        bars, p,
        lambda ind, j: None not in (ind.ma20[j - 1], ind.ma60[j - 1])
        and ind.ma20[j - 1] <= ind.ma60[j - 1] and ind.ma20[j] > ind.ma60[j],
    )


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.p = merge_params()
        self.bars = _bowl_bars()

    def test_m1_gc_watch(self):
        g = _gc_idx(self.bars, self.p)
        r = evaluate_signal(self.bars[: g + 1], self.p, last_week_closed=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["reason"], "WATCH_ADDED")
        self.assertEqual(r["m_type"], "M1_GC")
        self.assertGreaterEqual(r["ma60_slope_pct"], -0.2)
        self.assertTrue(r["r_date"] and r["w_date"] and r["bowl_date"])
        self.assertLessEqual(r["r_week_rsi"], 30)
        self.assertLessEqual(abs(r["r_pair_offset"]), 5)
        self.assertGreaterEqual(r["w_bottom_bars"], 2)
        self.assertGreater(r["w_date"], r["r_date"])

    def test_gc_four_days_old_is_stale(self):
        g = _gc_idx(self.bars, self.p)
        r = evaluate_signal(self.bars[: g + 4], self.p, last_week_closed=True)
        self.assertEqual(r["reason"], "MA_SETUP_STALE")

    def test_m2_near_watch_or_reason_is_structured(self):
        p = self.p
        ind = compute_indicators(self.bars, p, last_week_closed=True)
        g = _gc_idx(self.bars, p)
        m2 = [j for j in range(251, g) if ma_state_at(ind, j, p)["m_type"] == "M2_NEAR"]
        self.assertTrue(m2, "M2 구간이 있어야 함")
        r = evaluate_signal(self.bars[: m2[0] + 1], p, last_week_closed=True)
        self.assertEqual(r["m_type"], "M2_NEAR")
        self.assertLess(r["gap_pct"], 2.0 + 1e-9)
        self.assertLess(r["gap_pct"], r["gap_prev_pct"])

    def test_m2_requires_close_above_ma60(self):
        p = self.p
        ind = compute_indicators(self.bars, p, last_week_closed=True)
        g = _gc_idx(self.bars, p)
        j = [j for j in range(251, g) if ma_state_at(ind, j, p)["m_type"] == "M2_NEAR"][0]
        bars = [dict(b) for b in self.bars[: j + 1]]
        ma20, ma60 = ind.ma20[j], ind.ma60[j]
        new_close = (ma20 + ma60) / 2.0  # MA20 위, MA60 아래
        bars[-1]["close"] = new_close
        bars[-1]["low"] = min(bars[-1]["low"], new_close)
        r = evaluate_signal(bars, p, last_week_closed=True)
        self.assertIn(r["reason"], ("NO_MA_SETUP", "MA_SETUP_STALE"))
        self.assertNotEqual(r["m_type"], "M2_NEAR")

    def test_ma60_down_blocks(self):
        bars = _bowl_bars(base=20)
        g = _gc_idx(bars, self.p)
        r = evaluate_signal(bars[: g + 1], self.p, last_week_closed=True)
        self.assertEqual(r["reason"], "MA60_DOWN")
        self.assertLess(r["ma60_slope_pct"], -0.2)

    def test_no_weekly_oversold(self):
        g = _gc_idx(self.bars, self.p)
        p = merge_params({"rsi_os_weekly": 1.0})
        r = evaluate_signal(self.bars[: g + 1], p, last_week_closed=True)
        self.assertEqual(r["reason"], "NO_WEEKLY_OVERSOLD")

    def test_too_far_above_ma20(self):
        g = _gc_idx(self.bars, self.p)
        bars = [dict(b) for b in self.bars[: g + 1]]
        ind = compute_indicators(bars, self.p, last_week_closed=True)
        bars[-1]["close"] = ind.ma20[g] * 1.10
        bars[-1]["high"] = bars[-1]["close"]
        r = evaluate_signal(bars, self.p, last_week_closed=True)
        self.assertEqual(r["reason"], "TOO_FAR_ABOVE_MA20")

    def test_chase_day(self):
        g = _gc_idx(self.bars, self.p)
        bars = [dict(b) for b in self.bars[: g + 1]]
        bars[-1]["close"] = bars[-2]["close"] * 1.08
        bars[-1]["high"] = bars[-1]["close"]
        p = merge_params({"max_above_ma20_pct": 30})
        r = evaluate_signal(bars, p, last_week_closed=True)
        self.assertEqual(r["reason"], "CHASE_DAY")

    def test_cycle_done(self):
        g = _gc_idx(self.bars, self.p)
        r = evaluate_signal(self.bars[: g + 1], self.p, last_week_closed=True, cycle_bought=lambda d: True)
        self.assertEqual(r["reason"], "CYCLE_DONE")

    def test_insufficient_bars(self):
        r = evaluate_signal(self.bars[:200], self.p, last_week_closed=True)
        self.assertEqual(r["reason"], "INSUFFICIENT_BARS")

    def test_low_liquidity(self):
        g = _gc_idx(self.bars, self.p)
        bars = [dict(b, volume=10) for b in self.bars[: g + 1]]
        r = evaluate_signal(bars, self.p, last_week_closed=True)
        self.assertEqual(r["reason"], "LOW_LIQUIDITY")

    def test_deterministic(self):
        g = _gc_idx(self.bars, self.p)
        a = evaluate_signal(self.bars[: g + 1], self.p, last_week_closed=True)
        b = evaluate_signal(self.bars[: g + 1], self.p, last_week_closed=True)
        self.assertEqual(a, b)

    def test_priority(self):
        sigs = [
            {"m_type": "M2_NEAR", "r_week_rsi": 20, "above_ma20_pct": 1},
            {"m_type": "M1_GC", "r_week_rsi": 29, "above_ma20_pct": 5},
            {"m_type": "M1_GC", "r_week_rsi": 25, "above_ma20_pct": 6},
        ]
        ordered = sorted(sigs, key=signal_priority_key)
        self.assertEqual([s["r_week_rsi"] for s in ordered], [25, 29, 20])

    def test_manual_status_line(self):
        g = _gc_idx(self.bars, self.p)
        r = evaluate_signal(self.bars[: g + 1], self.p, last_week_closed=True)
        line = manual_status_line("005930", "삼성전자", r, self.p)
        self.assertIn("감시 등록", line)


class PairAndWilliamsTests(unittest.TestCase):
    def _week(self, idx, rsi=28.0):
        return WeekBar(key=(2025, 1), last_idx=idx, last_date=date(2025, 1, 3), close=1.0, rsi=rsi)

    def test_pair_within_4_days_passes(self):
        rsi = [50.0] * 100
        rsi[54] = 27.0  # E + 4
        best = find_daily_pair(rsi, [self._week(50)], 10, 99, 30.0, 5)
        self.assertEqual(best[0], 54)

    def test_pair_minus_2_days_passes(self):
        rsi = [50.0] * 100
        rsi[48] = 27.0
        rsi[53] = 26.0
        best = find_daily_pair(rsi, [self._week(50)], 10, 99, 30.0, 5)
        self.assertEqual(best[0], 48)

    def test_pair_8_days_fails(self):
        rsi = [50.0] * 100
        rsi[58] = 25.0  # E + 8
        self.assertIsNone(find_daily_pair(rsi, [self._week(50)], 10, 99, 30.0, 5))

    def test_williams_turn_after_r_date(self):
        wr = [-50.0] * 60
        wr[40], wr[41], wr[42], wr[43] = -85.0, -90.0, -88.0, -70.0
        idx, bottom = find_williams_turn(wr, 35, 10, 59, -80.0, 2)
        self.assertEqual(idx, 43)
        self.assertEqual(bottom, 3)

    def test_williams_turn_only_before_r_date(self):
        wr = [-50.0] * 60
        wr[20], wr[21], wr[22] = -85.0, -90.0, -60.0
        idx, _ = find_williams_turn(wr, 30, 10, 59, -80.0, 2)
        self.assertIsNone(idx)

    def test_williams_turn_needs_bottom_bars(self):
        wr = [-50.0] * 60
        wr[40], wr[41] = -85.0, -70.0
        idx, _ = find_williams_turn(wr, 30, 10, 59, -80.0, 2)
        self.assertIsNone(idx)


class WeeklyTests(unittest.TestCase):
    def test_resample_excludes_open_week(self):
        bars = normalize_daily_bars([
            {"timestamp": f"{d.isoformat()} 15:30:00", "open": 1, "high": 1, "low": 1, "close": 1 + i, "volume": 1}
            for i, d in enumerate(_bdays(12, date(2025, 1, 6)))
        ])
        closed = resample_weekly(bars, last_week_closed=True)
        open_ = resample_weekly(bars, last_week_closed=False)
        self.assertEqual(len(closed), 3)
        self.assertEqual(len(open_), 2)
        self.assertEqual(closed[0].close, 5)

    def test_week_closed_with_holiday_friday(self):
        thu = date(2025, 10, 9)
        self.assertFalse(is_week_closed(thu, lambda d: d.weekday() < 5))
        self.assertTrue(is_week_closed(thu, lambda d: d.weekday() < 4))

    def test_drop_unconfirmed_daily(self):
        bars = normalize_daily_bars([
            {"timestamp": "2025-10-08 15:30:00", "close": 100},
            {"timestamp": "2025-10-09 15:30:00", "close": 101},
        ])
        self.assertEqual(len(drop_unconfirmed_daily(bars, datetime(2025, 10, 9, 10, 0))), 1)
        self.assertEqual(len(drop_unconfirmed_daily(bars, datetime(2025, 10, 9, 15, 40))), 2)


def _mbar(day, hm, o, h, l, c):
    return {"timestamp": f"{day} {hm}:00", "open": o, "high": h, "low": l, "close": c, "volume": 100}


def _pullback_bars(touch_low=10045, touch_close=10060, confirm_low=10060, extra=()):
    prev = []
    t = datetime(2025, 4, 22, 9, 0)
    for i in range(130):
        c = 10000 + (5 if i % 2 else -5)
        prev.append(_mbar("2025-04-22", t.strftime("%H:%M"), c, c + 5, c - 5, c))
        t += timedelta(minutes=3)
    day = "2025-04-23"
    today = [
        _mbar(day, "09:00", 10050, 10100, 10040, 10090),
        _mbar(day, "09:03", 10090, 10180, 10080, 10170),
        _mbar(day, "09:06", 10170, 10200, 10150, 10190),
        _mbar(day, "09:09", 10150, 10155, touch_low, touch_close),
        _mbar(day, "09:12", confirm_low + 5, 10170, confirm_low, 10165),
    ] + list(extra)
    return normalize_minute_bars(prev + today)


class PullbackTests(unittest.TestCase):
    def setUp(self):
        self.p = merge_params()
        self.today = date(2025, 4, 23)

    def _eval(self, bars, now, ma20_d=9800, signal_close=10000, cap=10500):
        return evaluate_pullback(
            bars, today=self.today, now=now, ma20_d=ma20_d,
            signal_close=signal_close, cap=cap, p=self.p,
        )

    def test_buy_on_bounce(self):
        r = self._eval(_pullback_bars(), datetime(2025, 4, 23, 9, 15, 5))
        self.assertEqual(r["action"], "BUY", r)
        self.assertEqual(r["reason"], "PULLBACK_3M")
        self.assertEqual(r["entry_price"], 10165)
        self.assertGreaterEqual(r["pb_depth_pct"], 1.0)

    def test_forming_bar_not_used(self):
        r = self._eval(_pullback_bars(), datetime(2025, 4, 23, 9, 14, 0))
        self.assertNotEqual(r["action"], "BUY")

    def test_open_below_ma20(self):
        r = self._eval(_pullback_bars(), datetime(2025, 4, 23, 9, 15, 5), ma20_d=10100)
        self.assertEqual((r["action"], r["reason"]), ("EXPIRE", "OPEN_BELOW_MA20"))

    def test_close_below_daily_ma20(self):
        bars = _pullback_bars(touch_low=10020, touch_close=10030)
        r = self._eval(bars, datetime(2025, 4, 23, 9, 15, 5), ma20_d=10040)
        self.assertEqual((r["action"], r["reason"]), ("EXPIRE", "BELOW_DAILY_MA20"))

    def test_no_rise_no_buy(self):
        r = self._eval(_pullback_bars(), datetime(2025, 4, 23, 9, 15, 5), signal_close=10200)
        self.assertEqual(r["action"], "WAIT")

    def test_deep_pullback_not_touch(self):
        bars = _pullback_bars(touch_low=9650, confirm_low=10085)
        r = self._eval(bars, datetime(2025, 4, 23, 9, 15, 5))
        self.assertEqual(r["action"], "WAIT")

    def test_retouch_after_deep_bar_can_buy(self):
        bars = _pullback_bars(touch_low=9650)
        r = self._eval(bars, datetime(2025, 4, 23, 9, 15, 5))
        self.assertEqual(r["action"], "BUY")

    def test_entry_above_cap(self):
        r = self._eval(_pullback_bars(), datetime(2025, 4, 23, 9, 15, 5), cap=10100)
        self.assertEqual(r["action"], "WAIT")
        self.assertEqual(r["reason"], "ENTRY_ABOVE_CAP")

    def test_no_pullback_at_end(self):
        r = self._eval(_pullback_bars(), datetime(2025, 4, 23, 14, 31), signal_close=10200)
        self.assertEqual((r["action"], r["reason"]), ("EXPIRE", "NO_PULLBACK"))

    def test_missed_confirm_not_bought_late(self):
        extra = [_mbar("2025-04-23", "09:15", 10165, 10170, 10150, 10160)]
        r = self._eval(_pullback_bars(extra=extra), datetime(2025, 4, 23, 9, 18, 5))
        self.assertNotEqual(r["action"], "BUY")

    def test_nxt_bars_excluded(self):
        bars = normalize_minute_bars([
            _mbar("2025-04-23", "08:30", 1, 1, 1, 1),
            _mbar("2025-04-23", "09:00", 2, 2, 2, 2),
            _mbar("2025-04-23", "15:40", 3, 3, 3, 3),
        ])
        self.assertEqual([b["close"] for b in bars], [2])


class ExitAndSizingTests(unittest.TestCase):
    def setUp(self):
        self.p = merge_params()

    def _bars(self, closes, start=date(2025, 1, 1)):
        return [
            {"date": d, "open": c, "high": c, "low": c, "close": c, "volume": 1}
            for d, c in zip(_bdays(len(closes), start), closes)
        ]

    def test_exit_ma20_next_day(self):
        closes = [100 + i for i in range(40)] + [120]
        bars = self._bars(closes)
        r = evaluate_daily_exit(bars, self.p, entry_date=bars[-3]["date"])
        self.assertEqual(r["reason"], "EXIT_MA20")

    def test_no_exit_on_entry_day(self):
        closes = [100 + i for i in range(40)] + [120]
        bars = self._bars(closes)
        r = evaluate_daily_exit(bars, self.p, entry_date=bars[-1]["date"])
        self.assertIsNone(r["reason"])

    def test_no_exit_above_ma20(self):
        bars = self._bars([100 + i for i in range(41)])
        r = evaluate_daily_exit(bars, self.p, entry_date=bars[-5]["date"])
        self.assertIsNone(r["reason"])
        self.assertEqual(r["hard_price"], hard_stop_price(r["ma20"], self.p))

    def test_max_hold(self):
        bars = self._bars([100 + i for i in range(100)])
        r = evaluate_daily_exit(bars, self.p, entry_date=bars[-61]["date"])
        self.assertEqual(r["reason"], "MAX_HOLD")
        self.assertEqual(r["hold_days"], 60)

    def test_sizing_risk_and_caps(self):
        p = self.p
        s = size_position(100_000_000, 10_000, 9_700, p)
        # 리스크 100만 / 거리 max(300, 300) = 3333주 → 20% 캡 2000주
        self.assertEqual(s["qty"], 2000)
        s2 = size_position(100_000_000, 10_000, 9_000, p)
        self.assertEqual(s2["qty"], 1000)
        s3 = size_position(100_000_000, 10_000, 9_000, p, max_invest_amount=5_000_000)
        self.assertEqual(s3["qty"], 500)

    def test_min_risk_distance(self):
        s = size_position(10_000_000, 10_000, 9_990, merge_params({"max_position_pct": 100}))
        # 거리 10원 대신 최소 3%(300원) → 100,000 / 300 = 333
        self.assertEqual(s["qty"], 333)

    def test_entry_cap_and_hard(self):
        self.assertEqual(entry_cap(14600, self.p), 15330)
        self.assertEqual(hard_stop_price(14050, self.p), 13628)


class MiscTests(unittest.TestCase):
    def test_parse_code_list(self):
        self.assertEqual(parse_code_list("005930, A000660\n35720 005930"), ["005930", "000660", "035720"])

    def test_params_from_settings(self):
        class S:
            rwm_max_slots = 3
            rwm_params = '{"pb_min_rise_pct": 2.0, "bogus": 1}'
            rwm_condition_names = ""

        p = params_from_settings(S())
        self.assertEqual(p["max_positions"], 3)
        self.assertEqual(p["pb_min_rise_pct"], 2.0)
        self.assertEqual(p["rwm_condition_names"], "RWM밥그릇")
        self.assertNotIn("bogus", p)

    def test_state_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "s.json"
            st = RwmStateStore(path)
            st.set_watch("005930", {"status": "WATCH", "valid_date": "2025-04-23"})
            st.set_exit(7, {"reason": "EXIT_MA20"})
            st2 = RwmStateStore(path)
            self.assertIn("005930", st2.active_watch(date(2025, 4, 23)))
            self.assertEqual(st2.pending_exit(7)["reason"], "EXIT_MA20")
            st2.prune_watch(date(2025, 4, 24))
            self.assertEqual(st2.watch_rows(), {})


class IntegrationKeyTests(unittest.TestCase):
    def test_overnight_keeps_rwm_without_using_slots(self):
        from utils.overnight_keep import OvernightCandidate, select_overnight_keep

        rows = [
            OvernightCandidate(position_id=1, strategy_key="rwm_bowl", pnl_rate=-2.0),
            OvernightCandidate(position_id=2, strategy_key="rwm_bowl", pnl_rate=5.0),
            OvernightCandidate(position_id=3, strategy_key="legacy", pnl_rate=-1.0),
        ]
        keep_ids, _, liq = select_overnight_keep(rows, keep_slots=0, max_per_strategy=1)
        self.assertEqual(keep_ids, {1, 2})
        self.assertEqual([r.position_id for r in liq], [3])

    def test_buy_window(self):
        from types import SimpleNamespace

        from utils.auto_trade_engine import allows_strategy_new_buy
        from utils.datetime_kst import KST

        s = SimpleNamespace(use_rwm_bowl=True, rwm_pb_start="09:06", rwm_pb_end="14:30", liquidate_before_close=False)
        day = datetime(2026, 10, 7, tzinfo=KST)  # 수요일
        self.assertFalse(allows_strategy_new_buy(s, "rwm_bowl", now=day.replace(hour=9, minute=3))[0])
        self.assertTrue(allows_strategy_new_buy(s, "rwm_bowl", now=day.replace(hour=14, minute=33))[0])
        self.assertFalse(allows_strategy_new_buy(s, "rwm_bowl", now=day.replace(hour=14, minute=36))[0])
        s.use_rwm_bowl = False
        self.assertFalse(allows_strategy_new_buy(s, "rwm_bowl", now=day.replace(hour=10))[0])


if __name__ == "__main__":
    unittest.main()
