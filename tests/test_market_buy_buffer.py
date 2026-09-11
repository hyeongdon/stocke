"""시장가 매수 수량의 가격 여유 테스트."""
import unittest
from types import SimpleNamespace
from unittest import mock

from managers.buy_order_executor import BuyOrderExecutor
from utils.auto_trade_engine import compute_quantity


class MarketBuyBufferTests(unittest.TestCase):
    @mock.patch("managers.buy_order_executor.Config.MARKET_BUY_PRICE_BUFFER_PCT", 3.0)
    def test_market_order_reserves_price_buffer(self):
        settings = SimpleNamespace(order_method="MARKET")
        sizing_price = BuyOrderExecutor._buy_sizing_price(10_000, settings)
        self.assertEqual(sizing_price, 10_300)
        self.assertEqual(compute_quantity(1_000_000, sizing_price), 97)

    @mock.patch("managers.buy_order_executor.Config.MARKET_BUY_PRICE_BUFFER_PCT", 3.0)
    def test_limit_order_does_not_use_market_buffer(self):
        settings = SimpleNamespace(order_method="LIMIT")
        self.assertEqual(BuyOrderExecutor._buy_sizing_price(10_000, settings), 10_000)


if __name__ == "__main__":
    unittest.main()