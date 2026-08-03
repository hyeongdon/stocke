"""역매공파 EOD 리포트 — 박스 차이 집계·텔레그램 포맷 테스트."""
import unittest
from datetime import date
from unittest import mock

from notifications.ymgp_eod_notify import format_ymgp_eod_html
from utils.ymgp_eod_report import build_report_from_rows, enrich_ymgp_row


class YmgpEodEnrichTests(unittest.TestCase):
    def test_box_gaps(self):
        settings = mock.Mock(ymgp_box_width_pct=15.5)
        row = enrich_ymgp_row(
            stock_code="111111",
            stock_name="테스트",
            current_price=9700,
            change_rate=1.2,
            evaled={
                "stage": "FILTERED",
                "reason": "역배열 후보",
                "box": {"high": 10000, "low": 8000, "mid": 9000, "width_pct": 22.22},
                "checks": [
                    {"key": "box", "passed": False, "actual": "22.2%"},
                    {"key": "double_bottom", "passed": False, "actual": "—"},
                    {"key": "ma_support", "passed": True, "actual": "ok"},
                ],
            },
            settings=settings,
        )
        self.assertEqual(row["stage"], "FILTERED")
        self.assertAlmostEqual(row["width_over_pct"], 6.72, places=1)
        self.assertAlmostEqual(row["to_high_pct"], -3.0, places=1)
        self.assertIn("box", row["fail_keys"])
        self.assertIn("double_bottom", row["fail_keys"])
        self.assertNotIn("ma_support", row["fail_keys"])


class YmgpEodFormatTests(unittest.TestCase):
    @mock.patch("notifications.ymgp_eod_notify.now_kst")
    def test_html_table(self, mock_now):
        mock_now.return_value.strftime.return_value = "2026-07-28 15:45"
        report = build_report_from_rows(
            [
                {
                    "stock_code": "111111",
                    "stock_name": "테스트A",
                    "stage": "FILTERED",
                    "box_width_pct": 18.0,
                    "width_over_pct": 2.5,
                    "to_high_pct": -4.2,
                    "fail_keys": ["box", "double_bottom"],
                },
                {
                    "stock_code": "222222",
                    "stock_name": "테스트B",
                    "stage": "READY",
                    "box_width_pct": 10.0,
                    "width_over_pct": -5.5,
                    "to_high_pct": -1.0,
                    "fail_keys": ["accum_bar"],
                },
            ],
            day=date(2026, 7, 28),
            box_limit_pct=15.5,
            condition_names=["역매공파"],
        )
        html = format_ymgp_eod_html(report)
        self.assertIn("역매공파 단계", html)
        self.assertIn("【단계 퍼널】", html)
        self.assertIn("FILTERED", html)
        self.assertIn("폭초과", html)
        self.assertIn("고점差", html)
        self.assertIn("READY", html)
        self.assertIn("<pre>", html)


if __name__ == "__main__":
    unittest.main()
