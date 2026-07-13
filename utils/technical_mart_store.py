"""기술적분석 마트 — DB CRUD / upsert / 조회."""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from core.models import TechnicalSnapshot, get_db
from utils.datetime_kst import kst_today, utc_now_naive


def _row_to_dict(row: TechnicalSnapshot) -> dict:
    return {
        "id": row.id,
        "stock_code": row.stock_code,
        "stock_name": row.stock_name,
        "as_of_date": row.as_of_date.isoformat() if row.as_of_date else None,
        "market": row.market,
        "timeframe": row.timeframe,
        "open_price": row.open_price,
        "high_price": row.high_price,
        "low_price": row.low_price,
        "close_price": row.close_price,
        "volume": row.volume,
        "trading_value": row.trading_value,
        "return_1d": row.return_1d,
        "return_5d": row.return_5d,
        "return_20d": row.return_20d,
        "ma5": row.ma5,
        "ma20": row.ma20,
        "ma60": row.ma60,
        "ma120": row.ma120,
        "ma5_bias": row.ma5_bias,
        "ma20_bias": row.ma20_bias,
        "rsi14": row.rsi14,
        "atr14": row.atr14,
        "atr14_pct": row.atr14_pct,
        "high_20d": row.high_20d,
        "low_20d": row.low_20d,
        "pos_20d": row.pos_20d,
        "avg_volume_20d": row.avg_volume_20d,
        "avg_trading_value_20d": row.avg_trading_value_20d,
        "source": row.source,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
    }


def upsert_snapshot(db: Session, data: dict, as_of_date: date, timeframe: str = "1D") -> TechnicalSnapshot:
    """종목코드+기준일+타임프레임 기준 upsert."""
    code = str(data["stock_code"]).strip().zfill(6)
    tf = (timeframe or "1D").upper()
    row = (
        db.query(TechnicalSnapshot)
        .filter(
            TechnicalSnapshot.stock_code == code,
            TechnicalSnapshot.as_of_date == as_of_date,
            TechnicalSnapshot.timeframe == tf,
        )
        .first()
    )
    now = utc_now_naive()
    fields = {
        "stock_name": data.get("stock_name") or "",
        "market": data.get("market"),
        "timeframe": tf,
        "open_price": data.get("open_price"),
        "high_price": data.get("high_price"),
        "low_price": data.get("low_price"),
        "close_price": data.get("close_price"),
        "volume": data.get("volume"),
        "trading_value": data.get("trading_value"),
        "return_1d": data.get("return_1d"),
        "return_5d": data.get("return_5d"),
        "return_20d": data.get("return_20d"),
        "ma5": data.get("ma5"),
        "ma20": data.get("ma20"),
        "ma60": data.get("ma60"),
        "ma120": data.get("ma120"),
        "ma5_bias": data.get("ma5_bias"),
        "ma20_bias": data.get("ma20_bias"),
        "rsi14": data.get("rsi14"),
        "atr14": data.get("atr14"),
        "atr14_pct": data.get("atr14_pct"),
        "high_20d": data.get("high_20d"),
        "low_20d": data.get("low_20d"),
        "pos_20d": data.get("pos_20d"),
        "avg_volume_20d": data.get("avg_volume_20d"),
        "avg_trading_value_20d": data.get("avg_trading_value_20d"),
        "source": data.get("source") or "kiwoom",
        "fetched_at": now,
    }
    if row:
        for k, v in fields.items():
            setattr(row, k, v)
    else:
        row = TechnicalSnapshot(stock_code=code, as_of_date=as_of_date, **fields)
        db.add(row)
    return row


def upsert_many(rows: List[dict], as_of_date: Optional[date] = None, timeframe: str = "1D") -> int:
    """배치 upsert. 반환: 처리 건수."""
    if as_of_date is None:
        as_of_date = kst_today()
    count = 0
    for db in get_db():
        for data in rows:
            upsert_snapshot(db, data, as_of_date, timeframe=timeframe)
            count += 1
        db.commit()
        break
    return count


def latest_as_of_date(timeframe: str = "1D") -> Optional[date]:
    tf = (timeframe or "1D").upper()
    for db in get_db():
        row = (
            db.query(TechnicalSnapshot.as_of_date)
            .filter(TechnicalSnapshot.timeframe == tf)
            .order_by(TechnicalSnapshot.as_of_date.desc())
            .first()
        )
        return row[0] if row else None
    return None


