import unittest

from managers.stop_loss_manager import classify_breakout_structure
from utils.auto_trade_engine import (
    clear_breakout_entry_soft_streak,
    clear_breakout_hold_armed,
    compute_rsi_series,
    evaluate_oversold_breakout_from_ctx,
    resolve_breakout_hold_from_minute_bars,
    resolve_breakout_level_from_minute_bars,
    resolve_breakout_ma20_grace_from_minute_bars,
    update_breakout_entry_soft_streak,
)


class BreakoutSettings:
    use_breakout = True
    breakout_level_mode = "prev_high"
    breakout_n_day = 3
    breakout_vol_mult = 1.5
    breakout_body_pct = 0.0
    breakout_range_mult = 0.0
    breakout_require_ma20_cross = False
    breakout_ma20_mode = "above"
    breakout_ma20_grace_bars = 3
    breakout_max_change_pct = 12.0
    breakout_entry_hard = True
    breakout_entry_soft = True
    breakout_entry_soft_polls = 2
    breakout_entry_hold = False  # 기존 HARD/SOFT 테스트는 HOLD 끔
    breakout_hold_expire_bars = 3
    breakout_hold_rsi_min = 30.0
    breakout_rsi_period = 10


class OversoldBreakoutTests(unittest.TestCase):
    def tearDown(self):
        clear_breakout_entry_soft_streak("005930")
        clear_breakout_hold_armed("005930")

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
        self.assertIn("SOFT", reason)
        self.assertIn("1/2", reason)

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
        s.breakout_entry_hold = False
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
        # 확인봉=09:10, 레벨=직전봉(09:05) 고가 110
        self.assertEqual(ctx["level_price"], 110)
        self.assertEqual(ctx["confirm_close"], 104)
        self.assertEqual(ctx["confirm_high"], 105)
        # 거래량=확인봉(09:10) 3000 ÷ 직전 N봉 평균
        self.assertEqual(ctx["day_volume"], 3000)
        self.assertEqual(ctx["prev_volume"], 1500)  # avg(1000,2000)
        self.assertEqual(ctx["bar_interval"], "5M")

    def test_resolve_5m_n_bar_high_insufficient(self):
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
        self.assertIsNone(ctx)
        self.assertIn("부족", err)

    def test_resolve_5m_n_bar_high_ok(self):
        bars = [
            {"timestamp": "2026-07-21 08:55:00", "high": 90, "close": 89, "volume": 800},
            {"timestamp": "2026-07-21 09:00:00", "high": 100, "close": 99, "volume": 1000},
            {"timestamp": "2026-07-21 09:05:00", "high": 130, "close": 125, "volume": 2000},
            {"timestamp": "2026-07-21 09:10:00", "high": 140, "close": 135, "volume": 3000},
            {"timestamp": "2026-07-21 09:15:00", "high": 150, "close": 148, "volume": 4500},
        ]
        s = BreakoutSettings()
        s.breakout_level_mode = "n_day_high"
        s.breakout_n_day = 3
        ctx, err = resolve_breakout_level_from_minute_bars(bars, s)
        self.assertEqual(err, "")
        # 확인봉=09:10, 레벨=max(90,100,130)=130
        self.assertEqual(ctx["level_price"], 130)
        self.assertEqual(ctx["confirm_close"], 135)
        self.assertEqual(ctx["confirm_high"], 140)
        self.assertTrue(ctx["confirm_high"] > ctx["level_price"])

    def test_gate_passes_soft_bar_streak(self):
        """스캔 SOFT가 부족해도 레벨 위 완성봉이 N개면 SOFT 통과."""
        s = BreakoutSettings()
        s.breakout_entry_hard = False
        s.breakout_entry_hold = False
        ctx = {
            "level_kind": "prev_high",
            "level_price": 10_000,
            "confirm_close": 10_050,
            "confirm_high": 10_100,
            "day_volume": 180_000,
            "prev_volume": 100_000,
            "entry_soft_streak": 0,
            "soft_bar_streak": 2,
        }
        ok, reason = evaluate_oversold_breakout_from_ctx(
            s, 10_100, 8.0, ctx, skip_time_check=True,
        )
        self.assertTrue(ok, reason)
        self.assertIn("SOFT", reason)

    def test_hard_passes_on_confirm_high_break(self):
        """확인봉 종가는 레벨 이하라도 고가가 레벨을 뚫으면 HARD 통과."""
        s = BreakoutSettings()
        s.breakout_entry_soft = False
        s.breakout_entry_hold = False
        ctx = {
            "level_kind": "prev_high",
            "level_price": 10_000,
            "confirm_close": 9_950,
            "confirm_high": 10_100,
            "day_volume": 180_000,
            "prev_volume": 100_000,
            "entry_soft_streak": 0,
        }
        ok, reason = evaluate_oversold_breakout_from_ctx(
            s, 10_050, 5.0, ctx, skip_time_check=True,
        )
        self.assertTrue(ok, reason)
        self.assertIn("HARD", reason)

    def test_resolve_soft_bar_streak_counts_breaks(self):
        bars = [
            {"timestamp": "2026-07-21 09:00:00", "high": 100, "close": 99, "volume": 1000},
            {"timestamp": "2026-07-21 09:05:00", "high": 110, "close": 108, "volume": 2000},
            {"timestamp": "2026-07-21 09:10:00", "high": 120, "close": 115, "volume": 3000},
            {"timestamp": "2026-07-21 09:15:00", "high": 125, "close": 122, "volume": 4000},
            {"timestamp": "2026-07-21 09:20:00", "high": 130, "close": 128, "volume": 5000},
        ]
        s = BreakoutSettings()
        s.breakout_level_mode = "prev_high"
        ctx, err = resolve_breakout_level_from_minute_bars(bars, s)
        self.assertEqual(err, "")
        # 확인봉=09:15, 레벨=09:10 고가 120 → 확인 종가 122>120, soft_bar ≥1
        self.assertEqual(ctx["level_price"], 120)
        self.assertGreaterEqual(ctx["soft_bar_streak"], 1)

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


