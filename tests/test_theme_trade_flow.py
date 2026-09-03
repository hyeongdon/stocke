"""테마 거래대금 맵(거래대금순→테마 합산) 단위 테스트."""
import time
import unittest
from unittest.mock import AsyncMock, patch

from utils.jongga_engine import UNMAPPED_THEME
from utils.theme_trade_flow import (
    cache_is_fresh,
    filter_out_etf_items,
    get_theme_trade_flow,
    rank_themes_by_trade_amount,
)


class ThemeTradeFlowRankTests(unittest.TestCase):
    def test_rank_top_n_by_trade_amount(self):
        items = [
            {"stock_code": "111111", "stock_name": "A", "trade_amount": 100, "change_rate": 2},
            {"stock_code": "222222", "stock_name": "B", "trade_amount": 400, "change_rate": 5},
            {"stock_code": "333333", "stock_name": "C", "trade_amount": 50, "change_rate": -1},
            {"stock_code": "444444", "stock_name": "D", "trade_amount": 200, "change_rate": 3},
        ]
        theme_map = {
            "111111": {"themes": ["로봇"]},
            "222222": {"themes": ["AI"]},
            "333333": {"themes": ["로봇"]},
            "444444": {"themes": ["AI"]},
        }
        ranked = rank_themes_by_trade_amount(items, theme_map, top_n=10, top_stocks=2)
        self.assertEqual([r["theme"] for r in ranked], ["AI", "로봇"])
        self.assertEqual(ranked[0]["trade_amount"], 600)
        self.assertEqual(ranked[0]["trade_amount_eok"], 6.0)
        self.assertEqual(ranked[0]["stock_count"], 2)
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertEqual(ranked[0]["top_stocks"][0]["stock_code"], "222222")
        self.assertAlmostEqual(ranked[1]["avg_change_rate"], 0.5)

    def test_exclude_unmapped(self):
        items = [
            {"stock_code": "111111", "trade_amount": 999},
            {"stock_code": "222222", "trade_amount": 10},
        ]
        theme_map = {"222222": {"themes": ["바이오"]}}
        ranked = rank_themes_by_trade_amount(
            items, theme_map, top_n=10, include_unmapped=False,
        )
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["theme"], "바이오")

    def test_exclude_unmapped_by_default(self):
        items = [{"stock_code": "111111", "trade_amount": 50}]
        ranked = rank_themes_by_trade_amount(items, {}, top_n=5)
        self.assertEqual(ranked, [])

    def test_full_amount_is_counted_in_every_stock_theme(self):
        items = [
            {"stock_code": "111111", "stock_name": "A", "trade_amount": 100, "change_rate": 2},
            {"stock_code": "222222", "stock_name": "B", "trade_amount": 50, "change_rate": -1},
        ]
        theme_map = {
            "111111": {"themes": ["AI", "반도체", "AI"]},
            "222222": {"themes": ["반도체"]},
        }
        ranked = rank_themes_by_trade_amount(items, theme_map, top_n=10)
        by_theme = {row["theme"]: row for row in ranked}
        self.assertEqual(by_theme["AI"]["trade_amount"], 100)
        self.assertEqual(by_theme["반도체"]["trade_amount"], 150)
        self.assertEqual(by_theme["AI"]["stock_count"], 1)
        self.assertEqual(by_theme["반도체"]["stock_count"], 2)
        self.assertAlmostEqual(by_theme["반도체"]["avg_change_rate"], 0.5)

    def test_can_rank_by_average_change_rate(self):
        items = [
            {"stock_code": "111111", "trade_amount": 1000, "change_rate": 1},
            {"stock_code": "222222", "trade_amount": 100, "change_rate": 5},
        ]
        theme_map = {
            "111111": {"themes": ["대금상위"]},
            "222222": {"themes": ["상승률상위"]},
        }
        ranked = rank_themes_by_trade_amount(
            items, theme_map, top_n=10, sort_by="change_rate",
        )
        self.assertEqual([row["theme"] for row in ranked], ["상승률상위", "대금상위"])

    def test_include_unmapped_opt_in(self):
        items = [{"stock_code": "111111", "trade_amount": 50}]
        ranked = rank_themes_by_trade_amount(items, {}, top_n=5, include_unmapped=True)
        self.assertEqual(ranked[0]["theme"], UNMAPPED_THEME)


class ThemeTradeFlowEtfFilterTests(unittest.TestCase):
    def test_filter_out_etf_and_keep_stock(self):
        items = [
            {"stock_code": "069500", "stock_name": "KODEX 200", "trade_amount": 9000},
            {"stock_code": "122630", "stock_name": "KODEX 레버리지", "trade_amount": 8000},
            {"stock_code": "005930", "stock_name": "삼성전자", "trade_amount": 5000},
            {"stock_code": "252670", "stock_name": "KODEX 200선물인버스2X", "trade_amount": 7000},
        ]
        kept = filter_out_etf_items(items)
        self.assertEqual([k["stock_code"] for k in kept], ["005930"])


class ThemeTradeFlowCacheTests(unittest.TestCase):
    def test_fresh_same_day(self):
        from utils.theme_trade_flow import CACHE_SCHEMA

        payload = {
            "success": True,
            "schema": CACHE_SCHEMA,
            "biz_date": "2099-01-01",
            "built_at_epoch": time.time(),
            "items": [{"theme": "AI"}],
        }
        self.assertTrue(cache_is_fresh(payload, cache_sec=900, biz_date="2099-01-01"))

    def test_stale_old_schema(self):
        payload = {
            "success": True,
            "biz_date": "2099-01-01",
            "built_at_epoch": time.time(),
            "items": [{"theme": "미분류"}],
        }
        self.assertFalse(cache_is_fresh(payload, cache_sec=900, biz_date="2099-01-01"))

    def test_stale_by_age(self):
        from utils.theme_trade_flow import CACHE_SCHEMA

        payload = {
            "success": True,
            "schema": CACHE_SCHEMA,
            "biz_date": "2099-01-01",
            "built_at_epoch": time.time() - 1000,
            "items": [{"theme": "AI"}],
        }
        self.assertFalse(cache_is_fresh(payload, cache_sec=900, biz_date="2099-01-01"))

    def test_stale_other_day(self):
        from utils.theme_trade_flow import CACHE_SCHEMA

        payload = {
            "success": True,
            "schema": CACHE_SCHEMA,
            "biz_date": "2099-01-01",
            "built_at_epoch": time.time(),
            "items": [{"theme": "AI"}],
        }
        self.assertFalse(cache_is_fresh(payload, cache_sec=900, biz_date="2099-01-02"))


class ThemeTradeFlowCachedSortTests(unittest.IsolatedAsyncioTestCase):
    async def test_sort_change_uses_existing_cache_without_kiwoom_call(self):
        cached = {
            "success": True,
            "top_n": 40,
            "stock_limit": 300,
            "sort_by": "trade_amount",
            "items": [
                {"theme": "대금상위", "trade_amount": 1000, "avg_change_rate": 1},
                {"theme": "상승률상위", "trade_amount": 100, "avg_change_rate": 5},
            ],
        }
        api = AsyncMock()
        with (
            patch("utils.theme_trade_flow.load_theme_trade_flow_cache", return_value=cached),
            patch("utils.theme_trade_flow.cache_is_fresh", return_value=True),
        ):
            result = await get_theme_trade_flow(api, sort_by="change_rate")

        self.assertTrue(result["cached"])
        self.assertEqual(result["sort_by"], "change_rate")
        self.assertEqual(
            [row["theme"] for row in result["items"]],
            ["상승률상위", "대금상위"],
        )
        api.get_volume_rank.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
