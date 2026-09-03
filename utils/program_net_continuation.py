"""수급 돌파 — 프로그램 시간대(ka90008) 순매수 연속 판정.

한 칸은 보통 1분. 형성 중인 현재 분은 제외하고 완성 구간만 본다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_LOOKBACK = 5
DEFAULT_MIN_BUY = 3


def program_qty_int(v: Any) -> int:
    try:
        s = str(v or "").replace(",", "").replace("+", "").strip()
        if not s or s in ("-", "--"):
            return 0
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def parse_program_tm_minutes(tm: Any) -> Optional[int]:
    """HHMM / HHMMSS / HH:MM:SS → 자정 기준 분. 실패 시 None."""
    s = str(tm or "").replace(":", "").replace(" ", "").strip()
    if not s.isdigit():
        return None
    if len(s) < 4:
        return None
    try:
        hh = int(s[:2])
        mm = int(s[2:4])
    except (TypeError, ValueError):
        return None
    if hh > 23 or mm > 59:
        return None
    return hh * 60 + mm


def _row_net_qty(row: Dict[str, Any]) -> int:
    if not isinstance(row, dict):
        return 0
    if "net_qty" in row and row.get("net_qty") is not None:
        return program_qty_int(row.get("net_qty"))
    return program_qty_int(
        row.get("prm_netprps_qty")
        if row.get("prm_netprps_qty") not in (None, "")
        else row.get("prm_netprps_amt")
    )


def completed_program_slots(
    rows: List[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """시간순 완성 구간. 현재 분과 같은 tm은 형성 중으로 제외."""
    forming = None
    if now is not None:
        forming = now.hour * 60 + now.minute
    out: List[Tuple[int, Dict[str, Any]]] = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        tm = row.get("tm") or row.get("time") or ""
        minute = parse_program_tm_minutes(tm)
        if minute is None:
            continue
        if forming is not None and minute == forming:
            continue
        if minute in seen:
            continue
        seen.add(minute)
        slot = dict(row)
        slot["tm"] = str(tm)
        slot["minute_of_day"] = minute
        slot["net_qty"] = _row_net_qty(row)
        out.append((minute, slot))
    out.sort(key=lambda x: x[0])
    return [s for _, s in out]


def program_net_continuation_ok(
    rows: List[Dict[str, Any]],
    *,
    lookback: int = DEFAULT_LOOKBACK,
    min_buy: int = DEFAULT_MIN_BUY,
    now: Optional[datetime] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """최근 lookback칸 중 min_buy칸 이상이 순매수(net>0)."""
    n = max(1, int(lookback or DEFAULT_LOOKBACK))
    m = max(1, int(min_buy or DEFAULT_MIN_BUY))
    if m > n:
        m = n
    slots = completed_program_slots(rows, now=now)
    recent = slots[-n:]
    nets = [int(s.get("net_qty") or 0) for s in recent]
    buy_count = sum(1 for q in nets if q > 0)
    tms = [str(s.get("tm") or "") for s in recent]
    detail = {
        "program_lookback": n,
        "program_min_buy": m,
        "program_slot_count": len(recent),
        "program_buy_count": buy_count,
        "program_nets": nets,
        "program_tms": tms,
        "program_net_ok": False,
    }
    if len(recent) < n:
        reason = f"프로그램 구간 부족 ({len(recent)}/{n}칸)"
        detail["program_net_reason"] = reason
        return False, reason, detail
    ok = buy_count >= m
    reason = f"프로그램 순매수 {buy_count}/{n}칸 (필요 {m})"
    detail["program_net_ok"] = ok
    detail["program_net_reason"] = reason
    if not ok:
        reason = f"프로그램 매수세 부족 ({buy_count}/{n}칸 < {m})"
        detail["program_net_reason"] = reason
        return False, reason, detail
    return True, reason, detail
