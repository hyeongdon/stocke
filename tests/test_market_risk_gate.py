import unittest
from unittest import mock

from utils.market_risk_gate import (
    check_market_risk_buy_allowed,
    evaluate_market_risk,
    normalize_strategy_key,
    strategy_limited_when_bad,
)


class Settings:
    def __init__(self, **kwargs):
        self.market_risk_enabled = True
        self.market_risk_index = "kospi"
        self.market_risk_change_pct = -2.0
        self.market_risk_max_buys_per_strategy = 2
        self.market_risk_block_legacy = True
        self.market_risk_block_sangtta = True
        self.market_risk_block_breakout = False
        for k, v in kwargs.items():
            setattr(self, k, v)


def _indices(kospi=None, kosdaq=None):
    rows = []
    if kospi is not None:
        rows.append({"key": "kospi", "label": "코스피", "change_pct": kospi})
    if kosdaq is not None:
        rows.append({"key": "kosdaq", "label": "코스닥", "change_pct": kosdaq})
    return {"indices": rows, "errors": []}


class MarketRiskGateTests(unittest.TestCase):
    def test_normalize_strategy(self):
        self.assertEqual(normalize_strategy_key("screener"), "legacy")
        self.assertEqual(normalize_strategy_key("sangtta"), "sangtta")
        self.assertEqual(normalize_strategy_key("breakout"), "breakout")
        self.assertEqual(normalize_strategy_key("jongga"), "jongga")
        self.assertEqual(normalize_strategy_key("jongga_closing"), "jongga")

    def test_disabled_always_allows(self):
        s = Settings(market_risk_enabled=False)
        ok, reason = check_market_risk_buy_allowed(s, "legacy", used_today=99)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_kospi_bad_allows_until_cap(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=-2.1, kosdaq=0.3)
        s = Settings()
        risk = evaluate_market_risk(s)
        self.assertTrue(risk["is_bad"])

        ok1, _ = check_market_risk_buy_allowed(s, "legacy", used_today=0)
        ok2, _ = check_market_risk_buy_allowed(s, "legacy", used_today=1)
        ok3, reason = check_market_risk_buy_allowed(s, "legacy", used_today=2)
        self.assertTrue(ok1)
        self.assertTrue(ok2)
        self.assertFalse(ok3)
        self.assertIn("2/2", reason)
        self.assertIn("레거시", reason)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_below_threshold_not_bad(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=-1.5)
        s = Settings(market_risk_change_pct=-2.0)
        self.assertFalse(evaluate_market_risk(s)["is_bad"])
        ok, _ = check_market_risk_buy_allowed(s, "legacy", used_today=10)
        self.assertTrue(ok)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_breakout_not_limited_by_default(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=-3.0)
        s = Settings()
        self.assertFalse(strategy_limited_when_bad(s, "breakout"))
        ok, _ = check_market_risk_buy_allowed(s, "breakout", used_today=99)
        self.assertTrue(ok)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_zero_cap_blocks_all(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=-2.5)
        s = Settings(market_risk_max_buys_per_strategy=0)
        ok, reason = check_market_risk_buy_allowed(s, "sangtta", used_today=0)
        self.assertFalse(ok)
        self.assertIn("전면", reason)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_eval_cache_reused(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=-2.5)
        s = Settings()
        cache = {}
        check_market_risk_buy_allowed(s, "sangtta", eval_cache=cache, used_today=0)
        check_market_risk_buy_allowed(s, "legacy", eval_cache=cache, used_today=0)
        self.assertEqual(mock_fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
