import unittest
from datetime import date, datetime
from unittest import mock

from core.models import Position, PositionBuyFill, SellOrder
from notifications.daily_trade_journal_notify import format_daily_trade_journal_html
from utils.daily_trade_journal import collect_daily_trade_journal


class DailyTradeJournalFormatTests(unittest.TestCase):
    @mock.patch("notifications.daily_trade_journal_notify.now_kst")
    def test_empty_day_html(self, mock_now):
        mock_now.return_value.strftime.return_value = "2026-07-24 15:40"
        html = format_daily_trade_journal_html(
            {
                "day": "2026-07-24",
                "today_buy_eval": 0,
                "today_buy_realized": 0,
                "today_buy_unrealized": 0,
                "holding_unrealized": 0,
                "day_eval_total": 0,
                "today_buy_positions": [],
                "holdings": [],
                "has_activity": False,
            }
        )
        self.assertIn("매매 일지", html)
        self.assertIn("【합산】", html)
        self.assertIn("일일 평가합", html)
        self.assertIn("오늘 매수·매도·보유 없음", html)

    @mock.patch("notifications.daily_trade_journal_notify.now_kst")
    def test_eval_html(self, mock_now):
        mock_now.return_value.strftime.return_value = "2026-07-24 15:40"
        html = format_daily_trade_journal_html(
            {
                "day": "2026-07-24",
                "buy_count": 2,
                "sell_count": 1,
                "buy_amount_sum": 1_700_000,
                "sell_amount_sum": 750_000,
                "realized_pnl": 50_000,
                "win_count": 1,
                "loss_count": 0,
                "flat_count": 0,
                "reason_counts": {"TAKE_PROFIT": 1},
                "today_buy_eval": 40000,
                "today_buy_realized": 50000,
                "today_buy_unrealized": -10000,
                "holding_unrealized": -10000,
                "day_eval_total": 40000,
                "today_buy_positions": [
                    {
                        "stock_name": "삼성전자",
                        "stock_code": "005930",
                        "quantity": 10,
                        "buy_price": 70000,
                        "strategy": "sangtta",
                        "status": "청산",
                        "sell_reason": "TAKE_PROFIT",
                        "eval_pnl": 50000,
                        "eval_pnl_rate": 7.14,
                        "buy_time": "09:15",
                    },
                    {
                        "stock_name": "SK하이닉스",
                        "stock_code": "000660",
                        "quantity": 5,
                        "buy_price": 200000,
                        "strategy": "legacy",
                        "status": "보유",
                        "sell_reason": None,
                        "eval_pnl": -10000,
                        "eval_pnl_rate": -1.0,
                        "buy_time": "10:00",
                    },
                ],
                "sells": [
                    {
                        "stock_name": "삼성전자",
                        "stock_code": "005930",
                        "quantity": 10,
                        "price": 75000,
                        "sell_reason": "TAKE_PROFIT",
                        "profit_loss": 50000,
                        "profit_loss_rate": 7.14,
                        "time": "11:00",
                    }
                ],
                "holdings": [
                    {
                        "stock_name": "SK하이닉스",
                        "stock_code": "000660",
                        "quantity": 5,
                        "buy_price": 200000,
                        "current_profit_loss": -10000,
                        "current_profit_loss_rate": -1.0,
                        "bought_today": True,
                    }
                ],
                "has_activity": True,
            }
        )
        self.assertIn("【합산】", html)
        self.assertIn("매수 2건", html)
        self.assertIn("당일 실현손익", html)
        self.assertIn("익절 1", html)
        self.assertIn("전략별", html)
        self.assertIn("상따", html)
        self.assertIn("금일 매수 손익", html)
        self.assertIn("보유 평가손익", html)
        self.assertIn("+40,000원", html)
        self.assertIn("익절", html)
        self.assertIn("【금일 매도", html)
        self.assertIn("·금일", html)


