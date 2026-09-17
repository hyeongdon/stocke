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

    def test_ma1592_codes_keep_specific_label(self):
        self.assertEqual(
            sell_reason_ko("STOP_LOSS", detail="TP1_GAP | MA1592 TP1_GAP · frac=0.5 · 196/393주"),
            "전고 갭 반익절",
        )
        self.assertEqual(
            sell_reason_ko(
                "STOP_LOSS",
                profit_loss=-20000,
                detail="STOP_MA_DC_WIDEN | MA1592 STOP_MA_DC_WIDEN · frac=1.0 · 209/209주",
            ),
            "DC+이격 확대",
        )
        self.assertEqual(sell_reason_ko("TP1_HIGH", profit_loss=5000), "전고 반익절")
        self.assertEqual(
            sell_reason_ko("STOP_LOSS", detail="TRAILING→STOP_LOSS | TRAILING 청산: 현재가 1000"),
            "트레일링 스탑",
        )
        self.assertEqual(
            sell_reason_ko(
                "STOP_LOSS",
                profit_loss=-1000,
                detail="TRAILING→STOP_LOSS | TRAILING 청산: 현재가 1000",
            ),
            "손절 (트레일)",
        )

    def test_coarse_position_status_fits_varchar20(self):
        from utils.sell_reason_labels import coarse_position_status
        self.assertEqual(coarse_position_status("TP1_GAP"), "TAKE_PROFIT")
        self.assertEqual(coarse_position_status("STOP_MA_DC_WIDEN"), "STOP_MA_DC_WIDEN")
        self.assertEqual(
            coarse_position_status("STOP_3M_BEARISH_BELOW_MA15"),
            "STOP_LOSS",
        )
        self.assertLessEqual(len(coarse_position_status("STOP_3M_BEARISH_BELOW_MA15")), 20)

    def test_t1_gap_alias_and_compound_codes(self):
        self.assertEqual(sell_reason_ko("T1_GAP"), "전고 갭 반익절")
        self.assertEqual(
            sell_reason_ko("T1_GAP STOP_3M_BEARISH_BELOW_MA15"),
            "전고 갭 반익절 · 3분 음봉 MA15 이탈",
        )
        self.assertEqual(
            sell_reason_ko("TP1_GAP STOP_3M_BEARISH_BELOW_MA15"),
            "전고 갭 반익절 · 3분 음봉 MA15 이탈",
        )
        self.assertEqual(
            sell_reason_ko("STOP_3M_BEARISH_BELOW_MA15"),
            "3분 음봉 MA15 이탈",
        )

    def test_detail_ko_replaces_codes(self):
        from utils.sell_reason_labels import sell_reason_detail_ko
        text = sell_reason_detail_ko(
            "TP1_GAP | MA1592 TP1_GAP · frac=0.5 · 196/393주"
        )
        self.assertIn("전고 갭 반익절", text)
        self.assertNotIn("TP1_GAP", text)

    def test_effective_sell_price_fallbacks(self):
        from types import SimpleNamespace
        from utils.position_sell_backfill import effective_sell_price

        self.assertEqual(
            effective_sell_price(SimpleNamespace(sell_price=12345, sell_quantity=10, sell_amount=0, profit_loss=None)),
            12345,
        )
        self.assertEqual(
            effective_sell_price(SimpleNamespace(sell_price=0, sell_quantity=10, sell_amount=50000, profit_loss=None)),
            5000,
        )
        self.assertEqual(
            effective_sell_price(
                SimpleNamespace(sell_price=0, sell_quantity=10, sell_amount=0, profit_loss=20000),
                buy_price=1000,
            ),
            3000,
        )
        self.assertEqual(
            effective_sell_price(
                SimpleNamespace(sell_price=0, sell_quantity=10, sell_amount=0, profit_loss=None),
                fallback_price=7777,
            ),
            7777,
        )


if __name__ == "__main__":
    unittest.main()

