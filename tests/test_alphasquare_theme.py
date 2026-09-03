"""알파스퀘어 테마 수집·표시 헬퍼 단위 테스트."""
from __future__ import annotations

import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from utils.theme_alphasquare_crawler import (
    extract_key_point,
    flatten_all_themes,
    is_kr_stock_row,
    normalize_kr_stock_code,
)
from utils.theme_map_store import (
    SOURCE_ALPHASQUARE_THEME,
    _SOURCE_RANK,
    _pick_top_names,
    _store_alphasquare_theme_edges,
    build_theme_source_cross_report,
    get_trade_flow_theme_map,
    source_label,
    source_short,
)


class TestAlphasquareNormalize(unittest.TestCase):
    def test_normalize_code(self):
        self.assertEqual(normalize_kr_stock_code("970"), "000970")
        self.assertEqual(normalize_kr_stock_code("005930"), "005930")
        self.assertEqual(normalize_kr_stock_code("A005930"), "005930")
        self.assertIsNone(normalize_kr_stock_code(""))
        self.assertIsNone(normalize_kr_stock_code(None))

    def test_kr_filter(self):
        self.assertTrue(
            is_kr_stock_row({"code": "005930", "country_code": "KR", "market": "kospi"})
        )
        self.assertFalse(
            is_kr_stock_row({"code": "AAPL", "country_code": "US", "market": "nasdaq"})
        )
        self.assertTrue(is_kr_stock_row({"code": "000660", "market": "kosdaq"}))

    def test_key_point_extract(self):
        desc = "본문입니다.\n\n💡**KEY POINT**\n강관은 건설경기에 영향을 받는다."
        kp = extract_key_point(desc)
        self.assertIsNotNone(kp)
        self.assertIn("강관", kp or "")

    def test_flatten_all_themes(self):
        payload = {
            "data": [
                {
                    "id": 25,
                    "name": "건설/토목",
                    "themes": [
                        {
                            "id": 108,
                            "name": "강관",
                            "description": "설명\n\n💡**KEY POINT**\n핵심",
                            "stock_count": 12,
                            "big_theme_id": 25,
                            "aliases": ["pipe"],
                        }
                    ],
                }
            ]
        }
        themes = flatten_all_themes(payload)
        self.assertEqual(len(themes), 1)
        self.assertEqual(themes[0]["theme_id"], 108)
        self.assertEqual(themes[0]["theme_name"], "강관")
        self.assertEqual(themes[0]["category_name"], "건설/토목")
        self.assertIn("핵심", themes[0]["key_point"] or "")


class TestAlphasquareSourceRank(unittest.TestCase):
    def test_rank_peer_with_naver_kiwoom(self):
        self.assertEqual(
            _SOURCE_RANK["alphasquare_theme"], _SOURCE_RANK["naver_theme"]
        )
        self.assertEqual(
            _SOURCE_RANK["alphasquare_theme"], _SOURCE_RANK["kiwoom_theme"]
        )

    def test_pick_keeps_distinct_names(self):
        rows = [
            ("반도체", 1.0, "naver_theme", datetime(2026, 8, 4, 9, 0, 0)),
            ("반도체", 1.0, "alphasquare_theme", datetime(2026, 8, 4, 9, 5, 0)),
            ("AI반도체", 1.0, "alphasquare_theme", datetime(2026, 8, 4, 9, 5, 0)),
        ]
        names = _pick_top_names(rows, limit=5)
        self.assertEqual(names.count("반도체"), 1)
        self.assertIn("AI반도체", names)

    def test_source_labels(self):
        self.assertEqual(source_label("alphasquare_theme"), "알파스퀘어")
        self.assertEqual(source_short("alphasquare_theme"), "AS")
        self.assertEqual(source_short("naver_theme"), "N")
        self.assertEqual(source_short("kiwoom_theme"), "K")


