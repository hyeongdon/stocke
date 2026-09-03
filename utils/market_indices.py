"""네이버 금융 기준 주요 지수(코스피·코스닥·나스닥·다우) 조회."""
from __future__ import annotations

import ast
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from utils.datetime_kst import as_kst, kst_now_iso, kst_today
from utils.market_hours import is_krx_trading_day

HEADERS = {"User-Agent": "Mozilla/5.0"}
POLL_URL = "https://polling.finance.naver.com/api/realtime?query=SERVICE_INDEX:{code}"
WORLD_URL = "https://finance.naver.com/world/sise.naver?symbol={symbol}"
KR_DAY_URL = (
    "https://api.finance.naver.com/siseJson.naver"
    "?symbol={code}&requestType=1&startTime={start}&endTime={end}&timeframe=day"
)
WORLD_DAY_URL = (
    "https://finance.naver.com/world/worldDayListJson.naver?symbol={symbol}&fdtc=0&page={page}"
)
INVESTOR_TREND_URL = "https://m.stock.naver.com/api/index/{code}/trend"
INVESTOR_DAY_URL = (
    "https://finance.naver.com/sise/investorDealTrendDay.naver"
    "?bizdate={bizdate}&sosok={sosok}&page={page}"
)
INVESTOR_TIME_URL = (
    "https://finance.naver.com/sise/investorDealTrendTime.naver"
    "?bizdate={bizdate}&sosok={sosok}&page={page}"
)
INVESTOR_HISTORY_DAYS = 15
INVESTOR_TIME_MAX_PAGES = 45
INVESTOR_TIME_MAX_POINTS = 90
_INV_HIST_CACHE: Dict[str, Dict[str, Any]] = {}
_INV_HIST_CACHE_TTL_SEC = 1800
_INV_TIME_CACHE: Dict[str, Dict[str, Any]] = {}
_INV_TIME_CACHE_TTL_SEC = 90
_INV_FETCH_LOCKS: Dict[str, threading.Lock] = {}

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}
_CACHE_TTL_SEC = 45
_HIST_CACHE: Dict[str, Any] = {}
_HIST_CACHE_TTL_SEC = 3600
_INTRADAY_HISTORY: Dict[str, Dict[str, Any]] = {}

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INV_SNAPSHOT_PATH = os.path.join(_PROJECT_ROOT, "logs", "_investor_flow_snapshot.json")
INV_SNAPSHOT_INTERVAL_SEC = 300
INV_REFRESH_START = dt_time(8, 50)
INV_REFRESH_END = dt_time(15, 40)
_INV_SNAP_LOCK = threading.Lock()
_INV_SNAP: Dict[str, Any] = {"data": None}


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
    # Naver's sign code: 1 upper limit, 2 rise, 3 unchanged,
    # 4 lower limit, 5 fall.
    if code in {"1", "2"}:
        return "up"
    if code in {"4", "5"}:
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


