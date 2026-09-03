import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from notifications.kiwoom_db_pnl_sync_notify import format_kiwoom_db_pnl_sync_html
from utils.kiwoom_db_pnl_sync import (
    aggregate_db_sells,
    aggregate_ka10074_daily,
    aggregate_kiwoom_realized,
    allocate_by_sell_amount,
    allocate_target_net,
    compare_daily_totals,
    compare_realized,
    db_sells_by_day,
    format_diff_table,
    reconcile_stock_net_to_account_total,
    sync_account_balance_snapshot,
)


class AllocateTargetNetTests(unittest.TestCase):
    def test_single(self):
        self.assertEqual(allocate_target_net([100], -50), [-50])

    def test_proportional_and_sum(self):
        out = allocate_target_net([80, 20], 100)
        self.assertEqual(sum(out), 100)
        self.assertEqual(out, [80, 20])

    def test_zero_weights_split_even_rounding(self):
        out = allocate_target_net([0, 0, 0], 100)
        self.assertEqual(sum(out), 100)
        self.assertEqual(len(out), 3)

    def test_costs_are_allocated_by_sell_amount(self):
        sells = [
            SimpleNamespace(sell_amount=300_000, sell_price=0, sell_quantity=0),
            SimpleNamespace(sell_amount=100_000, sell_price=0, sell_quantity=0),
        ]
        self.assertEqual(allocate_by_sell_amount(sells, 400), [300, 100])


class CompareRealizedTests(unittest.TestCase):
    def test_mismatch_and_missing(self):
        kiwoom = aggregate_kiwoom_realized([
            {"dt": "20260818", "stk_cd": "A005930", "stk_nm": "삼성전자", "tdy_sel_pl": "-12000",
             "tdy_trde_cmsn": "70", "tdy_trde_tax": "0"},
            {"dt": "20260818", "stk_cd": "035420", "stk_nm": "NAVER", "tdy_sel_pl": "5000",
             "tdy_trde_cmsn": "30", "tdy_trde_tax": "10"},
        ])
        sells = [
            SimpleNamespace(
                status="COMPLETED",
                stock_code="005930",
                stock_name="삼성전자",
                profit_loss=-8000,
                completed_at=datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc),
            ),
            SimpleNamespace(
                status="COMPLETED",
                stock_code="000660",
                stock_name="SK하이닉스",
                profit_loss=1000,
                completed_at=datetime(2026, 8, 18, 7, 0, tzinfo=timezone.utc),
            ),
        ]
        from datetime import date

        db_map = aggregate_db_sells(sells, start_day=date(2026, 8, 18), end_day=date(2026, 8, 18))
        diffs = compare_realized(kiwoom, db_map)
        kinds = {d["stock_code"]: d["kind"] for d in diffs}
        self.assertEqual(kinds["005930"], "mismatch")
        self.assertEqual(kinds["035420"], "db_missing")
        self.assertEqual(kinds["000660"], "kiwoom_missing")
        samsung = next(d for d in diffs if d["stock_code"] == "005930")
        self.assertEqual(samsung["delta"], -4000)

    def test_daily_total_fallback(self):
        from datetime import date

        kiwoom = aggregate_ka10074_daily([
            {"dt": "20260818", "tdy_sel_pl": "-15000", "tdy_trde_cmsn": "100", "tdy_trde_tax": "20"},
        ])
        sells = [
            SimpleNamespace(
                status="COMPLETED",
                stock_code="005930",
                stock_name="삼성전자",
                profit_loss=-8000,
                completed_at=datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc),
            ),
            SimpleNamespace(
                status="COMPLETED",
                stock_code="000660",
                stock_name="SK하이닉스",
                profit_loss=-2000,
                completed_at=datetime(2026, 8, 18, 7, 0, tzinfo=timezone.utc),
            ),
        ]
        db_map = aggregate_db_sells(sells, start_day=date(2026, 8, 18), end_day=date(2026, 8, 18))
        diffs = compare_daily_totals(kiwoom, db_sells_by_day(db_map))
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]["kind"], "day_total")
        self.assertEqual(diffs[0]["delta"], -5000)
        self.assertEqual(diffs[0]["sell_count"], 2)

    def test_equal_net_with_missing_cost_is_still_a_diff(self):
        from datetime import date

        kiwoom = aggregate_ka10074_daily([
            {"dt": "20260818", "tdy_sel_pl": "-10000", "tdy_trde_cmsn": "300", "tdy_trde_tax": "700"},
        ])
        sells = [
            SimpleNamespace(
                status="COMPLETED",
                stock_code="005930",
                stock_name="삼성전자",
                profit_loss=-10000,
                trading_commission=None,
                transaction_tax=None,
                completed_at=datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc),
            ),
        ]
        db_map = aggregate_db_sells(
            sells, start_day=date(2026, 8, 18), end_day=date(2026, 8, 18),
        )
        diffs = compare_daily_totals(kiwoom, db_sells_by_day(db_map))
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]["delta"], 0)
        self.assertEqual(diffs[0]["cost_delta"], 1000)

    def test_format_table_empty(self):
        self.assertEqual(format_diff_table([]), "차이 없음")

    def test_stock_rows_are_reconciled_to_account_total(self):
        rows = {
            ("2026-08-18", "005930"): {"kiwoom_net": 400_000},
            ("2026-08-18", "000660"): {"kiwoom_net": 19_357},
        }
        adjustment = reconcile_stock_net_to_account_total(rows, 419_277)
        self.assertEqual(adjustment, -80)
        self.assertEqual(sum(r["kiwoom_net"] for r in rows.values()), 419_277)


class NotifyFormatTests(unittest.TestCase):
    def test_html_includes_totals(self):
        html = format_kiwoom_db_pnl_sync_html(
            {
                "start": "2026-08-01",
                "end": "2026-08-18",
                "applied": True,
                "kiwoom_net_sum": -100000,
                "db_net_sum": -70000,
                "delta_sum": -30000,
                "ka10074_total": -100000,
                "realized_diffs": [
                    {
                        "date": "2026-08-18",
                        "stock_name": "테스트",
                        "stock_code": "123456",
                        "kiwoom_net": -5000,
                        "db_net": -1000,
                        "delta": -4000,
                        "kind": "mismatch",
                    }
                ],
                "holding_diffs": [],
                "apply_result": {"updated_sells": 1, "backfilled": 0, "skipped": []},
                "holdings_updated": 0,
            }
        )
        self.assertIn("키움↔DB 손익 싱크", html)
        self.assertIn("테스트", html)
        self.assertIn("-100,000", html)


class AccountBalanceSnapshotTests(unittest.TestCase):
    def test_stores_d0_d2_and_settlement_gap(self):
        from datetime import date

        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        added = []
        session.add.side_effect = added.append

        result = sync_account_balance_snapshot(
            session,
            {
                "entr": "15224915",
                "d2_entra": "19939715",
                "tot_est_amt": "9074800",
                "tot_pur_amt": "8996715",
                "aset_evlt_amt": "19939715",
                "prsm_dpst_aset_amt": "28964625",
                "stk_acnt_evlt_prst": [{}, {}],
            },
            day=date(2026, 8, 18),
        )

        self.assertTrue(result["created"])
        self.assertEqual(result["settlement_gap"], 4_714_800)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].deposit_d0, 15_224_915)
        self.assertEqual(added[0].deposit_d2, 19_939_715)


if __name__ == "__main__":
    unittest.main()
