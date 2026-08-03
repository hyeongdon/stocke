"""역매공파(YMGP) 장후 단계·박스권 차이 리포트 집계."""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from core.models import AutoTradeSettings
from utils.datetime_kst import kst_today
from utils.ymgp_engine import (
    DEFAULTS,
    evaluate_ymgp_from_daily,
    get_stock_state,
    is_reentry_locked,
)

logger = logging.getLogger(__name__)

_STAGE_ORDER = (
    "NONE",
    "FILTERED",
    "READY",
    "ARMED",
    "ENTERED_1",
    "ENTERED_2",
    "MANAGING",
    "STOPPED",
    "DONE",
)

_STAGE_LABEL = {
    "NONE": "탈락",
    "FILTERED": "역배열",
    "READY": "준비",
    "ARMED": "대기",
    "ENTERED_1": "1차",
    "ENTERED_2": "2차",
    "MANAGING": "관리",
    "STOPPED": "락",
    "DONE": "종료",
}


def stage_label(stage: Optional[str]) -> str:
    key = str(stage or "").upper()
    return _STAGE_LABEL.get(key, key or "?")


def _box_limit(settings: Any) -> float:
    raw = getattr(settings, "ymgp_box_width_pct", None) if settings is not None else None
    if raw is None or raw == "":
        return float(DEFAULTS["ymgp_box_width_pct"])
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(DEFAULTS["ymgp_box_width_pct"])


def _failed_check_keys(checks: Sequence[dict]) -> List[str]:
    out: List[str] = []
    for ch in checks or []:
        if not isinstance(ch, dict) or ch.get("passed"):
            continue
        key = str(ch.get("key") or "")
        if key and key not in out:
            out.append(key)
    return out


def enrich_ymgp_row(
    *,
    stock_code: str,
    stock_name: str,
    current_price: Optional[int],
    change_rate: Optional[float],
    evaled: Dict[str, Any],
    settings: Any = None,
    condition_name: str = "",
) -> Dict[str, Any]:
    """evaluate_ymgp_from_daily 결과 → 리포트 행 (박스 폭差·고점差)."""
    box = evaled.get("box") if isinstance(evaled.get("box"), dict) else None
    limit = _box_limit(settings)
    width = None
    box_high = None
    box_low = None
    if box:
        try:
            width = float(box.get("width_pct")) if box.get("width_pct") is not None else None
        except (TypeError, ValueError):
            width = None
        try:
            box_high = float(box["high"]) if box.get("high") is not None else None
        except (TypeError, ValueError):
            box_high = None
        try:
            box_low = float(box["low"]) if box.get("low") is not None else None
        except (TypeError, ValueError):
            box_low = None

    width_over = None
    if width is not None:
        width_over = round(width - limit, 2)

    price = None
    try:
        if current_price is not None and int(current_price) > 0:
            price = int(current_price)
    except (TypeError, ValueError):
        price = None

    to_high_pct = None
    if price is not None and box_high and box_high > 0:
        to_high_pct = round((price - box_high) / box_high * 100.0, 2)

    to_low_pct = None
    if price is not None and box_low and box_low > 0:
        to_low_pct = round((price - box_low) / box_low * 100.0, 2)

    checks = evaled.get("checks") or []
    fail_keys = _failed_check_keys(checks)

    return {
        "stock_code": stock_code,
        "stock_name": stock_name or stock_code,
        "condition_name": condition_name or "",
        "stage": str(evaled.get("stage") or "NONE"),
        "reason": evaled.get("reason") or "",
        "price": price,
        "change_rate": change_rate,
        "box_width_pct": round(width, 2) if width is not None else None,
        "box_limit_pct": limit,
        "width_over_pct": width_over,
        "box_high": int(box_high) if box_high else None,
        "box_low": int(box_low) if box_low else None,
        "to_high_pct": to_high_pct,
        "to_low_pct": to_low_pct,
        "fail_keys": fail_keys,
        "checks": checks,
    }


