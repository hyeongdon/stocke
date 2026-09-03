"""슬롯/최대보유 한도 매수 실패 텔레그램 알림 테스트."""
import unittest
from unittest import mock

from notifications.trade_alert import (
    build_buy_slot_blocked_message,
    is_buy_slot_capacity_reason,
    notify_buy_slot_blocked,
)


class BuySlotBlockedNotifyTests(unittest.TestCase):
    def test_detects_strategy_slot_and_max_holdings(self):
        self.assertTrue(is_buy_slot_capacity_reason("종가배팅 슬롯 포화 (1/1)"))
        self.assertTrue(is_buy_slot_capacity_reason("돌파 슬롯 포화 (2/2)"))
        self.assertTrue(
            is_buy_slot_capacity_reason(
                "최대 동시 보유 6종목 초과 (슬롯 7: 보유+대기 신호)"
            )
        )
        self.assertTrue(
            is_buy_slot_capacity_reason(
                "최대 보유 종목 6종목 초과 (현재 7: 보유+대기 신호)"
            )
        )
        self.assertFalse(is_buy_slot_capacity_reason("게이트: 과열 컷"))
        self.assertFalse(is_buy_slot_capacity_reason(""))

    @mock.patch("notifications.trade_alert.now_kst")
    def test_message_format(self, mock_now):
        mock_now.return_value.strftime.return_value = "2026-08-04 14:40:00"
        msg = build_buy_slot_blocked_message(
            stock_name="셀바스AI",
            stock_code="108860",
            reason="최대 동시 보유 6종목 초과 (슬롯 7: 보유+대기 신호)",
            strategy="jongga",
        )
        self.assertIn("슬롯 부족", msg)
        self.assertIn("종가배팅", msg)
        self.assertIn("셀바스AI", msg)
        self.assertIn("108860", msg)
        self.assertIn("최대 동시 보유", msg)

    @mock.patch("notifications.trade_alert.send_trade_alert", return_value=True)
    def test_notify_only_for_slot_reasons(self, mock_send):
        self.assertFalse(
            notify_buy_slot_blocked(
                stock_name="A",
                stock_code="1",
                reason="등락률 미달",
                strategy="legacy",
            )
        )
        mock_send.assert_not_called()

        self.assertTrue(
            notify_buy_slot_blocked(
                stock_name="셀바스AI",
                stock_code="108860",
                reason="종가배팅 슬롯 포화 (2/2)",
                strategy="jongga",
            )
        )
        mock_send.assert_called_once()
        body = mock_send.call_args[0][0]
        self.assertIn("슬롯 포화", body)


if __name__ == "__main__":
    unittest.main()
