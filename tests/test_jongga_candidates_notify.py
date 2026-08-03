"""종가배팅 후보 텔레그램 포맷 테스트."""
import unittest
from unittest import mock

from notifications.jongga_candidates_notify import format_jongga_candidates_html


class JonggaCandidatesNotifyTests(unittest.TestCase):
    @mock.patch("notifications.jongga_candidates_notify.now_kst")
    def test_format_includes_theme_and_candidates(self, mock_now):
        mock_now.return_value.strftime.return_value = "2026-07-30 14:30"
        html = format_jongga_candidates_html(
            {
                "biz_date": "2026-07-30",
                "strongest_theme": "반도체",
                "theme_rank": [
                    {"theme": "반도체", "trade_amount": 5000},
                    {"theme": "2차전지", "trade_amount": 2000},
                ],
                "candidates": [
                    {
                        "stock_code": "000660",
                        "stock_name": "SK하이닉스",
                        "trade_amount": 3000,
                        "change_rate": 4.5,
                        "pullback_pct": 2.1,
                        "score": 0.91,
                    },
                    {
                        "stock_code": "005930",
                        "stock_name": "삼성전자",
                        "trade_amount": 2000,
                        "change_rate": 2.0,
                        "pullback_pct": 1.0,
                        "score": 0.55,
                    },
                ],
                "auto_pick": {
                    "stock_code": "000660",
                    "stock_name": "SK하이닉스",
                },
            }
        )
        self.assertIn("종가배팅 후보", html)
        self.assertIn("반도체", html)
        self.assertIn("000660", html)
        self.assertIn("자동매수 예정", html)
        self.assertIn("★", html)


if __name__ == "__main__":
    unittest.main()
