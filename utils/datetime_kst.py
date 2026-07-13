"""KST 비즈니스 시각 + DB(UTC naive) 변환 — 장 운영·일자 집계는 KST, 저장은 UTC."""
from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Optional

KST = timezone(timedelta(hours=9))
UTC = timezone.utc


def now_kst() -> datetime:
    """현재 시각 (KST, timezone-aware)."""
    return datetime.now(KST)


def kst_today() -> date:
    """KST 기준 오늘 날짜."""
    return now_kst().date()


def as_kst(dt: Optional[datetime] = None) -> datetime:
    """비교·장시간 판단용 — naive는 KST 벽시계로 해석."""
    if dt is None:
        return now_kst()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def utc_now_naive() -> datetime:
    """DB 저장용 UTC naive (datetime.utcnow 대체)."""
    return datetime.now(UTC).replace(tzinfo=None)


def kst_day_start_utc_naive(day: Optional[date] = None) -> datetime:
    """KST 해당일 00:00 → UTC naive (DB 시각 컬럼 하한)."""
    day = day or kst_today()
    start_kst = datetime.combine(day, dt_time.min, tzinfo=KST)
    return start_kst.astimezone(UTC).replace(tzinfo=None)


def kst_day_end_utc_naive_exclusive(day: Optional[date] = None) -> datetime:
    """KST 다음날 00:00 → UTC naive (DB 시각 컬럼 상한, 미포함)."""
    day = day or kst_today()
    next_day = day + timedelta(days=1)
    start_kst = datetime.combine(next_day, dt_time.min, tzinfo=KST)
    return start_kst.astimezone(UTC).replace(tzinfo=None)


def kst_date_str(day: Optional[date] = None) -> str:
    """YYYY-MM-DD (KST)."""
    day = day or kst_today()
    return day.isoformat()


def kst_now_iso(timespec: str = "seconds") -> str:
    """활동 로그·런타임 표시용 KST ISO (+09:00)."""
    return now_kst().isoformat(timespec=timespec)


def utc_naive_to_api_iso(dt: Optional[datetime]) -> Optional[str]:
    """DB에 timezone 없이 UTC로 저장된 값 → API ISO(Z)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def kst_naive_to_api_iso(dt: Optional[datetime]) -> Optional[str]:
    """DB에 timezone 없이 KST로 저장된 값(레거시 detected_at) → API ISO(Z)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    return dt.replace(tzinfo=KST).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


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
