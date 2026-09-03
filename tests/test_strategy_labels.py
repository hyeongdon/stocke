"""전략 키 → 한국어 라벨 / 정규화 (체결 로그·알림 오분류 방지)."""
import unittest

from notifications.trade_alert import build_buy_message, strategy_label_ko
from utils.stock_exit_replay import STRATEGY_LABELS, _normalize_strategy


class StrategyLabelKoTests(unittest.TestCase):
    def test_ma1592_is_not_legacy_trading_value(self):
        self.assertEqual(strategy_label_ko("ma1592"), "15/92홀드")
        self.assertEqual(strategy_label_ko("MA1592"), "15/92홀드")

    def test_legacy_aliases_still_trading_value(self):
        self.assertEqual(strategy_label_ko("legacy"), "거래대금")
        self.assertEqual(strategy_label_ko("screener"), "거래대금")

    def test_unknown_key_returns_key_not_trading_value(self):
        self.assertEqual(strategy_label_ko("turtle"), "turtle")
        self.assertEqual(strategy_label_ko(""), "기타")
        self.assertEqual(strategy_label_ko(None), "기타")

    def test_buy_message_uses_ma1592_tag(self):
        msg = build_buy_message(
            stock_name="테스트",
            stock_code="000000",
            quantity=10,
            price=1000,
            strategy="ma1592",
        )
        self.assertIn("[15/92홀드]", msg)
        self.assertIn("전략: 15/92홀드", msg)
        self.assertNotIn("거래대금", msg)


class NormalizeStrategyTests(unittest.TestCase):
    def test_ma1592_not_collapsed_to_legacy(self):
        self.assertEqual(_normalize_strategy("ma1592"), "ma1592")
        self.assertEqual(_normalize_strategy("MA1592"), "ma1592")
        self.assertEqual(STRATEGY_LABELS.get("ma1592"), "15/92홀드")

    def test_legacy_aliases(self):
        self.assertEqual(_normalize_strategy("screener"), "legacy")
        self.assertEqual(_normalize_strategy(None), "legacy")
        self.assertEqual(_normalize_strategy("legacy_momentum"), "legacy")


if __name__ == "__main__":
    unittest.main()
