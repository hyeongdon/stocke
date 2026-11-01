from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "logger",
    "SCREENER_VOLUME_RANK_FILTERS",
    "SANGTTA_CHANGE_RATE_RANK_FILTERS",
    "SANGTTA_UNIVERSE_MIN_CHANGE_RATE",
    "_VERIFY_INTRADAY_BARS_PER_DAY",
    "_expected_intraday_bars_for_day",
    "_account_balance_cache",
    "_ACCOUNT_BALANCE_FRESH_SEC",
    "_ACCOUNT_BALANCE_STALE_SEC",
    "_balance_fetch_lock",
    "_condition_ws_lock",
    "_CONDITION_SEARCH_MAX_ATTEMPTS",
    "_condition_list_cache",
    "_CONDITION_LIST_TTL_SEC",
    "_condition_search_cache",
    "_CONDITION_SEARCH_TTL_SEC",
    "_CONDITION_SEARCH_STALE_SEC",
    "_get_balance_fetch_lock",
    "_get_condition_ws_lock",
    "_copy_condition_rows",
    "_get_cached_condition_list",
    "_set_cached_condition_list",
    "_condition_search_cache_key",
    "_get_cached_condition_search",
    "_set_cached_condition_search",
    "_stale_account_balance",
    "_parse_kiwoom_int",
    "_parse_kiwoom_rate",
    "_parse_kiwoom_float",
    "_normalize_stock_code",
    "invalidate_account_balance_cache",
]

_VERIFY_INTRADAY_BARS_PER_DAY = {
    "1": 400,
    "3": 150,
    "5": 90,
    "10": 50,
    "15": 35,
    "30": 20,
    "60": 12,
}


def _expected_intraday_bars_for_day(tic_scope: str) -> int:
    return _VERIFY_INTRADAY_BARS_PER_DAY.get(str(tic_scope), 40)

# ka10030 당일거래량상위 — 자동매매 스크리너 기본 필터 (공식: openapi.kiwoom.com ka10030)
# mang_stk_incls: 1개 값만 지정 가능 — 0관리포함, 1관리제외, 3우선주제외, 4관리+우선주제외,
#   11정리매매제외, 14 ETF제외, 15 SPAC제외, 16 ETF+ETN제외 (조합 불가)
# → 16으로 API단 ETF/ETN 제거 + 후처리(SPAC·우선주·정리매매·API 누락 파생상품)
SCREENER_VOLUME_RANK_FILTERS = {
    "mang_stk_incls": "16",  # ETF+ETN 제외 (공식 ka10030)
    "crd_tp": "0",           # 0:전체
    "trde_qty_tp": "200",    # 200:20만주 이상
    "pric_tp": "0",          # 0:전체
    "trde_prica_tp": "0",    # 0:전체 — screener 시 대금하한≥10억이면 get_volume_rank가 100으로 상향
    "mrkt_open_tp": "0",     # 0:전체 (1장중 2장전 3장후)
    "stex_tp": "1",          # 1:KRX (3:통합)
}

# ka10027 전일대비등락률상위 — 상따 유니버스 기본 필터 (공식: openapi.kiwoom.com ka10027)
# stk_cnd: 0전체, 1관리종목제외, 3우선주제외, 4우선주+관리주제외, …
# pric_cnd: 8=1천원이상 / trde_prica_cnd: 100=10억원이상
# ETF 제외 옵션은 없어 후처리(_is_etf_family_item / _is_screener_stock)로 제거
SANGTTA_CHANGE_RATE_RANK_FILTERS = {
    "sort_tp": "1",          # 1:상승률
    "trde_qty_cnd": "0",     # 0:전체
    "stk_cnd": "1",          # 1:관리종목제외
    "crd_cnd": "0",          # 0:전체
    "updown_incls": "1",     # 1:상하한 포함 (게이트에서 상한가 진입 금지)
    "pric_cnd": "8",         # 8:1천원이상
    "trde_prica_cnd": "100", # 100:10억원이상
    "stex_tp": "1",          # 1:KRX
}
SANGTTA_UNIVERSE_MIN_CHANGE_RATE = 13.0  # 유니버스 등락률 하한(%) — 게이트 밴드와 별개

# 계좌 잔고 — KiwoomAPI 인스턴스 간 공유 캐시·동시 요청 합치기
_account_balance_cache: dict = {"at": 0.0, "data": None}
_ACCOUNT_BALANCE_FRESH_SEC = 15
_ACCOUNT_BALANCE_STALE_SEC = 300
_balance_fetch_lock: Optional[asyncio.Lock] = None


def invalidate_account_balance_cache() -> None:
    """매수/매도 주문 직후 — 15초 캐시가 예전 수량으로 포지션을 덮어쓰지 않게 무효화."""
    _account_balance_cache.clear()
    _account_balance_cache.update({"at": 0.0, "data": None})
