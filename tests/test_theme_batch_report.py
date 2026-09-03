import unittest
from datetime import date

from notifications.theme_batch_report import (
    collect_theme_batch_report_stats,
    format_theme_batch_report_html,
    format_theme_batch_report_text,
)


class _FakeKw:
    def __init__(self, keyword, mention_count, stock_count, delta, trend):
        self.keyword = keyword
        self.mention_count = mention_count
        self.stock_count = stock_count
        self.delta_vs_prev = delta
        self.trend_label = trend


class ThemeBatchReportFormatTests(unittest.TestCase):
    def test_format_includes_tables_and_warnings(self):
        stats = {
            "ok": True,
            "biz_date": "2026-07-21",
            "prev_biz_date": "2026-07-20",
            "duration_sec": 2912.4,
            "today": {
                "themes": 266,
                "edges": 6421,
                "keywords": 50,
                "scores": 6840,
                "stocks": 2342,
                "scores_ok": True,
                "kiwoom_themes": 180,
                "kiwoom_edges": 5100,
                "kiwoom_api_calls": 185,
                "alphasquare_themes": 454,
                "alphasquare_edges": 9200,
                "alphasquare_api_calls": 455,
            },
            "prev": {
                "edges": 6425,
                "keywords": 50,
                "scores": 6844,
                "stocks": 2342,
                "kiwoom_edges": 5080,
                "alphasquare_edges": 9100,
            },
            "top_keywords": [
                {
                    "keyword": "반도체",
                    "mention_count": 40,
                    "stock_count": 120,
                    "delta": 3,
                    "trend": "up",
                },
                {
                    "keyword": "원전",
                    "mention_count": 22,
                    "stock_count": 45,
                    "delta": 0,
                    "trend": "flat",
                },
            ],
            "rising": [{"keyword": "반도체", "delta": 3, "mention_count": 40}],
            "newcomers": [{"keyword": "SMR", "mention_count": 8, "stock_count": 12}],
            "falling": [{"keyword": "바이오", "delta": -4, "mention_count": 10}],
            "warnings": [],
            "error": None,
        }
        html = format_theme_batch_report_html(stats)
        self.assertIn("테마/키워드 배치 일일 리포트", html)
        self.assertIn("<pre>", html)
        self.assertIn("반도체", html)
        self.assertIn("특이사항 없음", html)
        text = format_theme_batch_report_text(stats)
        self.assertIn("편입(N)", text)
        self.assertIn("편입(K)", text)
        self.assertIn("편입(AS)", text)
        self.assertNotIn("<pre>", text)

    def test_failure_status_in_html(self):
        stats = {
            "ok": False,
            "biz_date": "2026-07-21",
            "prev_biz_date": None,
            "duration_sec": 12,
            "today": {
                "themes": 0,
                "edges": 0,
                "keywords": 0,
                "scores": 0,
                "stocks": 0,
                "scores_ok": False,
            },
            "prev": {"edges": None, "keywords": None, "scores": None, "stocks": None},
            "top_keywords": [],
            "rising": [],
            "newcomers": [],
            "falling": [],
            "warnings": ["배치 실패: boom"],
            "error": "boom",
        }
        html = format_theme_batch_report_html(stats)
        self.assertIn("실패", html)
        self.assertIn("boom", html)


if __name__ == "__main__":
    unittest.main()
