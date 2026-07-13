"""네이버 금융 기준 주요 지수(코스피·코스닥·나스닥·다우) 조회."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0"}
POLL_URL = "https://polling.finance.naver.com/api/realtime?query=SERVICE_INDEX:{code}"
WORLD_URL = "https://finance.naver.com/world/sise.naver?symbol={symbol}"

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}
_CACHE_TTL_SEC = 45


def _parse_digit_spans(container) -> str:
    if not container:
        return ""
    parts: List[str] = []
    for span in container.find_all("span"):
        cls = " ".join(span.get("class") or [])
        if cls == "shim":
            parts.append(",")
        elif cls == "jum":
            parts.append(".")
        elif cls.startswith("no"):
            parts.append(span.get_text(strip=True))
    return "".join(parts)


def _to_float(text: object) -> Optional[float]:
    if text is None:
        return None
    s = str(text).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _direction_from_rf(rf: object) -> str:
    code = str(rf or "")
    if code == "2":
        return "up"
    if code == "3":
        return "down"
    return "flat"


def _direction_from_em(em) -> str:
    if not em:
        return "flat"
    cls = " ".join(em.get("class") or [])
    if "no_up" in cls:
        return "up"
    if "no_down" in cls:
        return "down"
    return "flat"


def _signed_change(change: Optional[float], direction: str) -> Optional[float]:
    if change is None:
        return None
    if direction == "down" and change > 0:
        return -change
    if direction == "up" and change < 0:
        return abs(change)
    return change


def _fetch_domestic(code: str) -> Dict[str, Any]:
    resp = requests.get(POLL_URL.format(code=code), timeout=10, headers=HEADERS)
    resp.raise_for_status()
    rows = (resp.json().get("result") or {}).get("areas") or []
    item = None
    for area in rows:
        for row in area.get("datas") or []:
            if row.get("cd") == code:
                item = row
                break
    if not item:
        raise RuntimeError(f"{code} index empty")

    direction = _direction_from_rf(item.get("rf"))
    value = _to_float(item.get("nv", 0) / 100.0)
    change = _signed_change(_to_float(item.get("cv", 0) / 100.0), direction)
    pct = _to_float(item.get("cr"))
    if pct is not None and direction == "down" and pct > 0:
        pct = -pct
    return {
        "key": code.lower(),
        "label": "코스피" if code == "KOSPI" else "코스닥",
        "value": value,
        "change": change,
        "change_pct": pct,
        "direction": direction,
    }


def _fetch_world(symbol: str, label: str) -> Dict[str, Any]:
    resp = requests.get(WORLD_URL.format(symbol=symbol), timeout=12, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    today_em = soup.select_one("p.no_today em")
    exday = soup.select_one("p.no_exday")
    ems = exday.select("em") if exday else []

    direction = _direction_from_em(today_em)
    value = _to_float(_parse_digit_spans(today_em))
    change = _signed_change(_to_float(_parse_digit_spans(ems[0] if ems else None)), direction)

    pct = _to_float(_parse_digit_spans(ems[1] if len(ems) > 1 else None))
    if pct is None and value and change is not None and value - change:
        pct = (change / (value - change)) * 100.0
    if pct is not None and direction == "down" and pct > 0:
        pct = -pct

    return {
        "key": label,
        "label": label,
        "value": value,
        "change": change,
        "change_pct": round(pct, 2) if pct is not None else None,
        "direction": direction,
    }


def fetch_market_indices(force: bool = False) -> Dict[str, Any]:
    now = time.time()
    with _CACHE_LOCK:
        if not force and _CACHE.get("data") and now - float(_CACHE.get("at") or 0) < _CACHE_TTL_SEC:
            return dict(_CACHE["data"])

    indices: List[Dict[str, Any]] = []
    errors: List[str] = []

    for code in ("KOSPI", "KOSDAQ"):
        try:
            indices.append(_fetch_domestic(code))
        except Exception as exc:
            errors.append(f"{code}: {exc}")

    for symbol, label in (("NAS@IXIC", "나스닥"), ("DJI@DJI", "다우")):
        try:
            indices.append(_fetch_world(symbol, label))
        except Exception as exc:
            errors.append(f"{label}: {exc}")

    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "indices": indices,
        "errors": errors,
    }
    with _CACHE_LOCK:
        _CACHE["at"] = now
        _CACHE["data"] = payload
    return dict(payload)
