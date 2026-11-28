"""차트 로딩 속도 개선 테스트 — 키움 일·주·월봉 캐시 우회 수정 + 마트 즉시 응답/백그라운드 보강.

실제 DB·키움 API 는 사용하지 않는다 (fake 세션/마트 패치).
"""
from __future__ import annotations

import asyncio
import time
import unittest
from unittest import mock

from api import kiwoom_market_data
from api.kiwoom_api import KiwoomAPI


def _rows(n: int):
    """키움 파싱 결과 형태의 일봉 n개 (오름차순)."""
    return [
        {"timestamp": f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}", "open": 100, "high": 110,
         "low": 90, "close": 100 + i, "volume": 1000}
        for i in range(n)
    ]


class _FakeToken:
    def __init__(self):
        self.calls = 0

    def get_valid_token(self):
        self.calls += 1
        return None  # 캐시 미스 시 "토큰 없음" 으로 조기 종료 — 호출 횟수로 네트워크 시도 여부 판별


def _bare_api() -> KiwoomAPI:
    api = KiwoomAPI.__new__(KiwoomAPI)  # __init__(토큰·웹소켓 등) 우회
    api._chart_cache = {}
    api._chart_exhausted = {}
    api._chart_cache_ttl = 300
    api._daily_chart_cache_ttl = 43200
    api.token_manager = _FakeToken()
    return api


class ChartCacheExhaustedTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_cache_without_marker_refetches(self):
        """(수정 전 동작 확인) 캐시가 max_bars 보다 짧고 소진 표시가 없으면 다시 조회를 시도한다."""
        api = _bare_api()
        api._chart_cache["005930:1D"] = (_rows(400), time.time())
        await api.get_stock_chart_data("005930", "1D", max_bars=650, allow_off_hours=True)
        self.assertEqual(api.token_manager.calls, 1)

    async def test_exhausted_marker_serves_short_cache(self):
        """키움이 400봉이 전부로 소진됐다고 표시되면 같은 max_bars 로는 캐시를 쓴다."""
        api = _bare_api()
        ts = time.time()
        api._chart_cache["005930:1D"] = (_rows(400), ts)
        api._chart_exhausted["005930:1D"] = (650, ts)
        out = await api.get_stock_chart_data("005930", "1D", max_bars=650, allow_off_hours=True)
        self.assertEqual(len(out), 400)
        self.assertEqual(api.token_manager.calls, 0)
        # 더 작은 max_bars 도 캐시 사용 (슬라이스)
        out = await api.get_stock_chart_data("005930", "1D", max_bars=500, allow_off_hours=True)
        self.assertEqual(len(out), 400)
        self.assertEqual(api.token_manager.calls, 0)

    async def test_larger_request_than_marker_refetches(self):
        api = _bare_api()
        ts = time.time()
        api._chart_cache["005930:1D"] = (_rows(400), ts)
        api._chart_exhausted["005930:1D"] = (650, ts)
        await api.get_stock_chart_data("005930", "1D", max_bars=900, allow_off_hours=True)
        self.assertEqual(api.token_manager.calls, 1)

    async def test_stale_marker_from_older_cache_entry_is_ignored(self):
        api = _bare_api()
        ts = time.time()
        api._chart_cache["005930:1D"] = (_rows(400), ts)
        api._chart_exhausted["005930:1D"] = (650, ts - 100)  # 이전 캐시 항목의 표시
        await api.get_stock_chart_data("005930", "1D", max_bars=650, allow_off_hours=True)
        self.assertEqual(api.token_manager.calls, 1)

    async def test_expired_cache_refetches_even_with_marker(self):
        api = _bare_api()
        ts = time.time() - 50000  # 일봉 TTL(12h=43200s) 초과
        api._chart_cache["005930:1D"] = (_rows(400), ts)
        api._chart_exhausted["005930:1D"] = (650, ts)
        await api.get_stock_chart_data("005930", "1D", max_bars=650, allow_off_hours=True)
        self.assertEqual(api.token_manager.calls, 1)


