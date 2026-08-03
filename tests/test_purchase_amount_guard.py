"""키움 pur_amt 이상치 보정."""
import unittest

from utils.eval_pnl import apply_holding_to_position, resolve_purchase_amount


class _Pos:
    def __init__(self):
        self.buy_quantity = 84
        self.buy_price = 11840
        self.buy_amount = 994560
        self.actual_buy_amount = None
        self.current_price = None
        self.current_profit_loss = None
        self.current_profit_loss_rate = None


class ResolvePurchaseAmountTests(unittest.TestCase):
    def test_rejects_partial_pur_amt_right_after_buy(self):
        # 리파인 사례: 84주·단가 맞는데 매입금액만 4주분
        got = resolve_purchase_amount(84, 11840, 47360, fallback=994560)
        self.assertEqual(got, 11840 * 84)

    def test_accepts_fee_rounded_pur_amt(self):
        expected = 11878 * 84
        got = resolve_purchase_amount(84, 11878, expected + 8, fallback=expected)
        self.assertEqual(got, expected + 8)

    def test_apply_holding_keeps_sane_amount(self):
        pos = _Pos()
        apply_holding_to_position(
            pos,
            {
                "qty": "84",
                "avg_pr": "11840",
                "pur_amt": "47360",
                "cur_pr": "11840",
                "evlt_amt": "994560",
                "lspft_amt": "0",
                "lspft_rt": "0",
            },
        )
        self.assertEqual(pos.buy_quantity, 84)
        self.assertEqual(pos.buy_price, 11840)
        self.assertEqual(pos.buy_amount, 994560)
        self.assertEqual(pos.actual_buy_amount, 994560)


if __name__ == "__main__":
    unittest.main()
