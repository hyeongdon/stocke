"""MA1592 순수 로직 유닛테스트."""
import tempfile
import unittest
from unittest.mock import patch
from datetime import date, datetime
from pathlib import Path

from utils.ma1592 import (
    Ma1592UniverseStore,
    OverlayMA,
    UniverseRow,
    below_ma90,
    compute_bar_ma,
    compute_daily_overlay,
    compute_live_metrics_from_closes,
    evaluate_exit,
    evaluate_scale_add_on_15m,
    evaluate_setup_on_bar,
    gap_pct_above_ema,
    hard_break_below_ma90,
    is_golden_cross_bar,
    is_golden_cross_overlay,
    is_far_from_gc_zone,
    is_scale_gap_open,
    is_scale_pullback_bar,
    is_trend_lost,
    is_touch_bounce,
    chart_tf_interval_minutes,
    normalize_chart_tf,
    remove_on_below_ma90,
    remove_on_ema90_break,
    scale_leg_quantities,
    scale_leg_qty,
    size_position,
    sync_manage_ledger_with_holdings,
    tp1_price,
    try_insert_l2_from_overlay,
    update_impulse_seen,
)


def _closes_below_cross(n=90, base=10000.0):
    """MA15 < MA90 유지 후 마지막에 급등할 수 있는 시리즈."""
    return [base + i * 2 for i in range(n)]


class Ma1592OverlayTests(unittest.TestCase):
    def test_overlay_gc_when_live_crosses(self):
        # 긴 횡보 후 dayclose 급등으로 15가 90을 상회
        closes = [10000.0] * 92
        # yest: 둘 다 10000 → 15<=90
        # live with dayclose 12000: 15 live > 90 live
        ov = compute_daily_overlay(closes, 12000.0)
        self.assertIsNotNone(ov)
        assert ov is not None
        self.assertAlmostEqual(ov.ma15_yest, 10000.0)
        self.assertAlmostEqual(ov.ma92_yest, 10000.0)
        self.assertGreater(ov.ma15_live, ov.ma92_live)
        ok, code = is_golden_cross_overlay(ov, require_slope_up=True)
        self.assertTrue(ok)
        self.assertEqual(code, "GC")

    def test_no_gc_when_already_above(self):
        # 이미 상승 추세면 yest에서도 15>90 → NO_GC
        closes = [10000.0 + i * 50 for i in range(92)]
        ov = compute_daily_overlay(closes, closes[-1] + 100)
        self.assertIsNotNone(ov)
        assert ov is not None
        if ov.ma15_yest > ov.ma92_yest:
            ok, code = is_golden_cross_overlay(ov)
            self.assertFalse(ok)
            self.assertEqual(code, "NO_GC")

    def test_slope_down_blocks(self):
        ov = OverlayMA(
            ma15_live=10100, ma92_live=10000,
            ma15_yest=9900, ma92_yest=10050,
        )
        # live 교차지만 slope90 < 0
        self.assertLess(ov.slope92, 0)
        ok, code = is_golden_cross_overlay(ov, require_slope_up=True)
        self.assertFalse(ok)
        self.assertEqual(code, "SLOPE_DOWN")

    def test_5m_ema_golden_cross(self):
        # 횡보 후 급등 → EMA15가 EMA90 상향 돌파
        closes = [10000.0] * 100 + [10000.0 + i * 80 for i in range(1, 25)]
        f, s, pf, ps = compute_bar_ma(closes, ma_type="ema")
        self.assertIsNotNone(f)
        self.assertIsNotNone(s)
        self.assertGreater(f, s)
        # 직전 봉 대비 교차 판정 가능한 케이스 찾기
        ok, code = is_golden_cross_bar(f, s, pf, ps, require_slope_up=False)
        # 이미 교차 상태면 NO_GC일 수 있음 — 시리즈 중간에서 교차 봉 탐색
        from utils.ema_fractal import ema_series
        e15 = ema_series(closes, 15)
        e90 = ema_series(closes, 90)
        found = False
        for i in range(91, len(closes)):
            if e15[i - 1] is None or e90[i - 1] is None:
                continue
            if e15[i - 1] <= e90[i - 1] and e15[i] > e90[i]:
                ok2, code2 = is_golden_cross_bar(
                    e15[i], e90[i], e15[i - 1], e90[i - 1], require_slope_up=False,
                )
                self.assertTrue(ok2)
                self.assertEqual(code2, "GC")
                found = True
                break
        self.assertTrue(found or ok, "EMA15/92 교차 구간이 있어야 함")

    def test_live_metrics_from_closes(self):
        closes = [10000.0] * 100 + [10000.0 + i * 80 for i in range(1, 25)]
        highs = [c + 50 for c in closes]
        m = compute_live_metrics_from_closes(closes, highs)
        self.assertIsNotNone(m["ma15"])
        self.assertIsNotNone(m["ma92"])
        self.assertGreater(m["ma15"], m["ma92"])
        self.assertIsNotNone(m["prev_high"])
        self.assertGreaterEqual(m["prev_high"], int(max(highs[-90:])))


