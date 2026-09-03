"""청산 사유 손익 기반 분류 테스트."""
import unittest

from utils.sell_reason_labels import classify_exit_reason, sell_reason_ko


class SellReasonLabelsTests(unittest.TestCase):
    def test_trailing_profit_is_take_profit(self):
        self.assertEqual(
            classify_exit_reason("TRAILING", profit_loss=15000),
            "TAKE_PROFIT",
        )
        self.assertEqual(
            sell_reason_ko("TRAILING", profit_loss=15000),
            "익절 (트레일)",
        )

    def test_trailing_loss_is_stop_loss(self):
        self.assertEqual(
            classify_exit_reason("TRAILING", profit_loss=-5000),
            "STOP_LOSS",
        )
        self.assertEqual(
            sell_reason_ko("TRAILING", profit_loss=-5000),
            "손절 (트레일)",
        )

    def test_trailing_unknown_pnl_keeps_mechanism(self):
        self.assertEqual(classify_exit_reason("TRAILING"), "TRAILING")
        self.assertEqual(sell_reason_ko("TRAILING"), "트레일링 스탑")

    def test_profit_lock_profit(self):
        self.assertEqual(
            classify_exit_reason("PROFIT_LOCK", profit_loss_rate=2.5),
            "TAKE_PROFIT",
        )
        self.assertEqual(
            sell_reason_ko("PROFIT_LOCK", profit_loss_rate=2.5),
            "익절 (수익잠금)",
        )

    def test_sangtta_stop_loss_with_profit_is_take_profit(self):
        """상따 상한가/급락 이탈은 STOP_LOSS로 잡히지만 수익이면 익절."""
        self.assertEqual(
            classify_exit_reason("STOP_LOSS", profit_loss=1000),
            "TAKE_PROFIT",
        )
        self.assertEqual(
            sell_reason_ko("STOP_LOSS", profit_loss=1000),
            "익절 (이탈)",
        )
        self.assertEqual(
            sell_reason_ko("STOP_LOSS", profit_loss_rate=3.2),
            "익절 (이탈)",
        )

    def test_stop_loss_with_loss_stays_stop_loss(self):
        self.assertEqual(
            classify_exit_reason("STOP_LOSS", profit_loss=-1000),
            "STOP_LOSS",
        )
        self.assertEqual(
            sell_reason_ko("STOP_LOSS", profit_loss=-1000),
            "손절",
        )

    def test_take_profit_with_loss_becomes_stop_loss(self):
        self.assertEqual(
            classify_exit_reason("TAKE_PROFIT", profit_loss=-100),
            "STOP_LOSS",
        )

    def test_market_close_unchanged_by_pnl(self):
        self.assertEqual(
            classify_exit_reason("MARKET_CLOSE", profit_loss=500),
            "MARKET_CLOSE",
        )


if __name__ == "__main__":
    unittest.main()