# 조건식 WS(목록/검색) — 동시 연결 시 키움이 Bye/타임아웃으로 끊는 경우 방지
_condition_ws_lock: Optional[asyncio.Lock] = None
_CONDITION_SEARCH_MAX_ATTEMPTS = 2
_condition_list_cache: dict = {"at": 0.0, "data": None}
_CONDITION_LIST_TTL_SEC = 60.0
_condition_search_cache: Dict[str, dict] = {}
_CONDITION_SEARCH_TTL_SEC = 25.0
_CONDITION_SEARCH_STALE_SEC = 180.0


def _get_balance_fetch_lock() -> asyncio.Lock:
    global _balance_fetch_lock
    if _balance_fetch_lock is None:
        _balance_fetch_lock = asyncio.Lock()
    return _balance_fetch_lock


def _get_condition_ws_lock() -> asyncio.Lock:
    global _condition_ws_lock
    if _condition_ws_lock is None:
        _condition_ws_lock = asyncio.Lock()
    return _condition_ws_lock


def _copy_condition_rows(rows: Optional[List[Dict]]) -> List[Dict]:
    return [dict(r) for r in (rows or [])]


def _get_cached_condition_list() -> Optional[List[Dict]]:
    cached = _condition_list_cache.get("data")
    at = float(_condition_list_cache.get("at") or 0.0)
    if cached is None or time.monotonic() - at > _CONDITION_LIST_TTL_SEC:
        return None
    return _copy_condition_rows(cached)


def _set_cached_condition_list(rows: List[Dict]) -> None:
    _condition_list_cache["at"] = time.monotonic()
    _condition_list_cache["data"] = _copy_condition_rows(rows)


def _condition_search_cache_key(condition_id: str, condition_name: str) -> str:
    return f"{condition_id}|{condition_name}"


def _get_cached_condition_search(
    condition_id: str,
    condition_name: str,
    *,
    allow_stale: bool = False,
) -> Optional[List[Dict]]:
    key = _condition_search_cache_key(condition_id, condition_name)
    row = _condition_search_cache.get(key)
    if not row:
        return None
    age = time.monotonic() - float(row.get("at") or 0.0)
    ttl = _CONDITION_SEARCH_STALE_SEC if allow_stale else _CONDITION_SEARCH_TTL_SEC
    if age > ttl:
        return None
    return _copy_condition_rows(row.get("stocks"))


def _set_cached_condition_search(
    condition_id: str,
    condition_name: str,
    stocks: List[Dict],
) -> None:
    key = _condition_search_cache_key(condition_id, condition_name)
    _condition_search_cache[key] = {
        "at": time.monotonic(),
        "stocks": _copy_condition_rows(stocks),
    }


def _stale_account_balance(reason: str) -> Optional[Dict]:
    """최근 성공 조회 데이터가 있으면 stale로 반환."""
    cached = _account_balance_cache.get("data")
    cache_at = _account_balance_cache.get("at", 0.0)
    if not cached or time.monotonic() - cache_at > _ACCOUNT_BALANCE_STALE_SEC:
        return None
    out = dict(cached)
    out["_cached"] = True
    out["_stale"] = True
    logger.debug(f"계좌 잔고 캐시 사용 ({reason})")
    return out


def _parse_kiwoom_int(raw) -> int:
    """키움 금액/수량 문자열 → 정수 (+000010000000 → 10000000)."""
    if raw is None:
        return 0
    s = str(raw).strip()
    if not s:
        return 0
    sign = -1 if s.startswith("-") else 1
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return 0
    try:
        return sign * int(digits.lstrip("0") or "0")
    except (TypeError, ValueError):
        return 0


def _parse_kiwoom_rate(raw) -> str:
    """키움 수익률 문자열 — 소수점 유지."""
    if raw is None:
        return "0.00"
    s = str(raw).strip().replace(",", "")
    if not s:
        return "0.00"
    try:
        return f"{float(s):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _parse_kiwoom_float(raw) -> float:
    """키움 숫자 문자열을 실수로 변환. 부호와 소수점은 유지. 실패 시 0.0."""
    if raw is None:
        return 0.0
    s = str(raw).strip()
    if not s:
        return 0.0
    sign = -1.0 if s[0] == '-' else 1.0
    cleaned = ''.join(ch for ch in s if ch.isdigit() or ch == '.')
    if not cleaned or cleaned == '.':
        return 0.0
    try:
        return sign * float(cleaned)
    except ValueError:
        return 0.0

def _normalize_stock_code(stock_code: str) -> str:
    """Normalize a Kiwoom stock code by removing prefixes and venue suffixes."""
    code = str(stock_code or "").strip().replace("A", "")
    if "_" in code:
        code = code.split("_", 1)[0]
    return code.strip()
