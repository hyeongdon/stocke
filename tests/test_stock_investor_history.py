import unittest

from api.kiwoom_stock_analytics import _StockAnalyticsMixin


class StockInvestorHistoryTests(unittest.IsolatedAsyncioTestCase):
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
                            },
                            {
                                "dt": "20261104",
                                "for_daly_nettrde_qty": "90",
                                "orgn_daly_nettrde_qty": "-30",
                            },
                            {
                                "dt": "20261105",
                                "for_daly_nettrde_qty": 0,
                                "orgn_daly_nettrde_qty": "10",
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
                },
                {
                    "date": "20261106",
                    "foreign_net": -120,
                    "institution_net": 25,
                },
            ],
        )
        self.assertEqual(api.api_id, "ka10045")
        self.assertEqual(api.request_data["stk_cd"], "005930")
        self.assertEqual(len(api.request_data["strt_dt"]), 8)
        self.assertEqual(len(api.request_data["end_dt"]), 8)

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
