import unittest

from api.kiwoom_stock_analytics import _StockAnalyticsMixin


class StockInvestorHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_estimates_amounts_from_daily_chart_when_investor_rows_have_no_price(self):
        class Stub(_StockAnalyticsMixin):
            @staticmethod
            def normalize_stock_code(stock_code):
                return stock_code

            async def _request_stockinfo_tr(self, api_id, request_data):
                return {
                    "success": True,
                    "data": {
                        "stk_orgn_trde_trnsn": [
                            {
                                "dt": "20261106",
                                "for_daly_nettrde_qty": "100",
                                "orgn_daly_nettrde_qty": "-20",
                            }
                        ]
                    },
                }

            async def get_stock_chart_data(self, stock_code, period, max_bars, *, allow_off_hours):
                self.chart_request = (stock_code, period, max_bars, allow_off_hours)
                return [
                    {
                        "timestamp": "2026-11-06 15:30:00",
                        "close": 70000,
                        "volume": 1200000,
                    }
                ]

        api = Stub()
        result = await api.get_stock_institution_foreign_history("010140", days=60)

        self.assertEqual(api.chart_request, ("010140", "1D", 65, True))
        self.assertEqual(
            result["rows"],
            [
                {
                    "date": "20261106",
                    "foreign_net": 100,
                    "institution_net": -20,
                    "close_price": 70000,
                    "trade_volume": 1200000,
                    "foreign_net_estimated_amount": 7000000,
                    "institution_net_estimated_amount": -1400000,
                    "trade_value_estimated": 84000000000,
                }
            ],
        )

    async def test_returns_sorted_daily_net_qty_for_requested_days(self):
        class Stub(_StockAnalyticsMixin):
            @staticmethod
            def normalize_stock_code(stock_code):
                return stock_code[1:] if stock_code.startswith("A") else stock_code

            async def _request_stockinfo_tr(self, api_id, request_data):
                self.api_id = api_id
                self.request_data = request_data
                return {
                    "success": True,
                    "data": {
                        "stk_orgn_trde_trnsn": [
                            {
                                "dt": "20261106",
                                "for_daly_nettrde_qty": "-120",
                                "orgn_daly_nettrde_qty": "25",
                                "cur_prc": "-70000",
                                "trde_qty": "1,000",
                            },
                            {
                                "dt": "20261104",
                                "for_daly_nettrde_qty": "90",
                                "orgn_daly_nettrde_qty": "-30",
                                "cur_prc": "69000",
                                "trde_qty": "2,000",
                            },
                            {
                                "dt": "20261105",
                                "for_daly_nettrde_qty": 0,
                                "orgn_daly_nettrde_qty": "10",
                                "cur_prc": "69500",
                                "trde_qty": "1,500",
                            },
                        ]
                    },
                }

        api = Stub()
        result = await api.get_stock_institution_foreign_history("A005930", days=2)

        self.assertTrue(result["success"])
        self.assertEqual(result["stock_code"], "005930")
        self.assertEqual(result["unit"], "주")
        self.assertEqual(
            result["rows"],
            [
                {
                    "date": "20261105",
                    "foreign_net": 0,
                    "institution_net": 10,
                    "close_price": 69500,
                    "trade_volume": 1500,
                    "foreign_net_estimated_amount": 0,
                    "institution_net_estimated_amount": 695000,
                    "trade_value_estimated": 104250000,
                },
                {
                    "date": "20261106",
                    "foreign_net": -120,
                    "institution_net": 25,
                    "close_price": 70000,
                    "trade_volume": 1000,
                    "foreign_net_estimated_amount": -8400000,
                    "institution_net_estimated_amount": 1750000,
                    "trade_value_estimated": 70000000,
                },
            ],
        )
        self.assertEqual(api.api_id, "ka10045")
        self.assertEqual(api.request_data["stk_cd"], "005930")
        self.assertEqual(len(api.request_data["strt_dt"]), 8)
        self.assertEqual(len(api.request_data["end_dt"]), 8)

    async def test_fetches_enough_calendar_days_for_sixty_sessions(self):
        class Stub(_StockAnalyticsMixin):
            @staticmethod
            def normalize_stock_code(stock_code):
                return stock_code

            async def _request_stockinfo_tr(self, api_id, request_data):
                self.request_data = request_data
                return {
                    "success": True,
                    "data": {
                        "stk_orgn_trde_trnsn": [
                            {
                                "dt": "20261106",
                                "for_daly_nettrde_qty": "1",
                                "orgn_daly_nettrde_qty": "2",
                            }
                        ]
                    },
                }

            async def get_stock_chart_data(self, stock_code, period, max_bars, *, allow_off_hours):
                return []

        api = Stub()
        result = await api.get_stock_institution_foreign_history("005930", days=60)

        from datetime import datetime

        start = datetime.strptime(api.request_data["strt_dt"], "%Y%m%d").date()
        end = datetime.strptime(api.request_data["end_dt"], "%Y%m%d").date()
        self.assertTrue(result["success"])
        self.assertGreaterEqual((end - start).days, 125)

    async def test_reports_empty_history_as_failure(self):
        class Stub(_StockAnalyticsMixin):
            @staticmethod
            def normalize_stock_code(stock_code):
                return stock_code

            async def _request_stockinfo_tr(self, api_id, request_data):
                return {"success": True, "data": {"stk_orgn_trde_trnsn": []}}

        result = await Stub().get_stock_institution_foreign_history("005930")

        self.assertFalse(result["success"])
        self.assertIn("일별 수급", result["error"])

    async def test_surfaces_kiwoom_error(self):
        class Stub(_StockAnalyticsMixin):
            @staticmethod
            def normalize_stock_code(stock_code):
                return stock_code

            async def _request_stockinfo_tr(self, api_id, request_data):
                return {"success": False, "error": "API unavailable"}

        result = await Stub().get_stock_institution_foreign_history("005930")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "API unavailable")
