"""기본적분석 마트 — DB CRUD / upsert / 조회."""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from core.models import FundamentalSnapshot, get_db

_PRESERVE_ON_NULL = {
    "current_price", "market_cap", "volume", "per", "pbr", "roe", "eps",
    "dividend_per_share", "listed_shares", "foreign_ratio", "trading_value",
    "total_assets", "total_debt", "revenue", "operating_profit",
}


def _row_to_dict(row: FundamentalSnapshot) -> dict:
    return {
        "id": row.id,
        "stock_code": row.stock_code,
        "stock_name": row.stock_name,
        "as_of_date": row.as_of_date.isoformat() if row.as_of_date else None,
        "market": row.market,
        "current_price": row.current_price,
        "market_cap": row.market_cap,
        "volume": row.volume,
        "per": row.per,
        "pbr": row.pbr,
        "roe": row.roe,
        "eps": row.eps,
        "dividend_per_share": row.dividend_per_share,
        "listed_shares": row.listed_shares,
        "foreign_ratio": row.foreign_ratio,
        "trading_value": row.trading_value,
        "total_assets": row.total_assets,
        "total_debt": row.total_debt,
        "revenue": row.revenue,
        "operating_profit": row.operating_profit,
        "source": row.source,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
    }


def upsert_snapshot(db: Session, data: dict, as_of_date: date) -> FundamentalSnapshot:
    """종목코드+기준일 기준 upsert."""
    code = str(data["stock_code"]).strip()
    row = (
        db.query(FundamentalSnapshot)
        .filter(
            FundamentalSnapshot.stock_code == code,
            FundamentalSnapshot.as_of_date == as_of_date,
        )
        .first()
    )
    now = datetime.utcnow()
    fields = {
        "stock_name": data.get("stock_name") or "",
        "market": data.get("market"),
        "current_price": data.get("current_price"),
        "market_cap": data.get("market_cap"),
        "volume": data.get("volume"),
        "per": data.get("per"),
        "pbr": data.get("pbr"),
        "roe": data.get("roe"),
        "eps": data.get("eps"),
        "dividend_per_share": data.get("dividend_per_share"),
        "listed_shares": data.get("listed_shares"),
        "foreign_ratio": data.get("foreign_ratio"),
        "trading_value": data.get("trading_value"),
        "total_assets": data.get("total_assets"),
        "total_debt": data.get("total_debt"),
        "revenue": data.get("revenue"),
        "operating_profit": data.get("operating_profit"),
        "source": data.get("source") or "naver",
        "fetched_at": now,
    }
    if row:
        for k, v in fields.items():
            if v is None and k in _PRESERVE_ON_NULL and getattr(row, k) is not None:
                continue
            setattr(row, k, v)
    else:
        row = FundamentalSnapshot(stock_code=code, as_of_date=as_of_date, **fields)
        db.add(row)
    return row


def upsert_many(rows: List[dict], as_of_date: Optional[date] = None) -> int:
    """배치 upsert. 반환: 처리 건수."""
    if as_of_date is None:
        as_of_date = date.today()
    count = 0
    for db in get_db():
        for data in rows:
            upsert_snapshot(db, data, as_of_date)
            count += 1
        db.commit()
        break
    return count


def latest_as_of_date() -> Optional[date]:
    for db in get_db():
        row = (
            db.query(FundamentalSnapshot.as_of_date)
            .order_by(FundamentalSnapshot.as_of_date.desc())
            .first()
        )
        return row[0] if row else None
    return None


def get_latest_by_code(stock_code: str) -> Optional[dict]:
    code = str(stock_code).strip().zfill(6)
    for db in get_db():
        row = (
            db.query(FundamentalSnapshot)
            .filter(FundamentalSnapshot.stock_code == code)
            .order_by(FundamentalSnapshot.as_of_date.desc())
            .first()
        )
        return _row_to_dict(row) if row else None
    return None


def get_latest_map_by_codes(stock_codes: List[str]) -> Dict[str, dict]:
    """여러 종목코드의 최신 기본 스냅샷 맵 반환."""
    codes = [str(code).strip().zfill(6) for code in stock_codes if str(code or "").strip()]
    if not codes:
        return {}
    out: Dict[str, dict] = {}
    for db in get_db():
        as_of_date = latest_as_of_date()
        if as_of_date is None:
            return {}
        rows = (
            db.query(FundamentalSnapshot)
            .filter(
                FundamentalSnapshot.as_of_date == as_of_date,
                FundamentalSnapshot.stock_code.in_(codes),
            )
            .all()
        )
        return {row.stock_code: _row_to_dict(row) for row in rows}
    return out