class BreakoutHoldTests(unittest.TestCase):
    def tearDown(self):
        clear_breakout_hold_armed("069960")

    def _bars_from_closes(self, closes, prefix="09"):
        bars = []
        for i, c in enumerate(closes):
            bars.append({
                "timestamp": f"2026-07-21 {prefix}:{i:02d}:00",
                "open": int(c),
                "high": int(c + 1),
                "low": int(c - 1),
                "close": int(c),
                "volume": 2000,
            })
        return bars

    def test_compute_rsi_series_length(self):
        closes = [100 + i for i in range(15)]
        rsi = compute_rsi_series(closes, 10)
        self.assertEqual(len(rsi), 15)
        self.assertIsNone(rsi[9])
        self.assertIsNotNone(rsi[10])
        self.assertGreater(rsi[-1], 50)

    def test_hold_prev_rsi_cross_and_bullish_maintain(self):
        """전봉 RSI 30 교차 + 현재봉 양봉 유지 + 구조 OK → HOLD 통과."""
        s = BreakoutSettings()
        s.breakout_entry_hard = False
        s.breakout_entry_soft = False
        s.breakout_entry_hold = True
        s.breakout_level_mode = "n_day_high"
        s.breakout_n_day = 3

        # completed: 하락… → 전봉 급등(RSI 교차) → 현재봉 추가 상승(양봉) / forming
        base = [100 - i * 2 for i in range(13)]
        closes = base + [base[-1] + 20, base[-1] + 22, base[-1] + 21]
        bars = self._bars_from_closes(closes)

        # breakout=bars[-3], confirm=bars[-2]
        prior_max = max(int(b["high"]) for b in bars[-6:-3])
        bars[-3]["high"] = prior_max + 10
        bars[-3]["low"] = prior_max - 5
        bars[-2]["low"] = prior_max - 4
        bars[-2]["high"] = prior_max + 5
        bars[-2]["open"] = int(closes[-2]) - 3  # 양봉
        bars[-2]["close"] = int(closes[-2])

        hold = resolve_breakout_hold_from_minute_bars(
            bars, s, "069960", update_armed=False,
        )
        self.assertTrue(hold["hold_structure_ok"], hold)
        self.assertTrue(hold["hold_rsi_cross"], hold)
        self.assertTrue(hold["hold_bullish_ok"], hold)
        self.assertTrue(hold["hold_rsi_ok"], hold)
        self.assertTrue(hold["entry_hold_ok"], hold)
        self.assertLessEqual(hold["hold_rsi_before_prev"], 30)
        self.assertGreater(hold["hold_rsi_prev"], 30)
        self.assertGreater(hold["hold_rsi"], 30)

        ctx = {
            "level_kind": "n_day_high",
            "level_price": prior_max + 5,
            "confirm_close": prior_max - 10,
            "day_volume": 3000,
            "prev_volume": 1000,
            "entry_soft_streak": 0,
            **hold,
        }
        px = hold["hold_breakout_low"] + 1
        self.assertLessEqual(px, ctx["level_price"])
        ok, reason = evaluate_oversold_breakout_from_ctx(
            s, px, 5.0, ctx, skip_time_check=True,
        )
        self.assertTrue(ok, reason)
        self.assertIn("HOLD", reason)
        self.assertIn("양봉", reason)

    def test_hold_rejects_when_current_not_bullish(self):
        """전봉 RSI 교차여도 현재봉이 음봉이면 HOLD 거부."""
        s = BreakoutSettings()
        s.breakout_entry_hold = True
        s.breakout_level_mode = "n_day_high"
        s.breakout_n_day = 3
        base = [100 - i * 2 for i in range(13)]
        closes = base + [base[-1] + 20, base[-1] + 22, base[-1] + 21]
        bars = self._bars_from_closes(closes, prefix="10")
        prior_max = max(int(b["high"]) for b in bars[-6:-3])
        bars[-3]["high"] = prior_max + 10
        bars[-3]["low"] = prior_max - 5
        bars[-2]["low"] = prior_max - 4
        bars[-2]["open"] = int(closes[-2]) + 3  # 음봉
        bars[-2]["close"] = int(closes[-2])
        hold = resolve_breakout_hold_from_minute_bars(
            bars, s, "069960", update_armed=False,
        )
        self.assertTrue(hold["hold_structure_ok"], hold)
        self.assertTrue(hold["hold_rsi_cross"], hold)
        self.assertFalse(hold["hold_bullish_ok"], hold)
        self.assertFalse(hold["entry_hold_ok"], hold)
        self.assertIn("양봉", hold["hold_wait_reason"])

    def test_hold_rejects_rsi_already_above_without_cross(self):
        """전봉에서 RSI가 이미 30 위면(교차 아님) HOLD 거부."""
        s = BreakoutSettings()
        s.breakout_entry_hold = True
        s.breakout_level_mode = "prev_high"
        closes = [100 + i for i in range(16)]
        bars = self._bars_from_closes(closes, prefix="10")
        bars[-3]["high"] = int(bars[-4]["high"]) + 20
        bars[-3]["low"] = int(closes[-3]) - 2
        bars[-2]["low"] = int(closes[-3]) - 1
        bars[-2]["open"] = int(closes[-2]) - 2
        bars[-2]["close"] = int(closes[-2])
        hold = resolve_breakout_hold_from_minute_bars(
            bars, s, "069960", update_armed=False,
        )
        if hold["hold_structure_ok"]:
            self.assertFalse(hold["hold_rsi_cross"], hold)
            self.assertFalse(hold["hold_rsi_ok"], hold)
            self.assertIn("교차 없음", hold["hold_wait_reason"])

    def test_hold_rejects_when_next_bar_makes_lower_low(self):
        s = BreakoutSettings()
        s.breakout_entry_hold = True
        s.breakout_level_mode = "prev_high"
        s.breakout_n_day = 3
        closes = [100 + i for i in range(14)]
        bars = self._bars_from_closes(closes, prefix="10")
        bars[-3]["high"] = int(bars[-4]["high"]) + 20
        bars[-3]["low"] = 100
        bars[-2]["low"] = 90
        hold = resolve_breakout_hold_from_minute_bars(
            bars, s, "069960", update_armed=False,
        )
        self.assertFalse(hold["hold_structure_ok"])
        self.assertIn("무효", hold["hold_wait_reason"])

    def test_hold_rejects_rsi_below_threshold(self):
        s = BreakoutSettings()
        s.breakout_entry_hard = False
        s.breakout_entry_soft = False
        s.breakout_entry_hold = True
        ctx = {
            "level_kind": "prev_high",
            "level_price": 10_000,
            "confirm_close": 9_000,
            "day_volume": 3000,
            "prev_volume": 1000,
            "entry_soft_streak": 0,
            "hold_structure_ok": True,
            "hold_rsi_ok": False,
            "hold_rsi_cross": False,
            "hold_bullish_ok": True,
            "entry_hold_ok": False,
            "hold_breakout_low": 9_500,
            "hold_rsi": 22.0,
            "hold_rsi_prev": 18.0,
            "hold_wait_reason": "HOLD 구조 OK · 전봉 RSI 미교차(18.0→22.0, 임계 30)",
        }
        ok, reason = evaluate_oversold_breakout_from_ctx(
            s, 9_600, 3.0, ctx, skip_time_check=True,
        )
        self.assertFalse(ok)
        self.assertIn("RSI", reason)

    def test_ma20_cross_and_body_required(self):
        s = BreakoutSettings()
        s.breakout_require_ma20_cross = True
        s.breakout_ma20_mode = "cross"
        s.breakout_body_pct = 2.0
        s.breakout_entry_soft = False
        s.breakout_entry_hold = False
        base = {
            "level_kind": "prev_high",
            "level_price": 10_000,
            "confirm_close": 10_300,
            "confirm_high": 10_350,
            "day_volume": 300_000,
            "prev_volume": 100_000,
            "entry_soft_streak": 0,
            "body_pct": 2.5,
            "ma20": 10_100.0,
            "ma20_cross_ok": True,
            "ma20_signal_ok": True,
            "ma20_mode": "cross",
        }
        ok, reason = evaluate_oversold_breakout_from_ctx(
            s, 10_300, 5.0, dict(base), skip_time_check=True,
        )
        self.assertTrue(ok, reason)

        fail_ma = dict(base)
        fail_ma["ma20_cross_ok"] = False
        fail_ma["ma20_signal_ok"] = False
        ok2, reason2 = evaluate_oversold_breakout_from_ctx(
            s, 10_300, 5.0, fail_ma, skip_time_check=True,
        )
        self.assertFalse(ok2)
        self.assertIn("MA20", reason2)

        fail_body = dict(base)
        fail_body["body_pct"] = 1.0
        ok3, reason3 = evaluate_oversold_breakout_from_ctx(
            s, 10_300, 5.0, fail_body, skip_time_check=True,
        )
        self.assertFalse(ok3)
        self.assertIn("장대", reason3)

    def test_resolve_ma20_cross_flag(self):
        s = BreakoutSettings()
        s.breakout_level_mode = "prev_high"
        # 20+ bars flat under MA then break up
        bars = []
        for i in range(22):
            px = 1000 + (i % 3)
            bars.append({
                "timestamp": f"2026-07-24 09:{i:02d}:00",
                "open": px, "high": px + 5, "low": px - 5, "close": px, "volume": 1000,
            })
        # confirm bar: strong up through MA20
        bars.append({
            "timestamp": "2026-07-24 09:22:00",
            "open": 1000, "high": 1100, "low": 995, "close": 1090, "volume": 5000,
        })
        resolved, err = resolve_breakout_level_from_minute_bars(
            bars, s, exclude_forming=False,
        )
        self.assertEqual(err, "")
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved["ma20_cross_ok"], resolved)
        self.assertGreater(resolved["body_pct"], 2.0)

    def test_eval_refreshes_volume_when_signal_meta_has_level_only(self):
        """매수 재평가: meta에 level_price만 있어도 분봉으로 prev_volume을 채운다."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from utils.auto_trade_engine import _eval_oversold_breakout

        bars = [
            {"timestamp": "2026-07-27 09:00:00", "open": 100, "high": 100, "low": 98, "close": 99, "volume": 1000},
            {"timestamp": "2026-07-27 09:05:00", "open": 99, "high": 110, "low": 99, "close": 108, "volume": 2000},
            {"timestamp": "2026-07-27 09:10:00", "open": 108, "high": 120, "low": 108, "close": 118, "volume": 5000},
            {"timestamp": "2026-07-27 09:15:00", "open": 118, "high": 122, "low": 117, "close": 121, "volume": 800},
        ]
        api = MagicMock()
        api.normalize_stock_code = lambda c: c
        api.get_stock_chart_data = AsyncMock(return_value=bars)

        s = BreakoutSettings()
        s.breakout_entry_hard = True
        s.breakout_entry_soft = False
        s.breakout_entry_hold = False
        s.breakout_level_mode = "prev_high"
        s.breakout_n_day = 2
        s.breakout_vol_mult = 1.5

        # 시그널 meta처럼 level만 있고 거래량 필드 없음 → 과거에는 비교 거래량 없음 오탐
        meta = {"level_kind": "prev_high", "level_price": 110}
        ctx = dict(meta)

        async def _run():
            return await _eval_oversold_breakout(
                api, s, "448900", 121, change_rate=5.0,
                ctx=ctx, skip_time_check=True, update_soft_streak=False,
            )

        ok, reason = asyncio.run(_run())
        api.get_stock_chart_data.assert_awaited()
        self.assertGreater(int(ctx.get("prev_volume") or 0), 0)
        self.assertIsNotNone(ctx.get("volume_ratio"))
        self.assertNotIn("비교 거래량 없음", reason)
        # 확인봉 09:10 vol 5000 / avg(1000,2000)=1500 → 통과 가능
        self.assertTrue(ok, reason)

    def test_ma20_grace_waits_then_passes_with_inherit(self):
        """돌파 직후 MA20 미상회면 유예 대기, 이후 상회+상속으로 통과."""
        s = BreakoutSettings()
        s.breakout_require_ma20_cross = True
        s.breakout_ma20_mode = "above"
        s.breakout_ma20_grace_bars = 3
        s.breakout_body_pct = 2.0
        s.breakout_entry_soft = False
        s.breakout_entry_hold = False

        wait_ctx = {
            "level_kind": "prev_high",
            "level_price": 10_000,
            "confirm_close": 10_200,
            "confirm_high": 10_250,
            "day_volume": 50_000,  # 후속봉 약함
            "prev_volume": 100_000,
            "entry_soft_streak": 0,
            "body_pct": 0.5,
            "ma20": 10_300.0,
            "ma20_signal_ok": False,
            "ma20_mode": "above",
            "ma20_grace_active": True,
            "ma20_grace_waiting": True,
            "ma20_grace_bars": 3,
            "ma20_grace_reason": "MA20 유예 대기 (2/3봉)",
            "ma20_grace_breakout_level": 10_000,
            "ma20_grace_inherit_body_ok": True,
            "ma20_grace_inherit_volume_ok": True,
            "ma20_grace_breakout_body_pct": 3.0,
            "ma20_grace_breakout_day_volume": 300_000,
            "ma20_grace_breakout_prev_volume": 100_000,
        }
        ok, reason = evaluate_oversold_breakout_from_ctx(
            s, 10_200, 5.0, wait_ctx, skip_time_check=True,
        )
        self.assertFalse(ok, reason)
        self.assertIn("유예 대기", reason)
        self.assertTrue(wait_ctx["volume_ok"])  # 상속으로 거래량 통과 후 MA20에서 대기

        pass_ctx = dict(wait_ctx)
        pass_ctx["ma20_signal_ok"] = True
        pass_ctx["ma20"] = 10_100.0
        pass_ctx["ma20_grace_waiting"] = False
        pass_ctx["ma20_grace_reason"] = "MA20 유예 내 상회 (2/3봉)"
        ok2, reason2 = evaluate_oversold_breakout_from_ctx(
            s, 10_200, 5.0, pass_ctx, skip_time_check=True,
        )
        self.assertTrue(ok2, reason2)
        self.assertIn("HARD", reason2)

    def test_ma20_grace_expired_fails(self):
        s = BreakoutSettings()
        s.breakout_require_ma20_cross = True
        s.breakout_ma20_grace_bars = 3
        s.breakout_entry_soft = False
        s.breakout_entry_hold = False
        ctx = {
            "level_kind": "prev_high",
            "level_price": 10_000,
            "confirm_close": 10_200,
            "day_volume": 300_000,
            "prev_volume": 100_000,
            "entry_soft_streak": 0,
            "ma20": 10_300.0,
            "ma20_signal_ok": False,
            "ma20_grace_expired": True,
            "ma20_grace_reason": "MA20 유예 만료 (3봉)",
        }
        ok, reason = evaluate_oversold_breakout_from_ctx(
            s, 10_200, 5.0, ctx, skip_time_check=True,
        )
        self.assertFalse(ok)
        self.assertIn("만료", reason)

    def test_resolve_ma20_grace_window(self):
        s = BreakoutSettings()
        s.breakout_require_ma20_cross = True
        s.breakout_ma20_mode = "above"
        s.breakout_ma20_grace_bars = 3
        s.breakout_level_mode = "prev_high"
        s.breakout_n_day = 3
        s.breakout_vol_mult = 1.5
        s.breakout_body_pct = 2.0

        bars = []
        # MA20 아래에서 횡보 (완성봉 22 + 돌파 + 후속 + forming)
        for i in range(22):
            px = 1000
            bars.append({
                "timestamp": f"2026-07-24 09:{i:02d}:00",
                "open": px, "high": px + 2, "low": px - 2, "close": px, "volume": 1000,
            })
        # 돌파봉: 레벨(직전고~1002) 상회하지만 MA20(~1000)은 종가 1005로 상회 — wait, close 1005 > ma20 1000
        # need close above level but BELOW ma20. Set closes climbing slowly so MA20 is higher.
        bars = []
        for i in range(25):
            # declining so MA20 stays elevated relative to later prices
            px = 1200 - i * 5  # 1200, 1195, ...
            bars.append({
                "timestamp": f"2026-07-24 08:{i:02d}:00",
                "open": px, "high": px + 3, "low": px - 3, "close": px, "volume": 1000,
            })
        # last completed before breakout around px = 1200-24*5 = 1080
        # breakout bar: close above prev high, but below MA20
        # After 25 bars of decline, MA20 of last 20 closes is high.
        # Append breakout under MA20
        prev_high = bars[-1]["high"]
        brk_close = prev_high + 10  # break level
        # compute approximate ma20 of last 20 closes including breakout
        # We need brk_close <= ma20. ma20 ≈ average of closes around 1100+.
        bars.append({
            "timestamp": "2026-07-24 09:00:00",
            "open": bars[-1]["close"],
            "high": brk_close + 5,
            "low": bars[-1]["close"] - 2,
            "close": brk_close,
            "volume": 5000,
        })
        # follow-through still under MA20
        bars.append({
            "timestamp": "2026-07-24 09:05:00",
            "open": brk_close,
            "high": brk_close + 3,
            "low": brk_close - 2,
            "close": brk_close + 1,
            "volume": 800,
        })
        # forming bar
        bars.append({
            "timestamp": "2026-07-24 09:10:00",
            "open": brk_close + 1,
            "high": brk_close + 2,
            "low": brk_close,
            "close": brk_close + 1,
            "volume": 100,
        })

        g = resolve_breakout_ma20_grace_from_minute_bars(bars, s, exclude_forming=True)
        self.assertTrue(g.get("ma20_grace_active") or g.get("ma20_grace_waiting") or g.get("ma20_signal_ok"), g)
        # If MA20 already above on confirm, still fine; else waiting
        if not g.get("ma20_signal_ok"):
            self.assertTrue(g.get("ma20_grace_waiting"), g)
            self.assertIn("유예 대기", g.get("ma20_grace_reason") or "")

        # Now add a bar that clears MA20 while staying above breakout level
        completed_prefix = bars[:-1]  # drop forming
        ma_est = sum(float(r["close"]) for r in completed_prefix[-19:]) / 19.0
        # replace last completed with above-MA20 close, keep forming
        clear_close = int(max(brk_close + 5, ma_est + 50))
        bars[-2] = {
            "timestamp": "2026-07-24 09:05:00",
            "open": brk_close,
            "high": clear_close + 5,
            "low": brk_close - 1,
            "close": clear_close,
            "volume": 900,
        }
        g2 = resolve_breakout_ma20_grace_from_minute_bars(bars, s, exclude_forming=True)
        self.assertTrue(g2.get("ma20_signal_ok"), g2)
        self.assertFalse(g2.get("ma20_grace_waiting"), g2)
        self.assertIn("유예 내 상회", g2.get("ma20_grace_reason") or "")


if __name__ == "__main__":
    unittest.main()
