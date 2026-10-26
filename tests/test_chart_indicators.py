"""분석 화면 차트 지표 유닛테스트 (PRD_ANALYSIS_WEEKLY_CHART §8)."""
import unittest
from datetime import date, datetime, timedelta

from utils.chart_indicators import (
    SIGNAL_RSI_OS,
    SIGNAL_WR_BUY,
    annotate_indicators,
    chart_params,
    detect_signals,
    is_week_partial,
    mark_daily_partial,
    merge_weekly_rows,
    resample_weekly_ohlcv,
    warmup_warning,
)
from utils.rwm_bowl import DEFAULT_PARAMS, merge_params, wilder_rsi_series, williams_r_series

HOLIDAYS = {
    date(2024, 9, 16), date(2024, 9, 17), date(2024, 9, 18),  # 추석 (월~수)
    date(2024, 12, 31), date(2025, 1, 1),  # 연말 휴장 · 신정
    date(2025, 10, 3),  # 개천절 (금)
}


def _td(d):
    return d.weekday() < 5 and d not in HOLIDAYS


def _daily(start, end, base=1000):
    rows, d, i = [], start, 0
    while d <= end:
        if _td(d):
            c = base + i * 10
            rows.append({
                "timestamp": f"{d.isoformat()} 15:30:00",
                "open": c - 5, "high": c + 20, "low": c - 20, "close": c, "volume": 100,
            })
            i += 1
        d += timedelta(days=1)
    return rows


def _osc_rows(rsi=None, wr=None, partial_last=False):
    n = len(rsi or wr)
    rows = []
    for i in range(n):
        rows.append({
            "timestamp": (date(2026, 1, 5) + timedelta(weeks=i)).isoformat(),
            "rsi": rsi[i] if rsi else None,
            "wr": wr[i] if wr else None,
            "partial": partial_last and i == n - 1,
        })
    return rows


CP = {"rsi_period": 14, "rsi_os": 30.0, "wr_period": 93, "wr_os": -80.0, "wr_min_bottom": 2}


class ResampleWeeklyTests(unittest.TestCase):
    def test_chuseok_week_groups_remaining_days(self):
        rows = _daily(date(2024, 9, 9), date(2024, 9, 27))
        weeks = resample_weekly_ohlcv(rows, now=datetime(2024, 9, 30, 9, 0), is_trading_day=_td)
        self.assertEqual([w["timestamp"] for w in weeks], ["2024-09-13", "2024-09-20", "2024-09-27"])
        chuseok = weeks[1]  # 9/19(목) · 9/20(금) 만
        thu = next(r for r in rows if r["timestamp"].startswith("2024-09-19"))
        fri = next(r for r in rows if r["timestamp"].startswith("2024-09-20"))
        self.assertEqual(chuseok["open"], thu["open"])
        self.assertEqual(chuseok["close"], fri["close"])
        self.assertEqual(chuseok["high"], max(thu["high"], fri["high"]))
        self.assertEqual(chuseok["low"], min(thu["low"], fri["low"]))
        self.assertEqual(chuseok["volume"], 200)
        self.assertTrue(all(not w["partial"] for w in weeks))

    def test_year_end_holiday_week_is_one_iso_week(self):
        rows = _daily(date(2024, 12, 23), date(2025, 1, 10))
        weeks = resample_weekly_ohlcv(rows, now=datetime(2025, 1, 13, 9, 0), is_trading_day=_td)
        # 12/30(월) · 1/2(목) · 1/3(금) → ISO 2025-W01 한 주
        self.assertEqual([w["timestamp"] for w in weeks], ["2024-12-27", "2025-01-03", "2025-01-10"])
        self.assertEqual(weeks[1]["volume"], 300)
        self.assertEqual(weeks[1]["open"], rows[5]["open"])  # 12/30 시가

    def test_in_progress_week_is_partial(self):
        rows = _daily(date(2026, 10, 12), date(2026, 10, 21))  # 마지막 봉 10/21(수)
        weeks = resample_weekly_ohlcv(rows, now=datetime(2026, 10, 21, 16, 0), is_trading_day=_td)
        self.assertFalse(weeks[0]["partial"])
        self.assertTrue(weeks[-1]["partial"])
        self.assertEqual(weeks[-1]["timestamp"], "2026-10-21")

    def test_week_closes_after_last_trading_day_close(self):
        # 2025-10-03(금) 개천절 → 10/2(목)가 그 주 마지막 거래일
        last = date(2025, 10, 2)
        self.assertTrue(is_week_partial(last, datetime(2025, 10, 2, 14, 0), _td))
        self.assertFalse(is_week_partial(last, datetime(2025, 10, 2, 15, 30), _td))
        self.assertFalse(is_week_partial(last, datetime(2025, 10, 4, 10, 0), _td))
        # 지난 주는 항상 확정
        self.assertFalse(is_week_partial(date(2025, 9, 24), datetime(2025, 9, 29, 9, 0), _td))

    def test_merge_api_weeks_with_daily_resample(self):
        now = datetime(2024, 10, 2, 10, 0)
        api = [
            {"timestamp": "2024-09-09 15:30:00", "open": 1, "high": 9, "low": 1, "close": 5, "volume": 1},
            {"timestamp": "2024-09-16 15:30:00", "open": 2, "high": 9, "low": 1, "close": 6, "volume": 1},
            {"timestamp": "2024-09-23 15:30:00", "open": 3, "high": 9, "low": 1, "close": 7, "volume": 1},
        ]
        daily = resample_weekly_ohlcv(_daily(date(2024, 9, 19), date(2024, 10, 2)), now=now, is_trading_day=_td)
        merged = merge_weekly_rows(api, daily, now=now, is_trading_day=_td)
        self.assertEqual([w["timestamp"] for w in merged], ["2024-09-13", "2024-09-20", "2024-09-27", "2024-10-02"])
        self.assertEqual(merged[1]["open"], 2)  # 일봉 첫 주(잘렸을 수 있음) → 키움 주봉 유지
        self.assertEqual(merged[2]["close"], daily[1]["close"])  # 이후 주는 일봉 리샘플 우선
        self.assertTrue(merged[-1]["partial"])
        self.assertFalse(merged[0]["partial"])

    def test_daily_partial_before_close(self):
        rows = _daily(date(2026, 10, 19), date(2026, 10, 26))
        self.assertTrue(mark_daily_partial(rows, datetime(2026, 10, 26, 11, 0))[-1]["partial"])
        self.assertFalse(mark_daily_partial(rows, datetime(2026, 10, 26, 15, 40))[-1]["partial"])


