"""DB/API datetime — naive UTC 저장값을 KST 표시용 ISO(Z)로 직렬화."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

KST = timezone(timedelta(hours=9))


def utc_naive_to_api_iso(dt: Optional[datetime]) -> Optional[str]:
    """DB에 timezone 없이 UTC로 저장된 값 → API ISO(Z)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def kst_naive_to_api_iso(dt: Optional[datetime]) -> Optional[str]:
    """DB에 timezone 없이 KST로 저장된 값(레거시 detected_at) → API ISO(Z)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    return dt.replace(tzinfo=KST).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def migrate_pending_detected_at_kst_to_utc(session) -> int:
    """signal_manager가 datetime.now()(KST)로 저장한 detected_at을 UTC naive로 일괄 변환."""
    from core.models import PendingBuySignal

    rows = session.query(PendingBuySignal).filter(PendingBuySignal.detected_at.isnot(None)).all()
    n = 0
    for row in rows:
        dt = row.detected_at
        if dt is None or dt.tzinfo is not None:
            continue
        row.detected_at = dt - timedelta(hours=9)
        n += 1
    if n:
        session.commit()
    return n
