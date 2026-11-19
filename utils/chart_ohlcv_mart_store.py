"""차트 OHLCV 마트 — 종목×타임프레임 봉 시리즈 CRUD."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from core.models import ChartOhlcvSeries, ensure_chart_ohlcv_series_table, get_db
from utils.datetime_kst import kst_today, utc_now_naive

VALID_INTERVALS = ("1D", "1W", "1MO")


def _norm_code(stock_code: str) -> str:
    s = str(stock_code or "").strip()
    if s[:1] in ("A", "a"):
        s = s[1:]
    return s.zfill(6) if s.isdigit() else ""


def _norm_interval(interval: str) -> str:
    raw = (interval or "1D").strip().upper()
    if raw in {"1MO", "MO", "MONTH", "MONTHLY"}:
        return "1MO"
    if raw in {"1W", "W", "WEEK", "WEEKLY"}:
        return "1W"
    return "1D"


def _bar_date(ts: Any) -> Optional[str]:
    raw = str(ts or "").strip()
    if len(raw) >= 10 and raw[4] == "-":
        return raw[:10]
    if len(raw) >= 8 and raw[:8].isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return None


def normalize_bars(bars: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """OHLCV dict 정리·정렬. close<=0 제외."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for item in bars or []:
        try:
            ts = _bar_date(item.get("timestamp")) or str(item.get("timestamp") or "").strip()
            row = {
                "timestamp": ts,
                "open": int(float(item.get("open") or 0)),
                "high": int(float(item.get("high") or 0)),
                "low": int(float(item.get("low") or 0)),
                "close": int(float(item.get("close") or 0)),
                "volume": int(float(item.get("volume") or 0)),
            }
        except (TypeError, ValueError):
            continue
        if row["close"] <= 0 or not row["timestamp"] or row["timestamp"] in seen:
            continue
        seen.add(row["timestamp"])
        out.append(row)
    out.sort(key=lambda r: r["timestamp"])
    return out


def _row_meta(row: ChartOhlcvSeries) -> Dict[str, Any]:
    return {
        "stock_code": row.stock_code,
        "stock_name": row.stock_name,
        "interval": row.interval,
        "as_of_date": row.as_of_date.isoformat() if row.as_of_date else None,
        "bar_count": row.bar_count,
        "first_bar_date": row.first_bar_date,
        "last_bar_date": row.last_bar_date,
        "source": row.source,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
    }


def upsert_series(
    stock_code: str,
    interval: str,
    bars: Sequence[Dict[str, Any]],
    *,
    stock_name: str = "",
    as_of_date: Optional[date] = None,
    source: str = "kiwoom",
    db: Optional[Session] = None,
) -> Optional[ChartOhlcvSeries]:
    """종목×인터벌 시리즈 upsert. bars 비면 저장하지 않음."""
    ensure_chart_ohlcv_series_table()
    code = _norm_code(stock_code)
    tf = _norm_interval(interval)
    cleaned = normalize_bars(bars)
    if not code or not cleaned:
        return None
    if as_of_date is None:
        as_of_date = kst_today()
    now = utc_now_naive()
    first_d = _bar_date(cleaned[0]["timestamp"])
    last_d = _bar_date(cleaned[-1]["timestamp"])

    def _write(session: Session) -> ChartOhlcvSeries:
        row = (
            session.query(ChartOhlcvSeries)
            .filter(ChartOhlcvSeries.stock_code == code, ChartOhlcvSeries.interval == tf)
            .first()
        )
        fields = {
            "stock_name": stock_name or (row.stock_name if row else ""),
            "as_of_date": as_of_date,
            "bar_count": len(cleaned),
            "first_bar_date": first_d,
            "last_bar_date": last_d,
            "bars_json": cleaned,
            "source": source or "kiwoom",
            "fetched_at": now,
        }
        if row:
            for k, v in fields.items():
                setattr(row, k, v)
        else:
            row = ChartOhlcvSeries(stock_code=code, interval=tf, **fields)
            session.add(row)
        return row

    if db is not None:
        return _write(db)

    saved = None
    for session in get_db():
        saved = _write(session)
        session.commit()
        break
    return saved


def upsert_many(rows: Sequence[Dict[str, Any]], as_of_date: Optional[date] = None) -> int:
    """배치 upsert. 각 row: stock_code, interval, bars, stock_name?, source?"""
    ensure_chart_ohlcv_series_table()
    if as_of_date is None:
        as_of_date = kst_today()
    count = 0
    for db in get_db():
        for data in rows:
            row = upsert_series(
                data.get("stock_code", ""),
                data.get("interval", "1D"),
                data.get("bars") or [],
                stock_name=data.get("stock_name") or "",
                as_of_date=as_of_date,
                source=data.get("source") or "kiwoom",
                db=db,
            )
            if row:
                count += 1
        db.commit()
        break
    return count


def get_bars(stock_code: str, interval: str = "1D") -> Optional[List[Dict[str, Any]]]:
    """저장된 OHLCV 봉 목록. 없으면 None."""
    ensure_chart_ohlcv_series_table()
    code = _norm_code(stock_code)
    tf = _norm_interval(interval)
    for db in get_db():
        row = (
            db.query(ChartOhlcvSeries)
            .filter(ChartOhlcvSeries.stock_code == code, ChartOhlcvSeries.interval == tf)
            .first()
        )
        if not row:
            return None
        bars = normalize_bars(row.bars_json or [])
        return bars or None
    return None


def get_meta(stock_code: str, interval: str = "1D") -> Optional[Dict[str, Any]]:
    ensure_chart_ohlcv_series_table()
    code = _norm_code(stock_code)
    tf = _norm_interval(interval)
    for db in get_db():
        row = (
            db.query(ChartOhlcvSeries)
            .filter(ChartOhlcvSeries.stock_code == code, ChartOhlcvSeries.interval == tf)
            .first()
        )
        return _row_meta(row) if row else None
    return None


def get_summary() -> Dict[str, Any]:
    """마트 현황 (인터벌별 건수·최신 as_of)."""
    ensure_chart_ohlcv_series_table()
    out: Dict[str, Any] = {"total": 0, "by_interval": {}, "latest_as_of": None}
    for db in get_db():
        rows = db.query(ChartOhlcvSeries).all()
        out["total"] = len(rows)
        latest: Optional[date] = None
        for row in rows:
            bucket = out["by_interval"].setdefault(
                row.interval, {"count": 0, "as_of_date": None}
            )
            bucket["count"] += 1
            if row.as_of_date and (
                bucket["as_of_date"] is None or row.as_of_date.isoformat() > bucket["as_of_date"]
            ):
                bucket["as_of_date"] = row.as_of_date.isoformat()
            if row.as_of_date and (latest is None or row.as_of_date > latest):
                latest = row.as_of_date
        out["latest_as_of"] = latest.isoformat() if latest else None
        return out
    return out


def list_codes(interval: str = "1D") -> List[str]:
    ensure_chart_ohlcv_series_table()
    tf = _norm_interval(interval)
    for db in get_db():
        rows = (
            db.query(ChartOhlcvSeries.stock_code)
            .filter(ChartOhlcvSeries.interval == tf)
            .order_by(ChartOhlcvSeries.stock_code.asc())
            .all()
        )
        return [r[0] for r in rows]
    return []
