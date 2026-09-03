"""매수 시각 이후 고점만 반영 — 매수 전 당일 고가로 트레일링이 켜지는 것을 방지."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from utils.datetime_kst import KST

INTRADAY_BAR_MINUTES = 15


def buy_time_utc_naive_to_kst(buy_time: Optional[datetime]) -> Optional[datetime]:
    """DB buy_time(UTC naive) → KST aware."""
    if buy_time is None:
        return None
    if buy_time.tzinfo is not None:
        return buy_time.astimezone(KST)
    return buy_time.replace(tzinfo=timezone.utc).astimezone(KST)


def parse_bar_end_kst(timestamp: str) -> Optional[datetime]:
    raw = (timestamp or "").strip()[:19]
    if len(raw) < 16:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except ValueError:
        return None


def bar_period_start_kst(
    timestamp: str,
    *,
    interval_min: int = INTRADAY_BAR_MINUTES,
) -> Optional[datetime]:
    end = parse_bar_end_kst(timestamp)
    if end is None:
        return None
    return end - timedelta(minutes=interval_min)


def max_high_since_buy_from_intraday_bars(
    bars: List[Dict[str, Any]],
    buy_time_kst: datetime,
    *,
    interval_min: int = INTRADAY_BAR_MINUTES,
    session_start_kst: Optional[datetime] = None,
) -> int:
    """분봉 고가 — 봉 시작 시각이 기준 시각 이후인 것만 집계."""
    if buy_time_kst.tzinfo is None:
        buy_time_kst = buy_time_kst.replace(tzinfo=KST)
    else:
        buy_time_kst = buy_time_kst.astimezone(KST)

    cutoff = buy_time_kst.replace(second=0, microsecond=0)
    if session_start_kst is not None:
        if session_start_kst.tzinfo is None:
            session_start_kst = session_start_kst.replace(tzinfo=KST)
        else:
            session_start_kst = session_start_kst.astimezone(KST)
        if session_start_kst > cutoff:
            cutoff = session_start_kst.replace(second=0, microsecond=0)

    peak = 0
    for bar in bars or []:
        start = bar_period_start_kst(str(bar.get("timestamp") or ""), interval_min=interval_min)
        if start is None or start < cutoff:
            continue
        peak = max(peak, int(bar.get("high") or 0))
    return peak


def parse_daily_bar_date(timestamp: str) -> Optional[datetime.date]:
    raw = (timestamp or "").strip()[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def max_high_full_holding_days(
    daily_bars: List[Dict[str, Any]],
    buy_date,
    today,
) -> int:
    """매수일 다음날부터는 종일 보유로 일봉 고가 사용(당일 진행 중 일봉 포함).

    매수 당일 일봉은 매수 전 고가가 섞이므로 제외하고, 분봉으로만 집계한다.
    """
    peak = 0
    for bar in daily_bars or []:
        bar_date = parse_daily_bar_date(str(bar.get("timestamp") or ""))
        if bar_date is None:
            continue
        if buy_date < bar_date <= today:
            peak = max(peak, int(bar.get("high") or 0))
    return peak


def resolve_position_peak_price(
    *,
    buy_price: int,
    current_price: int,
    stored_peak: Optional[int],
    since_buy_high: int,
    allow_api: bool,
    inflation_gap_pct: float = 3.0,
) -> int:
    """관측 고점(매수 이후) + 저장값 — 매수 전 당일고가 오염 stored는 갭이 크면 제거."""
    buy = int(buy_price or 0)
    cur = int(current_price or 0)
    base = max(buy, cur)

    observed = base
    if allow_api and since_buy_high > 0:
        observed = max(observed, since_buy_high)

    stored = int(stored_peak or 0)
    if stored <= 0:
        return observed

    if (
        allow_api
        and since_buy_high > 0
        and stored > since_buy_high
    ):
        gap_pct = (stored - since_buy_high) / since_buy_high * 100.0
        if gap_pct > inflation_gap_pct:
            return observed

    return max(observed, stored)


def should_disarm_trailing(
    *,
    trailing_armed: bool,
    trail_start_rate: Optional[float],
    buy_price: int,
    peak: int,
) -> bool:
    """하위 호환 — 한 번 armed된 바닥은 유지(고점 보정으로 해제하지 않음)."""
    return False
