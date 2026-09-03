"""거래대금순 종목 → 테마맵 합산 → 테마별 대금 랭킹 (종가배팅과 동일 파이프라인).

장중 15분 캐시로 키움 호출을 줄인다. 스크리너 필터는 끄고 넓은 유니버스로
상위 테마를 잡는다 (종가배팅 후보 필터와 분리).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence

from utils.datetime_kst import kst_today, utc_now_naive
from utils.jongga_engine import UNMAPPED_THEME, _trade_amount

logger = logging.getLogger(__name__)

DEFAULT_STOCK_LIMIT = 300
DEFAULT_TOP_N = 40
DEFAULT_CACHE_SEC = 900  # 15분
DEFAULT_TOP_STOCKS_PER_THEME = 5
# 출력 의미 변경 시 bump — 구캐시(미분류·ETF 포함) 자동 무효화
CACHE_SCHEMA = 4

_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs",
    "_theme_trade_flow.json",
)


def _norm_code(code: Any) -> str:
    s = str(code or "").replace("A", "").strip()
    if not s:
        return ""
    return s.zfill(6) if s.isdigit() else s


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def filter_out_etf_items(items: Sequence[Dict[str, Any]], kiwoom_api: Any = None) -> List[Dict[str, Any]]:
    """ETF/ETN/레버리지·인버스·곱버스·SPAC·우선주 등 비개별주식 제외."""
    from api.kiwoom_api import KiwoomAPI

    checker = getattr(kiwoom_api, "_is_screener_stock", None) or KiwoomAPI._is_screener_stock
    out: List[Dict[str, Any]] = []
    for it in items or []:
        name = it.get("stock_name") or ""
        pt = it.get("product_type")
        try:
            keep = bool(checker(name, pt))
        except TypeError:
            keep = bool(checker(name))
        if keep:
            out.append(it)
    return out


def load_theme_trade_flow_cache(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or _STATE_PATH
    try:
        if not os.path.isfile(p):
            return {}
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("theme_trade_flow cache load 실패: %s", e)
        return {}


def save_theme_trade_flow_cache(payload: Dict[str, Any], path: Optional[str] = None) -> None:
    p = path or _STATE_PATH
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception as e:
        logger.warning("theme_trade_flow cache save 실패: %s", e)


def cache_is_fresh(
    payload: Dict[str, Any],
    *,
    cache_sec: int = DEFAULT_CACHE_SEC,
    biz_date: Optional[str] = None,
) -> bool:
    if not payload or not (payload.get("items") or payload.get("success")):
        return False
    try:
        if int(payload.get("schema") or 0) != CACHE_SCHEMA:
            return False
    except (TypeError, ValueError):
        return False
    today = biz_date or kst_today().isoformat()
    if str(payload.get("biz_date") or "") != today:
        return False
    built = payload.get("built_at_epoch")
    if built is None:
        return False
    try:
        age = time.time() - float(built)
    except (TypeError, ValueError):
        return False
    return age <= max(0, int(cache_sec))


def rank_themes_by_trade_amount(
    items: Sequence[Dict[str, Any]],
    theme_map: Dict[str, Dict[str, Any]],
    *,
    top_n: int = DEFAULT_TOP_N,
    top_stocks: int = DEFAULT_TOP_STOCKS_PER_THEME,
    include_unmapped: bool = False,
    sort_by: str = "trade_amount",
) -> List[Dict[str, Any]]:
    """종목 리스트 + 테마맵 → 테마별 대금 내림차순 상위 top_n.

    한 종목이 여러 테마에 속하면 거래대금을 각 테마에 전액 중복 반영한다.
    종가배팅의 대표테마 집계와 분리된, 테마지도 전용 집계다.
    기본은 미분류 제외 (테마맵에 매핑된 테마만).
    """
    totals: Dict[str, float] = {}
    enriched: List[Dict[str, Any]] = []
    for raw in items or []:
        code = _norm_code(raw.get("stock_code"))
        if not code:
            continue
        info = theme_map.get(code) or {}
        themes: List[str] = []
        seen = set()
        for value in info.get("themes") or []:
            theme = str(value or "").strip()
            key = theme.casefold()
            if not theme or key in seen:
                continue
            seen.add(key)
            themes.append(theme)
        if not themes:
            themes = [UNMAPPED_THEME]

        amt = _trade_amount(raw)
        for theme in themes:
            totals[theme] = totals.get(theme, 0.0) + amt
            row = dict(raw)
            row["stock_code"] = code
            row["theme"] = theme
            row["themes"] = themes
            row["trade_amount"] = amt
            row["theme_membership_count"] = len(themes)
            row["change_rate"] = _f(raw.get("change_rate"), 0.0)
            try:
                row["current_price"] = int(raw.get("current_price") or 0)
            except (TypeError, ValueError):
                row["current_price"] = 0
            enriched.append(row)

    by_theme: Dict[str, List[Dict[str, Any]]] = {}
    for row in enriched:
        theme = str(row.get("theme") or UNMAPPED_THEME)
        by_theme.setdefault(theme, []).append(row)

    ranked: List[Dict[str, Any]] = []
    for theme, amt in totals.items():
        if not include_unmapped and theme == UNMAPPED_THEME:
            continue
        rows = by_theme.get(theme) or []
        rows_sorted = sorted(rows, key=lambda r: _trade_amount(r), reverse=True)
        chgs = [_f(r.get("change_rate"), 0.0) for r in rows]
        avg_chg = sum(chgs) / len(chgs) if chgs else 0.0
        max_chg = max(chgs) if chgs else 0.0
        amt_f = float(amt or 0.0)
        ranked.append(
            {
                "theme": theme,
                "trade_amount": round(amt_f, 2),
                "trade_amount_eok": round(amt_f / 100.0, 2),
                "stock_count": len(rows),
                "avg_change_rate": round(avg_chg, 2),
                "max_change_rate": round(max_chg, 2),
                "top_stocks": [
                    {
                        "stock_code": _norm_code(r.get("stock_code")),
                        "stock_name": r.get("stock_name") or "",
                        "trade_amount": round(_trade_amount(r), 2),
                        "trade_amount_eok": round(_trade_amount(r) / 100.0, 2),
                        "change_rate": round(_f(r.get("change_rate"), 0.0), 2),
                        "current_price": int(r.get("current_price") or 0),
                    }
                    for r in rows_sorted[: max(0, int(top_stocks))]
                ],
            }
        )

    sort_mode = "change_rate" if str(sort_by).strip().lower() == "change_rate" else "trade_amount"
    if sort_mode == "change_rate":
        ranked.sort(
            key=lambda x: (
                float(x["avg_change_rate"]),
                float(x["trade_amount"]),
                x["theme"] != UNMAPPED_THEME,
            ),
            reverse=True,
        )
    else:
        ranked.sort(
            key=lambda x: (float(x["trade_amount"]), x["theme"] != UNMAPPED_THEME),
            reverse=True,
        )
    n = max(1, int(top_n or DEFAULT_TOP_N))
    out = ranked[:n]
    for i, row in enumerate(out, start=1):
        row["rank"] = i
    return out


async def build_theme_trade_flow(
    kiwoom_api: Any,
    *,
    stock_limit: int = DEFAULT_STOCK_LIMIT,
    top_n: int = DEFAULT_TOP_N,
    top_stocks: int = DEFAULT_TOP_STOCKS_PER_THEME,
    include_unmapped: bool = False,
    sort_by: str = "trade_amount",
    get_db=None,
) -> Dict[str, Any]:
    """키움 거래대금순 + 테마맵 합산 스냅샷 생성."""
    from utils.theme_map_store import get_trade_flow_theme_map

    limit = max(1, min(int(stock_limit or DEFAULT_STOCK_LIMIT), 500))
    # ETF 제외 후 limit를 채우기 위해 여유분 조회 (mang_stk_incls=16 = ETF+ETN API 제외)
    fetch_limit = min(500, max(limit + 80, int(limit * 1.35)))
    res = await kiwoom_api.get_volume_rank(
        market="000",
        sort_tp="3",
        limit=fetch_limit,
        screener_filters=False,
        positive_change_only=False,
        mang_stk_incls="16",
    )
    if not res.get("success"):
        return {
            "success": False,
            "error": res.get("error") or "거래대금순 조회 실패",
            "items": [],
            "biz_date": kst_today().isoformat(),
            "built_at": utc_now_naive().isoformat(),
            "built_at_epoch": time.time(),
        }

    raw_items = list(res.get("items") or [])
    filtered = filter_out_etf_items(raw_items, kiwoom_api)
    excluded_etf = max(0, len(raw_items) - len(filtered))
    items = filtered[:limit]
    codes = [
        kiwoom_api.normalize_stock_code(it.get("stock_code", ""))
        for it in items
        if it.get("stock_code")
    ]
    theme_map: Dict[str, Dict[str, Any]] = {}
    if get_db is not None:
        for db in get_db():
            theme_map = get_trade_flow_theme_map(db, codes) or {}
            break
    else:
        from core.models import get_db as default_get_db

        for db in default_get_db():
            theme_map = get_trade_flow_theme_map(db, codes) or {}
            break

    ranked = rank_themes_by_trade_amount(
        items,
        theme_map,
        top_n=top_n,
        top_stocks=top_stocks,
        include_unmapped=include_unmapped,
        sort_by=sort_by,
    )
    mapped = sum(1 for c in codes if (theme_map.get(c) or {}).get("themes"))
    now = time.time()
    return {
        "success": True,
        "schema": CACHE_SCHEMA,
        "biz_date": kst_today().isoformat(),
        "built_at": utc_now_naive().isoformat(),
        "built_at_epoch": now,
        "stock_universe": len(items),
        "stock_mapped": mapped,
        "aggregation_mode": "all_themes_full_amount",
        "sort_by": "change_rate" if sort_by == "change_rate" else "trade_amount",
        "excluded_etf": excluded_etf,
        "theme_count": len(ranked),
        "top_n": int(top_n),
        "stock_limit": limit,
        "unit_hint": "trade_amount=백만원, trade_amount_eok=억원",
        "items": ranked,
    }


async def get_theme_trade_flow(
    kiwoom_api: Any,
    *,
    rebuild: bool = False,
    stock_limit: int = DEFAULT_STOCK_LIMIT,
    top_n: int = DEFAULT_TOP_N,
    cache_sec: int = DEFAULT_CACHE_SEC,
    sort_by: str = "trade_amount",
    get_db=None,
) -> Dict[str, Any]:
    """캐시 우선 조회. 정렬 변경은 캐시를 재정렬하고, rebuild 또는 stale 시 재집계."""
    cached = load_theme_trade_flow_cache()
    if (
        not rebuild
        and cache_is_fresh(cached, cache_sec=cache_sec)
        and int(cached.get("top_n") or 0) >= int(top_n)
        and int(cached.get("stock_limit") or 0) >= int(stock_limit)
    ):
        out = dict(cached)
        out["cached"] = True
        items = list(out.get("items") or [])
        # 구캐시 방어: 미분류·ETF 테마가 남아 있으면 숨김
        items = [r for r in items if str(r.get("theme") or "") != UNMAPPED_THEME]
        sort_mode = "change_rate" if str(sort_by).strip().lower() == "change_rate" else "trade_amount"
        if sort_mode == "change_rate":
            items.sort(
                key=lambda x: (
                    _f(x.get("avg_change_rate"), 0.0),
                    _f(x.get("trade_amount"), 0.0),
                ),
                reverse=True,
            )
        else:
            items.sort(key=lambda x: _f(x.get("trade_amount"), 0.0), reverse=True)
        items = items[: max(1, int(top_n))]
        for i, row in enumerate(items, start=1):
            row["rank"] = i
        out["items"] = items
        out["theme_count"] = len(items)
        out["sort_by"] = sort_mode
        return out

    payload = await build_theme_trade_flow(
        kiwoom_api,
        stock_limit=stock_limit,
        top_n=top_n,
        sort_by=sort_by,
        get_db=get_db,
    )
    payload["cached"] = False
    if payload.get("success"):
        save_theme_trade_flow_cache(payload)
    return payload
