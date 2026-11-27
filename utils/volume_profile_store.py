"""매물대 마트 — 일봉 마트 봉으로 계산한 매물대 저장·근접 필터."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from core.models import VolumeProfileSnapshot, ensure_volume_profile_table, get_db
from utils.chart_ohlcv_mart_store import normalize_bars
from utils.datetime_kst import kst_today, utc_now_naive
from utils.volume_profile import compute_profiles

PROFILE_INTERVALS = ("1D", "1W")


def _upsert(
    session: Session,
    code: str,
    name: str,
    interval: str,
    prof: Dict[str, Any],
    as_of_date: date,
) -> None:
    row = (
        session.query(VolumeProfileSnapshot)
        .filter(
            VolumeProfileSnapshot.stock_code == code,
            VolumeProfileSnapshot.interval == interval,
            VolumeProfileSnapshot.lookback == prof["lookback"],
        )
        .first()
    )
    fields = {k: v for k, v in prof.items() if k not in ("lookback", "zones")}
    fields.update(
        stock_name=name or (row.stock_name if row else ""),
        as_of_date=as_of_date,
        zones_json=prof["zones"],
        computed_at=utc_now_naive(),
    )
    if row:
        for k, v in fields.items():
            setattr(row, k, v)
    else:
        session.add(
            VolumeProfileSnapshot(
                stock_code=code, interval=interval, lookback=prof["lookback"], **fields
            )
        )


def upsert_from_rows(rows: Sequence[Dict[str, Any]], as_of_date: Optional[date] = None) -> int:
    """마트 배치 row(stock_code, interval, bars) 목록으로 매물대 계산·저장. 일봉 → 주봉 순서."""
    ensure_volume_profile_table()
    as_of_date = as_of_date or kst_today()
    count = 0
    for db in get_db():
        for interval in PROFILE_INTERVALS:
            for data in rows:
                if str(data.get("interval") or "").upper() != interval:
                    continue
                code = str(data.get("stock_code") or "")
                bars = normalize_bars(data.get("bars") or [])
                for prof in compute_profiles(bars, interval):
                    _upsert(db, code, data.get("stock_name") or "", interval, prof, as_of_date)
                    count += 1
            db.commit()
        break
    return count


def _row_dict(r: VolumeProfileSnapshot) -> Dict[str, Any]:
    return {
        "stock_code": r.stock_code,
        "stock_name": r.stock_name,
        "interval": r.interval,
        "lookback": r.lookback,
        "as_of_date": r.as_of_date.isoformat() if r.as_of_date else None,
        "last_bar_date": r.last_bar_date,
        "close_price": r.close_price,
        "poc_price": r.poc_price,
        "poc_gap_pct": r.poc_gap_pct,
        "resist_low": r.resist_low,
        "resist_high": r.resist_high,
        "resist_share": r.resist_share,
        "resist_gap_pct": r.resist_gap_pct,
        "support_low": r.support_low,
        "support_high": r.support_high,
        "support_share": r.support_share,
        "support_gap_pct": r.support_gap_pct,
        "in_zone": bool(r.in_zone),
        "downtrend": bool(r.downtrend),
        "zones": r.zones_json or [],
    }


def get_profiles(stock_code: str) -> List[Dict[str, Any]]:
    ensure_volume_profile_table()
    for db in get_db():
        rows = (
            db.query(VolumeProfileSnapshot)
            .filter(VolumeProfileSnapshot.stock_code == str(stock_code).zfill(6))
            .order_by(VolumeProfileSnapshot.interval, VolumeProfileSnapshot.lookback)
            .all()
        )
        return [_row_dict(r) for r in rows]
    return []


def get_map_by_codes(
    codes: Sequence[str], interval: str = "1D", lookback: int = 120
) -> Dict[str, Dict[str, Any]]:
    if not codes:
        return {}
    ensure_volume_profile_table()
    for db in get_db():
        rows = (
            db.query(VolumeProfileSnapshot)
            .filter(
                VolumeProfileSnapshot.interval == interval.upper(),
                VolumeProfileSnapshot.lookback == int(lookback),
                VolumeProfileSnapshot.stock_code.in_(list(codes)),
            )
            .all()
        )
        return {r.stock_code: _row_dict(r) for r in rows}
    return {}


def find_resistance_near(
    interval: str = "1D",
    lookback: int = 120,
    *,
    max_gap_pct: float = 3.0,
    min_share: float = 5.0,
    downtrend_only: bool = True,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """현재가 위 매물대 하단까지 0~max_gap_pct% 남은 종목 (하락추세 옵션)."""
    ensure_volume_profile_table()
    for db in get_db():
        q = db.query(VolumeProfileSnapshot).filter(
            VolumeProfileSnapshot.interval == interval.upper(),
            VolumeProfileSnapshot.lookback == int(lookback),
            VolumeProfileSnapshot.resist_gap_pct.isnot(None),
            VolumeProfileSnapshot.resist_gap_pct >= 0,
            VolumeProfileSnapshot.resist_gap_pct <= float(max_gap_pct),
            VolumeProfileSnapshot.resist_share >= float(min_share),
        )
        if downtrend_only:
            q = q.filter(VolumeProfileSnapshot.downtrend.is_(True))
        rows = q.order_by(VolumeProfileSnapshot.resist_gap_pct.asc()).limit(int(limit)).all()
        return [_row_dict(r) for r in rows]
    return []


def find_poc_approach(
    interval: str = "1D",
    lookback: int = 120,
    *,
    max_gap_pct: float = 3.0,
    downtrend_only: bool = True,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """현재가 아래 POC 까지 0~max_gap_pct% 남은 종목 (하락추세 옵션)."""
    ensure_volume_profile_table()
    for db in get_db():
        q = db.query(VolumeProfileSnapshot).filter(
            VolumeProfileSnapshot.interval == interval.upper(),
            VolumeProfileSnapshot.lookback == int(lookback),
            VolumeProfileSnapshot.poc_gap_pct.isnot(None),
            VolumeProfileSnapshot.poc_gap_pct <= 0,
            VolumeProfileSnapshot.poc_gap_pct >= -float(max_gap_pct),
        )
        if downtrend_only:
            q = q.filter(VolumeProfileSnapshot.downtrend.is_(True))
        rows = q.order_by(VolumeProfileSnapshot.poc_gap_pct.desc()).limit(int(limit)).all()
        return [_row_dict(r) for r in rows]
    return []
