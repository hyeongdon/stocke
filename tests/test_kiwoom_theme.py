"""키움 테마 수집·표시 헬퍼 단위 테스트."""
from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import patch

from api.kiwoom_api import KiwoomAPI
from utils.theme_map_store import _pick_top_names, _SOURCE_RANK, _store_kiwoom_theme_edges


class TestKiwoomThemeParse(unittest.TestCase):
    def test_parse_theme_group_row(self):
        row = KiwoomAPI._parse_theme_group_row(
            {
                "thema_grp_cd": "243",
                "thema_nm": "자동차_차량용 반도체",
                "stk_num": "2",
                "flu_rt": "+15.03",
                "rising_stk_num": "2",
                "fall_stk_num": "0",
                "dt_prft_rt": "+19.61",
                "main_stk": "현대모비스, 한라홀딩스",
            }
        )
        self.assertEqual(row["theme_code"], "243")
        self.assertEqual(row["theme_name"], "자동차_차량용 반도체")
        self.assertEqual(row["stock_count"], 2)
        self.assertAlmostEqual(row["change_rate"], 15.03, places=2)

    def test_parse_theme_stock_row(self):
        row = KiwoomAPI._parse_theme_stock_row(
            {
                "stk_cd": "A005930",
                "stk_nm": "삼성전자",
                "cur_prc": "-56200",
                "flu_rt": "-0.18",
                "acc_trde_qty": "16886813",
            }
        )
        self.assertEqual(row["stock_code"], "005930")
        self.assertEqual(row["stock_name"], "삼성전자")
        self.assertEqual(row["current_price"], 56200)


class TestDualThemePick(unittest.TestCase):
    def test_source_rank_includes_kiwoom(self):
        self.assertEqual(_SOURCE_RANK["kiwoom_theme"], _SOURCE_RANK["naver_theme"])

    def test_same_name_dedupes(self):
        rows = [
            ("반도체", 1.0, "naver_theme", datetime(2026, 8, 1, 9, 0, 0)),
            ("반도체", 1.0, "kiwoom_theme", datetime(2026, 8, 1, 9, 5, 0)),
        ]
        names = _pick_top_names(rows, limit=3)
        self.assertEqual(names, ["반도체"])

    def test_different_names_both_kept(self):
        rows = [
            ("2차전지", 1.0, "naver_theme", datetime(2026, 8, 1, 9, 0, 0)),
            ("이차전지", 1.0, "kiwoom_theme", datetime(2026, 8, 1, 9, 5, 0)),
            ("AI반도체", 1.0, "kiwoom_theme", datetime(2026, 8, 1, 9, 5, 0)),
        ]
        names = _pick_top_names(rows, limit=5)
        self.assertIn("2차전지", names)
        self.assertIn("이차전지", names)
        self.assertIn("AI반도체", names)


class TestStoreKiwoomEdges(unittest.TestCase):
    def test_store_writes_kiwoom_source(self):
        class _Q:
            def filter(self, *a, **k):
                return self

            def delete(self, synchronize_session=False):
                return 0

            def first(self):
                return None

        class _Sess:
            def __init__(self):
                self.added = []

            def query(self, *a, **k):
                return _Q()

            def add(self, obj):
                self.added.append(obj)

            def flush(self):
                # ThemeTag.id 가 필요하므로 가짜 id 부여
                for obj in self.added:
                    if getattr(obj, "id", None) is None and hasattr(obj, "tag_key"):
                        obj.id = 101

        sess = _Sess()
        snap = {
            "ok": True,
            "api_calls": 3,
            "error_count": 0,
            "errors": [],
            "themes": [
                {
                    "theme_code": "550",
                    "theme_name": "반도체_생산",
                    "change_rate": 1.2,
                    "period_return": 3.4,
                    "stock_count": 2,
                    "main_stocks": "SK하이닉스",
                    "stocks": [
                        {"stock_code": "000660", "stock_name": "SK하이닉스"},
                        {"stock_code": "005930", "stock_name": "삼성전자"},
                    ],
                }
            ],
        }
        with patch(
            "utils.theme_map_store.crawl_kiwoom_theme_snapshot_sync",
            return_value=snap,
        ):
            result = _store_kiwoom_theme_edges(
                sess,
                biz=date(2026, 8, 1),
                now=datetime(2026, 8, 1, 10, 0, 0),
                top_n=0,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["themes"], 1)
        self.assertEqual(result["edges"], 2)
        self.assertEqual(result["api_calls"], 3)
        edge_sources = [getattr(x, "source", None) for x in sess.added]
        self.assertIn("kiwoom_theme", edge_sources)


if __name__ == "__main__":
    unittest.main()
