"""60일선 추세이탈 종목 — 키움 조건식(CNSRREQ) 결과를 파일 캐시로 보관.

기술적 마트 배치(종목별 일봉 조회)는 API 제한으로 전 종목 적재가 어려워,
HTS에 등록한 조건식 결과를 제외 목록으로 쓴다.
장외에는 조건검색이 실패할 수 있어 마지막 성공 결과를 그대로 사용한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Dict, Optional, Set

from utils.datetime_kst import now_kst
from utils.market_hours import is_krx_session

logger = logging.getLogger(__name__)

CONDITION_NAME = os.getenv("TREND_BREAK_CONDITION_NAME", "60일선이탈").strip()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_PATH = _PROJECT_ROOT / "logs" / "_trend_break_condition.json"

FRESH_SEC = 30 * 60
RETRY_BACKOFF_SEC = 10 * 60
_SESSION_CLOSE = dt_time(15, 30)

_refresh_lock: Optional[asyncio.Lock] = None


def _lock() -> asyncio.Lock:
    global _refresh_lock
    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()
    return _refresh_lock


def load_cache() -> Dict:
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(data: Dict) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_CACHE_PATH)


def _parse_ts(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _is_fresh(cache: Dict, now: datetime) -> bool:
    """장중 FRESH_SEC 이내, 장외에는 직전 장 마감 이후 조회분이면 갱신 불필요."""
    fetched = _parse_ts(cache.get("fetched_at"))
    if fetched is None or cache.get("condition_name") != CONDITION_NAME:
        return False
    if is_krx_session(now):
        return (now - fetched).total_seconds() < FRESH_SEC
    if now.time() >= _SESSION_CLOSE:
        return fetched.date() == now.date() and fetched.time() >= _SESSION_CLOSE
    return (now - fetched).total_seconds() < 12 * 3600


def _recently_failed(cache: Dict, now: datetime) -> bool:
    failed = _parse_ts(cache.get("last_error_at"))
    return failed is not None and (now - failed).total_seconds() < RETRY_BACKOFF_SEC


async def _resolve_condition_id(kiwoom_api) -> Optional[str]:
    from utils.screener_targets import _resolve_condition_api_id

    api_id, _ = _resolve_condition_api_id(CONDITION_NAME)
    if api_id:
        return api_id
    for row in await kiwoom_api.get_condition_list_websocket() or []:
        if str(row.get("condition_name", "")).strip() == CONDITION_NAME:
            return str(row.get("condition_id", ""))
    return None


async def refresh(kiwoom_api=None) -> Dict:
    """조건식 재조회 → 캐시 저장. 실패 시 기존 캐시에 오류만 기록하고 예외를 올린다."""
    if kiwoom_api is None:
        from api.kiwoom_api import kiwoom_api
    cache = load_cache()
    try:
        cond_id = await _resolve_condition_id(kiwoom_api)
        if not cond_id:
            raise RuntimeError(f"키움 조건식 '{CONDITION_NAME}' 없음 — HTS에서 등록 필요")
        async with kiwoom_api.condition_search_session() as session:
            stocks = await session.search_all(cond_id, CONDITION_NAME)
    except Exception as e:
        cache["last_error"] = str(e)
        cache["last_error_at"] = now_kst().isoformat(timespec="seconds")
        _save_cache(cache)
        raise
    codes = sorted({str(s.get("stock_code") or "").strip().zfill(6) for s in stocks if s.get("stock_code")})
    data = {
        "condition_name": CONDITION_NAME,
        "condition_id": cond_id,
        "fetched_at": now_kst().isoformat(timespec="seconds"),
        "count": len(codes),
        "codes": codes,
    }
    _save_cache(data)
    logger.info(f"60일선 추세이탈 조건식 갱신: {CONDITION_NAME} {len(codes)}종목")
    return data


async def get_trend_break_codes(*, force: bool = False) -> Dict:
    """{codes:set, fetched_at, count, condition_name, error} — 캐시가 오래됐으면 갱신 시도, 실패 시 마지막 결과."""
    now = now_kst()
    cache = load_cache()
    error: Optional[str] = None
    if force or (not _is_fresh(cache, now) and not _recently_failed(cache, now)):
        async with _lock():
            cache = load_cache()
            if force or not _is_fresh(cache, now):
                try:
                    cache = await refresh()
                except Exception as e:
                    logger.warning(f"60일선 추세이탈 조건식 조회 실패 — 캐시 사용: {e}")
                    error = str(e)
                    cache = load_cache()
    elif _recently_failed(cache, now) and not _is_fresh(cache, now):
        error = cache.get("last_error")
    usable = cache.get("condition_name") == CONDITION_NAME
    codes: Set[str] = set(cache.get("codes") or []) if usable else set()
    return {
        "condition_name": CONDITION_NAME,
        "codes": codes,
        "count": len(codes),
        "fetched_at": cache.get("fetched_at") if usable else None,
        "error": error,
    }
