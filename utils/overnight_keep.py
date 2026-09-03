"""장마감 오버나잇 슬롯 — 당일 종가배팅 제외 N종목만 유지."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from typing import List, Optional, Sequence, Set, Tuple

DEFAULT_OVERNIGHT_KEEP_SLOTS = 3
DEFAULT_OVERNIGHT_MAX_PER_STRATEGY = 1


def normalize_strategy_key(key: Optional[str]) -> str:
    k = (key or "legacy").strip().lower()
    return k or "legacy"


def is_today_jongga(
    strategy_key: Optional[str],
    buy_time: Optional[datetime],
    today: date,
) -> bool:
    """당일 종가배팅은 장마감에 매수하므로 오버나잇 슬롯과 별도 유지."""
    if normalize_strategy_key(strategy_key) != "jongga":
        return False
    from utils.position_peak_since_buy import buy_time_utc_naive_to_kst

    buy_kst = buy_time_utc_naive_to_kst(buy_time)
    if buy_kst is None:
        return True
    return buy_kst.date() == today


def jongga_force_liquidate_at_close(
    strategy_key: Optional[str],
    buy_time: Optional[datetime],
    today: date,
    pnl_rate: float,
) -> bool:
    """익일 플러스·사흘째 이상 종가배팅은 장마감에 슬롯과 무관하게 청산."""
    if normalize_strategy_key(strategy_key) != "jongga":
        return False
    from utils.datetime_kst import KST
    from utils.jongga_engine import should_flatten_jongga_at_close

    now = datetime.combine(today, dt_time(15, 10), tzinfo=KST)
    return should_flatten_jongga_at_close(buy_time, pnl_rate, now)


def jongga_close_force_reason(
    buy_time: Optional[datetime],
    today: date,
    pnl_rate: float,
) -> str:
    from utils.datetime_kst import KST
    from utils.jongga_engine import jongga_close_flatten_reason

    now = datetime.combine(today, dt_time(15, 10), tzinfo=KST)
    return jongga_close_flatten_reason(buy_time, pnl_rate, now) or ""


@dataclass(frozen=True)
class OvernightCandidate:
    position_id: int
    strategy_key: str
    pnl_rate: float
    is_today_jongga: bool = False
    force_liquidate: bool = False
    force_reason: str = ""
    stock_code: str = ""
    stock_name: str = ""


def keep_sort_key(row: OvernightCandidate) -> Tuple[int, float, int]:
    """유지 우선순위 (작을수록 남김).

    1) 손실·본전 우선 (익절은 뒤로)
    2) 손실 중에서는 손실이 작은 쪽을 남기고, 큰 손실은 정리
    3) 익절만 남을 때는 수익이 작은 쪽을 남김
    """
    if row.pnl_rate > 0:
        return (1, float(row.pnl_rate), int(row.position_id))
    return (0, -float(row.pnl_rate), int(row.position_id))


def select_overnight_keep(
    rows: Sequence[OvernightCandidate],
    *,
    keep_slots: int = DEFAULT_OVERNIGHT_KEEP_SLOTS,
    max_per_strategy: int = DEFAULT_OVERNIGHT_MAX_PER_STRATEGY,
) -> Tuple[Set[int], List[OvernightCandidate], List[OvernightCandidate]]:
    """당일 종가배팅은 항상 유지. 나머지는 전략당 최대 N개, 슬롯 한도까지.
    force_liquidate(프랙탈 당일청산·종가배팅 익일 플러스/이틀 초과)는 유지 대상에서 제외.

    Returns:
        keep_ids, kept_rows, liquidate_rows
    """
    slots = max(0, int(keep_slots))
    per = max(1, int(max_per_strategy))

    kept: List[OvernightCandidate] = []
    keep_ids: Set[int] = set()
    liquidate: List[OvernightCandidate] = []

    for row in rows:
        if row.is_today_jongga and not row.force_liquidate:
            keep_ids.add(row.position_id)
            kept.append(row)

    pool = [r for r in rows if r.position_id not in keep_ids]
    for row in pool:
        if row.force_liquidate:
            liquidate.append(row)
    pool = [r for r in pool if not r.force_liquidate]
    pool_sorted = sorted(pool, key=keep_sort_key)

    used: dict[str, int] = {}
    selected = 0
    for row in pool_sorted:
        sk = normalize_strategy_key(row.strategy_key)
        if selected >= slots or used.get(sk, 0) >= per:
            liquidate.append(row)
            continue
        used[sk] = used.get(sk, 0) + 1
        selected += 1
        keep_ids.add(row.position_id)
        kept.append(row)

    return keep_ids, kept, liquidate
