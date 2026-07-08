"""KRX 휴장일 — DB 저장 + 캐시. market_hours에서 사용."""
from __future__ import annotations

import os
import threading
from datetime import date, datetime
from typing import Dict, List, Optional, Set, Tuple

_lock = threading.Lock()
_dates_by_year: Dict[int, Set[date]] = {}
_names_by_date: Dict[date, str] = {}

# 최초 DB 비어 있을 때만 삽입 (기존 행은 덮어쓰지 않음)
SEED_2026: Tuple[Tuple[str, str], ...] = (
    ("2026-01-01", "신정"),
    ("2026-02-16", "설날"),
    ("2026-02-17", "설날"),
    ("2026-02-18", "설날"),
    ("2026-03-02", "삼일절(대체)"),
    ("2026-05-01", "근로자의날"),
    ("2026-05-05", "어린이날"),
    ("2026-05-25", "부처님오신날(대체)"),
    ("2026-06-03", "지방선거"),
    ("2026-07-17", "제헌절"),
    ("2026-08-15", "광복절"),
    ("2026-08-17", "광복절(대체)"),
    ("2026-09-24", "추석"),
    ("2026-09-25", "추석"),
    ("2026-09-26", "추석"),
    ("2026-10-03", "개천절"),
    ("2026-10-05", "개천절(대체)"),
    ("2026-10-09", "한글날"),
    ("2026-12-25", "성탄절"),
    ("2026-12-31", "연말휴장"),
)


def invalidate_holiday_cache() -> None:
    with _lock:
        _dates_by_year.clear()
        _names_by_date.clear()


def _parse_env_extra() -> Set[date]:
    raw = os.getenv("KRX_EXTRA_HOLIDAYS", "")
    out: Set[date] = set()
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        try:
            out.add(datetime.strptime(part, "%Y-%m-%d").date())
        except ValueError:
            continue
    return out


def _load_from_db() -> None:
    from core.models import KrxHoliday, get_db

    dates_by_year: Dict[int, Set[date]] = {}
    names: Dict[date, str] = {}
    for db in get_db():
        rows = (
            db.query(KrxHoliday)
            .filter(KrxHoliday.is_closed == True)  # noqa: E712
            .order_by(KrxHoliday.holiday_date)
            .all()
        )
        for row in rows:
            d = row.holiday_date
            if not d:
                continue
            dates_by_year.setdefault(d.year, set()).add(d)
            names[d] = row.name or "휴장"
        break
    for d in _parse_env_extra():
        dates_by_year.setdefault(d.year, set()).add(d)
        names.setdefault(d, "추가휴장")
    with _lock:
        _dates_by_year.clear()
        _dates_by_year.update(dates_by_year)
        _names_by_date.clear()
        _names_by_date.update(names)


def _ensure_loaded() -> None:
    with _lock:
        if _dates_by_year:
            return
    _load_from_db()


def holiday_dates_for_year(year: int) -> Set[date]:
    _ensure_loaded()
    with _lock:
        return set(_dates_by_year.get(year, ()))


def holiday_label(day: date) -> Optional[str]:
    _ensure_loaded()
    with _lock:
        return _names_by_date.get(day)


def is_holiday(day: date) -> bool:
    return day in holiday_dates_for_year(day.year)


def seed_default_holidays() -> int:
    """DB에 없는 기본 휴장일만 삽입. 추가된 행 수."""
    from core.models import KrxHoliday, get_db

    added = 0
    for db in get_db():
        existing = {
            r.holiday_date
            for r in db.query(KrxHoliday.holiday_date).all()
            if r.holiday_date
        }
        for ds, name in SEED_2026:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
            if d in existing:
                continue
            db.add(
                KrxHoliday(
                    holiday_date=d,
                    name=name,
                    is_closed=True,
                    source="seed",
                )
            )
            added += 1
        if added:
            db.commit()
        break
    if added:
        invalidate_holiday_cache()
    return added


def list_holidays(year: Optional[int] = None) -> List[dict]:
    from core.models import KrxHoliday, get_db

    out: List[dict] = []
    for db in get_db():
        q = db.query(KrxHoliday).order_by(KrxHoliday.holiday_date)
        if year is not None:
            start = date(year, 1, 1)
            end = date(year, 12, 31)
            q = q.filter(KrxHoliday.holiday_date >= start, KrxHoliday.holiday_date <= end)
        for row in q.all():
            out.append(
                {
                    "id": row.id,
                    "holiday_date": row.holiday_date.isoformat(),
                    "name": row.name,
                    "is_closed": bool(row.is_closed),
                    "source": row.source or "manual",
                }
            )
        break
    return out


def add_holiday(holiday_date: date, name: str, *, is_closed: bool = True) -> dict:
    from core.models import KrxHoliday, get_db

    for db in get_db():
        row = db.query(KrxHoliday).filter(KrxHoliday.holiday_date == holiday_date).first()
        if row:
            row.name = name
            row.is_closed = is_closed
            row.source = "manual"
            row.updated_at = datetime.utcnow()
        else:
            row = KrxHoliday(
                holiday_date=holiday_date,
                name=name,
                is_closed=is_closed,
                source="manual",
            )
            db.add(row)
        db.commit()
        db.refresh(row)
        invalidate_holiday_cache()
        return {
            "id": row.id,
            "holiday_date": row.holiday_date.isoformat(),
            "name": row.name,
            "is_closed": bool(row.is_closed),
            "source": row.source,
        }
    raise RuntimeError("DB unavailable")


def delete_holiday(holiday_id: int) -> bool:
    from core.models import KrxHoliday, get_db

    for db in get_db():
        row = db.query(KrxHoliday).filter(KrxHoliday.id == holiday_id).first()
        if not row:
            return False
        db.delete(row)
        db.commit()
        invalidate_holiday_cache()
        return True
    return False
