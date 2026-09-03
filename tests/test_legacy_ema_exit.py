"""레거시 5분 EMA SOFT 청산 판정 테스트."""
from datetime import datetime, timedelta
from unittest import TestCase

from utils.datetime_kst import KST
from utils.legacy_ema_exit import (
    classify_legacy_ema_exit_detail,
    evaluate_legacy_ema_soft_exit,
    legacy_ema_exit_params,
)


def _bars(n, start_px=10000.0, start=None, step=0.0):
    start = start or datetime(2026, 8, 21, 9, 0, tzinfo=KST)
    rows = []
    px = float(start_px)
    for i in range(n):
        ts = start + timedelta(minutes=i * 5)
        rows.append({
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "open": px,
            "high": px,
            "low": px,
            "close": px,
            "volume": 1000,
        })
        px += step
    return rows


class LegacyEmaExitParamsTests(TestCase):
    def test_defaults(self):
        on, period, soft, band = legacy_ema_exit_params(None)
        self.assertTrue(on)
        self.assertEqual(period, 90)
        self.assertEqual(soft, 10)
        self.assertEqual(band, 1.0)

    def test_disabled_keeps_numbers(self):
        on, period, soft, band = legacy_ema_exit_params({
            "legacy_ema_exit_enabled": False,
            "legacy_ema_exit_period": 60,
            "legacy_ema_exit_soft_min": 5,
            "legacy_ema_exit_band_pct": 1.5,
        })
        self.assertFalse(on)
        self.assertEqual(period, 60)
        self.assertEqual(soft, 5)
        self.assertEqual(band, 1.5)

    def test_clamp(self):
        on, period, soft, band = legacy_ema_exit_params({
            "legacy_ema_exit_period": 999,
            "legacy_ema_exit_soft_min": 0,
            "legacy_ema_exit_band_pct": 99,
        })
        self.assertTrue(on)
        self.assertEqual(period, 300)
        self.assertEqual(soft, 10)
        self.assertEqual(band, 10.0)


