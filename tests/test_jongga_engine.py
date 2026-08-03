"""종가배팅(jongga) 엔진 단위 테스트."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from utils.jongga_engine import (
    UNMAPPED_THEME,
    aggregate_theme_trade_amounts,
    build_session_payload,
    candidates_for_theme,
    is_exit_management_day,
    pick_auto_candidate,
    primary_theme,
    pullback_from_day_high_pct,
    score_candidates,
    strongest_theme,
)

KST = ZoneInfo("Asia/Seoul")


class JonggaThemeAggTests(unittest.TestCase):
    def test_unmapped_theme(self):
        self.assertEqual(primary_theme([]), UNMAPPED_THEME)
        self.assertEqual(primary_theme(None), UNMAPPED_THEME)
        self.assertEqual(primary_theme(["반도체"]), "반도체")

    def test_strongest_theme_by_trade_amount(self):
        items = [
            {"stock_code": "111111", "trade_amount": 100, "stock_name": "A"},
            {"stock_code": "222222", "trade_amount": 200, "stock_name": "B"},
            {"stock_code": "333333", "trade_amount": 50, "stock_name": "C"},
        ]
        theme_map = {
            "111111": {"themes": ["AI"]},
            "222222": {"themes": ["AI"]},
            "333333": {"themes": ["2차전지"]},
        }
        totals, enriched = aggregate_theme_trade_amounts(items, theme_map)
        self.assertEqual(totals["AI"], 300)
        self.assertEqual(totals["2차전지"], 50)
        self.assertEqual(strongest_theme(totals), "AI")
        cands = candidates_for_theme(enriched, "AI")
        self.assertEqual([c["stock_code"] for c in cands], ["222222", "111111"])

    def test_unmapped_bucket(self):
        items = [{"stock_code": "999999", "trade_amount": 999, "stock_name": "X"}]
        totals, enriched = aggregate_theme_trade_amounts(items, {})
        self.assertEqual(enriched[0]["theme"], UNMAPPED_THEME)
        self.assertEqual(strongest_theme(totals), UNMAPPED_THEME)


class JonggaScoreTests(unittest.TestCase):
    def test_pullback_pct(self):
        self.assertAlmostEqual(pullback_from_day_high_pct(9000, 10000), 10.0)
        self.assertAlmostEqual(pullback_from_day_high_pct(10000, 10000), 0.0)
        self.assertIsNone(pullback_from_day_high_pct(0, 10000))

    def test_score_prefers_pullback_amount_change(self):
        rows = [
            {
                "stock_code": "1",
                "pullback_pct": 10,
                "trade_amount": 100,
                "change_rate": 5,
            },
            {
                "stock_code": "2",
                "pullback_pct": 1,
                "trade_amount": 100,
                "change_rate": 5,
            },
            {
                "stock_code": "3",
                "pullback_pct": 10,
                "trade_amount": 500,
                "change_rate": 8,
            },
        ]
        scored = score_candidates(rows)
        top = pick_auto_candidate(scored)
        self.assertEqual(top["stock_code"], "3")
        self.assertGreater(scored[0]["score"], scored[-1]["score"])

    def test_build_session_payload(self):
        items = [
            {"stock_code": "005930", "trade_amount": 1000, "change_rate": 3, "current_price": 70000, "stock_name": "삼성"},
            {"stock_code": "000660", "trade_amount": 800, "change_rate": 5, "current_price": 200000, "stock_name": "하이닉스"},
            {"stock_code": "035420", "trade_amount": 100, "change_rate": 2, "current_price": 200000, "stock_name": "네이버"},
        ]
        theme_map = {
            "005930": {"themes": ["반도체"]},
            "000660": {"themes": ["반도체"]},
            "035420": {"themes": ["인터넷"]},
        }
        payload = build_session_payload(
            items=items,
            theme_map=theme_map,
            pullbacks={"005930": 4.0, "000660": 8.0, "035420": 1.0},
        )
        self.assertEqual(payload["strongest_theme"], "반도체")
        self.assertEqual(len(payload["candidates"]), 2)
        self.assertEqual(payload["auto_pick"]["stock_code"], "000660")

    def test_fill_pullbacks_from_daily_chart(self):
        import asyncio
        from utils.jongga_engine import fill_pullbacks_from_daily_chart

        class _FakeApi:
            async def get_intraday_chart_for_date(self, code, trade_date, tic_scope="15", max_pages=1, **kwargs):
                return {
                    "success": True,
                    "bars": [
                        {"high": 10500, "close": 10200},
                        {"high": 11000, "close": 10000},
                    ],
                }

            async def get_stock_chart_data(self, code, period="1D", max_bars=None, **kwargs):
                return [{"high": 11000, "close": 10000, "open": 9500, "low": 9000}]

        # 순위 API 현재가가 고점(11000)이어도 차트 종가(10000)로 눌림 계산
        rows = [{"stock_code": "005930", "current_price": 11000}]
        pb = asyncio.run(fill_pullbacks_from_daily_chart(_FakeApi(), rows))
        self.assertAlmostEqual(pb["005930"], 1000 / 11000 * 100.0, places=4)
        self.assertEqual(rows[0]["day_high"], 11000)
        self.assertEqual(rows[0]["chart_last"], 10000)
        self.assertAlmostEqual(rows[0]["pullback_pct"], pb["005930"], places=4)

    def test_attach_market_caps(self):
        from unittest.mock import patch
        from utils.jongga_engine import attach_market_caps

        rows = [
            {"stock_code": "005930", "stock_name": "삼성"},
            {"stock_code": "000660", "stock_name": "하이닉스"},
        ]
        with patch(
            "utils.fundamental_mart_store.get_latest_map_by_codes",
            return_value={"005930": {"market_cap": 450000.0}, "000660": {"market_cap": 120000.0}},
        ):
            attach_market_caps(rows)
        self.assertEqual(rows[0]["market_cap"], 450000.0)
        self.assertEqual(rows[1]["market_cap"], 120000.0)

    def test_fill_pullbacks_missing_high_is_none(self):
        import asyncio
        from utils.jongga_engine import fill_pullbacks_from_daily_chart

        class _FakeApi:
            async def get_intraday_chart_for_date(self, *a, **k):
                return {"success": True, "bars": []}

            async def get_stock_chart_data(self, *a, **k):
                return []

        rows = [{"stock_code": "005930", "current_price": 10000}]
        pb = asyncio.run(fill_pullbacks_from_daily_chart(_FakeApi(), rows))
        self.assertEqual(pb, {})
        self.assertIsNone(rows[0]["pullback_pct"])


class JonggaExitDayTests(unittest.TestCase):
    def test_same_day_not_exit(self):
        now = datetime(2026, 7, 30, 15, 0, tzinfo=KST)
        buy = datetime(2026, 7, 30, 5, 40)  # UTC naive ≈ 14:40 KST
        self.assertFalse(is_exit_management_day(buy, now))

    def test_next_day_is_exit(self):
        now = datetime(2026, 7, 31, 9, 10, tzinfo=KST)
        buy = datetime(2026, 7, 30, 5, 40)
        self.assertTrue(is_exit_management_day(buy, now))


class JonggaPigSplitTests(unittest.TestCase):
    def test_leg_fraction_default(self):
        from types import SimpleNamespace
        from utils.jongga_engine import leg_fraction

        s = SimpleNamespace(jongga_pig_split=True, jongga_leg1_pct=20, jongga_leg2_pct=30, jongga_leg3_pct=50)
        self.assertAlmostEqual(leg_fraction(s, 1), 0.2)
        self.assertAlmostEqual(leg_fraction(s, 2), 0.3)
        self.assertAlmostEqual(leg_fraction(s, 3), 0.5)

    def test_leg_fraction_off(self):
        from types import SimpleNamespace
        from utils.jongga_engine import leg_fraction

        s = SimpleNamespace(jongga_pig_split=False)
        self.assertEqual(leg_fraction(s, 1), 1.0)
        self.assertEqual(leg_fraction(s, 2), 0.0)

    def test_pig_orderbook_verdict(self):
        from utils.jongga_engine import pig_orderbook_verdict

        buy_book = [
            {"bid_qty": 300, "ask_qty": 100},
            {"bid_qty": 200, "ask_qty": 80},
        ]
        v, d = pig_orderbook_verdict(buy_book, levels=2, min_ratio=1.5)
        self.assertEqual(v, "buy")
        self.assertGreaterEqual(d["ratio"], 1.5)

        sell_book = [
            {"bid_qty": 50, "ask_qty": 200},
            {"bid_qty": 40, "ask_qty": 180},
        ]
        v2, _ = pig_orderbook_verdict(sell_book, levels=2, min_ratio=1.5)
        self.assertEqual(v2, "sell")

    def test_investor_net_ok(self):
        from utils.jongga_engine import investor_net_ok

        self.assertTrue(investor_net_ok(100, 50)[0])
        self.assertFalse(investor_net_ok(-1, 50)[0])
        self.assertFalse(investor_net_ok(0, 0)[0])

    def test_program_net_ok(self):
        from utils.jongga_engine import program_net_ok

        self.assertTrue(program_net_ok(57070)[0])
        self.assertFalse(program_net_ok(0)[0])
        self.assertFalse(program_net_ok(-100)[0])

    def test_low_support_ok(self):
        from utils.jongga_engine import low_support_ok

        bars = [{"low": 10000, "close": 10100}] * 10
        bars += [{"low": 10050, "close": 10150}] * 5
        ok, _ = low_support_ok(bars, 10120, lookback=5)
        self.assertTrue(ok)

        crash = bars + [{"low": 9000, "close": 9100}] * 5
        ok2, _ = low_support_ok(crash, 9100, lookback=5)
        self.assertFalse(ok2)


if __name__ == "__main__":
    unittest.main()