class Ma1592EntryTests(unittest.TestCase):
    def test_touch_bounce(self):
        ma15 = 10000.0
        bar = {"open": 9990, "high": 10100, "low": 9980, "close": 10050}
        self.assertTrue(is_touch_bounce(bar, ma15))
        bear = {"open": 10100, "high": 10100, "low": 9980, "close": 9995}
        self.assertFalse(is_touch_bounce(bear, ma15, require_bullish=True))

    def test_scale_in_leg1_price_lead(self):
        row = UniverseRow(
            stock_code="005930",
            state="GC_WATCH",
            expire_date="2099-01-01",
        )
        # EMA15 아직 EMA90 아래(근접) + 종가가 둘 다 상회 → 가격선행 매수
        ma15, ma90 = 10000.0, 10050.0
        bar = {"open": 10010, "high": 10150, "low": 10005, "close": 10120}
        r = evaluate_setup_on_bar(
            row, bar, ma15, ma90=ma90,
            params={"hold_mode": "scale_in_gc", "entry_trigger": "price_lead"},
        )
        self.assertTrue(r["buy"])
        self.assertEqual(r.get("entry_leg"), 1)
        self.assertIn("가격선행", r["reason"])

    def test_scale_in_price_lead_waits_when_below_ma(self):
        row = UniverseRow(
            stock_code="005930",
            state="GC_WATCH",
            expire_date="2099-01-01",
        )
        ma15, ma90 = 10100.0, 10000.0
        bar = {"open": 10070, "high": 10090, "low": 10060, "close": 10080}  # > ema90, < ema15
        r = evaluate_setup_on_bar(
            row, bar, ma15, ma90=ma90,
            params={"hold_mode": "scale_in_gc", "entry_trigger": "price_lead"},
        )
        self.assertFalse(r["buy"])
        self.assertEqual(r.get("reason_code"), "BELOW_MA")
        self.assertEqual(row.state, "GC_WATCH")  # EMA90 위 — 관찰 유지

    def test_scale_in_price_lead_far_discards(self):
        row = UniverseRow(
            stock_code="005930",
            state="GC_WATCH",
            expire_date="2099-01-01",
        )
        ma15, ma90 = 9700.0, 10000.0  # 3% 이격
        bar = {"open": 10010, "high": 10100, "low": 10000, "close": 10080}
        r = evaluate_setup_on_bar(
            row, bar, ma15, ma90=ma90,
            params={
                "hold_mode": "scale_in_gc",
                "entry_trigger": "price_lead",
                "price_lead_near_pct": 1.0,
                "price_lead_far_pct": 2.5,
            },
        )
        self.assertFalse(r["buy"])
        self.assertEqual(r.get("reason_code"), "FAR_FROM_GC")
        self.assertEqual(row.state, "DONE")

    def test_scale_in_leg1_gc_above_mode(self):
        row = UniverseRow(
            stock_code="005930",
            state="GC_WATCH",
            expire_date="2099-01-01",
        )
        ma15, ma90 = 10050.0, 9950.0
        ma15_prev, ma90_prev = 9800.0, 9900.0  # 이번 봉 GC 교차
        bar = {"open": 10050, "high": 10120, "low": 10040, "close": 10080}
        r = evaluate_setup_on_bar(
            row, bar, ma15, ma90=ma90,
            ma15_prev=ma15_prev, ma92_prev=ma90_prev,
            params={"hold_mode": "scale_in_gc", "entry_trigger": "gc_above"},
        )
        self.assertTrue(r["buy"])
        self.assertIn("GC교차", r["reason"])

    def test_scale_in_gc_above_rejects_stale_cross(self):
        row = UniverseRow(
            stock_code="005930",
            state="GC_WATCH",
            expire_date="2099-01-01",
        )
        ma15, ma90 = 10100.0, 10000.0
        bar = {"open": 10110, "high": 10200, "low": 10090, "close": 10150}
        with patch(
            "utils.ma1592.bars_since_golden_cross", return_value=5,
        ):
            r = evaluate_setup_on_bar(
                row, bar, ma15, ma90=ma90,
                closes=[10000.0] * 120,
                params={"hold_mode": "scale_in_gc", "entry_trigger": "gc_above"},
            )
        self.assertFalse(r["buy"])
        self.assertEqual(r.get("reason_code"), "GC_STALE")
        self.assertEqual(row.state, "DONE")

    def test_scale_in_gc_above_rejects_chase_gap(self):
        row = UniverseRow(
            stock_code="005930",
            state="GC_WATCH",
            expire_date="2099-01-01",
        )
        ma15, ma90 = 10000.0, 9950.0
        ma15_prev, ma90_prev = 9800.0, 9900.0
        bar = {"open": 10150, "high": 10200, "low": 10140, "close": 10150}  # +1.5%
        r = evaluate_setup_on_bar(
            row, bar, ma15, ma90=ma90,
            ma15_prev=ma15_prev, ma92_prev=ma90_prev,
            params={
                "hold_mode": "scale_in_gc",
                "entry_trigger": "gc_above",
                "gc_entry_max_price_gap_pct": 1.0,
            },
        )
        self.assertFalse(r["buy"])
        self.assertEqual(r.get("reason_code"), "GC_CHASE")
        self.assertEqual(row.state, "GC_WATCH")

    def test_check_fresh_gc_entry_on_cross_bar(self):
        from utils.ma1592 import check_fresh_gc_entry

        ok, code, _ = check_fresh_gc_entry(
            10050, 10000.0, 9950.0, None,
            ma15_prev=9800.0, ma92_prev=9900.0,
            params={"gc_entry_max_bars": 2, "gc_entry_max_price_gap_pct": 1.0},
        )
        self.assertTrue(ok)
        self.assertEqual(code, "GC_FRESH")

    def test_is_price_lead_inbody_like_1115(self):
        from utils.ma1592 import is_price_lead_breakout
        # 인바디 11:15 봉 근사: 종가>>이평, EMA15≈EMA90(미소 아래)
        ok, code = is_price_lead_breakout(
            61800, 61065.5, 61079.0, near_pct=1.0, far_pct=3.0,
        )
        self.assertTrue(ok)
        self.assertEqual(code, "PRICE_LEAD")
        # 10:29 알림 시점 근사: 종가 < EMA90
        ok2, code2 = is_price_lead_breakout(
            61000, 60881.0, 61073.0, near_pct=1.0, far_pct=3.0,
        )
        self.assertFalse(ok2)
        self.assertEqual(code2, "BELOW_MA")

    def test_hold_then_buy_legacy_mode(self):
        row = UniverseRow(
            stock_code="005930",
            state="GC_WATCH",
            expire_date=(date.today().replace(year=date.today().year + 1)).isoformat(),
        )
        ma15 = 10000.0
        hold_bar = {"open": 10010, "high": 10050, "low": 10005, "close": 10040}
        for _ in range(6):
            r = evaluate_setup_on_bar(
                row, hold_bar, ma15,
                params={"hold_bars": 6, "hold_mode": "no_break_then_touch"},
            )
            self.assertNotEqual(r.get("reason_code"), "MA90_BREAK_PRE")
        self.assertEqual(row.state, "WAIT_HOLD")
        bounce = {"open": 9990, "high": 10080, "low": 9970, "close": 10060}
        r = evaluate_setup_on_bar(
            row, bounce, ma15, params={"hold_mode": "no_break_then_touch"},
        )
        self.assertTrue(r["buy"])
        self.assertEqual(r["status"], "pass")
        # 체결 전엔 WAIT_HOLD 유지 (주문 실패 시 재관찰)
        self.assertEqual(row.state, "WAIT_HOLD")

    def test_pre_break_discards(self):
        """종가 EMA90 이탈만으로는 폐기하지 않음 — 추세(EMA15>EMA90) 유지 시 관찰 계속."""
        row = UniverseRow(
            stock_code="000660",
            state="GC_WATCH",
            expire_date="2099-01-01",
        )
        ma15, ma90 = 10100.0, 10000.0
        bar = {"open": 9900, "high": 9950, "low": 9800, "close": 9900}
        r = evaluate_setup_on_bar(
            row, bar, ma15, ma90=ma90, params={"break_before_entry_pct": 0.4},
        )
        self.assertNotEqual(r.get("reason_code"), "TREND_LOST")
        self.assertEqual(row.state, "GC_WATCH")

    def test_slight_below_ma90_keeps_near_dead_cross(self):
        row = UniverseRow(
            stock_code="005930",
            state="GC_WATCH",
            expire_date="2099-01-01",
        )
        ma15, ma90 = 10000.0, 10050.0
        bar = {"open": 10020, "high": 10040, "low": 9990, "close": 10020}
        self.assertTrue(below_ma90(10020, ma90))
        self.assertFalse(hard_break_below_ma90(10020, ma90, 0.4))
        r = evaluate_setup_on_bar(
            row, bar, ma15, ma90=ma90,
            params={"hold_mode": "scale_in_gc", "entry_trigger": "price_lead"},
        )
        self.assertNotEqual(r.get("reason_code"), "TREND_LOST")
        self.assertEqual(row.state, "GC_WATCH")

    def test_remove_on_below_ma90_skips_manage(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "u.json"
            store = Ma1592UniverseStore(path)
            store.upsert(UniverseRow(stock_code="005930", state="GC_WATCH"))
            self.assertTrue(
                remove_on_below_ma90("005930", ma15=9600, ma90=10000, store=store)
            )
            self.assertNotIn("005930", store.l3_codes())
            store.upsert(UniverseRow(stock_code="000660", state="MANAGE_FULL"))
            self.assertFalse(
                remove_on_below_ma90("000660", ma15=9900, ma90=10000, store=store)
            )
            self.assertIsNotNone(store.get("000660"))
            # 종가가 EMA90 아래여도 EMA15>EMA90이면 장부 유지
            store.upsert(UniverseRow(stock_code="035420", state="GC_WATCH"))
            self.assertFalse(
                remove_on_below_ma90("035420", ma15=10100, ma90=10000, close=9900, store=store)
            )
            self.assertIn("035420", store.l3_codes())


class Ma1592ScaleInTests(unittest.TestCase):
    def test_gap_pct(self):
        self.assertAlmostEqual(gap_pct_above_ema(10100, 10000), 1.0)
        self.assertTrue(is_scale_gap_open(10100, 10000, 1.0))
        self.assertFalse(is_scale_gap_open(10050, 10000, 1.0))

    def test_leg_qty_sum(self):
        q1, q2, q3 = scale_leg_quantities(100)
        self.assertEqual(q1 + q2 + q3, 100)
        self.assertEqual(q1, 15)
        self.assertEqual(q2, 35)
        self.assertEqual(q3, 50)
        self.assertEqual(scale_leg_qty(100, 2), 35)

    def test_leg2_on_gap(self):
        row = UniverseRow(
            stock_code="005930",
            state="MANAGE_FULL",
            entry_leg=1,
            planned_qty=100,
            expire_date="2099-01-01",
        )
        bars = [
            {"datetime": "2026-08-27T10:00:00", "close": 10000},
            {"datetime": "2026-08-27T10:15:00", "close": 10120},
        ]
        r = evaluate_scale_add_on_15m(row, bars, ma15=10000.0, params={"scale_gap_pct": 1.0})
        self.assertTrue(r["buy"])
        self.assertEqual(r["entry_leg"], 2)

    def test_leg3_pullback_bounce(self):
        row = UniverseRow(
            stock_code="005930",
            state="MANAGE_FULL",
            entry_leg=2,
            planned_qty=100,
            ma92=9800.0,
            scale_last_bar_at="2026-08-27T10:15:00",
            expire_date="2099-01-01",
        )
        bars = [
            {"datetime": "2026-08-27T10:15:00", "close": 10120},
            {
                "datetime": "2026-08-27T10:30:00",
                "open": 10020,
                "high": 10080,
                "low": 10010,
                "close": 10050,
            },
        ]
        r = evaluate_scale_add_on_15m(
            row, bars, ma15=10000.0, ma92=9800.0,
            params={"scale_leg3_mode": "pullback"},
        )
        self.assertTrue(r["buy"])
        self.assertEqual(r["entry_leg"], 3)
        self.assertIn("눌림", r["reason"])

    def test_leg3_pullback_rejects_ma92_break(self):
        row = UniverseRow(
            stock_code="005930",
            state="MANAGE_FULL",
            entry_leg=2,
            planned_qty=100,
            ma92=10000.0,
            scale_last_bar_at="2026-08-27T10:15:00",
            expire_date="2099-01-01",
        )
        bars = [
            {"datetime": "2026-08-27T10:15:00", "close": 10120},
            {
                "datetime": "2026-08-27T10:30:00",
                "open": 9900,
                "high": 9950,
                "low": 9850,
                "close": 9900,
            },
        ]
        self.assertFalse(
            is_scale_pullback_bar(
                bars[-1], 10000.0, 10000.0, break_pct=0.4,
            )
        )
        r = evaluate_scale_add_on_15m(
            row, bars, ma15=10000.0, ma92=10000.0,
            params={"scale_leg3_mode": "pullback"},
        )
        self.assertFalse(r["buy"])
        self.assertEqual(r["reason_code"], "WAIT_PULLBACK")

    def test_leg3_hold_mode_legacy(self):
        row = UniverseRow(
            stock_code="005930",
            state="MANAGE_FULL",
            entry_leg=2,
            planned_qty=100,
            scale_last_bar_at="2026-08-27T10:15:00",
            scale_ok_bars=0,
            expire_date="2099-01-01",
        )
        bars = [
            {"datetime": "2026-08-27T10:15:00", "close": 10120},
            {"datetime": "2026-08-27T10:30:00", "close": 10050},
            {"datetime": "2026-08-27T10:45:00", "close": 10080},
        ]
        r = evaluate_scale_add_on_15m(
            row, bars, ma15=10000.0,
            params={"scale_gap_pct": 1.0, "scale_hold_bars": 2, "scale_leg3_mode": "hold"},
        )
        self.assertTrue(r["buy"])
        self.assertEqual(r["entry_leg"], 3)
        self.assertGreaterEqual(row.scale_ok_bars, 2)


class Ma1592SizingExitTests(unittest.TestCase):
    def test_tp1_fallback(self):
        px, label = tp1_price(10000, 11000, 4.0)
        self.assertEqual(label, "TP1_FALLBACK")
        self.assertEqual(px, int(round(11000 * 1.04)))

    def test_tp1_high(self):
        px, label = tp1_price(12000, 11000, 4.0)
        self.assertEqual(label, "TP1_HIGH")
        self.assertEqual(px, 12000)

    def test_size_half(self):
        s = size_position(10_000_000, 10000, 9900, risk_per_trade_pct=2.0, stop_pct=4.0)
        self.assertGreaterEqual(s["qty"], 2)
        self.assertEqual(s["qty_tp1"] + s["qty_remain"], s["qty"])

    def test_impulse_sticky(self):
        self.assertTrue(
            update_impulse_seen(False, tp1_filled=True, entry=10000, peak=10000)
        )
        self.assertTrue(
            update_impulse_seen(
                False, tp1_filled=False, entry=10000, peak=10250, impulse_min_pct=2.0,
            )
        )
        self.assertTrue(
            update_impulse_seen(True, tp1_filled=False, entry=10000, peak=10000)
        )

    def test_exit_tp1_then_hard_not_after_impulse(self):
        # TP1 hit
        ex = evaluate_exit(
            state="MANAGE_FULL",
            entry=10000,
            last=12500,
            close=12500,
            open_=12000,
            high=12600,
            ma15=11000,
            tp1_price_val=12400,
            tp1_filled=False,
            impulse_seen=False,
            peak=12600,
            bars_since_peak=0,
            hold_days=1,
        )
        self.assertIsNotNone(ex)
        assert ex is not None
        self.assertEqual(ex["qty_frac"], 0.5)
        self.assertIn(ex["reason"], ("TP1_HIGH", "TP1_GAP"))

        # after impulse, small hard break should NOT exit
        ex2 = evaluate_exit(
            state="MANAGE_HALF",
            entry=10000,
            last=10950,
            close=10950,  # ~0.45% below ma92=11000 — hard but not large
            open_=11000,
            high=11100,
            ma15=11000,
            ma92=11000,
            tp1_price_val=12400,
            tp1_filled=True,
            impulse_seen=True,
            peak=12600,
            bars_since_peak=10,
            hold_days=2,
            params={"hard_break_pct": 0.4, "large_break_pct": 1.0, "crash_pct": 2.5},
        )
        self.assertIsNone(ex2)

    def test_stop_dc_crash_before_impulse(self):
        ex = evaluate_exit(
            state="MANAGE_FULL",
            entry=10600,
            last=10300,
            close=10300,
            open_=10400,
            high=10400,
            ma15=10900,
            ma92=11000,
            tp1_price_val=12400,
            tp1_filled=False,
            impulse_seen=False,
            peak=10650,
            bars_since_peak=2,
            hold_days=1,
            params={"crash_pct": 2.5, "crash_bars": 3, "impulse_min_pct": 2.0},
        )
        self.assertIsNotNone(ex)
        assert ex is not None
        self.assertEqual(ex["reason"], "STOP_MA_DC_CRASH")

    def test_pre_impulse_92_break_without_dc_holds(self):
        ex = evaluate_exit(
            state="MANAGE_FULL",
            entry=10600,
            last=10850,
            close=10850,
            open_=10900,
            high=10900,
            ma15=11100,
            ma92=11000,
            tp1_price_val=12400,
            tp1_filled=False,
            impulse_seen=False,
            peak=11000,
            bars_since_peak=2,
            hold_days=1,
            params={"crash_pct": 2.5, "crash_bars": 3, "impulse_min_pct": 2.0},
        )
        self.assertIsNone(ex)

    def test_pre_impulse_dc_without_crash_holds(self):
        ex = evaluate_exit(
            state="MANAGE_FULL",
            entry=10600,
            last=10550,
            close=10550,
            open_=10580,
            high=10600,
            ma15=10950,
            ma92=11000,
            tp1_price_val=12400,
            tp1_filled=False,
            impulse_seen=False,
            peak=10600,
            bars_since_peak=2,
            hold_days=1,
            params={"crash_pct": 2.5, "crash_bars": 3, "impulse_min_pct": 2.0},
        )
        self.assertIsNone(ex)

    def test_stop_ma_dc_widen_after_leg2_without_crash(self):
        ex = evaluate_exit(
            state="MANAGE_FULL",
            entry=10600,
            last=10550,
            close=10550,
            open_=10580,
            high=10600,
            ma15=10600,
            ma92=11000,
            tp1_price_val=12400,
            tp1_filled=False,
            impulse_seen=False,
            peak=10600,
            bars_since_peak=2,
            hold_days=1,
            entry_leg=2,
            params={"price_lead_far_pct": 3.0, "crash_pct": 2.5, "crash_bars": 3},
        )
        self.assertIsNotNone(ex)
        assert ex is not None
        self.assertEqual(ex["reason"], "STOP_MA_DC_WIDEN")

    def test_stop_ma_dc_widen_ignored_before_leg2(self):
        ex = evaluate_exit(
            state="MANAGE_FULL",
            entry=10600,
            last=10550,
            close=10550,
            open_=10580,
            high=10600,
            ma15=10600,
            ma92=11000,
            tp1_price_val=12400,
            tp1_filled=False,
            impulse_seen=False,
            peak=10600,
            bars_since_peak=2,
            hold_days=1,
            entry_leg=1,
            params={"price_lead_far_pct": 3.0, "crash_pct": 2.5, "crash_bars": 3},
        )
        self.assertIsNone(ex)

    def test_stop_ma_crash(self):
        ex = evaluate_exit(
            state="MANAGE_HALF",
            entry=10000,
            last=10800,
            close=10800,  # >1% below ma92=11000
            open_=12000,
            high=12000,
            ma15=11000,
            ma92=11000,
            tp1_price_val=12400,
            tp1_filled=True,
            impulse_seen=True,
            peak=12600,
            bars_since_peak=2,
            hold_days=2,
            params={"crash_pct": 2.5, "crash_bars": 3, "large_break_pct": 1.0},
        )
        self.assertIsNotNone(ex)
        assert ex is not None
        self.assertEqual(ex["reason"], "STOP_MA_CRASH")


class Ma1592UniverseTests(unittest.TestCase):
    def test_l2_insert_and_persist(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "u.json"
            store = Ma1592UniverseStore(path)
            closes = [10000.0] * 92
            ov = compute_daily_overlay(closes, 12000.0)
            assert ov is not None
            ok, code, row = try_insert_l2_from_overlay(
                "005930", "삼성전자", ov,
                dayclose=12000,
                trading_value=6_000_000_000,
                now=datetime(2026, 8, 26, 10, 20),
                store=store,
                params={"min_trading_value": 5_000_000_000},
            )
            self.assertTrue(ok)
            self.assertEqual(code, "GC")
            self.assertEqual(row.state, "GC_WATCH")
            store2 = Ma1592UniverseStore(path)
            self.assertIsNotNone(store2.get("005930"))
            self.assertIn("005930", store2.l3_codes())
            self.assertEqual(len(store2.all_rows()), 1)
            self.assertEqual(store2.all_rows()[0].stock_code, "005930")

    def test_all_rows_excludes_done_and_orders_active(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "u.json"
            store = Ma1592UniverseStore(path)
            store.upsert(UniverseRow(stock_code="111111", stock_name="A", state="GC_WATCH"))
            store.upsert(UniverseRow(stock_code="222222", stock_name="B", state="WAIT_HOLD"))
            store.upsert(UniverseRow(stock_code="333333", stock_name="C", state="MANAGE_FULL"))
            codes = {r.stock_code for r in store.all_rows()}
            self.assertEqual(codes, {"111111", "222222", "333333"})
            store.set_state("111111", "DONE")
            codes2 = {r.stock_code for r in store.all_rows()}
            self.assertEqual(codes2, {"222222", "333333"})
            self.assertEqual(set(store.l3_codes()), {"222222"})

    def test_condition_sync_add_and_remove(self):
        from utils.ma1592 import (
            remove_on_ema90_break,
            sync_universe_from_condition,
            upsert_from_condition,
        )
        _gc = {"in_ma15": 10100.0, "in_ma90": 10000.0}
        _gc2 = {"in_ma15": 201000.0, "in_ma90": 200000.0}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "u.json"
            store = Ma1592UniverseStore(path)
            stats = sync_universe_from_condition(
                {
                    "005930": {"stock_name": "삼성전자", "current_price": 70000, **_gc},
                    "000660": {"stock_name": "SK하이닉스", "current_price": 200000, **_gc2},
                },
                store=store,
            )
            self.assertEqual(stats["added"], 2)
            self.assertEqual(stats["rejected"], 0)
            self.assertEqual(set(store.l3_codes()), {"005930", "000660"})

            # EMA 없음/데드크로스는 신규 편입 거부
            stats_bad = sync_universe_from_condition(
                {
                    "035420": {"stock_name": "NAVER", "current_price": 200000},
                    "051910": {
                        "stock_name": "LG화학",
                        "current_price": 400000,
                        "in_ma15": 390000.0,
                        "in_ma90": 400000.0,
                    },
                },
                store=store,
            )
            self.assertEqual(stats_bad["added"], 0)
            self.assertEqual(stats_bad["rejected"], 2)
            self.assertNotIn("035420", store.l3_codes())
            self.assertNotIn("051910", store.l3_codes())

            # 조건식에서 빠져도 스티키 — 장부 유지
            stats2 = sync_universe_from_condition(
                {"005930": {"stock_name": "삼성전자", "current_price": 70000, **_gc}},
                store=store,
            )
            self.assertEqual(stats2["removed"], 0)
            self.assertEqual(set(store.l3_codes()), {"005930", "000660"})

            # EMA15≤EMA90(추세 전환) 시에만 제거
            self.assertTrue(
                remove_on_ema90_break(
                    "000660", ma15=9600, ma90=10000, store=store,
                )
            )
            self.assertNotIn("000660", store.l3_codes())
            self.assertIn("005930", store.l3_codes())

            # 보유 중이면 추세 전환으로도 장부 remove_on 은 False (청산 루프가 담당)
            row = store.get("005930")
            row.state = "MANAGE_FULL"
            store.upsert(row)
            self.assertFalse(
                remove_on_ema90_break(
                    "005930", ma15=9900, ma90=10000, store=store,
                )
            )
            self.assertIsNotNone(store.get("005930"))

            ok, _, row = upsert_from_condition("035420", "NAVER", price=200000, in_ma15=198000, in_ma90=195000, store=store)
            self.assertTrue(ok)
            self.assertEqual(row.in_ma15, 198000)
            self.assertEqual(row.in_ma92, 195000)
            self.assertTrue(row.in_at)

    def test_validate_condition_ledger_insert(self):
        from utils.ma1592 import upsert_from_condition, validate_condition_ledger_insert

        ok, reason = validate_condition_ledger_insert(10050, 10000)
        self.assertTrue(ok)
        self.assertEqual(reason, "OK")

        ok, reason = validate_condition_ledger_insert(9900, 10000)
        self.assertFalse(ok)
        self.assertEqual(reason, "NO_GC")

        ok, reason = validate_condition_ledger_insert(None, 10000)
        self.assertFalse(ok)
        self.assertEqual(reason, "NO_MA")

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "u.json"
            store = Ma1592UniverseStore(path)
            ok, reason, row = upsert_from_condition(
                "00010", "BAD", in_ma15=100.0, in_ma90=90.0, store=store,
            )
            self.assertFalse(ok)
            self.assertEqual(reason, "INVALID_CODE")
            self.assertIsNone(row)

            ok, reason, row = upsert_from_condition(
                "035420", "NAVER", in_ma15=198000, in_ma90=199000, store=store,
            )
            self.assertFalse(ok)
            self.assertEqual(reason, "NO_GC")

    def test_preview_price_lead_status(self):
        from utils.ma1592 import preview_price_lead_status

        gate = preview_price_lead_status(
            10100, 9700, 10000,
            params={"price_lead_near_pct": 1.0, "price_lead_far_pct": 3.0},
        )
        self.assertFalse(gate["gate_ok"])
        self.assertEqual(gate["reason_code"], "NOT_NEAR")

        gate_ok = preview_price_lead_status(
            10150, 10050, 10040,
            params={"price_lead_near_pct": 1.0, "price_lead_far_pct": 3.0},
        )
        self.assertTrue(gate_ok["gate_ok"])
        self.assertEqual(gate_ok["reason_code"], "PRICE_LEAD")


class Ma1592TrendLostTests(unittest.TestCase):
    def test_is_trend_lost_on_dead_cross(self):
        self.assertTrue(
            is_trend_lost(10000, 10050, params={"entry_trigger": "price_lead"}),
        )
        self.assertFalse(is_trend_lost(10100, 10000))

    def test_is_far_from_gc_zone(self):
        self.assertFalse(is_far_from_gc_zone(10000, 10050, params={"price_lead_far_pct": 3.0}))
        self.assertTrue(is_far_from_gc_zone(9600, 10000, params={"price_lead_far_pct": 3.0}))

    def test_normalize_chart_tf_maps_2m_to_3m(self):
        self.assertEqual(normalize_chart_tf("2M"), "3M")
        self.assertEqual(chart_tf_interval_minutes("3M"), 3)

    def test_price_lead_near_dc_kept_on_l3_bar(self):
        """5분 L3: 근접 역배열은 유지 — DC 정리는 purge 루프."""
        row = UniverseRow(
            stock_code="005930",
            state="GC_WATCH",
            expire_date="2099-01-01",
        )
        ma15, ma90 = 10000.0, 10050.0
        bar = {"open": 10100, "high": 10150, "low": 10090, "close": 10120}
        r = evaluate_setup_on_bar(
            row, bar, ma15, ma90=ma90,
            params={"hold_mode": "scale_in_gc", "entry_trigger": "price_lead"},
        )
        self.assertNotEqual(r.get("reason_code"), "TREND_LOST")
        self.assertEqual(row.state, "GC_WATCH")

    def test_purge_removes_on_dead_cross(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "u.json"
            store = Ma1592UniverseStore(path)
            store.upsert(UniverseRow(stock_code="024840", state="GC_WATCH"))
            self.assertTrue(
                remove_on_ema90_break(
                    "024840", ma15=5700, ma90=5720, store=store,
                )
            )
            self.assertNotIn("024840", store.l3_codes())

    def test_sync_manage_ledger_removes_without_holding(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "u.json"
            store = Ma1592UniverseStore(path)
            store.upsert(UniverseRow(stock_code="001820", state="MANAGE_FULL"))
            removed = sync_manage_ledger_with_holdings(set(), store=store)
            self.assertEqual(removed, ["001820"])
            self.assertNotIn("001820", store.manage_codes())

    def test_sync_manage_ledger_keeps_holding(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "u.json"
            store = Ma1592UniverseStore(path)
            store.upsert(UniverseRow(stock_code="001820", state="MANAGE_FULL"))
            removed = sync_manage_ledger_with_holdings({"001820"}, store=store)
            self.assertEqual(removed, [])
            self.assertIn("001820", store.manage_codes())

    def test_preview_entry_gate_status_gc_above(self):
        from utils.ma1592 import preview_entry_gate_status

        gate = preview_entry_gate_status(
            10050, 10000, 9900,
            params={"entry_trigger": "gc_above", "gc_entry_max_price_gap_pct": 1.0},
        )
        self.assertTrue(gate["gate_ok"])
        self.assertEqual(gate["reason_code"], "GC_ABOVE")

        gate_no = preview_entry_gate_status(
            10100, 9950, 10000,
            params={"entry_trigger": "gc_above"},
        )
        self.assertFalse(gate_no["gate_ok"])
        self.assertEqual(gate_no["reason_code"], "NO_GC")

        gate_chase = preview_entry_gate_status(
            10150, 10000, 9900,
            params={"entry_trigger": "gc_above", "gc_entry_max_price_gap_pct": 1.0},
        )
        self.assertFalse(gate_chase["gate_ok"])
        self.assertEqual(gate_chase["reason_code"], "GC_CHASE")

    def test_l1_limit_blocks_new_ledger_insert(self):
        from utils.ma1592 import is_l3_at_capacity, upsert_from_condition

        _gc = {"in_ma15": 10100.0, "in_ma90": 10000.0}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "u.json"
            store = Ma1592UniverseStore(path)
            params = {"l1_limit": 2}
            for i in range(2):
                code = f"{i:06d}"
                ok, reason, _ = upsert_from_condition(
                    code,
                    f"종목{i}",
                    price=10000,
                    **_gc,
                    store=store,
                    params=params,
                )
                self.assertTrue(ok, reason)
            self.assertTrue(is_l3_at_capacity(store, params=params))
            ok, reason, _ = upsert_from_condition(
                "999999",
                "초과",
                price=10000,
                **_gc,
                store=store,
                params=params,
            )
            self.assertFalse(ok)
            self.assertEqual(reason, "L1_LIMIT")
            self.assertEqual(len(store.l3_codes()), 2)

    def test_select_l3_codes_for_scan_newest_first(self):
        from utils.ma1592 import select_l3_codes_for_scan

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "u.json"
            store = Ma1592UniverseStore(path)
            store.upsert(UniverseRow(
                stock_code="111111", state="GC_WATCH",
                gc_at="2026-01-01T09:00:00",
            ))
            store.upsert(UniverseRow(
                stock_code="222222", state="GC_WATCH",
                gc_at="2026-01-02T09:00:00",
            ))
            store.upsert(UniverseRow(
                stock_code="333333", state="GC_WATCH",
                gc_at="2026-01-03T09:00:00",
            ))
            picked = select_l3_codes_for_scan(store, params={"l1_limit": 2})
            self.assertEqual(picked, ["333333", "222222"])

    def test_trim_l3_over_limit_removes_oldest(self):
        from utils.ma1592 import trim_l3_over_limit

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "u.json"
            store = Ma1592UniverseStore(path)
            store.upsert(UniverseRow(
                stock_code="111111", state="GC_WATCH",
                gc_at="2026-01-01T09:00:00",
            ))
            store.upsert(UniverseRow(
                stock_code="222222", state="GC_WATCH",
                gc_at="2026-01-02T09:00:00",
            ))
            store.upsert(UniverseRow(
                stock_code="333333", state="GC_WATCH",
                gc_at="2026-01-03T09:00:00",
            ))
            removed = trim_l3_over_limit(store, params={"l1_limit": 2})
            self.assertEqual(removed, ["111111"])
            self.assertEqual(set(store.l3_codes()), {"222222", "333333"})

    def test_sync_universe_counts_limit_skipped(self):
        from utils.ma1592 import sync_universe_from_condition

        _gc = {"in_ma15": 10100.0, "in_ma90": 10000.0}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "u.json"
            store = Ma1592UniverseStore(path)
            params = {"l1_limit": 1}
            stats = sync_universe_from_condition(
                {
                    "005930": {"stock_name": "삼성전자", "current_price": 70000, **_gc},
                    "000660": {"stock_name": "SK하이닉스", "current_price": 200000, **_gc},
                },
                store=store,
                params=params,
            )
            self.assertEqual(stats["added"], 1)
            self.assertEqual(stats["limit_skipped"], 1)
            self.assertEqual(len(store.l3_codes()), 1)


if __name__ == "__main__":
    unittest.main()
