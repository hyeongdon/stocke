"""키움 REST 테마 크롤러 (ka90001 / ka90002)."""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


async def crawl_kiwoom_theme_list(
    api,
    *,
    limit: int = 0,
    date_tp: str = "1",
    flu_pl_amt_tp: str = "3",
    stex_tp: str = "3",
) -> Dict:
    """테마 그룹 목록 수집. limit<=0 이면 전체."""
    result = await api.get_theme_group_list(
        qry_tp="0",
        date_tp=date_tp,
        flu_pl_amt_tp=flu_pl_amt_tp,
        stex_tp=stex_tp,
    )
    if not result.get("success"):
        return {
            "ok": False,
            "error": result.get("error") or "ka90001 실패",
            "themes": [],
            "api_calls": int(result.get("pages") or 0),
        }

    themes = list(result.get("items") or [])
    themes.sort(key=lambda t: float(t.get("change_rate") or 0), reverse=True)
    if limit and limit > 0:
        themes = themes[: int(limit)]

    return {
        "ok": True,
        "themes": themes,
        "api_calls": int(result.get("pages") or 1),
    }


async def crawl_kiwoom_theme_stocks(
    api,
    theme_code: str,
    *,
    date_tp: str = "1",
    stex_tp: str = "3",
) -> Dict:
    """단일 테마 구성종목 수집."""
    result = await api.get_theme_component_stocks(
        theme_code,
        date_tp=date_tp,
        stex_tp=stex_tp,
    )
    if not result.get("success"):
        return {
            "ok": False,
            "error": result.get("error") or "ka90002 실패",
            "stocks": [],
            "api_calls": int(result.get("pages") or 0),
            "theme_code": theme_code,
        }
    stocks = [
        {
            "stock_code": s.get("stock_code"),
            "stock_name": s.get("stock_name") or "",
            "change_rate": s.get("change_rate"),
            "period_return": s.get("period_return"),
        }
        for s in (result.get("items") or [])
        if s.get("stock_code")
    ]
    return {
        "ok": True,
        "stocks": stocks,
        "api_calls": int(result.get("pages") or 1),
        "theme_code": theme_code,
        "change_rate": result.get("change_rate"),
        "period_return": result.get("period_return"),
    }


async def crawl_kiwoom_theme_snapshot(
    api,
    *,
    limit: int = 0,
    date_tp: str = "1",
    flu_pl_amt_tp: str = "3",
    stex_tp: str = "3",
) -> Dict:
    """테마 목록 + 구성종목 일괄 수집 (장후 배치용).

    API 호출 대략: ka90001 페이지수 + 테마수(ka90002).
    기본 rate limit(3초/건, 분당 18) 기준 테마 200개 ≈ 10~15분.
    """
    listed = await crawl_kiwoom_theme_list(
        api,
        limit=limit,
        date_tp=date_tp,
        flu_pl_amt_tp=flu_pl_amt_tp,
        stex_tp=stex_tp,
    )
    if not listed.get("ok"):
        return listed

    themes = listed.get("themes") or []
    api_calls = int(listed.get("api_calls") or 0)
    out_themes: List[Dict] = []
    errors: List[str] = []

    for idx, theme in enumerate(themes):
        code = str(theme.get("theme_code") or "").strip()
        name = str(theme.get("theme_name") or "").strip()
        if not code:
            continue
        detail = await crawl_kiwoom_theme_stocks(
            api,
            code,
            date_tp=date_tp,
            stex_tp=stex_tp,
        )
        api_calls += int(detail.get("api_calls") or 0)
        if not detail.get("ok"):
            err = f"{code}:{name}:{detail.get('error')}"
            errors.append(err)
            logger.warning("[KIWOOM_THEME] stocks fail %s", err)
            continue
        out_themes.append(
            {
                **theme,
                "stocks": detail.get("stocks") or [],
            }
        )
        if (idx + 1) % 25 == 0:
            logger.info(
                "[KIWOOM_THEME] progress %s/%s api_calls=%s",
                idx + 1,
                len(themes),
                api_calls,
            )

    return {
        "ok": True,
        "themes": out_themes,
        "theme_count": len(out_themes),
        "edge_count": sum(len(t.get("stocks") or []) for t in out_themes),
        "api_calls": api_calls,
        "errors": errors[:20],
        "error_count": len(errors),
    }


def crawl_kiwoom_theme_snapshot_sync(
    *,
    limit: int = 0,
    date_tp: str = "1",
    flu_pl_amt_tp: str = "3",
    stex_tp: str = "3",
    api=None,
) -> Dict:
    """동기 래퍼 — 배치 스크립트용."""
    from api.kiwoom_api import KiwoomAPI

    own_api = api is None
    client = api or KiwoomAPI()
    if own_api and not client.authenticate():
        return {"ok": False, "error": "키움 인증 실패", "themes": [], "api_calls": 0}

    async def _run():
        return await crawl_kiwoom_theme_snapshot(
            client,
            limit=limit,
            date_tp=date_tp,
            flu_pl_amt_tp=flu_pl_amt_tp,
            stex_tp=stex_tp,
        )

    try:
        return asyncio.run(_run())
    except RuntimeError:
        # 이미 이벤트 루프가 있으면 새 루프에서 실행
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()
