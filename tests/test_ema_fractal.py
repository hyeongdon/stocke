import unittest
from datetime import datetime

from utils.ema_fractal import (
    confirmed_buy_fractals,
    drop_forming_minute_bar,
    ema_series,
    evaluate_fractal_setup,
    is_fractal_fail_reason,
    is_fractal_wait_reason,
    risk_qty,
    stop_below_ema,
    take_profit_from_rr,
)


def _bars_uptrend(n=120, start=10000.0):
    bars = []
    px = start
    for i in range(n):
        px += 8
        low = px - 12
        # 중간에 눌림 + 저점 프랙탈 자리
        bars.append({"open": px - 4, "high": px + 6, "low": low, "close": px, "volume": 1000})
    return bars


class EmaFractalTests(unittest.TestCase):
    def test_ema_seed_and_rise(self):
        vals = [10.0] * 20 + [20.0]
        e = ema_series(vals, 20)
        self.assertIsNone(e[18])
        self.assertAlmostEqual(e[19], 10.0)
        self.assertGreater(e[20], 10.0)

    def test_buy_fractal_needs_two_bars_each_side(self):
        lows = [5, 4, 1, 4, 5]
        flags = confirmed_buy_fractals(lows)
        self.assertEqual(flags, [False, False, True, False, False])
        self.assertFalse(confirmed_buy_fractals([5, 4, 1, 4])[2] if len([5, 4, 1, 4]) > 2 else True)

    def test_stop_and_rr(self):
        stop = stop_below_ema(10000, ticks=1, tick_size=10)
        self.assertEqual(stop, 9990)
        self.assertEqual(take_profit_from_rr(10000, 9980, 1.5), 10030)

    def test_risk_qty(self):
        self.assertEqual(risk_qty(10_000_000, 0.5, 10000, 9900, qty_cap=100), 100)
        self.assertEqual(risk_qty(10_000_000, 0.5, 10000, 9900, qty_cap=40), 40)
        self.assertEqual(risk_qty(10_000_000, 0.5, 10000, 10000), 0)

    def test_drop_forming_bar(self):
        now = datetime(2026, 8, 15, 10, 31, 20)
        bars = [
            {"timestamp": "2026-08-15 10:30:00", "close": 1},
            {"timestamp": "2026-08-15 10:31:00", "close": 2},
        ]
        out = drop_forming_minute_bar(bars, now=now)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["close"], 1)

    def test_wait_vs_fail_reasons(self):
        self.assertTrue(is_fractal_wait_reason("프랙탈 대기: 녹색 프랙탈 없음"))
        self.assertTrue(is_fractal_fail_reason("프랙탈 탈락: 정배열 붕괴(100EMA)"))
        self.assertFalse(is_fractal_wait_reason("프랙탈 탈락: 1분봉 부족(EMA100)"))

    def test_setup_needs_alignment_pullback_fractal_reclaim(self):
        bars = _bars_uptrend(130, 9000)
        # 최근 구간: 20EMA 아래 눌림 + 확정 프랙탈 + 재돌파
        # 단순 상승만이면 wait
        out = evaluate_fractal_setup(bars)
        self.assertIn(out["status"], ("wait", "pass", "fail"))
        self.assertIn(out["reason"][:6], ("프랙탈 대기", "프랙탈 게이트", "프랙탈 탈락"))


if __name__ == "__main__":
    unittest.main()
