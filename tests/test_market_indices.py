import os
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

from utils.datetime_kst import KST
from utils.market_indices import (
    _CACHE,
    _HIST_CACHE,
    _INV_HIST_CACHE,
    _INV_TIME_CACHE,
    _downsample_investor_intraday,
    _fetch_investor_daily,
    _fetch_investor_intraday,
    _fetch_investor_trend,
    _merge_live_investor,
    _parse_investor_day_rows,
    _parse_investor_time_rows,
    _parse_signed_int,
    _parse_sise_json,
    clear_investor_snapshot_cache,
    fetch_market_indices,
    fetch_market_indices_for_date,
    investor_refresh_due,
    refresh_investor_flow_snapshot,
)


KR_JSON = """
[['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],
["20260813", 6773.92, 6895.63, 6748.13, 6813.34, 418947, 0.0],
["20260814", 6995.67, 7010.86, 6848.43, 6977.94, 332942, 0.0]
]
"""

KOSDAQ_JSON = """
[['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],
["20260813", 863.15, 870.96, 853.09, 861.37, 1, 0.0],
["20260814", 868.07, 879.29, 845.41, 864.65, 1, 0.0]
]
"""

NAS_ROWS = [
    {
        "symb": "NAS@IXIC",
        "xymd": "20260814",
        "open": 26631.34,
        "high": 26875.52,
        "low": 26612.85,
        "clos": 26803.03,
        "diff": 214.54,
        "rate": 0.81,
    },
]
DJI_ROWS = [
    {
        "symb": "DJI@DJI",
        "xymd": "20260814",
        "open": 53828.55,
        "high": 54049.14,
        "low": 53622.46,
        "clos": 53839.99,
        "diff": 69.72,
        "rate": 0.13,
    },
]


class MarketIndicesDateTests(unittest.TestCase):
    def setUp(self):
        _HIST_CACHE.clear()

    def test_parse_sise_json_closes(self):
        rows = _parse_sise_json(KR_JSON)
        self.assertEqual(rows[-1]["date"], "2026-08-14")
        self.assertAlmostEqual(rows[-1]["close"], 6977.94)
        self.assertAlmostEqual(rows[-1]["open"], 6995.67)

    @mock.patch("utils.market_indices.kst_today")
    @mock.patch("utils.market_indices.requests.get")
    def test_fetch_for_date_uses_close_and_prev(self, mock_get, mock_today):
        mock_today.return_value = mock.Mock(isoformat=lambda: "2026-08-15")

        def _resp(url, **kwargs):
            r = mock.Mock()
            r.raise_for_status = mock.Mock()
            if "symbol=KOSPI" in url:
                r.text = KR_JSON
                r.json = mock.Mock(side_effect=AssertionError)
            elif "symbol=KOSDAQ" in url:
                r.text = KOSDAQ_JSON
            elif "NAS@IXIC" in url:
                r.text = ""
                r.json = mock.Mock(return_value=NAS_ROWS)
            elif "DJI@DJI" in url:
                r.text = ""
                r.json = mock.Mock(return_value=DJI_ROWS)
            else:
                raise AssertionError(url)
            return r

        mock_get.side_effect = _resp
        payload = fetch_market_indices_for_date("2026-08-14")
        self.assertEqual(payload["date"], "2026-08-14")
        self.assertEqual(payload["as_of"], "close")
        by_key = {x["key"]: x for x in payload["indices"]}
        kospi = by_key["kospi"]
        self.assertAlmostEqual(kospi["value"], 6977.94)
        self.assertAlmostEqual(kospi["change"], round(6977.94 - 6813.34, 2))
        self.assertAlmostEqual(kospi["open"], 6995.67)
        self.assertAlmostEqual(kospi["high"], 7010.86)
        self.assertAlmostEqual(kospi["low"], 6848.43)
        self.assertGreaterEqual(len(kospi["bars"]), 1)
        self.assertAlmostEqual(kospi["bars"][-1]["open"], 6995.67)
        self.assertFalse(kospi["closed"])
        self.assertAlmostEqual(by_key["nasdaq"]["value"], 26803.03)
        self.assertEqual(by_key["dow"]["label"], "다우")

    @mock.patch("utils.market_indices.kst_today")
    @mock.patch("utils.market_indices.requests.get")
    def test_closed_when_no_exact_bar(self, mock_get, mock_today):
        mock_today.return_value = mock.Mock(isoformat=lambda: "2026-08-16")

        def _resp(url, **kwargs):
            r = mock.Mock()
            r.raise_for_status = mock.Mock()
            if "siseJson" in url:
                r.text = KR_JSON
            else:
                r.json = mock.Mock(return_value=NAS_ROWS)
            return r

        mock_get.side_effect = _resp
        payload = fetch_market_indices_for_date("2026-08-15")
        self.assertTrue(all(x["closed"] for x in payload["indices"]))


