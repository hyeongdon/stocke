"""차트 OHLCV 마트 스토어·정규화 테스트."""
from __future__ import annotations

import unittest
from datetime import date

from utils.chart_ohlcv_mart_store import (
    get_bars,
    get_meta,
    normalize_bars,
    upsert_series,
)


def _bars(n=5, start="2026-01-02"):
    from datetime import timedelta

    day = date.fromisoformat(start)
    out = []
    i = 0
    while len(out) < n:
        if day.weekday() < 5:
            c = 10000 + i * 100
            out.append({
                "timestamp": f"{day.isoformat()} 15:30:00",
                "open": c - 50,
                "high": c + 80,
                "low": c - 90,
                "close": c,
                "volume": 1000 + i,
            })
            i += 1
        day += timedelta(days=1)
    return out


class ChartOhlcvMartTests(unittest.TestCase):
    def test_normalize_bars_sorts_and_dedupes(self):
        raw = [
            {"timestamp": "2026-01-03", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10},
            {"timestamp": "2026-01-02 15:30:00", "open": 1, "high": 2, "low": 1, "close": 1, "volume": 5},
            {"timestamp": "2026-01-02", "open": 9, "high": 9, "low": 9, "close": 9, "volume": 9},
            {"timestamp": "2026-01-04", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0},
        ]
        cleaned = normalize_bars(raw)
        self.assertEqual([r["timestamp"] for r in cleaned], ["2026-01-02", "2026-01-03"])
        self.assertEqual(cleaned[0]["close"], 1)

    def test_upsert_and_get_roundtrip(self):
        code = "000150"
        bars = _bars(6)
        self.assertTrue(len(bars) >= 3)
        row = upsert_series(code, "1D", bars, stock_name="두산", as_of_date=date(2026, 11, 19))
        self.assertIsNotNone(row)
        loaded = get_bars(code, "1D")
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded), len(normalize_bars(bars)))
        self.assertEqual(loaded[0]["timestamp"][:10], normalize_bars(bars)[0]["timestamp"][:10])
        meta = get_meta(code, "1D")
        self.assertEqual(meta["stock_name"], "두산")
        self.assertEqual(meta["bar_count"], len(loaded))
        self.assertEqual(meta["as_of_date"], "2026-11-19")


if __name__ == "__main__":
    unittest.main()
