"""역매공파(ymgp) 일봉 단계·진입·익절 단위 테스트."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from utils.ymgp_engine import (
    entry1_breakout_ok,
    entry2_pullback_ok,
    evaluate_ymgp_from_daily,
    is_reverse_array,
    partial_sell_qty,
    sma_at,
    stop_invalidated,
    take_profit_target,
)


def _bar(o, h, l, c, v=1000, day="2026-01-01"):
    return {
        "timestamp": f"{day} 15:30:00",
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
    }


class TestYmgpEngine(unittest.TestCase):
    def test_sma_and_reverse_array(self):
        closes = [float(i) for i in range(1, 11)]
        self.assertEqual(sma_at(closes, 5), sum(closes[-5:]) / 5)
        mas = {"ma_fast": 30.0, "ma_mid": 20.0, "ma_slow": 10.0}
        self.assertFalse(is_reverse_array(mas))  # 정배열
        self.assertTrue(is_reverse_array({"ma_fast": 10.0, "ma_mid": 20.0, "ma_slow": 30.0}))

    def test_entry1_ref_high(self):
        settings = SimpleNamespace(ymgp_entry_mode="ref_high")
        ref = {"high": 10000, "low": 9000, "open": 9500}
        ok, _ = entry1_breakout_ok(10001, ref, settings)
        self.assertTrue(ok)
        ok2, _ = entry1_breakout_ok(10000, ref, settings)
        self.assertFalse(ok2)

    def test_entry2_pullback_ma20(self):
        settings = SimpleNamespace(ymgp_enable_pullback_add=True, ymgp_pullback_tol_pct=2.0)
        ref = {"high": 12000, "low": 9000, "open": 10000}
        mas = {"ma20": 10000.0}
        ok, reason = entry2_pullback_ok(10050, ref, mas, settings)
        self.assertTrue(ok)
        self.assertTrue("MA20" in reason or "기준봉" in reason)

    def test_stop_ref_low(self):
        settings = SimpleNamespace(
            struct_break_soft_pct=1.0,
            struct_break_hard_pct=2.0,
            ymgp_stop_ma_mode="ma60",
        )
        ref = {"low": 10000}
        mas = {"ma60": 8000.0}
        ok, detail = stop_invalidated(9800, ref, mas, settings)
        self.assertTrue(ok)
        self.assertIn("저점", detail)

    def test_partial_sell_and_tp_targets(self):
        settings = SimpleNamespace(ymgp_tp1_pct_of_pos=0.35, ymgp_tp2_pct_of_pos=0.35)
        self.assertEqual(partial_sell_qty(100, 0, settings), 35)
        self.assertEqual(partial_sell_qty(100, 1, settings), 35)
        self.assertEqual(partial_sell_qty(10, 2, settings), 10)
        t1, l1 = take_profit_target(0, {"high": 15000.0}, {})
        self.assertEqual(t1, 15000.0)
        self.assertIn("T1", l1)
        t2, l2 = take_profit_target(1, None, {"ma224": 20000.0})
        self.assertEqual(t2, 20000.0)
        self.assertIn("T2", l2)

    def test_evaluate_needs_bars(self):
        out = evaluate_ymgp_from_daily([], None)
        self.assertEqual(out["stage"], "NONE")
        self.assertTrue("일봉" in out["reason"])

    def test_accum_bull_and_wick_paths(self):
        from utils.ymgp_engine import find_accum_bar

        bars = []
        for i in range(30):
            day = f"2026-06-{(i % 28) + 1:02d}"
            bars.append(_bar(1000, 1010, 990, 1005, v=1000, day=day))
        # 짧은 양봉(+1.8%) — 몸통 7% 미달 → 탈락
        bars.append(_bar(1000, 1020, 990, 1018, v=3000, day="2026-07-14"))
        settings = SimpleNamespace(
            ymgp_accum_vol_mult=2.0,
            ymgp_accum_body_pct=7.0,
            ymgp_accum_wick_vol_mult=4.0,
            ymgp_accum_wick_body_mult=1.5,
        )
        self.assertIsNone(find_accum_bar(bars, settings, lookback=5))

        # 장대 양봉(+18%) + vol 3x
        bars.append(_bar(1000, 1200, 1000, 1180, v=3000, day="2026-07-15"))
        bull = find_accum_bar(bars, settings, lookback=5)
        self.assertIsNotNone(bull)
        self.assertEqual(bull["kind"], "bull")
        self.assertEqual(bull["date"], "2026-07-15")
        self.assertGreaterEqual(bull.get("body_pct") or 0, 7.0)

        # 장대 윗꼬리(음봉) — 동화 7/16 유사
        bars.append(_bar(6230, 7560, 5650, 5740, v=8000, day="2026-07-16"))
        wick = find_accum_bar(bars, settings, lookback=5)
        self.assertIsNotNone(wick)
        self.assertEqual(wick["kind"], "wick")
        self.assertEqual(wick["date"], "2026-07-16")
        self.assertEqual(wick["high"], 7560)

        bars[-1] = _bar(6230, 7560, 5650, 5740, v=3500, day="2026-07-16")
        only_bull = find_accum_bar(bars, settings, lookback=5)
        self.assertEqual(only_bull["kind"], "bull")
        self.assertEqual(only_bull["date"], "2026-07-15")

    def test_ymgp_entry_helper_and_date_norm(self):
        from utils.ymgp_engine import bars_for_ymgp_eval, evaluate_ymgp_entry_from_daily

        bars = []
        for i in range(80):
            c = 10000 - i * 10
            day = f"2026-01-{(i % 28) + 1:02d}"
            bars.append({
                "date": day,
                "open": c, "high": c + 50, "low": c - 50, "close": c, "volume": 1000,
            })
        settings = SimpleNamespace(
            ymgp_ma_fast=20, ymgp_ma_mid=40, ymgp_ma_slow=60,
            ymgp_box_days=15, ymgp_box_width_pct=50.0,
            ymgp_accum_vol_mult=2.0, ymgp_accum_body_pct=7.0, ymgp_accum_wick_vol_mult=4.0,
            ymgp_accum_wick_body_mult=1.5, ymgp_ma_near_pct=5.0,
            ymgp_pivot_tol_pct=3.0, ymgp_drop_lookback=60, ymgp_drop_pct=-10.0,
            ymgp_max_change_pct=30.0, ymgp_entry_mode="ref_high",
        )
        norm = bars_for_ymgp_eval(bars)
        self.assertTrue(str(norm[0].get("timestamp") or "").startswith("2026-"))
        ok, reason, meta = evaluate_ymgp_entry_from_daily(
            bars, settings, current_price=99999, change_rate=1.0, asof_idx=len(bars) - 2,
        )
        self.assertIn("ymgp_stage", meta)
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(reason, str)

    def test_evaluate_reverse_filtered_minimal(self):
        bars = []
        for i in range(500):
            c = 20000 - i * 20
            if c < 5000:
                c = 5000 + (i % 10) * 5
            day = f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
            bars.append(_bar(c, c + 50, c - 50, c, v=1000 + (i % 5) * 100, day=day))
        settings = SimpleNamespace(
            ymgp_ma_fast=20,
            ymgp_ma_mid=40,
            ymgp_ma_slow=60,
            ymgp_box_days=15,
            ymgp_box_width_pct=50.0,
            ymgp_accum_vol_mult=1.2,
            ymgp_ma_near_pct=5.0,
            ymgp_pivot_tol_pct=3.0,
            ymgp_drop_lookback=60,
            ymgp_drop_pct=-10.0,
            ymgp_max_change_pct=30.0,
        )
        out = evaluate_ymgp_from_daily(bars, settings, current_price=int(bars[-1]["close"]))
        self.assertIn(out["stage"], ("NONE", "FILTERED", "READY", "ARMED"))
        self.assertIsInstance(out["checks"], list)

    def test_checks_have_numeric_actuals_and_summary(self):
        from utils.ymgp_engine import (
            format_ymgp_checks_summary,
            format_ymgp_fail_brief,
            log_ymgp_stage_metrics,
        )

        bars = []
        for i in range(120):
            # 완만한 하락 → 역배열 유도
            c = 15000 - i * 30
            day = f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
            bars.append(_bar(c, c + 80, c - 80, c, v=2000 + i * 10, day=day))
        settings = SimpleNamespace(
            ymgp_ma_fast=20,
            ymgp_ma_mid=40,
            ymgp_ma_slow=60,
            ymgp_box_days=15,
            ymgp_box_width_pct=12.0,
            ymgp_accum_vol_mult=2.0,
            ymgp_accum_body_pct=7.0,
            ymgp_ma_near_pct=3.0,
            ymgp_pivot_tol_pct=2.0,
            ymgp_drop_lookback=60,
            ymgp_drop_pct=-20.0,
            ymgp_max_change_pct=30.0,
        )
        out = evaluate_ymgp_from_daily(bars, settings, current_price=int(bars[-1]["close"]))
        by_key = {c["key"]: c for c in out["checks"]}
        self.assertIn("reverse_array", by_key)
        self.assertRegex(str(by_key["drop_sideways"]["actual"]), r"%")
        self.assertRegex(str(by_key["vol_revival"]["actual"]), r"×|평균|봉부족")
        self.assertIn("box", by_key)
        summary = format_ymgp_checks_summary(out)
        self.assertIn("stage=", summary)
        self.assertIn("reverse_array=", summary)
        brief = format_ymgp_fail_brief(out)
        self.assertTrue(isinstance(brief, str))
        # force log twice: first emits, identical second within cooldown returns None
        self.assertIsNotNone(log_ymgp_stage_metrics("999999", out, stock_name="테스트", force=True))
        self.assertIsNone(log_ymgp_stage_metrics("999999", out, stock_name="테스트"))


if __name__ == "__main__":
    unittest.main()
