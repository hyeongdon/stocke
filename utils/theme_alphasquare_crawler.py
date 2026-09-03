"""알파스퀘어 내부 API 테마 크롤러 (`api.alphasquare.co.kr`).

공개 테마 목록·구성종목은 로그인 없이 조회 가능.
편입 사유는 `/theme/v3/themes-for-stock?stock_id=` (내부 id) — 기본 OFF.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional

import requests

from core.config import Config

logger = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _base_url() -> str:
    return (Config.ALPHASQUARE_BASE_URL or "https://api.alphasquare.co.kr").rstrip("/")


def _headers() -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": Config.ALPHASQUARE_USER_AGENT or _DEFAULT_UA,
        "Origin": "https://alphasquare.co.kr",
        "Referer": "https://alphasquare.co.kr/home/theme-factor",
    }


def _timeout() -> float:
    return float(Config.ALPHASQUARE_TIMEOUT_SEC or 20)


def _sleep_sec() -> float:
    return max(0.0, float(Config.ALPHASQUARE_SLEEP_SEC or 0.35))


def _get_json(path: str, *, params: Optional[dict] = None) -> Dict:
    url = f"{_base_url()}{path}"
    resp = requests.get(url, headers=_headers(), params=params or {}, timeout=_timeout())
    resp.raise_for_status()
    return resp.json()


def normalize_kr_stock_code(raw_code) -> Optional[str]:
    """숫자만 추출 후 6자리. 실패 시 None."""
    digits = re.sub(r"\D", "", str(raw_code or ""))
    if not digits:
        return None
    if len(digits) > 6:
        digits = digits[-6:]
    code = digits.zfill(6)
    if len(code) != 6 or not code.isdigit():
        return None
    return code


def is_kr_stock_row(row: dict) -> bool:
    """국내 종목만 통과 (country_code / market 힌트)."""
    country = str(row.get("country_code") or "").strip().upper()
    if country and country != "KR":
        return False
    market = str(row.get("market") or "").strip().lower()
    if market and market not in ("kospi", "kosdaq", "konex", ""):
        # 해외/기타 시장 명시 시 제외
        if country != "KR":
            return False
    code = normalize_kr_stock_code(row.get("code"))
    return bool(code)


def extract_key_point(description: str) -> Optional[str]:
    """description 내 KEY POINT 구간 추출."""
    text = description or ""
    m = re.search(
        r"(?:💡\s*)?\*?\*?KEY\s*POINT\*?\*?\s*[:：]?\s*(.+)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None
    body = m.group(1).strip()
    # 다음 섹션/과도한 길이 컷
    body = re.split(r"\n{2,}", body, maxsplit=1)[0].strip()
    return body[:800] if body else None


def _normalize_stock_row(row: dict) -> Optional[Dict]:
    if not is_kr_stock_row(row):
        return None
    code = normalize_kr_stock_code(row.get("code"))
    if not code:
        return None
    name = (
        str(row.get("ko_name") or "").strip()
        or str(row.get("cname") or "").strip()
        or str(row.get("en_name") or "").strip()
    )
    return {
        "stock_code": code,
        "stock_name": name,
        "alphasquare_stock_id": row.get("id"),
        "market": row.get("market"),
        "country_code": row.get("country_code") or "KR",
    }


def flatten_all_themes(payload) -> List[Dict]:
    """all-themes 응답 → 테마 flat list."""
    cats = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(cats, list):
        return []
    out: List[Dict] = []
    for cat in cats:
        if not isinstance(cat, dict):
            continue
        cat_id = cat.get("id")
        cat_name = str(cat.get("name") or "").strip()
        for t in cat.get("themes") or []:
            if not isinstance(t, dict):
                continue
            tid = t.get("id")
            name = str(t.get("name") or "").strip()
            if tid is None or not name:
                continue
            desc = str(t.get("description") or "")
            out.append(
                {
                    "theme_id": int(tid),
                    "theme_name": name,
                    "description": desc,
                    "key_point": extract_key_point(desc),
                    "stock_count": t.get("stock_count"),
                    "big_theme_id": t.get("big_theme_id") if t.get("big_theme_id") is not None else cat_id,
                    "category_name": cat_name,
                    "image": t.get("image"),
                    "aliases": list(t.get("aliases") or []),
                    "is_old": bool(t.get("is_old")),
                }
            )
    return out


def fetch_all_themes() -> Dict:
    """테마 카탈로그 1회 수집."""
    try:
        payload = _get_json("/theme/v2/all-themes")
        themes = flatten_all_themes(payload)
        return {
            "ok": True,
            "themes": themes,
            "api_calls": 1,
            "raw_category_count": len((payload or {}).get("data") or [])
            if isinstance(payload, dict)
            else 0,
        }
    except Exception as e:
        logger.warning("[ALPHASQUARE_THEME] all-themes fail: %s", e)
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "themes": [],
            "api_calls": 1,
        }


def fetch_theme_stocks(theme_id: int) -> Dict:
    """단일 테마 구성종목 (KR만)."""
    try:
        payload = _get_json(f"/theme/v2/themes/{int(theme_id)}/stocks")
        rows = payload if isinstance(payload, list) else (payload.get("data") or [])
        stocks: List[Dict] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            norm = _normalize_stock_row(row)
            if norm:
                stocks.append(norm)
        return {
            "ok": True,
            "theme_id": int(theme_id),
            "stocks": stocks,
            "api_calls": 1,
        }
    except Exception as e:
        logger.warning("[ALPHASQUARE_THEME] stocks fail theme_id=%s: %s", theme_id, e)
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "theme_id": int(theme_id),
            "stocks": [],
            "api_calls": 1,
        }


def fetch_stock_themes(stock_id: int) -> Dict:
    """종목→테마 (+ reason). stock_id 는 알파스퀘어 내부 id."""
    try:
        payload = _get_json(
            "/theme/v3/themes-for-stock",
            params={"stock_id": int(stock_id)},
        )
        rows = payload.get("data") if isinstance(payload, dict) else payload
        themes = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            tid = row.get("id")
            name = str(row.get("name") or "").strip()
            if tid is None or not name:
                continue
            themes.append(
                {
                    "theme_id": int(tid),
                    "theme_name": name,
                    "big_theme_id": row.get("big_theme_id"),
                    "reason": str(row.get("reason") or "").strip() or None,
                    "is_old": bool(row.get("is_old")),
                }
            )
        return {
            "ok": True,
            "stock_id": int(stock_id),
            "themes": themes,
            "api_calls": 1,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "stock_id": int(stock_id),
            "themes": [],
            "api_calls": 1,
        }


def crawl_alphasquare_theme_snapshot(
    *,
    limit: int = 0,
    fetch_reasons: Optional[bool] = None,
    sleep_sec: Optional[float] = None,
) -> Dict:
    """테마 목록 + 구성종목 일괄 수집.

    fetch_reasons=True 이면 고유 stock_id 별로 themes-for-stock 호출해
    edge별 reason 맵을 채운다 (호출 수↑ · v1 기본 OFF).
    """
    listed = fetch_all_themes()
    if not listed.get("ok"):
        return listed

    themes = list(listed.get("themes") or [])
    if limit and limit > 0:
        themes = themes[: int(limit)]

    api_calls = int(listed.get("api_calls") or 0)
    pause = _sleep_sec() if sleep_sec is None else max(0.0, float(sleep_sec))
    do_reasons = (
        bool(Config.ALPHASQUARE_FETCH_REASONS)
        if fetch_reasons is None
        else bool(fetch_reasons)
    )

    out_themes: List[Dict] = []
    errors: List[str] = []
    code_by_stock_id: Dict[int, str] = {}

    for idx, theme in enumerate(themes):
        tid = int(theme["theme_id"])
        detail = fetch_theme_stocks(tid)
        api_calls += int(detail.get("api_calls") or 0)
        if not detail.get("ok"):
            err = f"{tid}:{theme.get('theme_name')}:{detail.get('error')}"
            errors.append(err)
            continue
        stocks = detail.get("stocks") or []
        for s in stocks:
            sid = s.get("alphasquare_stock_id")
            if sid is not None:
                try:
                    code_by_stock_id[int(sid)] = s["stock_code"]
                except (TypeError, ValueError):
                    pass
        out_themes.append({**theme, "stocks": stocks})
        if pause:
            time.sleep(pause)
        if (idx + 1) % 50 == 0:
            logger.info(
                "[ALPHASQUARE_THEME] progress %s/%s api_calls=%s",
                idx + 1,
                len(themes),
                api_calls,
            )

    reason_map: Dict[tuple, str] = {}  # (stock_code, theme_id) -> reason
    if do_reasons and code_by_stock_id:
        for i, (sid, code) in enumerate(code_by_stock_id.items()):
            r = fetch_stock_themes(sid)
            api_calls += int(r.get("api_calls") or 0)
            if not r.get("ok"):
                continue
            for th in r.get("themes") or []:
                reason = th.get("reason")
                if reason:
                    reason_map[(code, int(th["theme_id"]))] = reason
            if pause:
                time.sleep(pause)
            if (i + 1) % 100 == 0:
                logger.info(
                    "[ALPHASQUARE_THEME] reasons progress %s/%s",
                    i + 1,
                    len(code_by_stock_id),
                )
        for theme in out_themes:
            tid = int(theme["theme_id"])
            for s in theme.get("stocks") or []:
                key = (s.get("stock_code"), tid)
                if key in reason_map:
                    s["reason"] = reason_map[key]

    return {
        "ok": True,
        "themes": out_themes,
        "theme_count": len(out_themes),
        "edge_count": sum(len(t.get("stocks") or []) for t in out_themes),
        "api_calls": api_calls,
        "errors": errors[:20],
        "error_count": len(errors),
        "fetch_reasons": do_reasons,
        "reason_count": len(reason_map),
        "stock_id_map_size": len(code_by_stock_id),
    }


def crawl_alphasquare_theme_snapshot_sync(
    *,
    limit: int = 0,
    fetch_reasons: Optional[bool] = None,
    sleep_sec: Optional[float] = None,
) -> Dict:
    """동기 래퍼 — 배치/스토어용 (네트워크 I/O만, 별도 이벤트루프 불필요)."""
    if not Config.ALPHASQUARE_ENABLED:
        return {
            "ok": False,
            "error": "ALPHASQUARE_ENABLED=false",
            "themes": [],
            "api_calls": 0,
            "skipped": True,
        }
    return crawl_alphasquare_theme_snapshot(
        limit=limit,
        fetch_reasons=fetch_reasons,
        sleep_sec=sleep_sec,
    )
