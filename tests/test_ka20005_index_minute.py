"""ka20005 업종분봉조회 — inds_min_pole_qry 파싱 (검증 페이지 지수 카드 분봉)."""
import unittest

from api.kiwoom_api import KiwoomAPI


SAMPLE_KA20005 = {
    "inds_cd": "001",
    "inds_min_pole_qry": [
        # 최신 먼저 오는 역순 응답 가정
        {
            "cntr_tm": "20260814093000",
            "cur_prc": "2493.18",
            "open_pric": "2490.00",
            "high_pric": "2495.50",
            "low_pric": "2489.10",
            "trde_qty": "12345",
            "acc_trde_qty": "99999",
        },
        {
            "cntr_tm": "20260814091500",
            "cur_prc": "2488.02",
            "open_pric": "2485.00",
            "high_pric": "2490.30",
            "low_pric": "2484.00",
            "trde_qty": "23456",
            "acc_trde_qty": "87654",
        },
        # 다른 날짜 봉은 필터링되어야 함
        {
            "cntr_tm": "20260813153000",
            "cur_prc": "2470.00",
            "open_pric": "2468.00",
            "high_pric": "2472.00",
            "low_pric": "2467.00",
            "trde_qty": "11111",
            "acc_trde_qty": "11111",
        },
    ],
    "return_code": 0,
    "return_msg": "정상적으로 처리되었습니다",
}


class ParseKa20005BarsTests(unittest.TestCase):
    def test_filters_and_sorts(self):
        bars = KiwoomAPI.parse_ka20005_bars(SAMPLE_KA20005, "20260814")
        self.assertEqual(len(bars), 2)
        # 오름차순 정렬
        self.assertEqual(bars[0]["timestamp"], "2026-08-14 09:15:00")
        self.assertEqual(bars[1]["timestamp"], "2026-08-14 09:30:00")

    def test_ohlcv_float(self):
        bars = KiwoomAPI.parse_ka20005_bars(SAMPLE_KA20005, "20260814")
        b = bars[1]
        self.assertAlmostEqual(b["open"], 2490.00)
        self.assertAlmostEqual(b["high"], 2495.50)
        self.assertAlmostEqual(b["low"], 2489.10)
        self.assertAlmostEqual(b["close"], 2493.18)
        self.assertEqual(b["volume"], 12345)

    def test_signed_values_abs(self):
        data = {
            "inds_min_pole_qry": [
                {
                    "cntr_tm": "20260814090000",
                    "cur_prc": "-800.47",
                    "open_pric": "-798.00",
                    "high_pric": "-801.00",
                    "low_pric": "-797.50",
                    "trde_qty": "-100",
                }
            ]
        }
        bars = KiwoomAPI.parse_ka20005_bars(data, "20260814")
        self.assertEqual(len(bars), 1)
        self.assertAlmostEqual(bars[0]["close"], 800.47)
        self.assertAlmostEqual(bars[0]["open"], 798.00)
        self.assertEqual(bars[0]["volume"], 100)

    def test_empty(self):
        self.assertEqual(KiwoomAPI.parse_ka20005_bars({}, "20260814"), [])
        self.assertEqual(KiwoomAPI.parse_ka20005_bars(None, "20260814"), [])
        self.assertEqual(KiwoomAPI.parse_ka20005_bars({"inds_min_pole_qry": None}, "20260814"), [])

    def test_no_matching_date(self):
        self.assertEqual(KiwoomAPI.parse_ka20005_bars(SAMPLE_KA20005, "20260815"), [])


if __name__ == "__main__":
    unittest.main()
