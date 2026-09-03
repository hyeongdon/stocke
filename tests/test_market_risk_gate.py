import unittest
from unittest import mock

from utils.market_risk_gate import (
    check_crash_sync_pullback,
    check_market_risk_buy_allowed,
    evaluate_market_risk,
    normalize_strategy_key,
    pullback_from_high_pp,
    strategy_limited_when_bad,
    strategy_limited_when_surge,
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
        self.market_surge_enabled = False
        self.market_surge_index = "either"
        self.market_surge_change_pct = 3.0
        self.market_surge_max_buys_per_strategy = 0
        self.market_surge_block_legacy = True
        self.market_surge_block_sangtta = True
        self.market_surge_block_breakout = True
        self.market_surge_block_jongga = True
        self.market_surge_block_fractal = True
        self.crash_sync_block_enabled = True
        self.crash_sync_index_pct = -1.5
        self.crash_sync_error_pct = 0.5
        self.crash_sync_pullback_cap_pct = 2.0
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
        self.assertEqual(normalize_strategy_key("fractal"), "fractal")
        self.assertEqual(normalize_strategy_key("ema_fractal_pullback"), "fractal")

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

    def test_pullback_from_high_pp(self):
        self.assertAlmostEqual(pullback_from_high_pp(10800, 10600, 10000), 2.0)
        self.assertEqual(pullback_from_high_pp(10000, 10100, 10000), 0.0)
        self.assertIsNone(pullback_from_high_pp(0, 100, 100))

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_crash_sync_blocks_when_pullback_tracks_index(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=-1.6)
        s = Settings(market_risk_enabled=False)
        # 전일 10000, 고점 10800, 현재 10620 → 눌림 1.8%p, 지수 1.6, 오차 0.2
        ok, reason = check_crash_sync_pullback(
            s, current_price=10620, day_high=10800, prev_close=10000,
        )
        self.assertFalse(ok)
        self.assertIn("지수연동", reason)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_crash_sync_allows_relative_strength(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=-1.6)
        s = Settings(market_risk_enabled=False)
        # 눌림 0.3%p vs 지수 1.6 → 오차 큼 → 통과
        ok, _ = check_crash_sync_pullback(
            s, current_price=10770, day_high=10800, prev_close=10000,
        )
        self.assertTrue(ok)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_crash_sync_skips_when_index_not_crash(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=-1.2)
        s = Settings(market_risk_enabled=False)
        ok, _ = check_crash_sync_pullback(
            s, current_price=10620, day_high=10800, prev_close=10000,
        )
        self.assertTrue(ok)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_crash_sync_disabled(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=-2.0)
        s = Settings(crash_sync_block_enabled=False)
        ok, _ = check_crash_sync_pullback(
            s, current_price=10620, day_high=10800, prev_close=10000,
        )
        self.assertTrue(ok)
        mock_fetch.assert_not_called()

    def test_pullback_cap_blocks_without_crash(self):
        s = Settings()
        # 고점 10800 현재 10500 전일 10000 → 눌림 3.0%p ≥ 2
        ok, reason = check_crash_sync_pullback(
            s, current_price=10500, day_high=10800, prev_close=10000,
        )
        self.assertFalse(ok)
        self.assertIn("눌림 과다", reason)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_pullback_cap_allows_under_cap(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=0.4)
        s = Settings(crash_sync_pullback_cap_pct=2.0)
        ok, _ = check_crash_sync_pullback(
            s, current_price=10650, day_high=10800, prev_close=10000,
        )
        self.assertTrue(ok)


class MarketSurgeGateTests(unittest.TestCase):
    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_kospi_surge_blocks_all_by_default(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=3.2, kosdaq=0.4)
        s = Settings(market_risk_enabled=False, market_surge_enabled=True)
        risk = evaluate_market_risk(s)
        self.assertTrue(risk["is_surge"])
        self.assertFalse(risk["is_bad"])
        ok, reason = check_market_risk_buy_allowed(s, "legacy", used_today=0)
        self.assertFalse(ok)
        self.assertIn("전면", reason)
        self.assertIn("급등장", reason)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_kosdaq_surge_either_mode_blocks(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=0.8, kosdaq=3.1)
        s = Settings(
            market_risk_enabled=False,
            market_surge_enabled=True,
            market_surge_index="either",
        )
        self.assertTrue(evaluate_market_risk(s)["is_surge"])
        ok, reason = check_market_risk_buy_allowed(s, "breakout", used_today=0)
        self.assertFalse(ok)
        self.assertIn("코스닥", reason)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_below_surge_threshold_allows(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=2.9, kosdaq=2.4)
        s = Settings(market_risk_enabled=False, market_surge_enabled=True)
        self.assertFalse(evaluate_market_risk(s)["is_surge"])
        ok, _ = check_market_risk_buy_allowed(s, "legacy", used_today=10)
        self.assertTrue(ok)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_both_mode_requires_two_indices(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=3.5, kosdaq=1.2)
        s = Settings(
            market_risk_enabled=False,
            market_surge_enabled=True,
            market_surge_index="both",
        )
        self.assertFalse(evaluate_market_risk(s)["is_surge"])
        mock_fetch.return_value = _indices(kospi=3.5, kosdaq=3.1)
        self.assertTrue(evaluate_market_risk(s)["is_surge"])

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_surge_cap_allows_until_limit(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=3.4)
        s = Settings(
            market_risk_enabled=False,
            market_surge_enabled=True,
            market_surge_max_buys_per_strategy=1,
        )
        ok1, _ = check_market_risk_buy_allowed(s, "sangtta", used_today=0)
        ok2, reason = check_market_risk_buy_allowed(s, "sangtta", used_today=1)
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertIn("1/1", reason)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_surge_strategy_opt_out(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=4.0)
        s = Settings(
            market_risk_enabled=False,
            market_surge_enabled=True,
            market_surge_block_jongga=False,
        )
        self.assertFalse(strategy_limited_when_surge(s, "jongga"))
        ok, _ = check_market_risk_buy_allowed(s, "jongga", used_today=99)
        self.assertTrue(ok)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_surge_disabled_allows(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=5.0)
        s = Settings(market_risk_enabled=False, market_surge_enabled=False)
        ok, _ = check_market_risk_buy_allowed(s, "legacy", used_today=0)
        self.assertTrue(ok)
        mock_fetch.assert_not_called()


class PerMarketGateTests(unittest.TestCase):
    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_kospi_bad_blocks_only_kospi_stock(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=-2.5, kosdaq=0.3)
        s = Settings(
            market_risk_index="per_market",
            market_risk_max_buys_per_strategy=0,
            market_surge_enabled=False,
        )
        risk = evaluate_market_risk(s)
        self.assertTrue(risk["is_bad"])
        self.assertTrue(risk["kospi_bad"])
        self.assertFalse(risk["kosdaq_bad"])

        ok_kq, _ = check_market_risk_buy_allowed(
            s, "legacy", used_today=0, stock_market="kosdaq",
        )
        ok_kp, reason = check_market_risk_buy_allowed(
            s, "legacy", used_today=0, stock_market="kospi",
        )
        self.assertTrue(ok_kq)
        self.assertFalse(ok_kp)
        self.assertIn("코스피", reason)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_kosdaq_bad_blocks_only_kosdaq_stock(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=0.2, kosdaq=-2.8)
        s = Settings(
            market_risk_index="per_market",
            market_risk_max_buys_per_strategy=0,
            market_surge_enabled=False,
        )
        ok_kp, _ = check_market_risk_buy_allowed(
            s, "legacy", used_today=0, stock_market="kospi",
        )
        ok_kq, reason = check_market_risk_buy_allowed(
            s, "legacy", used_today=0, stock_market="kosdaq",
        )
        self.assertTrue(ok_kp)
        self.assertFalse(ok_kq)
        self.assertIn("코스닥", reason)

    @mock.patch("utils.market_risk_gate.resolve_stock_market", return_value=None)
    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_unknown_market_allows_with_log(self, mock_fetch, _mock_resolve):
        mock_fetch.return_value = _indices(kospi=-3.0, kosdaq=-3.0)
        s = Settings(
            market_risk_index="per_market",
            market_risk_max_buys_per_strategy=0,
            market_surge_enabled=False,
        )
        with self.assertLogs("utils.market_risk_gate", level="INFO") as cm:
            ok, reason = check_market_risk_buy_allowed(
                s, "legacy", used_today=0, stock_code="999999",
            )
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertTrue(any("시장 미상" in line for line in cm.output))

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_per_market_cap_is_market_scoped(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=-2.5, kosdaq=0.1)
        s = Settings(
            market_risk_index="per_market",
            market_risk_max_buys_per_strategy=2,
            market_surge_enabled=False,
        )
        ok1, _ = check_market_risk_buy_allowed(
            s, "legacy", used_today=1, stock_market="kospi",
        )
        ok2, reason = check_market_risk_buy_allowed(
            s, "legacy", used_today=2, stock_market="kospi",
        )
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertIn("2/2", reason)
        self.assertIn("코스피", reason)

    @mock.patch("utils.market_risk_gate.fetch_market_indices")
    def test_surge_per_market_only_matching(self, mock_fetch):
        mock_fetch.return_value = _indices(kospi=3.5, kosdaq=0.5)
        s = Settings(
            market_risk_enabled=False,
            market_surge_enabled=True,
            market_surge_index="per_market",
            market_surge_max_buys_per_strategy=0,
        )
        ok_kq, _ = check_market_risk_buy_allowed(
            s, "legacy", used_today=0, stock_market="kosdaq",
        )
        ok_kp, reason = check_market_risk_buy_allowed(
            s, "legacy", used_today=0, stock_market="kospi",
        )
        self.assertTrue(ok_kq)
        self.assertFalse(ok_kp)
        self.assertIn("코스피", reason)

    def test_normalize_stock_market(self):
        from utils.market_risk_gate import normalize_stock_market

        self.assertEqual(normalize_stock_market("KOSPI"), "kospi")
        self.assertEqual(normalize_stock_market("kosdaq"), "kosdaq")
        self.assertEqual(normalize_stock_market("001"), "kospi")
        self.assertEqual(normalize_stock_market("101"), "kosdaq")
        self.assertIsNone(normalize_stock_market(""))
        self.assertIsNone(normalize_stock_market(None))


if __name__ == "__main__":
    unittest.main()