def _sort_filtered(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """폭 초과 큰 순 → 고점 근접(덜 음수) 순."""

    def key(r: Dict[str, Any]):
        over = r.get("width_over_pct")
        to_h = r.get("to_high_pct")
        return (
            -(over if over is not None else -999),
            -(to_h if to_h is not None else -999),
            str(r.get("stock_name") or ""),
        )

    return sorted(rows, key=key)


def build_report_from_rows(
    rows: Sequence[Dict[str, Any]],
    *,
    day: Optional[date] = None,
    box_limit_pct: Optional[float] = None,
    condition_names: Optional[List[str]] = None,
    errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    day = day or kst_today()
    stage_counts: Dict[str, int] = {k: 0 for k in _STAGE_ORDER}
    for r in rows:
        st = str(r.get("stage") or "NONE").upper()
        if st not in stage_counts:
            stage_counts[st] = 0
        stage_counts[st] += 1

    filtered = _sort_filtered([r for r in rows if r.get("stage") == "FILTERED"])
    ready = [r for r in rows if r.get("stage") == "READY"]
    armed = [r for r in rows if r.get("stage") == "ARMED"]

    width_over_vals = [
        float(r["width_over_pct"])
        for r in filtered
        if r.get("width_over_pct") is not None
    ]
    to_high_vals = [
        float(r["to_high_pct"])
        for r in filtered
        if r.get("to_high_pct") is not None
    ]

    return {
        "day": day.isoformat(),
        "total": len(rows),
        "stage_counts": [(k, stage_counts[k]) for k in _STAGE_ORDER if stage_counts.get(k)],
        "box_limit_pct": box_limit_pct,
        "condition_names": list(condition_names or []),
        "errors": list(errors or []),
        "items": list(rows),
        "filtered": filtered,
        "ready": ready,
        "armed": armed,
        "filtered_count": len(filtered),
        "ready_count": len(ready),
        "armed_count": len(armed),
        "filtered_width_over_avg": (
            round(sum(width_over_vals) / len(width_over_vals), 2) if width_over_vals else None
        ),
        "filtered_to_high_avg": (
            round(sum(to_high_vals) / len(to_high_vals), 2) if to_high_vals else None
        ),
        "has_candidates": bool(rows),
    }


async def collect_ymgp_eod_report(
    session: Session,
    kiwoom_api: Any,
    *,
    day: Optional[date] = None,
    limit: int = 40,
    pause_sec: float = 0.15,
) -> Dict[str, Any]:
    """조건식 후보를 일봉 재판정해 장후 리포트 dict 생성."""
    from utils.screener_targets import fetch_condition_target_items, parse_condition_names

    day = day or kst_today()
    settings = session.query(AutoTradeSettings).first()
    names = parse_condition_names(getattr(settings, "ymgp_condition_names", None) if settings else None)
    box_limit = _box_limit(settings)

    if not names:
        return build_report_from_rows(
            [],
            day=day,
            box_limit_pct=box_limit,
            condition_names=[],
            errors=["역매공파 조건식 미설정"],
        )

    rows_raw, errors = await fetch_condition_target_items(kiwoom_api, names, pause_sec=0.3)
    out_rows: List[Dict[str, Any]] = []
    normalize = getattr(kiwoom_api, "normalize_stock_code", lambda c: c)

    for item in (rows_raw or [])[: min(max(int(limit), 1), 60)]:
        code = str(item.get("stock_code") or "")
        if not code:
            continue
        try:
            price = int(item.get("current_price") or 0)
        except (TypeError, ValueError):
            price = 0
        try:
            change_rate = (
                float(item.get("change_rate"))
                if item.get("change_rate") is not None
                else None
            )
        except (TypeError, ValueError):
            change_rate = None

        bars = await kiwoom_api.get_stock_chart_data(
            normalize(code),
            "1D",
            max_bars=520,
            allow_off_hours=True,
        )
        prior = get_stock_state(code)
        evaled = evaluate_ymgp_from_daily(
            bars or [],
            settings,
            current_price=price,
            change_rate=change_rate,
            prior_stage=prior.get("stage"),
            stopped_lock=is_reentry_locked(code, settings),
        )
        out_rows.append(
            enrich_ymgp_row(
                stock_code=code,
                stock_name=str(item.get("stock_name") or code),
                current_price=price,
                change_rate=change_rate,
                evaled=evaled,
                settings=settings,
                condition_name=str(item.get("condition_name") or ""),
            )
        )
        if pause_sec:
            await asyncio.sleep(pause_sec)

    return build_report_from_rows(
        out_rows,
        day=day,
        box_limit_pct=box_limit,
        condition_names=names,
        errors=list(errors or []),
    )
