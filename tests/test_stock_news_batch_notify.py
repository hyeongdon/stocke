import unittest
from unittest import mock

from notifications.stock_news_batch_notify import (
    format_stock_news_done_html,
    format_stock_news_error_html,
    format_stock_news_start_html,
)


class StockNewsBatchNotifyFormatTests(unittest.TestCase):
    @mock.patch("notifications.stock_news_batch_notify.now_kst")
    def test_start_html(self, mock_now):
        mock_now.return_value.strftime.return_value = "2026-07-21 16:30"
        html = format_stock_news_start_html(
            biz_date="2026-07-21",
            universe="theme",
            max_per_day=120,
            chunk=40,
            mode="loop",
        )
        self.assertIn("종목 뉴스 배치 시작", html)
        self.assertIn("이어달리기", html)
        self.assertIn("theme", html)
        self.assertIn("120", html)

    @mock.patch("notifications.stock_news_batch_notify.now_kst")
    def test_done_html_with_failures(self, mock_now):
        mock_now.return_value.strftime.return_value = "2026-07-21 17:00"
        html = format_stock_news_done_html(
            biz_date="2026-07-21",
            ok=True,
            universe="theme",
            status="all_done",
            done_count=120,
            ok_count=38,
            fail_count=2,
            skip_count=0,
            remaining=0,
            day_cap=120,
            duration_sec=3661,
            error="종목 실패 2건",
            mode="run",
        )
        self.assertIn("종목 뉴스 배치 종료", html)
        self.assertIn("일부 실패", html)
        self.assertIn("종목 실패 2건", html)
        self.assertIn("1시간 1분 1초", html)

    @mock.patch("notifications.stock_news_batch_notify.now_kst")
    def test_error_html(self, mock_now):
        mock_now.return_value.strftime.return_value = "2026-07-21 17:05"
        html = format_stock_news_error_html(
            biz_date="2026-07-21",
            error="청크 실행 실패 exit=1",
            duration_sec=12,
            context="auto_loop_chunk",
        )
        self.assertIn("종목 뉴스 배치 오류", html)
        self.assertIn("청크 실행 실패", html)
        self.assertIn("auto_loop_chunk", html)


if __name__ == "__main__":
    unittest.main()
