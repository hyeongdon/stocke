"""금융투자협회 FreeSIS 증시자금(투자자예탁금·신용융자) 조회. 단위: 백만원, 전 영업일 기준."""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

import requests

from utils.datetime_kst import kst_now_iso, kst_today

KOFIA_URL = "https://freesis.kofia.or.kr/meta/getMetaDataList.do"
KOFIA_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/json; charset=UTF-8",
    "Referer": "https://freesis.kofia.or.kr/",
}
OBJ_DEPOSIT = "STATSCU0100000060BO"  # 증시자금추이
OBJ_CREDIT = "STATSCU0100000070BO"  # 신용공여 잔고 추이
HISTORY_DAYS = 400
SNAPSHOT_TTL_SEC = 3 * 3600
ERROR_RETRY_SEC = 600

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_PATH = os.path.join(_PROJECT_ROOT, "logs", "_market_funds_snapshot.json")

_LOCK = threading.Lock()


def _to_num(v: object) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _ymd(s: object) -> Optional[str]:
    t = str(s or "").strip()
    if len(t) != 8 or not t.isdigit():
        return None
    return f"{t[:4]}-{t[4:6]}-{t[6:]}"


def _fetch_kofia(obj_nm: str, start: str, end: str) -> List[Dict[str, Any]]:
    body = {
        "dmSearch": {
            "tmpV40": "1000000",
            "tmpV41": "1",
            "tmpV1": "D",
            "tmpV45": start,
            "tmpV46": end,
            "OBJ_NM": obj_nm,
        }
    }
    resp = requests.post(KOFIA_URL, json=body, headers=KOFIA_HEADERS, timeout=15)
    resp.raise_for_status()
    rows = (resp.json() or {}).get("ds1")
    if not isinstance(rows, list):
        raise RuntimeError(f"{obj_nm} 응답 형식 오류")
    return rows


def parse_deposit_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        d = _ymd(r.get("TMPV1"))
        if not d:
            continue
        out.append(
            {
                "date": d,
                "deposit": _to_num(r.get("TMPV2")),
                "derivative_deposit": _to_num(r.get("TMPV3")),
                "rp_balance": _to_num(r.get("TMPV4")),
                "uncollected": _to_num(r.get("TMPV5")),
                "forced_sale": _to_num(r.get("TMPV6")),
                "forced_sale_pct": _to_num(r.get("TMPV7")),
            }
        )
    out.sort(key=lambda x: x["date"])
    return out


def parse_credit_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        d = _ymd(r.get("TMPV1"))
        if not d:
            continue
        out.append(
            {
                "date": d,
                "credit": _to_num(r.get("TMPV2")),
                "credit_kospi": _to_num(r.get("TMPV3")),
                "credit_kosdaq": _to_num(r.get("TMPV4")),
                "stock_backed_loan": _to_num(r.get("TMPV9")),
            }
        )
    out.sort(key=lambda x: x["date"])
    return out


def merge_series(deposit: List[Dict[str, Any]], credit: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_day: Dict[str, Dict[str, Any]] = {}
    for r in deposit:
        by_day.setdefault(r["date"], {"date": r["date"]}).update(r)
    for r in credit:
        by_day.setdefault(r["date"], {"date": r["date"]}).update(r)
    return [by_day[d] for d in sorted(by_day)]


def load_snapshot() -> Optional[Dict[str, Any]]:
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_snapshot(payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
    tmp = SNAPSHOT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, SNAPSHOT_PATH)


def _snapshot_fresh(snap: Optional[Dict[str, Any]]) -> bool:
    if not snap:
        return False
    return time.time() - float(snap.get("at") or 0) < SNAPSHOT_TTL_SEC


def _response(snap: Dict[str, Any], *, stale: bool, errors: List[str]) -> Dict[str, Any]:
    return {
        "updated_at": snap.get("updated_at"),
        "stale": stale,
        "errors": errors,
        "unit": "백만원",
        "rows": merge_series(snap.get("deposit") or [], snap.get("credit") or []),
    }


def fetch_market_funds(force: bool = False) -> Dict[str, Any]:
    """스냅샷이 TTL 안이면 그대로, 아니면 KOFIA 재조회. 실패한 시리즈는 직전 스냅샷 값을 유지."""
    snap = load_snapshot()
    if not force and _snapshot_fresh(snap):
        return _response(snap, stale=False, errors=[])

    with _LOCK:
        snap = load_snapshot()
        if not force and _snapshot_fresh(snap):
            return _response(snap, stale=False, errors=[])

        end = kst_today()
        start = (end - timedelta(days=HISTORY_DAYS)).strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")
        jobs: Dict[str, tuple[str, Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]]] = {
            "deposit": (OBJ_DEPOSIT, parse_deposit_rows),
            "credit": (OBJ_CREDIT, parse_credit_rows),
        }
        fresh: Dict[str, List[Dict[str, Any]]] = {}
        errors: List[str] = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = {key: pool.submit(_fetch_kofia, obj, start, end_s) for key, (obj, _) in jobs.items()}
            for key, fut in futs.items():
                try:
                    parsed = jobs[key][1](fut.result())
                    if not parsed:
                        raise RuntimeError("빈 응답")
                    fresh[key] = parsed
                except Exception as exc:
                    errors.append(f"{key}: {exc}")

        if not fresh:
            if snap:
                return _response(snap, stale=True, errors=errors)
            raise RuntimeError("; ".join(errors) or "증시자금 조회 실패")

        prev = snap or {}
        now = time.time()
        payload = {
            "at": now - SNAPSHOT_TTL_SEC + ERROR_RETRY_SEC if errors else now,
            "updated_at": kst_now_iso(),
            "deposit": fresh.get("deposit", prev.get("deposit") or []),
            "credit": fresh.get("credit", prev.get("credit") or []),
        }
        _save_snapshot(payload)
        return _response(payload, stale=bool(errors), errors=errors)
