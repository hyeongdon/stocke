import unittest
from unittest import mock

from notifications.trade_industry_batch_notify import (
    format_trade_industry_done_html,
    format_trade_industry_error_html,
    format_trade_industry_start_html,
)


class TradeIndustryBatchNotifyFormatTests(unittest.TestCase):
    @mock.patch("notifications.trade_industry_batch_notify.now_kst")
    def test_start_html(self, mock_now):
        mock_now.return_value.strftime.return_value = "2026-07-16 10:00"
        html = format_trade_industry_start_html(
            end_yyyymm="202606",
            months=24,
            hs_count=20,
            country_count=10,
        )
        self.assertIn("수출입 지표 배치 시작", html)
        self.assertIn("202606", html)
        self.assertIn("24", html)

    @mock.patch("notifications.trade_industry_batch_notify.now_kst")
    def test_done_html_with_top_tags(self, mock_now):
        mock_now.return_value.strftime.return_value = "2026-07-16 10:10"
        html = format_trade_industry_done_html(
            ok=True,
            end_yyyymm="202606",
            months=24,
            hs_rows=4032,
            industry_rows=576,
            errors=1,
            source="data.go.kr/nitemtrade+partners",
            duration_sec=358,
            top_tags=[
                {"tag": "반도체", "exp_usd": 3.0e10, "exp_yoy": 12.5},
                {"tag": "자동차", "exp_usd": 3.6e9, "exp_yoy": -2.1},
            ],
        )
        self.assertIn("일부 실패", html)
        self.assertIn("반도체", html)
        self.assertIn("+12.5%", html)
        self.assertIn("5분 58초", html)

    @mock.patch("notifications.trade_industry_batch_notify.now_kst")
    def test_error_html(self, mock_now):
        mock_now.return_value.strftime.return_value = "2026-07-16 10:05"
        html = format_trade_industry_error_html(
            end_yyyymm="202606",
            error="DATA_GO_KR_SERVICE_KEY 미설정",
            duration_sec=1,
            context="preflight",
        )
        self.assertIn("수출입 지표 배치 오류", html)
        self.assertIn("DATA_GO_KR_SERVICE_KEY", html)
        self.assertIn("preflight", html)


if __name__ == "__main__":
    unittest.main()
