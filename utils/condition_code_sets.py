"""키움 조건식(CNSRREQ) 결과 종목코드를 파일 캐시로 보관 — 추세이탈·주봉RSI·일봉RSI·주봉윌리암·일봉윌리암·밥그릇·2060돌파.

기술적 마트 배치(종목별 일봉 조회)는 API 제한으로 전 종목 적재가 어려워,
HTS에 등록한 조건식 결과를 필터 목록으로 쓴다.
장외에는 조건검색이 실패할 수 있어 마지막 성공 결과를 그대로 사용한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Dict, List, Optional, Set

from utils.datetime_kst import now_kst
from utils.market_hours import is_krx_session

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGS_DIR = _PROJECT_ROOT / "logs"

FRESH_SEC = 30 * 60
RETRY_BACKOFF_SEC = 10 * 60
_SESSION_CLOSE = dt_time(15, 30)

# 키움 조건검색 세션이 겹치면 응답이 섞이므로 모든 조건식 조회를 직렬화
_refresh_lock: Optional[asyncio.Lock] = None


def _lock() -> asyncio.Lock:
    global _refresh_lock
    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()
    return _refresh_lock


def _env_names(env_key: str, default: str) -> List[str]:
    return [s.strip() for s in os.getenv(env_key, default).split(",") if s.strip()]


def _parse_ts(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


class ConditionCodeSet:
    def __init__(self, key: str, label: str, names: List[str], cache_file: str):
        self.key = key
        self.label = label
        self.names = names
        self.condition_name = ",".join(names)
        self.cache_path = _LOGS_DIR / cache_file

    def load_cache(self) -> Dict:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_cache(self, data: Dict) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.cache_path)

    def _is_fresh(self, cache: Dict, now: datetime) -> bool:
        """장중 FRESH_SEC 이내, 장외에는 직전 장 마감 이후 조회분이면 갱신 불필요."""
        fetched = _parse_ts(cache.get("fetched_at"))
        if fetched is None or cache.get("condition_name") != self.condition_name:
            return False
        if is_krx_session(now):
            return (now - fetched).total_seconds() < FRESH_SEC
        if now.time() >= _SESSION_CLOSE:
            return fetched.date() == now.date() and fetched.time() >= _SESSION_CLOSE
        return (now - fetched).total_seconds() < 12 * 3600

    @staticmethod
    def _recently_failed(cache: Dict, now: datetime) -> bool:
        failed = _parse_ts(cache.get("last_error_at"))
        return failed is not None and (now - failed).total_seconds() < RETRY_BACKOFF_SEC

    async def _resolve_condition_ids(self, kiwoom_api) -> Dict[str, str]:
        from utils.screener_targets import _resolve_condition_api_id

        ids: Dict[str, str] = {}
        for name in self.names:
            api_id, _ = _resolve_condition_api_id(name)
            if api_id:
                ids[name] = api_id
        if len(ids) < len(self.names):
            for row in await kiwoom_api.get_condition_list_websocket() or []:
                name = str(row.get("condition_name", "")).strip()
                if name in self.names and name not in ids:
                    ids[name] = str(row.get("condition_id", ""))
        return ids

    async def refresh(self, kiwoom_api=None) -> Dict:
        """조건식 재조회 → 캐시 저장. 실패 시 기존 캐시에 오류만 기록하고 예외를 올린다."""
        if kiwoom_api is None:
            from api.kiwoom_api import kiwoom_api
        cache = self.load_cache()
        try:
            cond_ids = await self._resolve_condition_ids(kiwoom_api)
            missing = [n for n in self.names if not cond_ids.get(n)]
            if missing:
                raise RuntimeError(f"키움 조건식 {', '.join(repr(n) for n in missing)} 없음 — HTS에서 등록 필요")
            stocks = []
            counts: Dict[str, int] = {}
            # 한 웹소켓에서 연속 CNSRREQ 시 두 번째 조건식이 0건으로 응답 → 조건식마다 세션 분리
            for name in self.names:
                async with kiwoom_api.condition_search_session() as session:
                    found = await session.search_all(cond_ids[name], name)
                counts[name] = len(found)
                stocks.extend(found)
        except Exception as e:
            cache["last_error"] = str(e)
            cache["last_error_at"] = now_kst().isoformat(timespec="seconds")
            self._save_cache(cache)
            raise
        codes = sorted({str(s.get("stock_code") or "").strip().zfill(6) for s in stocks if s.get("stock_code")})
        data = {
            "condition_name": self.condition_name,
            "condition_ids": cond_ids,
            "condition_counts": counts,
            "fetched_at": now_kst().isoformat(timespec="seconds"),
            "count": len(codes),
            "codes": codes,
        }
        self._save_cache(data)
        logger.info(f"{self.label} 조건식 갱신: {counts} → 합계 {len(codes)}종목")
        return data

    def _result(self, cache: Dict, error: Optional[str]) -> Dict:
        usable = cache.get("condition_name") == self.condition_name
        codes: Set[str] = set(cache.get("codes") or []) if usable else set()
        return {
            "key": self.key,
            "label": self.label,
            "condition_name": self.condition_name,
            "codes": codes,
            "count": len(codes),
            "fetched_at": cache.get("fetched_at") if usable else None,
            "error": error,
        }

    def cached_codes(self) -> Dict:
        """조회 없이 마지막 캐시만 — 필터 미사용 시 배지 표시용."""
        return self._result(self.load_cache(), None)

    async def get_codes(self, *, force: bool = False) -> Dict:
        """{codes:set, fetched_at, count, condition_name, error} — 캐시가 오래됐으면 갱신 시도, 실패 시 마지막 결과."""
        now = now_kst()
        cache = self.load_cache()
        error: Optional[str] = None
        if force or (not self._is_fresh(cache, now) and not self._recently_failed(cache, now)):
            async with _lock():
                cache = self.load_cache()
                if force or not self._is_fresh(cache, now):
                    try:
                        cache = await self.refresh()
                    except Exception as e:
                        logger.warning(f"{self.label} 조건식 조회 실패 — 캐시 사용: {e}")
                        error = str(e)
                        cache = self.load_cache()
        elif self._recently_failed(cache, now) and not self._is_fresh(cache, now):
            error = cache.get("last_error")
        return self._result(cache, error)


TREND_BREAK = ConditionCodeSet(
    "trend_break",
    "추세이탈",
    _env_names("TREND_BREAK_CONDITION_NAMES", "코스피추세이탈,코스닥추세이탈"),
    "_trend_break_condition.json",
)
WEEKLY_RSI = ConditionCodeSet(
    "weekly_rsi",
    "주봉RSI",
    _env_names("WEEKLY_RSI_CONDITION_NAMES", "주봉RSI"),
    "_weekly_rsi_condition.json",
)
DAILY_RSI = ConditionCodeSet(
    "daily_rsi",
    "일봉RSI",
    _env_names("DAILY_RSI_CONDITION_NAMES", "일봉RSI"),
    "_daily_rsi_condition.json",
)
WEEKLY_WILLIAMS = ConditionCodeSet(
    "weekly_williams",
    "주봉윌리암",
    _env_names("WEEKLY_WILLIAMS_CONDITION_NAMES", "주봉윌리암"),
    "_weekly_williams_condition.json",
)
DAILY_WILLIAMS = ConditionCodeSet(
    "daily_williams",
    "일봉윌리암",
    _env_names("DAILY_WILLIAMS_CONDITION_NAMES", "일봉윌리암"),
    "_daily_williams_condition.json",
)
BOWL = ConditionCodeSet(
    "bowl",
    "밥그릇",
    _env_names("BOWL_CONDITION_NAMES", "밥그릇"),
    "_bowl_condition.json",
)
MA2060 = ConditionCodeSet(
    "ma2060",
    "2060돌파",
    _env_names("MA2060_CONDITION_NAMES", "2060돌파"),
    "_ma2060_condition.json",
)