class TestStoreAlphasquareEdges(unittest.TestCase):
    def test_store_writes_alphasquare_source(self):
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
                for obj in self.added:
                    if getattr(obj, "id", None) is None and hasattr(obj, "tag_key"):
                        obj.id = 201

        sess = _Sess()
        snap = {
            "ok": True,
            "api_calls": 3,
            "error_count": 0,
            "errors": [],
            "fetch_reasons": False,
            "reason_count": 0,
            "themes": [
                {
                    "theme_id": 108,
                    "theme_name": "강관",
                    "description": "설명",
                    "key_point": "핵심",
                    "stock_count": 2,
                    "big_theme_id": 25,
                    "category_name": "건설/토목",
                    "stocks": [
                        {
                            "stock_code": "000970",
                            "stock_name": "한국주철관",
                            "alphasquare_stock_id": 2608,
                            "reason": "주철관·강관 생산",
                        },
                        {
                            "stock_code": "000660",
                            "stock_name": "SK하이닉스",
                            "alphasquare_stock_id": 1,
                        },
                    ],
                }
            ],
        }
        with patch(
            "utils.theme_map_store.crawl_alphasquare_theme_snapshot_sync",
            return_value=snap,
        ):
            result = _store_alphasquare_theme_edges(
                sess,
                biz=date(2026, 8, 4),
                now=datetime(2026, 8, 4, 10, 0, 0),
                top_n=0,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["themes"], 1)
        self.assertEqual(result["edges"], 2)
        tags = [x for x in sess.added if getattr(x, "tag_key", None)]
        edges = [
            x
            for x in sess.added
            if getattr(x, "stock_code", None) is not None
            and getattr(x, "source", None) == SOURCE_ALPHASQUARE_THEME
        ]
        self.assertTrue(any(t.tag_key.startswith("alphasquare_theme_108_") for t in tags))
        self.assertEqual(len(edges), 2)
        self.assertEqual(edges[0].reason_text, "주철관·강관 생산")
        self.assertIn("알파스퀘어 테마", edges[1].reason_text)


class TestTradeFlowThemeMap(unittest.TestCase):
    def test_combines_all_sources_and_deduplicates_names(self):
        biz = date(2026, 8, 20)
        rows = [
            (
                SimpleNamespace(
                    stock_code="005930", source="naver_theme", biz_date=biz,
                    inclusion_flag=True, rank=1,
                ),
                SimpleNamespace(name_ko="반도체", tag_type="theme"),
            ),
            (
                SimpleNamespace(
                    stock_code="005930", source="alphasquare_theme", biz_date=biz,
                    inclusion_flag=True, rank=2,
                ),
                SimpleNamespace(name_ko="AI반도체", tag_type="theme"),
            ),
            (
                SimpleNamespace(
                    stock_code="005930", source="kiwoom_theme", biz_date=biz,
                    inclusion_flag=True, rank=3,
                ),
                SimpleNamespace(name_ko="반도체", tag_type="theme"),
            ),
        ]

        class FakeQuery:
            def __init__(self, values):
                self.values = values

            def join(self, *args, **kwargs):
                return self

            def filter(self, *args, **kwargs):
                return self

            def order_by(self, *args, **kwargs):
                return self

            def scalar(self):
                return biz

            def all(self):
                return self.values

        class FakeSession:
            def __init__(self):
                self.calls = 0

            def query(self, *args):
                self.calls += 1
                return FakeQuery([] if self.calls == 1 else rows)

        result = get_trade_flow_theme_map(FakeSession(), ["005930"])
        self.assertEqual(result["005930"]["themes"], ["반도체", "AI반도체"])
        self.assertEqual(result["005930"]["tag_freshness"], "2026-08-20")


class TestSourceCrossReport(unittest.TestCase):
    def test_empty_biz(self):
        sess = MagicMock()
        q = MagicMock()
        sess.query.return_value = q
        q.scalar.return_value = None
        r = build_theme_source_cross_report(sess)
        self.assertFalse(r["ok"])

    def test_overlap_math(self):
        class FakeQ:
            def __init__(self, rows):
                self._rows = rows

            def join(self, *a, **k):
                return self

            def filter(self, *a, **k):
                return self

            def all(self):
                return self._rows

            def scalar(self):
                return date(2026, 8, 4)

        class FakeSess:
            def __init__(self):
                self._calls = 0
                self._maps = [
                    [("005930", "반도체"), ("000660", "반도체")],
                    [("000660", "반도체"), ("035420", "인터넷")],
                    [
                        ("005930", "반도체"),
                        ("035420", "인터넷"),
                        ("000970", "강관"),
                    ],
                ]

            def query(self, *a, **k):
                if self._calls == 0:
                    self._calls += 1
                    return FakeQ([])
                idx = min(self._calls - 1, len(self._maps) - 1)
                rows = self._maps[idx]
                self._calls += 1
                return FakeQ(rows)

        r = build_theme_source_cross_report(FakeSess())
        self.assertTrue(r["ok"])
        self.assertEqual(r["stocks"]["naver"], 2)
        self.assertEqual(r["stocks"]["kiwoom"], 2)
        self.assertEqual(r["stocks"]["alphasquare"], 3)
        self.assertEqual(r["stocks"]["union"], 4)
        self.assertEqual(r["stocks"]["alphasquare_only"], 1)
        self.assertEqual(r["stocks"]["all_three"], 0)
        self.assertGreater(r["name_overlap"]["naver_alphasquare"]["share_pct"], 0)


if __name__ == "__main__":
    unittest.main()
