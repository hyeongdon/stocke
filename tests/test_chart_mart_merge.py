"""차트 OHLCV 마트 — 이력 병합(merge_bars)·깊게 받기 계획·배치 _fetch_one 테스트.

실제 키움 API 는 사용하지 않는다. DB 테스트는 더미 종목코드를 쓰고 끝나면 지운다.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

from utils.chart_ohlcv_mart_store import (
    FULL_SOURCE,
    MAX_KEEP,
    MIN_DEPTH,
    get_bars,
    get_depth_map,
    get_meta,
    merge_bars,
    needs_deep_fetch,
    upsert_series,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _days(n: int, end: date = date(2026, 11, 27)):
    """end 까지의 평일 n개 날짜(오름차순)."""
    out = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def _bar(d: date, close: int):
    return {
        "timestamp": d.isoformat(), "open": close, "high": close + 5,
        "low": close - 5, "close": close, "volume": 1000,
    }


def _daily(n: int, end: date = date(2026, 11, 27), base: int = 10000):
    """날짜별로 고유한 종가(날짜 서수 기반) → 같은 날짜는 항상 같은 값."""
    return [_bar(d, base + d.toordinal() % 5000) for d in _days(n, end)]


class MergeBarsTests(unittest.TestCase):
    def test_keeps_deep_history_and_appends_new_bar(self):
        old = _daily(600, end=date(2026, 11, 26))
        new = _daily(400, end=date(2026, 11, 27))          # 최신 1페이지(400) — 오늘 봉 1개 추가
        merged, ok = merge_bars(old, new, "1D")
        self.assertTrue(ok)
        self.assertEqual(len(merged), 601)                 # 600 + 새 봉 1개 (400개 창이 600개 이력을 지우지 않음)
        self.assertEqual(merged[0]["timestamp"], old[0]["timestamp"])
        self.assertEqual(merged[-1]["timestamp"], "2026-11-27")

    def test_new_value_wins_for_same_period(self):
        old = _daily(100)
        new = [dict(b) for b in old[-50:]]
        new[-1]["close"] += 777                            # 마지막(진행 중) 봉 값이 갱신됨
        merged, ok = merge_bars(old, new, "1D")
        self.assertTrue(ok)
        self.assertEqual(len(merged), 100)
        self.assertEqual(merged[-1]["close"], new[-1]["close"])

    def test_price_adjustment_replaces_instead_of_merging(self):
        old = _daily(300)
        new = [dict(b, close=b["close"] // 2, open=b["open"] // 2,
                    high=b["high"] // 2, low=b["low"] // 2) for b in old[-200:]]  # 액면분할 → 과거 값 변경
        merged, ok = merge_bars(old, new, "1D")
        self.assertFalse(ok)
        self.assertEqual(len(merged), 200)                 # 과거 이력을 섞지 않고 새 값으로 교체
        self.assertEqual(merged[0]["close"], new[0]["close"])

    def test_no_comparable_overlap_replaces(self):
        old = _daily(50, end=date(2026, 6, 30))
        new = _daily(50, end=date(2026, 11, 27))           # 겹침 없음(공백) → 병합 불가
        merged, ok = merge_bars(old, new, "1D")
        self.assertFalse(ok)
        self.assertEqual(len(merged), 50)

    def test_only_latest_overlap_is_not_enough_to_verify(self):
        old = _daily(10, end=date(2026, 11, 26))
        new = old[-1:] + [_bar(date(2026, 11, 27), 12345)]  # 겹침이 최신 1개뿐 → 검증 불가
        merged, ok = merge_bars(old, new, "1D")
        self.assertFalse(ok)

    def test_weekly_same_week_not_duplicated(self):
        # 같은 ISO 주(11/23~11/27)에 대해 기존은 수요일, 새 값은 금요일 timestamp
        weeks = [date(2026, 11, 27) - timedelta(weeks=i) for i in range(9, -1, -1)]  # 마지막 = 11/27(금)
        old = [_bar(d, 1000 + i) for i, d in enumerate(weeks)]
        old[-1] = _bar(date(2026, 11, 25), 1009)           # 진행 중 주(수요일 기준 값)
        new = [_bar(d, 1000 + i) for i, d in enumerate(weeks[-5:], start=5)]
        new[-1] = _bar(date(2026, 11, 27), 1100)           # 같은 주, 금요일 기준으로 갱신
        merged, ok = merge_bars(old, new, "1W")
        self.assertTrue(ok)
        self.assertEqual(len(merged), 10)
        self.assertEqual(merged[-1]["timestamp"], "2026-11-27")
        self.assertEqual(merged[-1]["close"], 1100)

    def test_monthly_bucket_key(self):
        months = [date(2026, m, 1) for m in range(1, 12)]
        old = [_bar(d, 100 + i) for i, d in enumerate(months)]
        new = [_bar(d, 100 + i) for i, d in enumerate(months[-4:], start=7)]
        new[-1] = _bar(date(2026, 11, 27), 999)            # 같은 달, 다른 일자 표기
        merged, ok = merge_bars(old, new, "1MO")
        self.assertTrue(ok)
        self.assertEqual(len(merged), 11)

    def test_caps_to_max_keep_dropping_oldest(self):
        cap = MAX_KEEP["1D"]
        old = _daily(cap, end=date(2026, 11, 26))
        new = _daily(50, end=date(2026, 11, 27))
        merged, ok = merge_bars(old, new, "1D")
        self.assertTrue(ok)
        self.assertEqual(len(merged), cap)
        self.assertEqual(merged[-1]["timestamp"], "2026-11-27")
        self.assertGreater(merged[0]["timestamp"], old[0]["timestamp"])

    def test_empty_old_returns_new(self):
        merged, ok = merge_bars([], _daily(5), "1D")
        self.assertFalse(ok)
        self.assertEqual(len(merged), 5)


class NeedsDeepFetchTests(unittest.TestCase):
    def test_matrix(self):
        need = MIN_DEPTH["1D"]
        self.assertTrue(needs_deep_fetch("1D", 400, "kiwoom", None))          # 얕음 + 무제한
        self.assertTrue(needs_deep_fetch("1D", 400, "kiwoom", 3))
        self.assertFalse(needs_deep_fetch("1D", 400, "kiwoom", 0))            # 예산 소진
        self.assertFalse(needs_deep_fetch("1D", need, "kiwoom", None))        # 이미 충분
        self.assertFalse(needs_deep_fetch("1D", 100, FULL_SOURCE, None))      # 키움 이력 전부 확인됨
        self.assertTrue(needs_deep_fetch("1MO", 36, "", 1))
        self.assertFalse(needs_deep_fetch("1MO", MIN_DEPTH["1MO"], "", None))


class UpsertMergeDbTests(unittest.TestCase):
    CODE = "999997"

    def setUp(self):
        self.addCleanup(self._cleanup)

    @staticmethod
    def _cleanup():
        from core.models import ChartOhlcvSeries, get_db

        for db in get_db():
            db.query(ChartOhlcvSeries).filter(ChartOhlcvSeries.stock_code == UpsertMergeDbTests.CODE).delete()
            db.commit()
            break

    def test_merge_preserves_depth_and_source(self):
        upsert_series(self.CODE, "1D", _daily(600, end=date(2026, 11, 26)), source=FULL_SOURCE)
        # 얕은 갱신(source=None, merge=True): 깊이 유지 + 기존 source 유지
        upsert_series(self.CODE, "1D", _daily(400, end=date(2026, 11, 27)), source=None, merge=True)
        bars = get_bars(self.CODE, "1D")
        self.assertEqual(len(bars), 601)
        meta = get_meta(self.CODE, "1D")
        self.assertEqual(meta["bar_count"], 601)
        self.assertEqual(meta["last_bar_date"], "2026-11-27")
        self.assertEqual(meta["source"], FULL_SOURCE)
        self.assertEqual(get_depth_map("1D")[self.CODE], (601, FULL_SOURCE))

    def test_adjustment_replaces_and_drops_source_marker(self):
        upsert_series(self.CODE, "1D", _daily(300), source=FULL_SOURCE)
        halved = [dict(b, close=b["close"] // 2) for b in _daily(200)]
        upsert_series(self.CODE, "1D", halved, source=None, merge=True)
        self.assertEqual(len(get_bars(self.CODE, "1D")), 200)
        self.assertEqual(get_meta(self.CODE, "1D")["source"], "kiwoom")

    def test_non_merge_still_replaces(self):
        upsert_series(self.CODE, "1D", _daily(300))
        upsert_series(self.CODE, "1D", _daily(50))
        self.assertEqual(len(get_bars(self.CODE, "1D")), 50)


def _load_batch_module():
    """배치 스크립트 import — win32 에서 sys.stdout 을 감싸는 부작용을 피한다 (pytest 캡처 보호)."""
    path = os.path.join(PROJECT_ROOT, "scripts", "chart_ohlcv_mart_batch.py")
    spec = importlib.util.spec_from_file_location("chart_ohlcv_mart_batch_under_test", path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(sys, "platform", "linux"):
        spec.loader.exec_module(module)
    return module


class _FakeApi:
    def __init__(self, raw_len: int, exhausted: bool = False):
        self.raw_len = raw_len
        self.exhausted = exhausted
        self.calls = []

    async def get_stock_chart_data(self, code, period="1D", max_bars=None, **kw):
        self.calls.append((code, period, max_bars))
        return _daily(self.raw_len)

    def chart_history_exhausted(self, code, period, max_bars):
        return self.exhausted


class BatchFetchPlanTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = _load_batch_module()

    def _plan(self, depth, budget):
        return self.b.FetchPlan({"1D": depth}, {"1D": budget})

    async def _run(self, api, plan, code="005930"):
        sem = asyncio.Semaphore(1)
        return await self.b._fetch_one(api, sem, {"stock_code": code}, ["1D"], 0.0, plan)

    async def test_shallow_stock_fetches_deep_and_marks_full_when_exhausted(self):
        api = _FakeApi(raw_len=300, exhausted=True)
        plan = self._plan({"005930": (400, "kiwoom")}, 5)
        out = await self._run(api, plan)
        self.assertEqual(api.calls, [("005930", "1D", self.b.INTERVAL_BARS["1D"])])
        self.assertEqual(out[0]["source"], FULL_SOURCE)
        self.assertTrue(out[0]["merge"])
        self.assertEqual(plan.budget["1D"], 4)
        self.assertEqual(plan.deep_exhausted_full["1D"], 1)

    async def test_deep_fetch_not_marked_full_when_paging_was_interrupted(self):
        api = _FakeApi(raw_len=400, exhausted=False)
        plan = self._plan({"005930": (400, "kiwoom")}, 5)
        out = await self._run(api, plan)
        self.assertEqual(out[0]["source"], "kiwoom")

    async def test_deep_fetch_reaching_min_depth_is_not_marked_full(self):
        api = _FakeApi(raw_len=640, exhausted=True)
        plan = self._plan({"005930": (400, "kiwoom")}, 5)
        out = await self._run(api, plan)
        self.assertEqual(out[0]["source"], "kiwoom")

    async def test_deep_stock_only_fetches_latest_page_and_keeps_source(self):
        api = _FakeApi(raw_len=400)
        plan = self._plan({"005930": (700, "kiwoom")}, 5)
        out = await self._run(api, plan)
        self.assertEqual(api.calls, [("005930", "1D", None)])
        self.assertIsNone(out[0]["source"])
        self.assertTrue(out[0]["merge"])
        self.assertEqual(plan.budget["1D"], 5)

    async def test_full_marked_stock_never_goes_deep(self):
        api = _FakeApi(raw_len=400)
        plan = self._plan({"005930": (100, FULL_SOURCE)}, None)
        await self._run(api, plan)
        self.assertEqual(api.calls[0][2], None)

    async def test_budget_limits_deep_fetches(self):
        api = _FakeApi(raw_len=400)
        plan = self._plan({"000020": (400, "kiwoom"), "000040": (400, "kiwoom"), "000050": (400, "kiwoom")}, 2)
        for code in ("000020", "000040", "000050"):
            await self._run(api, plan, code)
        self.assertEqual([c[2] for c in api.calls], [self.b.INTERVAL_BARS["1D"]] * 2 + [None])
        self.assertEqual(plan.deep_taken["1D"], 2)

    async def test_unknown_stock_without_mart_row_is_deep(self):
        api = _FakeApi(raw_len=400)
        plan = self._plan({}, None)
        await self._run(api, plan)
        self.assertEqual(api.calls[0][2], self.b.INTERVAL_BARS["1D"])

    def test_resolve_budget(self):
        b = self.b
        ns = mock.Mock(deep_budget=None)
        self.assertEqual(b.resolve_budget(ns, ["1D", "1W", "1MO"]), {"1D": 400, "1W": 1500, "1MO": 1500})
        ns = mock.Mock(deep_budget=0)
        self.assertEqual(b.resolve_budget(ns, ["1D"]), {"1D": None})
        ns = mock.Mock(deep_budget=50)
        self.assertEqual(b.resolve_budget(ns, ["1D", "1MO"]), {"1D": 50, "1MO": 50})

    def test_prioritize_universe_favorites_first(self):
        items = [{"stock_code": c} for c in ("000020", "000040", "005930", "035720")]
        out = self.b.prioritize_universe(items, {"035720", "005930"})
        self.assertEqual([i["stock_code"] for i in out], ["005930", "035720", "000020", "000040"])


if __name__ == "__main__":
    unittest.main()