TREND_KOSPI = {"bizdate": "20260818", "personalValue": "+7,414", "foreignValue": "+914", "institutionalValue": "-7,951"}
TREND_KOSDAQ = {"bizdate": "20260818", "personalValue": "+3,905", "foreignValue": "+366", "institutionalValue": "-4,177"}


def _time_page_html(rows):
    """rows: [(HH:MM, personal, foreign, institution), ...] → 네이버 시간별 표 HTML 모사."""
    trs = "".join(
        f"<tr><td>{t}</td>"
        f"<td>{p}</td><td>{f}</td><td>{i}</td>"
        f"<td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td><td>7</td></tr>"
        for t, p, f, i in rows
    )
    return f'<table class="type_1"><tr class="udline"><th rowspan="2">시간</th></tr>{trs}</table>'


def _day_page_html(rows):
    """rows: [(dot_date, personal, foreign, institution), ...] → 네이버 표 HTML 모사."""
    trs = "".join(
        f'<tr><td class="date2">{d}</td>'
        f'<td class="rate_up3">{p}</td><td class="rate_up3">{f}</td><td class="rate_down3">{i}</td>'
        f"<td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td><td>7</td></tr>"
        for d, p, f, i in rows
    )
    return f'<table class="type_1"><tr class="udline"><th rowspan="2">날짜</th></tr>{trs}</table>'


def _poll_payload(code):
    return {
        "result": {
            "areas": [
                {"datas": [{"cd": code, "nv": 697794, "cv": 2500, "cr": 0.36, "rf": "2", "ov": 695000, "hv": 700000, "lv": 690000}]}
            ]
        }
    }