class LegacyEmaSoftExitTests(TestCase):
    def test_not_enough_bars(self):
        out = evaluate_legacy_ema_soft_exit(
            _bars(40), 10000, period=90, soft_minutes=10,
            now=datetime(2026, 8, 21, 10, 0, tzinfo=KST),
        )
        self.assertFalse(out["ok"])
        self.assertFalse(out["triggered"])
        self.assertIn("부족", out["reason"])

    def test_above_ema_not_triggered(self):
        bars = _bars(100, start_px=10000)
        now = datetime(2026, 8, 21, 10, 40, tzinfo=KST)
        out = evaluate_legacy_ema_soft_exit(
            bars, 10100, now=now, period=90, soft_minutes=10,
        )
        self.assertTrue(out["ok"])
        self.assertFalse(out["below"])
        self.assertFalse(out["triggered"])
        self.assertEqual(out["consecutive"], 0)

    def test_one_confirmed_bar_waits(self):
        flat = _bars(90, start_px=10000)
        drop_start = datetime(2026, 8, 21, 16, 30, tzinfo=KST)
        drop = _bars(1, start_px=9000, start=drop_start)
        now = datetime(2026, 8, 21, 16, 35, tzinfo=KST)
        out = evaluate_legacy_ema_soft_exit(
            flat + drop, 9000, now=now, period=90, soft_minutes=10,
        )
        self.assertTrue(out["below"])
        self.assertFalse(out["triggered"])
        self.assertEqual(out["consecutive"], 1)
        self.assertEqual(out["reason"], "soft_wait")

    def test_two_confirmed_bars_trigger(self):
        flat = _bars(90, start_px=10000)
        drop_start = datetime(2026, 8, 21, 16, 30, tzinfo=KST)
        drop = _bars(2, start_px=9000, start=drop_start)
        now = datetime(2026, 8, 21, 16, 40, tzinfo=KST)
        out = evaluate_legacy_ema_soft_exit(
            flat + drop, 9000, now=now, period=90, soft_minutes=10,
        )
        self.assertTrue(out["triggered"])
        self.assertEqual(out["consecutive"], 2)
        self.assertEqual(out["required_bars"], 2)
        self.assertIn("SOFT≧10분", out["detail"])
        self.assertIn("EMA90", out["detail"])

    def test_live_recovery_resets(self):
        flat = _bars(90, start_px=10000)
        drop_start = datetime(2026, 8, 21, 10, 30, tzinfo=KST)
        drop = _bars(2, start_px=9000, start=drop_start)
        now = datetime(2026, 8, 21, 10, 40, tzinfo=KST)
        out = evaluate_legacy_ema_soft_exit(
            flat + drop, 10050, now=now, period=90, soft_minutes=10,
        )
        self.assertFalse(out["below"])
        self.assertFalse(out["triggered"])
        self.assertEqual(out["consecutive"], 0)

    def test_yesterday_bars_do_not_count(self):
        yday = datetime(2026, 8, 20, 14, 50, tzinfo=KST)
        yesterday = _bars(90, start_px=10000, start=yday)
        yesterday_drop = _bars(4, start_px=9000, start=datetime(2026, 8, 20, 15, 0, tzinfo=KST))
        today = _bars(
            1, start_px=9000, start=datetime(2026, 8, 21, 9, 0, tzinfo=KST),
        )
        now = datetime(2026, 8, 21, 9, 5, tzinfo=KST)
        out = evaluate_legacy_ema_soft_exit(
            yesterday + yesterday_drop + today, 9000,
            now=now, period=90, soft_minutes=10,
        )
        self.assertTrue(out["below"])
        self.assertFalse(out["triggered"])
        self.assertEqual(out["consecutive"], 1)

    def test_buy_time_cuts_streak(self):
        flat = _bars(90, start_px=10000)
        drop_start = datetime(2026, 8, 21, 10, 30, tzinfo=KST)
        drop = _bars(3, start_px=9000, start=drop_start)
        buy = datetime(2026, 8, 21, 10, 39, tzinfo=KST)
        now = datetime(2026, 8, 21, 10, 45, tzinfo=KST)
        out = evaluate_legacy_ema_soft_exit(
            flat + drop, 9000, now=now, period=90, soft_minutes=10,
            buy_time=buy,
        )
        self.assertTrue(out["below"])
        self.assertFalse(out["triggered"])
        self.assertLess(out["consecutive"], 2)

    def test_custom_period_and_soft(self):
        flat = _bars(20, start_px=10000)
        drop = _bars(1, start_px=8000, start=datetime(2026, 8, 21, 10, 40, tzinfo=KST))
        now = datetime(2026, 8, 21, 10, 45, tzinfo=KST)
        out = evaluate_legacy_ema_soft_exit(
            flat + drop, 8000, now=now, period=20, soft_minutes=5,
        )
        self.assertTrue(out["triggered"])
        self.assertEqual(out["period"], 20)
        self.assertEqual(out["soft_minutes"], 5)

    def test_within_1pct_is_not_break(self):
        bars = _bars(100, start_px=10000)
        now = datetime(2026, 8, 21, 10, 40, tzinfo=KST)
        out = evaluate_legacy_ema_soft_exit(
            bars, 9950, now=now, period=90, soft_minutes=10, band_pct=1.0,
        )
        self.assertFalse(out["below"])
        self.assertFalse(out["triggered"])

    def test_exactly_1pct_is_allowed(self):
        bars = _bars(100, start_px=10000)
        now = datetime(2026, 8, 21, 10, 40, tzinfo=KST)
        out = evaluate_legacy_ema_soft_exit(
            bars, 9900, now=now, period=90, soft_minutes=10, band_pct=1.0,
        )
        self.assertFalse(out["below"])

    def test_beyond_1pct_counts_as_break(self):
        flat = _bars(90, start_px=10000)
        drop_start = datetime(2026, 8, 21, 10, 30, tzinfo=KST)
        drop = _bars(2, start_px=9800, start=drop_start)
        now = datetime(2026, 8, 21, 10, 40, tzinfo=KST)
        out = evaluate_legacy_ema_soft_exit(
            flat + drop, 9800, now=now, period=90, soft_minutes=10, band_pct=1.0,
        )
        self.assertTrue(out["below"])
        self.assertTrue(out["triggered"])
        self.assertIn("이격>1%", out["detail"])

    def test_recovery_into_band_resets(self):
        flat = _bars(90, start_px=10000)
        drop_start = datetime(2026, 8, 21, 10, 30, tzinfo=KST)
        drop = _bars(2, start_px=9000, start=drop_start)
        now = datetime(2026, 8, 21, 10, 40, tzinfo=KST)
        out = evaluate_legacy_ema_soft_exit(
            flat + drop, 9950, now=now, period=90, soft_minutes=10, band_pct=1.0,
        )
        self.assertFalse(out["below"])
        self.assertFalse(out["triggered"])
        self.assertEqual(out["consecutive"], 0)

    def test_classify_detail(self):
        self.assertEqual(
            classify_legacy_ema_exit_detail(
                "EMA90 이탈(SOFT≧10분): 현재 9,000 ≤ EMA 10,000 (연속 10분)"
            ),
            "ema_soft",
        )
        self.assertIsNone(classify_legacy_ema_exit_detail("고정손절 PCT"))


class UsesLegacyEmaExitScopeTests(TestCase):
    def test_legacy_breakout_sangtta(self):
        from types import SimpleNamespace
        from managers.stop_loss_manager import StopLossManager

        uses = StopLossManager._uses_legacy_ema_exit
        self.assertTrue(uses(SimpleNamespace(strategy_key="legacy")))
        self.assertTrue(uses(SimpleNamespace(strategy_key="screener")))
        self.assertTrue(uses(SimpleNamespace(strategy_key="breakout")))
        self.assertTrue(uses(SimpleNamespace(strategy_key="sangtta")))
        self.assertFalse(uses(SimpleNamespace(strategy_key="jongga")))
        self.assertFalse(uses(SimpleNamespace(strategy_key="fractal")))


if __name__ == "__main__":
    import unittest
    unittest.main()