class SignalTests(unittest.TestCase):
    def test_rsi_os_only_first_bar_below(self):
        rows = _osc_rows(rsi=[35, 28, 25, 32, 29])
        sigs = detect_signals(rows, CP)
        self.assertEqual([(s["idx"], s["type"], s["value"]) for s in sigs], [(1, SIGNAL_RSI_OS, 28), (4, SIGNAL_RSI_OS, 29)])

    def test_wr_buy_after_two_bottom_bars(self):
        rows = _osc_rows(wr=[-50, -85, -90, -70])
        sigs = detect_signals(rows, CP)
        self.assertEqual(len(sigs), 1)
        self.assertEqual((sigs[0]["idx"], sigs[0]["type"], sigs[0]["bottom_bars"]), (3, SIGNAL_WR_BUY, 2))

    def test_wr_one_bottom_bar_no_signal(self):
        self.assertEqual(detect_signals(_osc_rows(wr=[-50, -85, -70]), CP), [])

    def test_partial_and_warmup_excluded(self):
        rows = _osc_rows(rsi=[35, 28, 40, 40, 29], partial_last=True)
        sigs = detect_signals(rows, CP, start=2)
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["idx"], 2)  # 표시 구간 기준
        self.assertTrue(sigs[0]["partial"])


class IndicatorTests(unittest.TestCase):
    def _rows(self, n=200):
        rows = []
        for i in range(n):
            c = 1000 + (i % 17) * 13 - (i % 5) * 7
            rows.append({"timestamp": f"2025-01-{1 + i % 28:02d}", "open": c, "high": c + 15, "low": c - 15, "close": c})
        return rows

    def test_params_follow_rwm(self):
        self.assertEqual(chart_params("1W")["wr_period"], DEFAULT_PARAMS["w_period"])
        self.assertEqual(chart_params("1W", merge_params({"rsi_os_weekly": 25}))["rsi_os"], 25.0)
        self.assertEqual(chart_params("1D", merge_params({"rsi_os_daily": 33}))["rsi_os"], 33.0)
        self.assertEqual(chart_params("1D", merge_params({"w_period": 14}))["wr_period"], 14)

    def test_annotate_matches_rwm_series(self):
        rows = annotate_indicators(self._rows(), CP)
        closes = [r["close"] for r in rows]
        rsi = wilder_rsi_series(closes, 14)
        wr = williams_r_series([r["high"] for r in rows], [r["low"] for r in rows], closes, 93)
        self.assertEqual(rows[-1]["rsi"], round(rsi[-1], 2))
        self.assertEqual(rows[-1]["wr"], round(wr[-1], 2))
        self.assertIsNone(rows[91]["wr"])
        self.assertIsNotNone(rows[92]["wr"])
        self.assertNotIn("ma120", rows[118])
        self.assertIn("ma120", rows[119])

    def test_warmup_warning(self):
        short = annotate_indicators(self._rows(100), CP)
        self.assertIn("봉 부족", warmup_warning(short, "주"))
        full = annotate_indicators(self._rows(250), CP)
        self.assertEqual(warmup_warning(full[-104:], "주"), "")


if __name__ == "__main__":
    unittest.main()