class _FakeResponse:
    status = 200
    headers = {}

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    posts = 0

    def __init__(self, payload):
        self._payload = payload

    def post(self, *a, **k):
        type(self).posts += 1
        return _FakeResponse(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _TokenOk:
    def get_valid_token(self):
        return "tok"


class ChartFetchMarksExhaustionTests(unittest.IsolatedAsyncioTestCase):
    """실제 get_stock_chart_data 흐름(가짜 aiohttp)에서 소진 표시·재호출 여부."""

    def _api(self, cont_yn: str, slot_ok: bool = True, rows_per_page: int = 400):
        api = _bare_api()
        api.token_manager = _TokenOk()
        payload = {"return_code": 0, "cont_yn": cont_yn, "next_key": "k" if cont_yn == "Y" else ""}
        _FakeSession.posts = 0
        api._parse_kiwoom_chart_data = lambda data, code: _rows(rows_per_page)

        slot_calls = {"n": 0}

        async def _slot(*a, **k):
            # 첫 요청 슬롯은 항상 성공. slot_ok=False 면 연속조회(2번째부터) 슬롯만 실패시킨다.
            slot_calls["n"] += 1
            return True if slot_calls["n"] == 1 else slot_ok

        api._acquire_api_slot = _slot
        api._slot_calls = slot_calls  # 테스트에서 호출 사이에 리셋
        patcher = mock.patch.object(
            kiwoom_market_data.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(payload),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return api

    async def test_exhausted_after_complete_paging_then_cache_hit(self):
        api = self._api(cont_yn="N")  # 연속조회 없음 = 키움이 줄 수 있는 전부(400)
        first = await api.get_stock_chart_data("005930", "1D", max_bars=650, allow_off_hours=True)
        self.assertEqual(len(first), 400)
        self.assertEqual(_FakeSession.posts, 1)
        self.assertIn("005930:1D", api._chart_exhausted)

        again = await api.get_stock_chart_data("005930", "1D", max_bars=650, allow_off_hours=True)
        self.assertEqual(len(again), 400)
        self.assertEqual(_FakeSession.posts, 1, "소진 표시된 캐시는 재호출하지 않아야 함")

    async def test_interrupted_paging_is_not_marked_exhausted(self):
        api = self._api(cont_yn="Y", slot_ok=False)  # 연속조회 슬롯 확보 실패 = 일시적 중단
        first = await api.get_stock_chart_data("005930", "1D", max_bars=650, allow_off_hours=True)
        self.assertEqual(len(first), 400)
        self.assertNotIn("005930:1D", api._chart_exhausted)
        self.assertEqual(_FakeSession.posts, 1)

        api._slot_calls["n"] = 0  # 다음 호출도 첫 슬롯은 성공
        await api.get_stock_chart_data("005930", "1D", max_bars=650, allow_off_hours=True)
        self.assertEqual(_FakeSession.posts, 2, "중단된 조회는 다음 호출에서 재시도해야 함")

    async def test_fully_filled_clears_marker(self):
        api = self._api(cont_yn="N", rows_per_page=700)
        api._chart_exhausted["005930:1D"] = (650, 0.0)
        out = await api.get_stock_chart_data("005930", "1D", max_bars=650, allow_off_hours=True)
        self.assertEqual(len(out), 650)
        self.assertNotIn("005930:1D", api._chart_exhausted)


class FetchMorePagesTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_flag(self):
        api = _bare_api()
        api._parse_kiwoom_chart_data = lambda data, code: []

        async def _slot_ok(*a, **k):
            return True

        async def _slot_fail(*a, **k):
            return False

        resp = mock.Mock(headers={})
        first_page = _rows(10)

        # 더 줄 페이지 없음 → 정상 종료
        api._acquire_api_slot = _slot_ok
        rows, complete = await api._fetch_more_chart_pages(
            None, "u", {}, {}, "005930", resp, {"cont_yn": "N"}, first_page, 100,
        )
        self.assertTrue(complete)
        self.assertEqual(len(rows), 10)

        # 슬롯 실패 → 일시 중단
        api._acquire_api_slot = _slot_fail
        rows, complete = await api._fetch_more_chart_pages(
            None, "u", {}, {}, "005930", resp, {"cont_yn": "Y", "next_key": "k"}, first_page, 100,
        )
        self.assertFalse(complete)
        self.assertEqual(len(rows), 10)


class LoadOhlcvRowsTests(unittest.IsolatedAsyncioTestCase):
    """core.main._load_ohlcv_rows — 마트 즉시 응답 / 백그라운드 보강 / 폴백."""

    @classmethod
    def setUpClass(cls):
        import core.main as m

        cls.m = m

    def setUp(self):
        m = self.m
        m._mart_upgrade_last.clear()
        m._mart_upgrade_inflight.clear()
        self.kiwoom_calls = []
        self.upserts = []
        self.mart = {}

        def _get_bars(code, interval="1D"):
            return self.mart.get((code, interval))

        def _upsert(code, interval, rows, **kw):
            self.upserts.append((code, interval, len(rows)))

        self.kiwoom_len = 650

        class _K:
            async def get_stock_chart_data(inner, code, interval, max_bars=None, **kw):
                self.kiwoom_calls.append((code, interval, max_bars))
                await asyncio.sleep(0)
                return _rows(self.kiwoom_len)

        for target, name, fn in (
            ("utils.chart_ohlcv_mart_store.get_bars", None, _get_bars),
            ("utils.chart_ohlcv_mart_store.upsert_series", None, _upsert),
        ):
            p = mock.patch(target, fn)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(m, "kiwoom_api", _K())
        p.start()
        self.addCleanup(p.stop)

    async def _drain(self):
        tasks = list(self.m._mart_upgrade_tasks)
        if tasks:
            await asyncio.gather(*tasks)

    async def test_full_mart_never_calls_kiwoom(self):
        self.mart[("005930", "1D")] = _rows(700)
        rows, src = await self.m._load_ohlcv_rows("005930", "1D", min_bars=623, accept_bars=504)
        self.assertEqual((len(rows), src), (700, "mart"))
        self.assertEqual(self.kiwoom_calls, [])
        self.assertEqual(self.m._mart_upgrade_tasks, set())

    async def test_short_mart_serves_immediately_and_upgrades_in_background(self):
        self.mart[("005930", "1W")] = _rows(260)
        rows, src = await self.m._load_ohlcv_rows("005930", "1W", min_bars=379, accept_bars=260)
        self.assertEqual((len(rows), src), (260, "mart"))
        self.assertEqual(self.kiwoom_calls, [], "응답 시점에는 키움을 기다리지 않아야 함")
        await self._drain()
        self.assertEqual(self.kiwoom_calls, [("005930", "1W", 379)])
        self.assertEqual(self.upserts, [("005930", "1W", 650)])

    async def test_background_upgrade_respects_cooldown_and_inflight(self):
        self.mart[("005930", "1W")] = _rows(260)
        for _ in range(3):
            await self.m._load_ohlcv_rows("005930", "1W", min_bars=379, accept_bars=260)
        await self._drain()
        self.assertEqual(len(self.kiwoom_calls), 1, "같은 종목×간격은 쿨다운 동안 1회만 보강")
        await self.m._load_ohlcv_rows("005930", "1W", min_bars=379, accept_bars=260)
        await self._drain()
        self.assertEqual(len(self.kiwoom_calls), 1)

    async def test_upgrade_that_does_not_grow_mart_skips_write(self):
        self.mart[("005930", "1MO")] = _rows(36)
        self.kiwoom_len = 36  # 키움도 36개가 전부
        await self.m._load_ohlcv_rows("005930", "1MO", min_bars=143, accept_bars=24)
        await self._drain()
        self.assertEqual(len(self.kiwoom_calls), 1)
        self.assertEqual(self.upserts, [])

    async def test_mart_below_accept_falls_back_to_kiwoom_blocking(self):
        self.mart[("005930", "1D")] = _rows(100)
        rows, src = await self.m._load_ohlcv_rows("005930", "1D", min_bars=308, accept_bars=189)
        self.assertEqual((len(rows), src), (650, "kiwoom"))
        self.assertEqual(self.kiwoom_calls, [("005930", "1D", 308)])
        self.assertEqual(self.upserts, [("005930", "1D", 650)])

    async def test_no_mart_row_falls_back_to_kiwoom(self):
        rows, src = await self.m._load_ohlcv_rows("005930", "1D", min_bars=308, accept_bars=189)
        self.assertEqual(src, "kiwoom")
        self.assertEqual(len(self.kiwoom_calls), 1)

    async def test_refresh_bypasses_mart(self):
        self.mart[("005930", "1D")] = _rows(700)
        rows, src = await self.m._load_ohlcv_rows(
            "005930", "1D", prefer_mart=False, min_bars=623, accept_bars=504,
        )
        self.assertEqual(src, "kiwoom")
        self.assertEqual(len(self.kiwoom_calls), 1)


if __name__ == "__main__":
    unittest.main()
