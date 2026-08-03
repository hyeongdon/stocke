"""분봉 통합(_AL) 종목코드 헬퍼."""
import unittest

from api.kiwoom_api import KiwoomAPI


class MinuteChartStkCdTests(unittest.TestCase):
    def test_normalize_strips_venue_suffix(self):
        self.assertEqual(KiwoomAPI.normalize_stock_code("A460930_AL"), "460930")
        self.assertEqual(KiwoomAPI.normalize_stock_code("460930_NX"), "460930")
        self.assertEqual(KiwoomAPI.normalize_stock_code("005930"), "005930")

    def test_minute_chart_uses_al_suffix(self):
        self.assertEqual(KiwoomAPI.minute_chart_stk_cd("460930"), "460930_AL")
        self.assertEqual(KiwoomAPI.minute_chart_stk_cd("A005930_NX"), "005930_AL")
        self.assertEqual(KiwoomAPI.minute_chart_stk_cd(""), "")


if __name__ == "__main__":
    unittest.main()