def get_summary(as_of_date: Optional[date] = None, timeframe: str = "1D") -> Dict:
    """최신 스냅샷 현황 (건수·시장별 분포)."""
    tf = (timeframe or "1D").upper()
    empty = {"as_of_date": None, "timeframe": tf, "total": 0, "kospi": 0, "kosdaq": 0}
    for db in get_db():
        if as_of_date is None:
            as_of_date = latest_as_of_date(tf)
        if as_of_date is None:
            return empty
        base = db.query(TechnicalSnapshot).filter(
            TechnicalSnapshot.as_of_date == as_of_date,
            TechnicalSnapshot.timeframe == tf,
        )
        return {
            "as_of_date": as_of_date.isoformat(),
            "timeframe": tf,
            "total": base.count(),
            "kospi": base.filter(TechnicalSnapshot.market == "KOSPI").count(),
            "kosdaq": base.filter(TechnicalSnapshot.market == "KOSDAQ").count(),
        }
    return empty


def get_latest_map_by_codes(stock_codes: List[str], timeframe: str = "1D") -> Dict[str, dict]:
    """여러 종목코드의 최신 기술 스냅샷 맵 반환."""
    codes = [str(code).strip().zfill(6) for code in stock_codes if str(code or "").strip()]
    if not codes:
        return {}
    tf = (timeframe or "1D").upper()
    out: Dict[str, dict] = {}
    for db in get_db():
        as_of_date = latest_as_of_date(tf)
        if as_of_date is None:
            return {}
        rows = (
            db.query(TechnicalSnapshot)
            .filter(
                TechnicalSnapshot.as_of_date == as_of_date,
                TechnicalSnapshot.timeframe == tf,
                TechnicalSnapshot.stock_code.in_(codes),
            )
            .all()
        )
        return {row.stock_code: _row_to_dict(row) for row in rows}
    return out


def list_latest(
    *,
    market: Optional[str] = None,
    as_of_date: Optional[date] = None,
    timeframe: str = "1D",
    limit: int = 500,
    offset: int = 0,
    sort_by: str = "trading_value",
    sort_desc: bool = True,
    min_rsi: Optional[float] = None,
    max_rsi: Optional[float] = None,
    min_return_20d: Optional[float] = None,
) -> Dict:
    """최신 기술적분석 스냅샷 목록."""
    tf = (timeframe or "1D").upper()
    for db in get_db():
        if as_of_date is None:
            as_of_date = latest_as_of_date(tf)
        if as_of_date is None:
            return {"as_of_date": None, "timeframe": tf, "count": 0, "items": []}

        q = db.query(TechnicalSnapshot).filter(
            TechnicalSnapshot.as_of_date == as_of_date,
            TechnicalSnapshot.timeframe == tf,
        )
        if market:
            q = q.filter(TechnicalSnapshot.market == market.upper())
        if min_rsi is not None:
            q = q.filter(TechnicalSnapshot.rsi14.isnot(None), TechnicalSnapshot.rsi14 >= min_rsi)
        if max_rsi is not None:
            q = q.filter(TechnicalSnapshot.rsi14.isnot(None), TechnicalSnapshot.rsi14 <= max_rsi)
        if min_return_20d is not None:
            q = q.filter(
                TechnicalSnapshot.return_20d.isnot(None),
                TechnicalSnapshot.return_20d >= min_return_20d,
            )

        sort_col = getattr(TechnicalSnapshot, sort_by, TechnicalSnapshot.trading_value)
        q = q.order_by(sort_col.desc() if sort_desc else sort_col.asc())
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        return {
            "as_of_date": as_of_date.isoformat(),
            "timeframe": tf,
            "count": total,
            "items": [_row_to_dict(r) for r in rows],
        }
    return {"as_of_date": None, "timeframe": tf, "count": 0, "items": []}


def get_daily_bars_for_code(
    stock_code: str,
    *,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    timeframe: str = "1D",
) -> List[dict]:
    """종목별 일봉 OHLCV — as_of_date 오름차순."""
    code = str(stock_code or "").strip().zfill(6)
    if not code:
        return []
    tf = (timeframe or "1D").upper()
    for db in get_db():
        q = db.query(TechnicalSnapshot).filter(
            TechnicalSnapshot.stock_code == code,
            TechnicalSnapshot.timeframe == tf,
        )
        if start_date is not None:
            q = q.filter(TechnicalSnapshot.as_of_date >= start_date)
        if end_date is not None:
            q = q.filter(TechnicalSnapshot.as_of_date <= end_date)
        rows = q.order_by(TechnicalSnapshot.as_of_date.asc()).all()
        out: List[dict] = []
        for row in rows:
            d = _row_to_dict(row)
            d["date"] = row.as_of_date.isoformat()
            d["open"] = row.open_price
            d["high"] = row.high_price
            d["low"] = row.low_price
            d["close"] = row.close_price
            out.append(d)
        return out
    return []

