"""키움 pur_amt 이상치 보정."""
import unittest

from utils.eval_pnl import apply_holding_to_position, resolve_purchase_amount
from utils.position_buy_fills import reconcile_position_buy_with_fills


class _Pos:
    def __init__(self):
        self.buy_quantity = 84
        self.buy_price = 11840
        self.buy_amount = 994560
        self.actual_buy_amount = None
        self.current_price = None
        self.current_profit_loss = None
        self.current_profit_loss_rate = None
        self.order_quantity = None
        self.id = 1


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

    def test_apply_holding_skips_qty_shrink_on_cached_balance(self):
        """추가매수 낙관적 반영 후 캐시된 예전 잔고로 수량이 깎이지 않아야 함."""
        pos = _Pos()
        pos.buy_quantity = 911
        pos.buy_price = 2302
        pos.buy_amount = 2096677
        pos.actual_buy_amount = 2096677
        apply_holding_to_position(
            pos,
            {
                "qty": "592",
                "avg_pr": "2200",
                "pur_amt": "1302400",
                "cur_pr": "2500",
                "evlt_amt": "1480000",
                "lspft_amt": "0",
                "lspft_rt": "0",
                "_cached": True,
            },
        )
        self.assertEqual(pos.buy_quantity, 911)
        self.assertEqual(pos.buy_amount, 2096677)
        self.assertEqual(pos.current_price, 2500)


class ReconcilePreferFillsTests(unittest.TestCase):
    def test_prefer_fills_when_cached_api_lags(self):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock

        pos = _Pos()
        pos.buy_quantity = 911
        pos.buy_price = 2302
        pos.buy_amount = 2096677
        pos.actual_buy_amount = 2096677
        pos.order_quantity = 592

        initial = MagicMock(
            fill_type="INITIAL",
            quantity=592,
            order_quantity=592,
            amount=1302367,
            price=2200,
            filled_at=datetime(2026, 8, 4, 3, 0, 56, tzinfo=timezone.utc),
        )
        add = MagicMock(
            fill_type="ADD",
            quantity=319,
            order_quantity=319,
            amount=799095,
            price=2505,
            filled_at=datetime.now(timezone.utc),
        )

        session = MagicMock()
        q = session.query.return_value
        q.filter.return_value.order_by.return_value.all.return_value = [initial, add]

        reconcile_position_buy_with_fills(
            session,
            pos,
            {
                "qty": "592",
                "avg_pr": "2200",
                "pur_amt": "1302400",
                "cur_pr": "2500",
                "_cached": True,
            },
        )
        self.assertEqual(pos.buy_quantity, 911)
        self.assertEqual(pos.order_quantity, 911)
        self.assertEqual(pos.buy_amount, 1302367 + 799095)


if __name__ == "__main__":
    unittest.main()