def get_summary(as_of_date: Optional[date] = None) -> Dict:
    """최신 스냅샷 현황 (건수·시장별 분포)."""
    empty = {"as_of_date": None, "total": 0, "kospi": 0, "kosdaq": 0}
    for db in get_db():
        if as_of_date is None:
            as_of_date = latest_as_of_date()
        if as_of_date is None:
            return empty
        base = db.query(FundamentalSnapshot).filter(
            FundamentalSnapshot.as_of_date == as_of_date
        )
        return {
            "as_of_date": as_of_date.isoformat(),
            "total": base.count(),
            "kospi": base.filter(FundamentalSnapshot.market == "KOSPI").count(),
            "kosdaq": base.filter(FundamentalSnapshot.market == "KOSDAQ").count(),
        }
    return empty


def list_latest(
    *,
    market: Optional[str] = None,
    as_of_date: Optional[date] = None,
    limit: int = 500,
    offset: int = 0,
    sort_by: str = "market_cap",
    sort_desc: bool = True,
    max_per: Optional[float] = None,
    max_pbr: Optional[float] = None,
    min_roe: Optional[float] = None,
) -> Dict:
    """최신 스냅샷 목록 (기준일 미지정 시 DB 최신일)."""
    for db in get_db():
        if as_of_date is None:
            as_of_date = latest_as_of_date()
        if as_of_date is None:
            return {"as_of_date": None, "count": 0, "items": []}

        q = db.query(FundamentalSnapshot).filter(
            FundamentalSnapshot.as_of_date == as_of_date
        )
        if market:
            q = q.filter(FundamentalSnapshot.market == market.upper())
        if max_per is not None:
            q = q.filter(FundamentalSnapshot.per.isnot(None), FundamentalSnapshot.per <= max_per)
        if max_pbr is not None:
            q = q.filter(FundamentalSnapshot.pbr.isnot(None), FundamentalSnapshot.pbr <= max_pbr)
        if min_roe is not None:
            q = q.filter(FundamentalSnapshot.roe.isnot(None), FundamentalSnapshot.roe >= min_roe)

        sort_col = getattr(FundamentalSnapshot, sort_by, FundamentalSnapshot.market_cap)
        q = q.order_by(sort_col.desc() if sort_desc else sort_col.asc())
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        return {
            "as_of_date": as_of_date.isoformat(),
            "count": total,
            "items": [_row_to_dict(r) for r in rows],
        }
    return {"as_of_date": None, "count": 0, "items": []}


def filter_by_metrics(
    *,
    as_of_date: Optional[date] = None,
    max_per: Optional[float] = None,
    max_pbr: Optional[float] = None,
    min_roe: Optional[float] = None,
    min_dividend_per_share: Optional[float] = None,
    market: Optional[str] = None,
    limit: int = 200,
) -> List[dict]:
    """간단 스크리닝 (스캐너 연동용 v1)."""
    for db in get_db():
        if as_of_date is None:
            as_of_date = latest_as_of_date()
        if as_of_date is None:
            return []

        q = db.query(FundamentalSnapshot).filter(
            FundamentalSnapshot.as_of_date == as_of_date
        )
        if market:
            q = q.filter(FundamentalSnapshot.market == market.upper())
        if max_per is not None:
            q = q.filter(FundamentalSnapshot.per.isnot(None), FundamentalSnapshot.per <= max_per)
        if max_pbr is not None:
            q = q.filter(FundamentalSnapshot.pbr.isnot(None), FundamentalSnapshot.pbr <= max_pbr)
        if min_roe is not None:
            q = q.filter(FundamentalSnapshot.roe.isnot(None), FundamentalSnapshot.roe >= min_roe)
        if min_dividend_per_share is not None:
            q = q.filter(
                FundamentalSnapshot.dividend_per_share.isnot(None),
                FundamentalSnapshot.dividend_per_share >= min_dividend_per_share,
            )
        rows = q.order_by(FundamentalSnapshot.market_cap.desc()).limit(limit).all()
        return [_row_to_dict(r) for r in rows]
    return []