class InvestorTrendTests(unittest.TestCase):
    def setUp(self):
        _CACHE["at"] = 0.0
        _CACHE["data"] = None
        _INV_HIST_CACHE.clear()
        _INV_TIME_CACHE.clear()
        self._snap_dir = tempfile.TemporaryDirectory()
        self._snap_path = os.path.join(self._snap_dir.name, "snap.json")
        self._path_patch = mock.patch("utils.market_indices.INV_SNAPSHOT_PATH", self._snap_path)
        self._path_patch.start()
        clear_investor_snapshot_cache()

    def tearDown(self):
        self._path_patch.stop()
        clear_investor_snapshot_cache()
        self._snap_dir.cleanup()

    def test_parse_signed_int(self):
        self.assertEqual(_parse_signed_int("+7,414"), 7414)
        self.assertEqual(_parse_signed_int("-7,951"), -7951)
        self.assertIsNone(_parse_signed_int(""))
        self.assertIsNone(_parse_signed_int(None))
        self.assertIsNone(_parse_signed_int("abc"))

    @mock.patch("utils.market_indices.requests.get")
    def test_fetch_investor_trend(self, mock_get):
        r = mock.Mock()
        r.raise_for_status = mock.Mock()
        r.json = mock.Mock(return_value=TREND_KOSPI)
        mock_get.return_value = r
        trend = _fetch_investor_trend("KOSPI")
        self.assertEqual(trend["foreign"], 914)
        self.assertEqual(trend["institution"], -7951)
        self.assertEqual(trend["personal"], 7414)
        self.assertEqual(trend["bizdate"], "2026-08-18")

    @mock.patch("utils.market_indices.requests.get")
    def test_fetch_investor_trend_empty(self, mock_get):
        r = mock.Mock()
        r.raise_for_status = mock.Mock()
        r.json = mock.Mock(return_value={})
        mock_get.return_value = r
        self.assertIsNone(_fetch_investor_trend("KOSPI"))

    def test_parse_investor_day_rows(self):
        html = _day_page_html([
            ("26.08.18", "7,420", "914", "-7,951"),
            ("26.08.14", "-19,820", "30,387", "-10,298"),
        ])
        rows = _parse_investor_day_rows(html)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["date"], "2026-08-18")
        self.assertEqual(rows[0]["personal"], 7420)
        self.assertEqual(rows[0]["foreign"], 914)
        self.assertEqual(rows[0]["institution"], -7951)
        self.assertEqual(rows[1]["foreign"], 30387)

    def test_parse_investor_time_rows(self):
        html = _time_page_html([
            ("18:06", "7,420", "914", "-7,951"),
            ("14:22", "4,922", "2,707", "-7,113"),
        ])
        rows = _parse_investor_time_rows(html)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["time"], "18:06")
        self.assertEqual(rows[0]["foreign"], 914)
        self.assertEqual(rows[0]["institution"], -7951)
        self.assertEqual(rows[1]["time"], "14:22")
        self.assertEqual(rows[1]["foreign"], 2707)

    def test_downsample_keeps_first_last(self):
        rows = [{"time": f"{9 + i // 60:02d}:{i % 60:02d}", "foreign": i} for i in range(200)]
        out = _downsample_investor_intraday(rows, max_points=20)
        self.assertLessEqual(len(out), 20)
        self.assertEqual(out[0]["time"], rows[0]["time"])
        self.assertEqual(out[-1]["time"], rows[-1]["time"])
        self.assertEqual(out[-1]["foreign"], 199)

    @mock.patch("utils.market_indices.kst_today")
    @mock.patch("utils.market_indices.requests.get")
    def test_fetch_investor_intraday_sorted(self, mock_get, mock_today):
        mock_today.return_value = mock.Mock(
            isoformat=lambda: "2026-08-18",
            strftime=lambda fmt: "20260818",
        )
        pages = {
            1: _time_page_html([
                ("18:06", "7420", "914", "-7951"),
                ("15:30", "7300", "1000", "-7800"),
            ]),
            2: _time_page_html([
                ("10:00", "200", "400", "-500"),
                ("09:01", "50", "80", "-90"),
            ]),
        }

        def _resp(url, **kwargs):
            r = mock.Mock()
            r.raise_for_status = mock.Mock()
            page = int(url.rsplit("page=", 1)[1])
            r.text = pages.get(page, "")
            return r

        mock_get.side_effect = _resp
        rows = _fetch_investor_intraday("KOSPI")
        self.assertEqual([r["time"] for r in rows], ["09:01", "10:00", "15:30", "18:06"])
        self.assertEqual(rows[-1]["institution"], -7951)
        called = [c.args[0] for c in mock_get.call_args_list]
        self.assertTrue(any("sosok=01" in u for u in called))
        mock_get.reset_mock()
        again = _fetch_investor_intraday("KOSPI")
        self.assertEqual(len(again), 4)
        mock_get.assert_not_called()

    @mock.patch("utils.market_indices.kst_today")
    @mock.patch("utils.market_indices.requests.get")
    def test_incomplete_intraday_uses_ttl_instead_of_full_recrawl(self, mock_get, mock_today):
        mock_today.return_value = mock.Mock(
            isoformat=lambda: "2026-08-18",
            strftime=lambda fmt: "20260818",
        )

        def _resp(url, **kwargs):
            r = mock.Mock()
            r.raise_for_status = mock.Mock()
            r.text = _time_page_html([("14:22", "4922", "2707", "-7113")])
            return r

        mock_get.side_effect = _resp
        rows = _fetch_investor_intraday("KOSPI")
        self.assertEqual([r["time"] for r in rows], ["14:22"])
        first_calls = mock_get.call_count
        self.assertGreater(first_calls, 0)
        self.assertLessEqual(first_calls, 6)
        mock_get.reset_mock()
        again = _fetch_investor_intraday("KOSPI")
        self.assertEqual(again, rows)
        mock_get.assert_not_called()

    def test_merge_live_investor_replaces_today(self):
        hist = [
            {"date": "2026-08-14", "personal": -19820, "foreign": 30387, "institution": -10298},
            {"date": "2026-08-18", "personal": 7420, "foreign": 914, "institution": -7951},
        ]
        live = {"bizdate": "2026-08-18", "personal": 7414, "foreign": 900, "institution": -7900}
        merged = _merge_live_investor(hist, live)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[-1]["foreign"], 900)

    def test_merge_live_investor_appends_new_day(self):
        hist = [{"date": "2026-08-14", "personal": 1, "foreign": 2, "institution": 3}]
        live = {"bizdate": "2026-08-18", "personal": 7414, "foreign": 914, "institution": -7951}
        merged = _merge_live_investor(hist, live)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[-1]["date"], "2026-08-18")

    @mock.patch("utils.market_indices.requests.get")
    def test_fetch_investor_daily_sorted_deduped(self, mock_get):
        pages = {
            1: _day_page_html([
                ("26.08.18", "1", "10", "-10"),
                ("26.08.14", "2", "20", "-20"),
                ("26.08.13", "3", "30", "-30"),
                ("26.08.12", "4", "40", "-40"),
                ("26.08.11", "5", "50", "-50"),
            ]),
            2: _day_page_html([
                ("26.08.10", "6", "60", "-60"),
                ("26.08.07", "7", "70", "-70"),
                ("26.08.06", "8", "80", "-80"),
                ("26.08.05", "9", "90", "-90"),
                ("26.08.04", "10", "100", "-100"),
            ]),
            3: _day_page_html([
                ("26.08.03", "11", "110", "-110"),
                ("26.07.31", "12", "120", "-120"),
                ("26.07.30", "13", "130", "-130"),
                ("26.07.29", "14", "140", "-140"),
                ("26.07.28", "15", "150", "-150"),
            ]),
        }

        def _resp(url, **kwargs):
            r = mock.Mock()
            r.raise_for_status = mock.Mock()
            page = int(url.rsplit("page=", 1)[1])
            r.text = pages[page]
            return r

        mock_get.side_effect = _resp
        rows = _fetch_investor_daily("KOSDAQ", days=15)
        self.assertEqual(len(rows), 15)
        dates = [r["date"] for r in rows]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(rows[0]["date"], "2026-07-28")
        self.assertEqual(rows[-1]["date"], "2026-08-18")
        self.assertEqual(rows[-1]["institution"], -10)
        # sosok=02 for KOSDAQ
        called_urls = [c.args[0] for c in mock_get.call_args_list]
        self.assertTrue(all("sosok=02" in u for u in called_urls))
        # 캐시 히트: 두 번째 호출은 네트워크 없이
        mock_get.reset_mock()
        again = _fetch_investor_daily("KOSDAQ", days=15)
        self.assertEqual(len(again), 15)
        mock_get.assert_not_called()

    @mock.patch("utils.market_indices.kst_today")
    @mock.patch("utils.market_indices.requests.get")
    def test_live_indices_skip_investor_http(self, mock_get, mock_today):
        mock_today.return_value = mock.Mock(
            isoformat=lambda: "2026-08-18",
            strftime=lambda fmt: "20260818",
        )

        def _resp(url, **kwargs):
            r = mock.Mock()
            r.raise_for_status = mock.Mock()
            if "api/realtime" in url:
                code = "KOSPI" if "KOSPI" in url else "KOSDAQ"
                r.json = mock.Mock(return_value=_poll_payload(code))
            elif "investorDealTrendTime" in url or "/trend" in url:
                raise AssertionError(url)
            else:
                r.text = ""
            return r

        mock_get.side_effect = _resp
        payload = fetch_market_indices(force=True)
        by_key = {x["key"]: x for x in payload["indices"]}
        self.assertIsNotNone(by_key["kospi"]["value"])
        self.assertNotIn("investor_intraday", by_key["kospi"])
        self.assertEqual(payload["investor_as_of"], "none")

    @mock.patch("utils.market_indices.kst_today")
    @mock.patch("utils.market_indices.requests.get")
    def test_live_indices_include_investor(self, mock_get, mock_today):
        mock_today.return_value = mock.Mock(
            isoformat=lambda: "2026-08-18",
            strftime=lambda fmt: "20260818",
        )

        def _resp(url, **kwargs):
            r = mock.Mock()
            r.raise_for_status = mock.Mock()
            if "api/realtime" in url:
                code = "KOSPI" if "KOSPI" in url else "KOSDAQ"
                r.json = mock.Mock(return_value=_poll_payload(code))
            elif "index/KOSPI/trend" in url:
                r.json = mock.Mock(return_value=TREND_KOSPI)
            elif "index/KOSDAQ/trend" in url:
                r.json = mock.Mock(return_value=TREND_KOSDAQ)
            elif "investorDealTrendTime" in url:
                r.text = _time_page_html([
                    ("18:06", "7,420", "914", "-7,951"),
                    ("14:22", "4,922", "2,707", "-7,113"),
                    ("09:01", "120", "80", "-90"),
                ])
            elif "investorDealTrendDay" in url:
                r.text = _day_page_html([("26.08.14", "2", "20", "-20")])
            else:
                r.text = ""
            return r

        mock_get.side_effect = _resp
        snap = refresh_investor_flow_snapshot()
        self.assertEqual(snap["date"], "2026-08-18")
        payload = fetch_market_indices(force=True)
        by_key = {x["key"]: x for x in payload["indices"]}
        self.assertEqual(payload["investor_as_of"], "batch")
        self.assertEqual(by_key["kospi"]["investor"]["foreign"], 914)
        self.assertEqual(by_key["kospi"]["investor"]["institution"], -7951)
        self.assertEqual(by_key["kosdaq"]["investor"]["foreign"], 366)
        series = by_key["kospi"]["investor_intraday"]
        self.assertEqual([h["time"] for h in series], ["09:01", "14:22", "18:06"])
        self.assertEqual(series[0]["institution"], -90)
        self.assertEqual(series[-1]["institution"], -7951)
        self.assertEqual(series[-1]["foreign"], 914)

    @mock.patch("utils.market_indices.kst_today")
    @mock.patch("utils.market_indices.requests.get")
    def test_live_indices_survive_trend_failure(self, mock_get, mock_today):
        mock_today.return_value = mock.Mock(isoformat=lambda: "2026-08-18")

        def _resp(url, **kwargs):
            if "/trend" in url:
                raise RuntimeError("trend down")
            r = mock.Mock()
            r.raise_for_status = mock.Mock()
            if "api/realtime" in url:
                code = "KOSPI" if "KOSPI" in url else "KOSDAQ"
                r.json = mock.Mock(return_value=_poll_payload(code))
            else:
                r.text = ""
            return r

        mock_get.side_effect = _resp
        payload = fetch_market_indices(force=True)
        by_key = {x["key"]: x for x in payload["indices"]}
        self.assertIsNone(by_key["kospi"].get("investor"))
        self.assertIsNotNone(by_key["kospi"]["value"])

    @mock.patch("utils.market_indices.is_krx_trading_day", return_value=True)
    def test_investor_refresh_due(self, _trading):
        now = datetime(2026, 8, 18, 10, 0, tzinfo=KST)
        self.assertTrue(investor_refresh_due(now))
        from utils.market_indices import _INV_SNAP
        _INV_SNAP["data"] = {
            "date": "2026-08-18",
            "at": time.time(),
            "markets": {},
        }
        self.assertFalse(investor_refresh_due(now))
        _INV_SNAP["data"]["at"] = time.time() - 301
        self.assertTrue(investor_refresh_due(now))
        self.assertFalse(investor_refresh_due(datetime(2026, 8, 18, 8, 0, tzinfo=KST)))
        self.assertFalse(investor_refresh_due(datetime(2026, 8, 18, 16, 0, tzinfo=KST)))


if __name__ == "__main__":
    unittest.main()
