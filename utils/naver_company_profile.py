"""네이버 금융 — 종목 회사 개요·동종업종 (분석 화면용, 온디맨드)."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL_SEC = 6 * 3600


def _norm_code(stock_code: str) -> str:
    raw = str(stock_code or "").strip().replace("A", "")
    if raw.isdigit():
        return raw.zfill(6)
    return raw


def _get_json(url: str, *, timeout: float = 12.0) -> Optional[Dict[str, Any]]:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _summary_paragraphs(summary: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(summary, dict):
        return []
    out: List[str] = []
    for key in ("comment1", "comment2", "comment3"):
        text = str(summary.get(key) or "").strip()
        if text:
            out.append(text)
    return out


def fetch_company_profile(stock_code: str) -> Dict[str, Any]:
    """종목 회사 개요 — 네이버 모바일 API (재무 annual + integration)."""
    code = _norm_code(stock_code)
    if not code:
        return {"stock_code": "", "ok": False, "error": "NO_CODE"}

    now = time.monotonic()
    cached = _CACHE.get(code)
    if cached and now - cached[0] < _CACHE_TTL_SEC:
        return dict(cached[1])

    annual = _get_json(f"https://m.stock.naver.com/api/stock/{code}/finance/annual")
    integ = _get_json(f"https://m.stock.naver.com/api/stock/{code}/integration")
    basic = _get_json(f"https://m.stock.naver.com/api/stock/{code}/basic")

    overview = _summary_paragraphs((annual or {}).get("corporationSummary"))
    stock_name = (
        str((integ or {}).get("stockName") or "")
        or str((basic or {}).get("stockName") or "")
    ).strip()

    market = ""
    ex = (basic or {}).get("stockExchangeType") if isinstance(basic, dict) else None
    if isinstance(ex, dict):
        market = str(ex.get("nameKor") or ex.get("name") or "").strip()

    peers: List[str] = []
    for row in (integ or {}).get("industryCompareInfo") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("stockName") or "").strip()
        if name and name not in peers:
            peers.append(name)

    industry_code = str((integ or {}).get("industryCode") or "").strip()

    out: Dict[str, Any] = {
        "ok": bool(overview or peers or stock_name),
        "stock_code": code,
        "stock_name": stock_name,
        "market": market,
        "industry_code": industry_code or None,
        "overview": overview,
        "overview_text": "\n\n".join(overview),
        "industry_peers": peers,
        "source": "naver",
    }
    if not out["ok"]:
        out["error"] = "PROFILE_NOT_FOUND"

    _CACHE[code] = (now, out)
    return dict(out)