def _parse_signed_int(text: object) -> Optional[int]:
    if text is None:
        return None
    s = str(text).strip().replace(",", "")
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _fetch_investor_trend(code: str) -> Optional[Dict[str, Any]]:
    """네이버 모바일 지수 투자자 동향 (단위: 억원). 장중에는 잠정치, 마감 후 확정치."""
    resp = requests.get(INVESTOR_TREND_URL.format(code=code), timeout=8, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json() or {}
    foreign = _parse_signed_int(data.get("foreignValue"))
    institution = _parse_signed_int(data.get("institutionalValue"))
    personal = _parse_signed_int(data.get("personalValue"))
    if foreign is None and institution is None:
        return None
    return {
        "foreign": foreign,
        "institution": institution,
        "personal": personal,
        "unit": "억원",
        "bizdate": _parse_ymd(str(data.get("bizdate") or "")),
    }


def _parse_dot_ymd(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", s):
        return None
    try:
        return datetime.strptime(s, "%y.%m.%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _parse_investor_day_rows(html: str) -> List[Dict[str, Any]]:
    """투자자별 매매동향 표 → [{date, personal, foreign, institution}, ...] (단위: 억원)."""
    soup = BeautifulSoup(html or "", "html.parser")
    table = soup.select_one("table.type_1")
    if not table:
        return []
    out: List[Dict[str, Any]] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 11:
            continue
        ymd = _parse_dot_ymd(tds[0].get_text(strip=True))
        if not ymd:
            continue
        out.append(
            {
                "date": ymd,
                "personal": _parse_signed_int(tds[1].get_text(strip=True)),
                "foreign": _parse_signed_int(tds[2].get_text(strip=True)),
                "institution": _parse_signed_int(tds[3].get_text(strip=True)),
            }
        )
    return out


def _fetch_investor_daily(code: str, days: int = INVESTOR_HISTORY_DAYS, force: bool = False) -> List[Dict[str, Any]]:
    """최근 N거래일 일별 투자자 순매수, 날짜 오름차순."""
    key = code.upper()
    now = time.time()
    cached = _INV_HIST_CACHE.get(key)
    if not force and cached and now - float(cached.get("at") or 0) < _INV_HIST_CACHE_TTL_SEC:
        return [dict(r) for r in cached["rows"]]
    sosok = "01" if key == "KOSPI" else "02"
    bizdate = kst_today().strftime("%Y%m%d")
    pages = max(1, (days + 4) // 5)  # 페이지당 5거래일
    rows: List[Dict[str, Any]] = []
    for page in range(1, pages + 1):
        resp = requests.get(
            INVESTOR_DAY_URL.format(bizdate=bizdate, sosok=sosok, page=page),
            timeout=10,
            headers=HEADERS,
        )
        resp.raise_for_status()
        rows.extend(_parse_investor_day_rows(resp.text))
    by_day = {r["date"]: r for r in rows}
    ordered = [by_day[d] for d in sorted(by_day)]
    _INV_HIST_CACHE[key] = {"at": now, "rows": ordered}
    return [dict(r) for r in ordered]


def _merge_live_investor(
    history: List[Dict[str, Any]], live: Optional[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """당일 잠정치(모바일 API)로 히스토리의 당일 행을 덮어쓰거나 추가."""
    rows = [dict(r) for r in history]
    if live and live.get("bizdate"):
        merged = {
            "date": live["bizdate"],
            "personal": live.get("personal"),
            "foreign": live.get("foreign"),
            "institution": live.get("institution"),
        }
        for i, r in enumerate(rows):
            if r["date"] == merged["date"]:
                rows[i] = merged
                break
        else:
            rows.append(merged)
    return rows[-INVESTOR_HISTORY_DAYS:]


def _parse_hm(text: object) -> Optional[str]:
    s = str(text or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _parse_investor_time_rows(html: str) -> List[Dict[str, Any]]:
    """시간별 순매수 표 → [{time, personal, foreign, institution}, ...] (단위: 억원)."""
    soup = BeautifulSoup(html or "", "html.parser")
    table = soup.select_one("table.type_1")
    if not table:
        return []
    out: List[Dict[str, Any]] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        hm = _parse_hm(tds[0].get_text(strip=True))
        if not hm:
            continue
        out.append(
            {
                "time": hm,
                "personal": _parse_signed_int(tds[1].get_text(strip=True)),
                "foreign": _parse_signed_int(tds[2].get_text(strip=True)),
                "institution": _parse_signed_int(tds[3].get_text(strip=True)),
            }
        )
    return out


def _merge_time_rows(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_time: Dict[str, Dict[str, Any]] = {}
    for rows in groups:
        for row in rows:
            t = str(row.get("time") or "")
            if t:
                by_time[t] = dict(row)
    return [by_time[t] for t in sorted(by_time)]


def _time_series_complete(rows: List[Dict[str, Any]]) -> bool:
    return any(str(r.get("time") or "") <= "09:10" for r in rows)


def _downsample_investor_intraday(
    rows: List[Dict[str, Any]], max_points: int = INVESTOR_TIME_MAX_POINTS
) -> List[Dict[str, Any]]:
    if len(rows) <= max_points or max_points < 3:
        return [dict(r) for r in rows]
    last_i = len(rows) - 1
    idxs = sorted({round(i * last_i / (max_points - 1)) for i in range(max_points)})
    idxs[-1] = last_i
    return [dict(rows[i]) for i in idxs]


def _fetch_investor_time_page(sosok: str, bizdate: str, page: int) -> List[Dict[str, Any]]:
    resp = requests.get(
        INVESTOR_TIME_URL.format(bizdate=bizdate, sosok=sosok, page=page),
        timeout=8,
        headers=HEADERS,
    )
    resp.raise_for_status()
    return _parse_investor_time_rows(resp.text)


_INV_TIME_PAGE_BATCH = 6


def _fetch_investor_time_pages(sosok: str, bizdate: str, start_page: int = 1) -> List[Dict[str, Any]]:
    """시간별 순매수를 배치 병렬로 모으고, 09:10 이전 봉이 보이거나 빈 배치면 중단."""
    collected: List[Dict[str, Any]] = []
    page = max(1, start_page)
    while page <= INVESTOR_TIME_MAX_PAGES:
        batch = list(range(page, min(page + _INV_TIME_PAGE_BATCH, INVESTOR_TIME_MAX_PAGES + 1)))
        before = {str(r.get("time") or "") for r in collected}
        nonempty_sets = []
        with ThreadPoolExecutor(max_workers=len(batch) or 1) as pool:
            futs = [pool.submit(_fetch_investor_time_page, sosok, bizdate, p) for p in batch]
            for fut in as_completed(futs):
                try:
                    rows = fut.result() or []
                except Exception:
                    rows = []
                if rows:
                    nonempty_sets.append(frozenset(str(r.get("time") or "") for r in rows))
                    collected = _merge_time_rows(collected, rows)
        after = {str(r.get("time") or "") for r in collected}
        same_page_repeated = len(set(nonempty_sets)) <= 1
        if (
            _time_series_complete(collected)
            or not nonempty_sets
            or after == before
            or same_page_repeated
        ):
            break
        page += _INV_TIME_PAGE_BATCH
    return collected


def _fetch_investor_intraday(code: str, force: bool = False) -> List[Dict[str, Any]]:
    """당일 시간대별 누적 순매수(억원), 시각 오름차순. 차트용으로 다운샘플."""
    key = code.upper()
    lock = _INV_FETCH_LOCKS.setdefault(key, threading.Lock())
    with lock:
        return _fetch_investor_intraday_locked(key, force)


def _fetch_investor_intraday_locked(key: str, force: bool) -> List[Dict[str, Any]]:
    today = kst_today().isoformat()
    now = time.time()
    cached = _INV_TIME_CACHE.get(key)
    sosok = "01" if key == "KOSPI" else "02"
    bizdate = kst_today().strftime("%Y%m%d")

    if (
        not force
        and cached
        and cached.get("date") == today
        and now - float(cached.get("at") or 0) < _INV_TIME_CACHE_TTL_SEC
    ):
        return _downsample_investor_intraday(cached["rows"])

    rows: List[Dict[str, Any]] = []
    if cached and cached.get("date") == today:
        rows = [dict(r) for r in cached.get("rows") or []]

    try:
        if rows:
            # 이미 시리즈가 있으면 최신 페이지만 갱신. 미완성이어도 45페이지를 매번 긁지 않는다.
            rows = _merge_time_rows(rows, _fetch_investor_time_page(sosok, bizdate, 1))
            if not _time_series_complete(rows):
                rows = _merge_time_rows(rows, _fetch_investor_time_pages(sosok, bizdate, start_page=2))
        else:
            rows = _fetch_investor_time_pages(sosok, bizdate, start_page=1)
    except Exception:
        if not rows:
            raise

    _INV_TIME_CACHE[key] = {
        "at": now,
        "date": today,
        "rows": rows,
        "complete": _time_series_complete(rows),
    }
    return _downsample_investor_intraday(rows)


def _record_intraday(code: str, value: Optional[float], open_: Optional[float]) -> List[float]:
    today = kst_today().isoformat()
    state = _INTRADAY_HISTORY.get(code)
    if not state or state.get("date") != today:
        points = [open_] if open_ is not None and open_ > 0 else []
        state = {"date": today, "points": points}
        _INTRADAY_HISTORY[code] = state
    points = state["points"]
    if value is not None and value > 0 and (not points or points[-1] != value):
        points.append(value)
    return list(points[-240:])


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
    open_ = _to_float(item.get("ov", 0) / 100.0)
    high = _to_float(item.get("hv", 0) / 100.0)
    low = _to_float(item.get("lv", 0) / 100.0)
    if pct is not None and direction == "down" and pct > 0:
        pct = -pct
    intraday = _record_intraday(code, value, open_)
    row = {
        "key": code.lower(),
        "label": "코스피" if code == "KOSPI" else "코스닥",
        "value": value,
        "change": change,
        "change_pct": pct,
        "direction": direction,
        "open": open_,
        "high": high,
        "low": low,
        "intraday": intraday,
    }
    return row


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


def clear_investor_snapshot_cache() -> None:
    with _INV_SNAP_LOCK:
        _INV_SNAP["data"] = None


def load_investor_flow_snapshot() -> Optional[Dict[str, Any]]:
    with _INV_SNAP_LOCK:
        cached = _INV_SNAP.get("data")
        if isinstance(cached, dict):
            return dict(cached)
    try:
        with open(INV_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    with _INV_SNAP_LOCK:
        _INV_SNAP["data"] = data
    return dict(data)


def _save_investor_flow_snapshot(payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(INV_SNAPSHOT_PATH), exist_ok=True)
    tmp = INV_SNAPSHOT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, INV_SNAPSHOT_PATH)
    with _INV_SNAP_LOCK:
        _INV_SNAP["data"] = dict(payload)


def _fetch_investor_market(code: str) -> Dict[str, Any]:
    investor = None
    series: List[Dict[str, Any]] = []
    try:
        investor = _fetch_investor_trend(code)
    except Exception:
        investor = None
    try:
        series = _fetch_investor_intraday(code, force=True)
    except Exception:
        series = []
    return {"investor": investor, "investor_intraday": series}


def refresh_investor_flow_snapshot() -> Dict[str, Any]:
    """네이버에서 코스피·코스닥 수급을 모아 JSON 스냅샷으로 저장. 화면 요청 경로에서는 호출하지 않는다."""
    markets: Dict[str, Any] = {}
    errors: List[str] = []
    jobs = [("KOSPI", "kospi"), ("KOSDAQ", "kosdaq")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = {pool.submit(_fetch_investor_market, code): key for code, key in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                markets[key] = fut.result()
            except Exception as exc:
                errors.append(f"{key}: {exc}")
                markets[key] = {"investor": None, "investor_intraday": []}
    payload = {
        "updated_at": kst_now_iso(),
        "at": time.time(),
        "date": kst_today().isoformat(),
        "markets": markets,
        "errors": errors,
    }
    _save_investor_flow_snapshot(payload)
    return dict(payload)


def investor_refresh_due(now: Optional[datetime] = None) -> bool:
    """장중 5분 주기. 오늘 스냅샷이 없으면 장 외·주말도 한 번 채운다."""
    kst = as_kst(now)
    today = kst.date().isoformat()
    snap = load_investor_flow_snapshot()
    if snap is None or str(snap.get("date") or "") != today:
        return True
    if not is_krx_trading_day(kst):
        return False
    t = kst.time()
    if t < INV_REFRESH_START:
        return False
    age = time.time() - float(snap.get("at") or 0)
    if t <= INV_REFRESH_END:
        return age >= INV_SNAPSHOT_INTERVAL_SEC
    return False


def _with_investor_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    indices = [dict(row) for row in (payload.get("indices") or [])]
    out["indices"] = indices
    snap = load_investor_flow_snapshot()
    today = str(payload.get("date") or kst_today().isoformat())
    if not snap or str(snap.get("date") or "") != today:
        out["investor_updated_at"] = None
        out["investor_as_of"] = "none"
        return out
    markets = snap.get("markets") or {}
    for row in indices:
        key = str(row.get("key") or "").lower()
        bundle = markets.get(key)
        if not isinstance(bundle, dict):
            continue
        row["investor"] = bundle.get("investor")
        row["investor_intraday"] = list(bundle.get("investor_intraday") or [])
    out["investor_updated_at"] = snap.get("updated_at")
    out["investor_as_of"] = "batch"
    return out


def fetch_market_indices(force: bool = False) -> Dict[str, Any]:
    now = time.time()
    with _CACHE_LOCK:
        if not force and _CACHE.get("data") and now - float(_CACHE.get("at") or 0) < _CACHE_TTL_SEC:
            return _with_investor_snapshot(dict(_CACHE["data"]))

    indices: List[Dict[str, Any]] = []
    errors: List[str] = []

    jobs = [
        ("KOSPI", lambda: _fetch_domestic("KOSPI")),
        ("KOSDAQ", lambda: _fetch_domestic("KOSDAQ")),
        ("나스닥", lambda: _fetch_world("NAS@IXIC", "나스닥")),
        ("다우", lambda: _fetch_world("DJI@DJI", "다우")),
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(fn): name for name, fn in jobs}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                indices.append(fut.result())
            except Exception as exc:
                errors.append(f"{name}: {exc}")
    order = {"kospi": 0, "kosdaq": 1, "나스닥": 2, "다우": 3}
    indices.sort(key=lambda row: order.get(str(row.get("key") or row.get("label") or ""), 9))

    if not indices:
        with _CACHE_LOCK:
            stale = _CACHE.get("data")
        if stale and stale.get("indices"):
            payload = dict(stale)
            payload["errors"] = list(stale.get("errors") or []) + errors + ["using_cache"]
            return _with_investor_snapshot(payload)

    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "date": kst_today().isoformat(),
        "as_of": "live",
        "indices": indices,
        "errors": errors,
    }
    with _CACHE_LOCK:
        _CACHE["at"] = now
        _CACHE["data"] = payload
    return _with_investor_snapshot(dict(payload))


def _ymd_compact(ymd: str) -> str:
    return ymd.replace("-", "")


def _parse_ymd(ymd: str) -> Optional[str]:
    s = (ymd or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            return None
        return s
    if re.fullmatch(r"\d{8}", s):
        try:
            dt = datetime.strptime(s, "%Y%m%d")
        except ValueError:
            return None
        return dt.strftime("%Y-%m-%d")
    return None


def _direction_from_change(change: Optional[float]) -> str:
    if change is None or change == 0:
        return "flat"
    return "up" if change > 0 else "down"


def _index_row(
    key: str,
    label: str,
    value: Optional[float],
    change: Optional[float],
    pct: Optional[float],
    *,
    session_date: Optional[str] = None,
    closed: bool = False,
    open_: Optional[float] = None,
    high: Optional[float] = None,
    low: Optional[float] = None,
    bars: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if pct is not None:
        pct = round(pct, 2)
    if change is not None:
        change = round(change, 2)
    row = {
        "key": key,
        "label": label,
        "value": value,
        "open": round(open_, 2) if open_ is not None else None,
        "high": round(high, 2) if high is not None else None,
        "low": round(low, 2) if low is not None else None,
        "change": change,
        "change_pct": pct,
        "direction": _direction_from_change(change if change is not None else pct),
        "session_date": session_date,
        "closed": closed,
        "bars": bars or [],
    }
    return row


def _closed_row(key: str, label: str, ymd: str) -> Dict[str, Any]:
    return _index_row(key, label, None, None, None, session_date=ymd, closed=True)


def _bar_dict(
    ymd: str,
    open_: Optional[float],
    high: Optional[float],
    low: Optional[float],
    close: Optional[float],
) -> Dict[str, Any]:
    return {
        "date": ymd,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def _parse_sise_json(text: str) -> List[Dict[str, Any]]:
    """일봉 JSON → [{date, open, high, low, close}, ...] 오름차순."""
    rows = ast.literal_eval((text or "").strip() or "[]")
    out: List[Dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for row in rows[1:]:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        ymd = _parse_ymd(str(row[0]))
        open_ = _to_float(row[1])
        high = _to_float(row[2])
        low = _to_float(row[3])
        close = _to_float(row[4])
        if ymd and close is not None:
            out.append(_bar_dict(ymd, open_, high, low, close))
    out.sort(key=lambda x: x["date"])
    return out


def _fetch_kr_day_bars(code: str, ymd: str) -> List[Dict[str, Any]]:
    end_dt = datetime.strptime(ymd, "%Y-%m-%d")
    start = (end_dt - timedelta(days=60)).strftime("%Y%m%d")
    end = end_dt.strftime("%Y%m%d")
    resp = requests.get(
        KR_DAY_URL.format(code=code, start=start, end=end),
        timeout=12,
        headers=HEADERS,
    )
    resp.raise_for_status()
    return [b for b in _parse_sise_json(resp.text) if b["date"] <= ymd]


def _spark_bars(bars: List[Dict[str, Any]], ymd: str, limit: int = 15) -> List[Dict[str, Any]]:
    picked = [b for b in bars if b.get("date") and b["date"] <= ymd]
    return picked[-limit:]


def _bar_on_date(
    bars: List[Dict[str, Any]], ymd: str
) -> Optional[Tuple[Dict[str, Any], Optional[float]]]:
    prev_close = None
    for bar in bars:
        if bar["date"] == ymd:
            return bar, prev_close
        if bar["date"] < ymd:
            prev_close = bar.get("close")
    return None


def _kr_index_on_date(code: str, label: str, ymd: str) -> Dict[str, Any]:
    key = code.lower()
    bars = _fetch_kr_day_bars(code, ymd)
    picked = _bar_on_date(bars, ymd)
    if picked is None:
        return _closed_row(key, label, ymd)
    bar, prev = picked
    close = bar.get("close")
    change = (close - prev) if prev is not None and close is not None else None
    pct = ((change / prev) * 100.0) if prev and change is not None else None
    return _index_row(
        key,
        label,
        close,
        change,
        pct,
        session_date=bar["date"],
        open_=bar.get("open"),
        high=bar.get("high"),
        low=bar.get("low"),
        bars=_spark_bars(bars, ymd),
    )


def _fetch_world_day_pages(symbol: str, ymd: str, max_pages: int = 4) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    need = 20
    for page in range(1, max_pages + 1):
        resp = requests.get(
            WORLD_DAY_URL.format(symbol=symbol, page=page),
            timeout=12,
            headers=HEADERS,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            break
        found.extend(rows)
        usable = [
            r for r in found
            if (_parse_ymd(str(r.get("xymd") or "")) or "") <= ymd
        ]
        last = _parse_ymd(str(rows[-1].get("xymd") or ""))
        if len(usable) >= need and last and last < ymd:
            break
        if last and last < ymd and len(usable) >= 8:
            break
    return found


def _world_bars(rows: List[Dict[str, Any]], ymd: str) -> List[Dict[str, Any]]:
    bars: List[Dict[str, Any]] = []
    for row in rows:
        d = _parse_ymd(str(row.get("xymd") or ""))
        close = _to_float(row.get("clos"))
        if not d or d > ymd or close is None:
            continue
        bars.append(
            _bar_dict(
                d,
                _to_float(row.get("open")),
                _to_float(row.get("high")),
                _to_float(row.get("low")),
                close,
            )
        )
    bars.sort(key=lambda x: x["date"])
    # 같은 날짜 중복 제거
    by_day: Dict[str, Dict[str, Any]] = {b["date"]: b for b in bars}
    return [by_day[d] for d in sorted(by_day)]


def _world_index_on_date(symbol: str, label: str, ymd: str) -> Dict[str, Any]:
    key = "nasdaq" if "NAS" in symbol else "dow"
    rows = _fetch_world_day_pages(symbol, ymd)
    bars = _world_bars(rows, ymd)
    picked = _bar_on_date(bars, ymd)
    if picked is None:
        return _closed_row(key, label, ymd)
    bar, prev = picked
    close = bar.get("close")
    match = None
    for row in rows:
        if _parse_ymd(str(row.get("xymd") or "")) == ymd:
            match = row
            break
    change = _to_float(match.get("diff")) if match else None
    pct = _to_float(match.get("rate")) if match else None
    if change is None and prev is not None and close is not None:
        change = close - prev
    if pct is None and prev and change is not None:
        pct = (change / prev) * 100.0
    if pct is not None and change is not None and change < 0 and pct > 0:
        pct = -pct
    if change is not None and pct is not None and pct < 0 and change > 0:
        change = -change
    return _index_row(
        key,
        label,
        close,
        change,
        pct,
        session_date=bar["date"],
        open_=bar.get("open"),
        high=bar.get("high"),
        low=bar.get("low"),
        bars=_spark_bars(bars, ymd),
    )


def fetch_market_indices_for_date(ymd: str, force: bool = False) -> Dict[str, Any]:
    """특정일(KST YYYY-MM-DD) 종가 기준 지수. 당일이면 실시간 스냅샷."""
    parsed = _parse_ymd(ymd)
    if not parsed:
        raise ValueError("date must be YYYY-MM-DD")
    if parsed == kst_today().isoformat():
        return fetch_market_indices(force=force)

    now = time.time()
    with _CACHE_LOCK:
        cached = _HIST_CACHE.get(parsed)
        if (
            not force
            and cached
            and now - float(cached.get("at") or 0) < _HIST_CACHE_TTL_SEC
        ):
            return dict(cached["data"])

    indices: List[Dict[str, Any]] = []
    errors: List[str] = []
    for code, label in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
        try:
            indices.append(_kr_index_on_date(code, label, parsed))
        except Exception as exc:
            errors.append(f"{code}: {exc}")
            indices.append(_closed_row(code.lower(), label, parsed))
    for symbol, label in (("NAS@IXIC", "나스닥"), ("DJI@DJI", "다우")):
        try:
            indices.append(_world_index_on_date(symbol, label, parsed))
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            key = "nasdaq" if "NAS" in symbol else "dow"
            indices.append(_closed_row(key, label, parsed))

    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "date": parsed,
        "as_of": "close",
        "indices": indices,
        "errors": errors,
    }
    with _CACHE_LOCK:
        _HIST_CACHE[parsed] = {"at": now, "data": payload}
    return dict(payload)
