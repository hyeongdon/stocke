"""차트 OHLCV 마트 — 종목×타임프레임 봉 시리즈 CRUD."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from core.models import ChartOhlcvSeries, ensure_chart_ohlcv_series_table, get_db
from utils.datetime_kst import kst_today, utc_now_naive

VALID_INTERVALS = ("1D", "1W", "1MO")

# 차트 요청이 DB만으로 응답하려면 필요한 봉 수 (기본 표시 일504·주260·월120 + MA120 워밍업 119)
MIN_DEPTH = {"1D": 623, "1W": 379, "1MO": 239}
# 병합 시 보관 상한 (오래된 봉부터 잘라 JSON 이 무한히 커지지 않게)
MAX_KEEP = {"1D": 1500, "1W": 700, "1MO": 300}
# 키움이 줄 수 있는 이력을 끝까지 받았는데도 MIN_DEPTH 미만(신규 상장 등) → 더 깊게 재시도 불필요
FULL_SOURCE = "kiwoom_full"


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


def _period_key(ts: Any, interval: str) -> Optional[tuple]:
    """봉이 속한 기간 키 — 일봉=날짜, 주봉=ISO 주, 월봉=연월 (주·월봉 timestamp 가 주중에 바뀌어도 같은 기간으로 묶는다)."""
    raw = _bar_date(ts)
    if not raw:
        return None
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        return None
    if interval == "1W":
        iso = d.isocalendar()
        return (iso[0], iso[1])
    if interval == "1MO":
        return (d.year, d.month)
    return (d.isoformat(),)


def merge_bars(
    old: Sequence[Dict[str, Any]],
    new: Sequence[Dict[str, Any]],
    interval: str,
) -> tuple:
    """기존 마트 봉(old) 과 새로 받은 봉(new) 병합 → (bars, merged).

    새로 받은 구간은 new 가 우선하고, new 보다 오래된 이력은 old 에서 보존한다(깊이 유지·누적).
    수정주가(액면분할 등)로 과거 값이 바뀌었을 수 있으므로, 겹치는 기간의 종가가 하나라도 다르면
    병합하지 않고 new 로 교체한다(merged=False). 겹침이 없거나 비교 가능한 기간이 없을 때도 교체.
    가장 최근 겹침 기간은 진행 중(부분) 봉일 수 있어 비교에서 제외한다.
    """
    tf = _norm_interval(interval)
    old_n = normalize_bars(old)
    new_n = normalize_bars(new)
    if not old_n or not new_n:
        return (new_n or old_n), False

    def _by_key(bars: List[Dict[str, Any]]) -> Dict[tuple, Dict[str, Any]]:
        out: Dict[tuple, Dict[str, Any]] = {}
        for b in bars:
            k = _period_key(b["timestamp"], tf)
            if k is not None:
                out[k] = b
        return out

    old_k, new_k = _by_key(old_n), _by_key(new_n)
    overlap = sorted(set(old_k) & set(new_k))
    comparable = overlap[:-1]  # 최신 겹침 기간 제외
    if not comparable:
        return new_n, False
    if any(old_k[k]["close"] != new_k[k]["close"] for k in comparable):
        return new_n, False

    merged_k = dict(old_k)
    merged_k.update(new_k)
    merged = sorted(merged_k.values(), key=lambda r: r["timestamp"])
    keep = MAX_KEEP.get(tf)
    if keep and len(merged) > keep:
        merged = merged[-keep:]
    return merged, True


def needs_deep_fetch(
    interval: str, depth: int, source: str, budget_left: Optional[int],
) -> bool:
    """배치에서 이 종목×간격을 '깊게(연속조회로 MIN_DEPTH 까지)' 받을지.

    이미 충분히 깊거나(depth>=MIN_DEPTH), 키움 이력을 끝까지 받은 종목(FULL_SOURCE)이거나,
    깊게 받는 예산이 소진됐으면 False → 최신 1페이지만 받아 병합한다. budget_left=None 이면 무제한.
    """
    tf = _norm_interval(interval)
    if int(depth or 0) >= MIN_DEPTH.get(tf, 0):
        return False
    if (source or "") == FULL_SOURCE:
        return False
    return budget_left is None or budget_left > 0


def get_depth_map(interval: str) -> Dict[str, tuple]:
    """간격별 {종목코드: (bar_count, source)} — bars_json 은 읽지 않는 경량 조회."""
    ensure_chart_ohlcv_series_table()
    tf = _norm_interval(interval)
    out: Dict[str, tuple] = {}
    for db in get_db():
        rows = (
            db.query(
                ChartOhlcvSeries.stock_code,
                ChartOhlcvSeries.bar_count,
                ChartOhlcvSeries.source,
            )
            .filter(ChartOhlcvSeries.interval == tf)
            .all()
        )
        for code, n, src in rows:
            out[code] = (int(n or 0), src or "")
        break
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
    source: Optional[str] = "kiwoom",
    db: Optional[Session] = None,
    merge: bool = False,
) -> Optional[ChartOhlcvSeries]:
    """종목×인터벌 시리즈 upsert. bars 비면 저장하지 않음.

    merge=True 면 기존 마트 봉과 병합해 과거 이력을 보존한다(`merge_bars`; 수정주가로 값이 달라졌으면 교체).
    source 가 비어 있으면(None) 병합에 성공한 경우 기존 source 를 유지하고, 그 외에는 "kiwoom".
    """
    ensure_chart_ohlcv_series_table()
    code = _norm_code(stock_code)
    tf = _norm_interval(interval)
    cleaned = normalize_bars(bars)
    if not code or not cleaned:
        return None
    if as_of_date is None:
        as_of_date = kst_today()
    now = utc_now_naive()

    def _write(session: Session) -> ChartOhlcvSeries:
        row = (
            session.query(ChartOhlcvSeries)
            .filter(ChartOhlcvSeries.stock_code == code, ChartOhlcvSeries.interval == tf)
            .first()
        )
        final_bars, merged = cleaned, False
        if merge and row and row.bars_json:
            final_bars, merged = merge_bars(row.bars_json, cleaned, tf)
        fields = {
            "stock_name": stock_name or (row.stock_name if row else ""),
            "as_of_date": as_of_date,
            "bar_count": len(final_bars),
            "first_bar_date": _bar_date(final_bars[0]["timestamp"]),
            "last_bar_date": _bar_date(final_bars[-1]["timestamp"]),
            "bars_json": final_bars,
            "source": source or ((row.source if (row and merged) else None) or "kiwoom"),
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
    """배치 upsert. 각 row: stock_code, interval, bars, stock_name?, source?, merge?

    source 가 없으면 병합 성공 시 기존 source 유지(없으면 "kiwoom"), merge=True 면 기존 이력과 병합.
    """
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
                source=data.get("source") or None,
                db=db,
                merge=bool(data.get("merge")),
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
