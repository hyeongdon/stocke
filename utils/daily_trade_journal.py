"""당일(KST) 매매 일지 집계 — 금일 매수 평가 + 보유 평가손익."""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from core.models import Position, PositionBuyFill, SellOrder
from utils.datetime_kst import kst_day_end_utc_naive_exclusive, kst_day_start_utc_naive, kst_today

KST = timezone(timedelta(hours=9))


def _is_sample(code: Optional[str]) -> bool:
    return str(code or "").startswith("SAMPLE_")


def _fmt_hhmm(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%H:%M")


def _pnl_rate(pnl: Optional[int], cost: Optional[int]) -> Optional[float]:
    if pnl is None or not cost:
        return None
    return float(pnl) / float(cost) * 100.0


def collect_daily_trade_journal(
    session: Session,
    *,
    day: Optional[date] = None,
) -> Dict[str, Any]:
    """지정일(KST) 매수·매도·보유 요약.

    평가 기준:
    - 금일 매수 손익 = 오늘 매수한 포지션의 실현 + 미실현
    - 보유 손익 = 현재 HOLDING 전체 미실현
    - 일일 평가합 = 금일 매수 실현 + 보유 전체 미실현 (더블카운트 없음)
    """
    day = day or kst_today()
    start = kst_day_start_utc_naive(day)
    end = kst_day_end_utc_naive_exclusive(day)

    buy_rows = (
        session.query(PositionBuyFill)
        .filter(
            PositionBuyFill.filled_at >= start,
            PositionBuyFill.filled_at < end,
        )
        .order_by(PositionBuyFill.filled_at.asc())
        .all()
    )

    today_position_ids: Set[int] = set()
    buys: List[Dict[str, Any]] = []
    buy_amount_sum = 0
    pos_cache: Dict[int, Position] = {}

    for r in buy_rows:
        if _is_sample(r.stock_code):
            continue
        amount = int(r.amount or 0)
        buy_amount_sum += amount
        today_position_ids.add(int(r.position_id))
        pos = pos_cache.get(int(r.position_id))
        if pos is None:
            pos = session.query(Position).filter(Position.id == r.position_id).first()
            if pos is not None:
                pos_cache[int(r.position_id)] = pos
        strategy = (pos.strategy_key if pos else None) or None
        buys.append(
            {
                "position_id": int(r.position_id),
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "quantity": int(r.quantity or 0),
                "price": int(r.price or 0),
                "amount": amount,
                "fill_type": (r.fill_type or "INITIAL").upper(),
                "strategy": strategy,
                "filled_at": r.filled_at,
                "time": _fmt_hhmm(r.filled_at),
            }
        )

    # 포지션 buy_time 기준 보강 (fill 누락 대비)
    pos_bought_today = (
        session.query(Position)
        .filter(Position.buy_time >= start, Position.buy_time < end)
        .all()
    )
    for p in pos_bought_today:
        if _is_sample(p.stock_code):
            continue
        today_position_ids.add(int(p.id))
        pos_cache[int(p.id)] = p

    sell_rows = (
        session.query(SellOrder)
        .filter(
            SellOrder.status == "COMPLETED",
            SellOrder.completed_at >= start,
            SellOrder.completed_at < end,
        )
        .order_by(SellOrder.completed_at.asc())
        .all()
    )

    sells: List[Dict[str, Any]] = []
    realized_pnl_all = 0
    today_buy_realized = 0
    win = 0
    loss = 0
    flat = 0
    sell_amount_sum = 0
    reason_counts: Dict[str, int] = {}
    sells_by_pos: Dict[int, List[Dict[str, Any]]] = {}

    for r in sell_rows:
        if _is_sample(r.stock_code):
            continue
        pnl = int(r.profit_loss or 0)
        realized_pnl_all += pnl
        sell_amount_sum += int(r.sell_amount or 0)
        from_today_buy = int(r.position_id) in today_position_ids
        if from_today_buy:
            today_buy_realized += pnl
        if pnl > 0:
            win += 1
        elif pnl < 0:
            loss += 1
        else:
            flat += 1
        reason = str(r.sell_reason or "MANUAL").strip() or "MANUAL"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        row = {
            "position_id": int(r.position_id),
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "quantity": int(r.sell_quantity or 0),
            "price": int(r.sell_price or 0),
            "amount": int(r.sell_amount or 0),
            "sell_reason": reason,
            "sell_reason_detail": r.sell_reason_detail or "",
            "profit_loss": pnl,
            "profit_loss_rate": float(r.profit_loss_rate)
            if r.profit_loss_rate is not None
            else None,
            "from_today_buy": from_today_buy,
            "completed_at": r.completed_at,
            "time": _fmt_hhmm(r.completed_at),
        }
        sells.append(row)
        sells_by_pos.setdefault(int(r.position_id), []).append(row)

    hold_rows = (
        session.query(Position)
        .filter(Position.status == "HOLDING")
        .order_by(Position.buy_time.asc())
        .all()
    )
    holdings: List[Dict[str, Any]] = []
    holding_unrealized = 0
    today_buy_unrealized = 0
    for p in hold_rows:
        if _is_sample(p.stock_code):
            continue
        pnl = int(p.current_profit_loss) if p.current_profit_loss is not None else None
        rate = (
            float(p.current_profit_loss_rate)
            if p.current_profit_loss_rate is not None
            else None
        )
        bought_today = int(p.id) in today_position_ids
        if pnl is not None:
            holding_unrealized += pnl
            if bought_today:
                today_buy_unrealized += pnl
        holdings.append(
            {
                "position_id": int(p.id),
                "stock_code": p.stock_code,
                "stock_name": p.stock_name,
                "quantity": int(p.buy_quantity or 0),
                "buy_price": int(p.buy_price or 0),
                "current_price": int(p.current_price) if p.current_price is not None else None,
                "current_profit_loss": pnl,
                "current_profit_loss_rate": rate,
                "strategy": p.strategy_key,
                "bought_today": bought_today,
            }
        )

    # 금일 매수 포지션별 평가 행
    today_buy_positions: List[Dict[str, Any]] = []
    for pid in sorted(today_position_ids):
        pos = pos_cache.get(pid)
        if pos is None:
            pos = session.query(Position).filter(Position.id == pid).first()
            if pos is not None:
                pos_cache[pid] = pos
        if pos is None or _is_sample(pos.stock_code):
            continue

        pos_sells = sells_by_pos.get(pid, [])
        realized = sum(int(s["profit_loss"] or 0) for s in pos_sells)
        holding = pos.status == "HOLDING"
        unrealized = (
            int(pos.current_profit_loss)
            if holding and pos.current_profit_loss is not None
            else 0
        )
        # 부분매도 후 보유: 실현+미실현 / 전량청산: 실현만
        eval_pnl = realized + (unrealized if holding else 0)
        cost = int(pos.buy_amount or 0) or int(pos.buy_price or 0) * int(pos.buy_quantity or 0)
        status = "보유" if holding else ("청산" if pos_sells else "매수")
        last_reason = pos_sells[-1]["sell_reason"] if pos_sells else None
        today_buy_positions.append(
            {
                "position_id": pid,
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "quantity": int(pos.buy_quantity or 0),
                "buy_price": int(pos.buy_price or 0),
                "strategy": pos.strategy_key,
                "status": status,
                "holding": holding,
                "realized_pnl": realized,
                "unrealized_pnl": unrealized if holding else None,
                "eval_pnl": eval_pnl,
                "eval_pnl_rate": _pnl_rate(eval_pnl, cost),
                "sell_reason": last_reason,
                "buy_time": _fmt_hhmm(pos.buy_time),
            }
        )

    today_buy_eval = today_buy_realized + today_buy_unrealized
    # 더블카운트 없는 일일 평가: 금일매수 실현 + 보유 전체 미실현
    day_eval_total = today_buy_realized + holding_unrealized

    return {
        "day": day.isoformat(),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "holding_count": len(holdings),
        "today_buy_position_count": len(today_buy_positions),
        "buy_amount_sum": buy_amount_sum,
        "sell_amount_sum": sell_amount_sum,
        # 레거시/참고: 당일 모든 매도 실현
        "realized_pnl": realized_pnl_all,
        # 평가 핵심
        "today_buy_realized": today_buy_realized,
        "today_buy_unrealized": today_buy_unrealized,
        "today_buy_eval": today_buy_eval,
        "holding_unrealized": holding_unrealized,
        "day_eval_total": day_eval_total,
        "win_count": win,
        "loss_count": loss,
        "flat_count": flat,
        "reason_counts": reason_counts,
        "buys": buys,
        "sells": sells,
        "holdings": holdings,
        "today_buy_positions": today_buy_positions,
        "has_activity": bool(buys or sells or holdings),
    }
