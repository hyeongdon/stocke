"""당일 FAILED 매수 신호 집계·텔레그램 포맷 테스트."""
import unittest
from datetime import date, datetime
from unittest import mock

from core.models import PendingBuySignal
from notifications.failed_buy_signals_notify import format_failed_buy_signals_html
from utils.failed_buy_signals_report import (
    collect_failed_buy_signals,
    signal_strategy_key,
)


class FailedBuySignalsFormatTests(unittest.TestCase):
    @mock.patch("notifications.failed_buy_signals_notify.now_kst")
    def test_empty_html(self, mock_now):
        mock_now.return_value.strftime.return_value = "2026-07-28 15:42"
        html = format_failed_buy_signals_html(
            {
                "day": "2026-07-28",
                "count": 0,
                "items": [],
                "strategy_counts": [],
                "reason_counts": [],
                "has_failures": False,
            }
        )
        self.assertIn("매수 실패", html)
        self.assertIn("FAILED 매수 신호 없음", html)

    @mock.patch("notifications.failed_buy_signals_notify.now_kst")
    def test_table_html(self, mock_now):
        mock_now.return_value.strftime.return_value = "2026-07-28 15:42"
        html = format_failed_buy_signals_html(
            {
                "day": "2026-07-28",
                "count": 2,
                "items": [
                    {
                        "stock_name": "테스트A",
                        "stock_code": "111111",
                        "strategy": "breakout",
                        "reason": "돌파 게이트: MA20 미상회",
                        "time": "09:15",
                    },
                    {
                        "stock_name": "테스트B",
                        "stock_code": "222222",
                        "strategy": "sangtta",
                        "reason": "상따 시간 외",
                        "time": "10:02",
                    },
                ],
                "strategy_counts": [("breakout", 1), ("sangtta", 1)],
                "reason_counts": [
                    ("돌파 게이트: MA20 미상회", 1),
                    ("상따 시간 외", 1),
                ],
                "has_failures": True,
            }
        )
        self.assertIn("실패 신호: <b>2</b>건", html)
        self.assertIn("【전략별】", html)
        self.assertIn("【사유 TOP】", html)
        self.assertIn("【상세", html)
        self.assertIn("돌파", html)
        self.assertIn("상따", html)
        self.assertIn("<pre>", html)


class FailedBuySignalsCollectTests(unittest.TestCase):
    def test_collect_filters_and_groups(self):
        day = date(2026, 7, 28)
        sig_ok = mock.Mock(
            id=1,
            stock_code="111111",
            stock_name="A",
            status="FAILED",
            detected_date=day,
            detected_at=datetime(2026, 7, 28, 0, 15, 0),
            failure_reason="게이트: 거래량 부족",
            signal_type="auto_trade",
            additional_data={"strategy": "breakout"},
        )
        sig_legacy = mock.Mock(
            id=2,
            stock_code="222222",
            stock_name="B",
            status="FAILED",
            detected_date=day,
            detected_at=datetime(2026, 7, 28, 1, 0, 0),
            failure_reason="게이트: 거래량 부족",
            signal_type="auto_trade",
            additional_data={"source": "scanner"},
        )
        sig_sample = mock.Mock(
            id=3,
            stock_code="SAMPLE_X",
            stock_name="샘플",
            status="FAILED",
            detected_date=day,
            detected_at=datetime(2026, 7, 28, 2, 0, 0),
            failure_reason="테스트",
            signal_type="auto_trade",
            additional_data={"strategy": "sangtta"},
        )

        session = mock.Mock()
        q = mock.Mock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = [sig_ok, sig_legacy, sig_sample]
        session.query.return_value = q

        report = collect_failed_buy_signals(session, day=day)
        self.assertEqual(report["count"], 2)
        self.assertTrue(report["has_failures"])
        self.assertEqual(dict(report["strategy_counts"]), {"breakout": 1, "legacy": 1})
        self.assertEqual(report["reason_counts"][0][0], "게이트: 거래량 부족")
        self.assertEqual(report["reason_counts"][0][1], 2)
        session.query.assert_called_with(PendingBuySignal)

    def test_strategy_key(self):
        sig = mock.Mock(additional_data={"strategy": "ymgp"})
        self.assertEqual(signal_strategy_key(sig), "ymgp")
        sig2 = mock.Mock(additional_data={"source": "watchlist"})
        self.assertEqual(signal_strategy_key(sig2), "legacy")


if __name__ == "__main__":
    unittest.main()
