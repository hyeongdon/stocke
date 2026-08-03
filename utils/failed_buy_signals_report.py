"""당일(KST) FAILED 매수 신호 집계 — 장후 텔레그램용."""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.models import PendingBuySignal
from utils.datetime_kst import kst_today

KST = timezone(timedelta(hours=9))

_LEGACY_KEYS = frozenset(
    {"", "legacy", "scanner", "screener", "condition", "both", "watchlist"}
)


def _is_sample(code: Optional[str]) -> bool:
    return str(code or "").startswith("SAMPLE_")


def _fmt_hhmm(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%H:%M")


def signal_strategy_key(sig: PendingBuySignal) -> str:
    """additional_data.strategy / source → 전략 키."""
    meta = sig.additional_data if isinstance(getattr(sig, "additional_data", None), dict) else {}
    raw = str(meta.get("strategy") or meta.get("source") or "").strip().lower()
    if raw in ("sangtta", "breakout", "ymgp"):
        return raw
    if raw in _LEGACY_KEYS:
        return "legacy"
    return raw or "legacy"


def _reason_bucket(reason: Optional[str]) -> str:
    text = " ".join(str(reason or "").split()).strip()
    if not text:
        return "(사유 없음)"
    if len(text) > 80:
        return text[:77] + "…"
    return text


def collect_failed_buy_signals(
    session: Session,
    *,
    day: Optional[date] = None,
) -> Dict[str, Any]:
    """지정일(KST) FAILED 매수 신호 목록 + 전략/사유 집계."""
    day = day or kst_today()
    rows = (
        session.query(PendingBuySignal)
        .filter(
            PendingBuySignal.status == "FAILED",
            PendingBuySignal.detected_date == day,
        )
        .order_by(PendingBuySignal.detected_at.asc())
        .all()
    )

    items: List[Dict[str, Any]] = []
    by_strategy: Dict[str, int] = {}
    by_reason: Dict[str, int] = {}

    for sig in rows:
        if _is_sample(sig.stock_code):
            continue
        strategy = signal_strategy_key(sig)
        reason = _reason_bucket(sig.failure_reason)
        by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1
        items.append(
            {
                "id": int(sig.id),
                "stock_code": sig.stock_code,
                "stock_name": sig.stock_name,
                "strategy": strategy,
                "reason": reason,
                "time": _fmt_hhmm(sig.detected_at),
                "signal_type": sig.signal_type,
            }
        )

    strategy_counts: List[Tuple[str, int]] = sorted(
        by_strategy.items(), key=lambda x: (-x[1], x[0])
    )
    reason_counts: List[Tuple[str, int]] = sorted(
        by_reason.items(), key=lambda x: (-x[1], x[0])
    )

    return {
        "day": day.isoformat(),
        "count": len(items),
        "items": items,
        "strategy_counts": strategy_counts,
        "reason_counts": reason_counts,
        "has_failures": bool(items),
    }
