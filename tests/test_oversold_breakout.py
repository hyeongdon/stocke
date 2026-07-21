import unittest

from managers.stop_loss_manager import classify_breakout_structure
from utils.auto_trade_engine import (
    clear_breakout_entry_soft_streak,
    evaluate_oversold_breakout_from_ctx,
    resolve_breakout_level_from_minute_bars,
    update_breakout_entry_soft_streak,
)


class BreakoutSettings:
    use_breakout = True
    breakout_level_mode = "prev_high"
    breakout_n_day = 3
    breakout_vol_mult = 1.5
    breakout_max_change_pct = 12.0
    breakout_entry_hard = True
    breakout_entry_soft = True
    breakout_entry_soft_polls = 2


class OversoldBreakoutTests(unittest.TestCase):
    def tearDown(self):
        clear_breakout_entry_soft_streak("005930")

    def test_gate_passes_hard_when_prior_close_above_level(self):
        ctx = {
            "level_kind": "prev_high",
            "level_price": 10_000,
            "confirm_close": 10_050,
            "day_volume": 180_000,
            "prev_volume": 100_000,
            "entry_soft_streak": 0,
        }
        ok, reason = evaluate_oversold_breakout_from_ctx(
            BreakoutSettings(), 10_100, 8.0, ctx, skip_time_check=True,
        )
        self.assertTrue(ok)
        self.assertIn("HARD", reason)
        self.assertEqual(ctx["entry_confirm_mode"], "HARD")
        self.assertEqual(ctx["volume_ratio"], 1.8)

    def test_gate_waits_without_hard_or_soft(self):
        ctx = {
            "level_kind": "prev_high",
            "level_price": 10_000,
            "confirm_close": 9_900,
            "day_volume": 180_000,
            "prev_volume": 100_000,
            "entry_soft_streak": 1,
        }
        ok, reason = evaluate_oversold_breakout_from_ctx(
            BreakoutSettings(), 10_100, 8.0, ctx, skip_time_check=True,
        )
        self.assertFalse(ok)
        self.assertIn("진입 확인 대기", reason)
        self.assertIn("SOFT 1/2", reason)

    def test_gate_passes_soft_streak(self):
        ctx = {
            "level_kind": "prev_high",
            "level_price": 10_000,
            "confirm_close": 9_900,
            "day_volume": 180_000,
            "prev_volume": 100_000,
            "entry_soft_streak": 2,
        }
        ok, reason = evaluate_oversold_breakout_from_ctx(
            BreakoutSettings(), 10_100, 8.0, ctx, skip_time_check=True,
        )
        self.assertTrue(ok)
        self.assertIn("SOFT 2/2", reason)

    def test_gate_touch_when_both_disabled(self):
        s = BreakoutSettings()
        s.breakout_entry_hard = False
        s.breakout_entry_soft = False
        ctx = {
            "level_kind": "prev_high",
            "level_price": 10_000,
            "confirm_close": 9_000,
            "day_volume": 180_000,
            "prev_volume": 100_000,
            "entry_soft_streak": 0,
        }
        ok, reason = evaluate_oversold_breakout_from_ctx(
            s, 10_100, 8.0, ctx, skip_time_check=True,
        )
        self.assertTrue(ok)
        self.assertIn("TOUCH", reason)

    def test_gate_rejects_overheat_and_weak_volume(self):
        base = {
            "level_kind": "n_day_high",
            "level_price": 10_000,
            "confirm_close": 10_100,
            "day_volume": 200_000,
            "prev_volume": 100_000,
            "entry_soft_streak": 2,
        }
        overheat, _ = evaluate_oversold_breakout_from_ctx(
            BreakoutSettings(), 10_100, 12.0, dict(base), skip_time_check=True,
        )
        weak_volume, _ = evaluate_oversold_breakout_from_ctx(
            BreakoutSettings(),
            10_100,
            8.0,
            {**base, "day_volume": 149_000},
            skip_time_check=True,
        )
        self.assertFalse(overheat)
        self.assertFalse(weak_volume)

    def test_soft_streak_counter(self):
        clear_breakout_entry_soft_streak("005930")
        self.assertEqual(update_breakout_entry_soft_streak("005930", True), 1)
        self.assertEqual(update_breakout_entry_soft_streak("005930", True), 2)
        self.assertEqual(update_breakout_entry_soft_streak("005930", False), 0)

    def test_resolve_5m_prev_high_and_volume_avg(self):
        bars = [
            {"timestamp": "2026-07-21 09:00:00", "high": 100, "close": 98, "volume": 1000},
            {"timestamp": "2026-07-21 09:05:00", "high": 110, "close": 108, "volume": 2000},
            {"timestamp": "2026-07-21 09:10:00", "high": 105, "close": 104, "volume": 3000},
            {"timestamp": "2026-07-21 09:15:00", "high": 120, "close": 115, "volume": 4500},
        ]
        s = BreakoutSettings()
        s.breakout_level_mode = "prev_high"
        s.breakout_n_day = 3
        ctx, err = resolve_breakout_level_from_minute_bars(bars, s)
        self.assertEqual(err, "")
        self.assertEqual(ctx["level_kind"], "prev_high")
        self.assertEqual(ctx["level_price"], 105)
        self.assertEqual(ctx["confirm_close"], 104)
        self.assertEqual(ctx["day_volume"], 4500)
        self.assertEqual(ctx["prev_volume"], 2000)  # avg(1000,2000,3000)
        self.assertEqual(ctx["bar_interval"], "5M")

    def test_resolve_5m_n_bar_high(self):
        bars = [
            {"timestamp": "2026-07-21 09:00:00", "high": 100, "close": 99, "volume": 1000},
            {"timestamp": "2026-07-21 09:05:00", "high": 130, "close": 125, "volume": 2000},
            {"timestamp": "2026-07-21 09:10:00", "high": 105, "close": 104, "volume": 3000},
            {"timestamp": "2026-07-21 09:15:00", "high": 120, "close": 118, "volume": 4500},
        ]
        s = BreakoutSettings()
        s.breakout_level_mode = "n_day_high"
        s.breakout_n_day = 3
        ctx, err = resolve_breakout_level_from_minute_bars(bars, s)
        self.assertEqual(err, "")
        self.assertEqual(ctx["level_kind"], "n_day_high")
        self.assertEqual(ctx["level_price"], 130)
        self.assertEqual(ctx["confirm_close"], 104)

    def test_structure_classification_respects_hard_soft_order(self):
        self.assertEqual(
            classify_breakout_structure(9_790, 10_000, 1.0, 2.0), "HARD",
        )
        self.assertEqual(
            classify_breakout_structure(9_890, 10_000, 1.0, 2.0), "SOFT",
        )
        self.assertEqual(
            classify_breakout_structure(9_950, 10_000, 1.0, 2.0), "NONE",
        )


if __name__ == "__main__":
    unittest.main()