class DailyTradeJournalCollectTests(unittest.TestCase):
    def test_today_buy_eval_plus_holding(self):
        day = date(2026, 7, 24)
        filled = datetime(2026, 7, 24, 1, 0, 0)
        completed = datetime(2026, 7, 24, 5, 0, 0)

        pos_closed = mock.Mock(
            id=1,
            stock_code="005930",
            stock_name="삼성전자",
            buy_quantity=10,
            buy_price=70000,
            buy_amount=700000,
            current_profit_loss=None,
            current_profit_loss_rate=None,
            strategy_key="sangtta",
            status="TAKE_PROFIT",
            buy_time=filled,
        )
        pos_hold_today = mock.Mock(
            id=2,
            stock_code="000660",
            stock_name="SK하이닉스",
            buy_quantity=5,
            buy_price=200000,
            buy_amount=1000000,
            current_price=198000,
            current_profit_loss=-10000,
            current_profit_loss_rate=-1.0,
            strategy_key="legacy",
            status="HOLDING",
            buy_time=filled,
        )
        pos_hold_prior = mock.Mock(
            id=3,
            stock_code="035420",
            stock_name="NAVER",
            buy_quantity=2,
            buy_price=200000,
            buy_amount=400000,
            current_price=210000,
            current_profit_loss=20000,
            current_profit_loss_rate=5.0,
            strategy_key="legacy",
            status="HOLDING",
            buy_time=datetime(2026, 7, 23, 1, 0, 0),
        )
        by_id = {1: pos_closed, 2: pos_hold_today, 3: pos_hold_prior}

        buy_ok = mock.Mock(
            stock_code="005930",
            stock_name="삼성전자",
            quantity=10,
            price=70000,
            amount=700000,
            fill_type="INITIAL",
            position_id=1,
            filled_at=filled,
        )
        buy_hold = mock.Mock(
            stock_code="000660",
            stock_name="SK하이닉스",
            quantity=5,
            price=200000,
            amount=1000000,
            fill_type="INITIAL",
            position_id=2,
            filled_at=filled,
        )
        sell_ok = mock.Mock(
            position_id=1,
            stock_code="005930",
            stock_name="삼성전자",
            sell_quantity=10,
            sell_price=75000,
            sell_amount=750000,
            sell_reason="TAKE_PROFIT",
            sell_reason_detail="",
            profit_loss=50000,
            profit_loss_rate=7.14,
            completed_at=completed,
            status="COMPLETED",
        )

        session = mock.Mock()
        pos_all_calls = {"n": 0}

        def _query(model):
            q = mock.Mock()
            q.filter.return_value = q
            q.order_by.return_value = q

            if model is PositionBuyFill:
                q.all.return_value = [buy_ok, buy_hold]
            elif model is SellOrder:
                q.all.return_value = [sell_ok]
            elif model is Position:

                def _first(*_a, **_k):
                    # Position.id == pid — 마지막 filter 인자에서 id를 알 수 없으므로
                    # 호출 순서: fill1, fill2, (캐시 미스 시) …
                    # fill 단계는 순서대로 1, 2
                    return None

                first_seq = iter([pos_closed, pos_hold_today])

                def _first_seq():
                    try:
                        return next(first_seq)
                    except StopIteration:
                        return None

                q.first.side_effect = _first_seq

                def _all():
                    pos_all_calls["n"] += 1
                    if pos_all_calls["n"] == 1:
                        return [pos_closed, pos_hold_today]  # buy_time 보강
                    return [pos_hold_today, pos_hold_prior]  # HOLDING

                q.all.side_effect = _all
            return q

        session.query.side_effect = _query

        journal = collect_daily_trade_journal(session, day=day)
        self.assertEqual(journal["today_buy_realized"], 50000)
        self.assertEqual(journal["today_buy_unrealized"], -10000)
        self.assertEqual(journal["today_buy_eval"], 40000)
        self.assertEqual(journal["holding_unrealized"], 10000)
        self.assertEqual(journal["day_eval_total"], 60000)
        self.assertEqual(journal["today_buy_position_count"], 2)
        codes = {r["stock_code"] for r in journal["today_buy_positions"]}
        self.assertEqual(codes, {"005930", "000660"})
        self.assertIs(by_id[1], pos_closed)


if __name__ == "__main__":
    unittest.main()
