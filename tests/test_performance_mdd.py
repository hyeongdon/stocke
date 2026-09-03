import unittest

from utils.performance_stats import compute_performance


def _trades(*rows):
    out = []
    for date, net in rows:
        out.append({
            "ts": f"{date}T06:00:00",
            "date": date,
            "reason": "매도",
            "stock_code": "000000",
            "stock_name": "T",
            "gross": float(net),
            "cost": 0.0,
            "net": float(net),
        })
    return out


class PerformanceMddTests(unittest.TestCase):
    def test_daily_rows_include_win_and_loss_counts(self):
        trades = _trades(
            ("2026-08-04", 100_000),
            ("2026-08-04", -50_000),
            ("2026-08-04", 0),
        )
        out = compute_performance(trades, 10_000_000, "db", "app")

        self.assertEqual(out["daily"][0]["count"], 3)
        self.assertEqual(out["daily"][0]["wins"], 1)
        self.assertEqual(out["daily"][0]["losses"], 1)

    def test_mdd_from_daily_equity_high_not_intraday_trough(self):
        """같은 날 장중 큰 손실 후 회복하면 MDD에 장중 저점을 쓰지 않는다."""
        trades = _trades(
            ("2026-08-04", -200_000),
            ("2026-08-04", 250_000),
            ("2026-08-05", -10_000),
        )
        out = compute_performance(trades, 10_000_000, "db", "app")
        self.assertEqual(out["mdd"], -10_000)
        self.assertEqual(out["mdd_peak_date"], "2026-08-04")
        self.assertEqual(out["mdd_trough_date"], "2026-08-05")
        self.assertAlmostEqual(out["mdd_pct"], -0.1, places=1)

    def test_mdd_giveback_after_new_equity_high(self):
        trades = _trades(
            ("2026-08-01", 100_000),
            ("2026-08-02", 50_000),
            ("2026-08-03", -80_000),
        )
        out = compute_performance(trades, 10_000_000, "db", "app")
        self.assertEqual(out["mdd"], -80_000)
        self.assertEqual(out["mdd_peak_date"], "2026-08-02")
        self.assertEqual(out["mdd_trough_date"], "2026-08-03")

    def test_mdd_from_seed_when_never_in_profit(self):
        trades = _trades(
            ("2026-07-24", -100_000),
            ("2026-07-25", -50_000),
        )
        out = compute_performance(trades, 10_000_000, "db", "app")
        self.assertEqual(out["mdd"], -150_000)
        self.assertEqual(out["mdd_pct"], -1.5)
        self.assertEqual(out["mdd_peak_date"], "2026-07-24")
        self.assertEqual(out["mdd_trough_date"], "2026-07-25")


if __name__ == "__main__":
    unittest.main()
