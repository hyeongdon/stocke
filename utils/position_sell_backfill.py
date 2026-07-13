"""청산 포지션 중 sell_orders(COMPLETED)가 없는 건 보정."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from core.models import Position, SellOrder


def _infer_sell_price(pos: Position) -> int:
    for v in (pos.current_price, pos.peak_price, pos.buy_price):
        if v is not None and int(v) > 0:
            return int(v)
    return 0


def ensure_completed_sell_order(
    session: Session,
    pos: Position,
    *,
    sell_reason: str = "MANUAL",
    sell_reason_detail: str,
    completed_at: Optional[datetime] = None,
    sell_price: Optional[int] = None,
) -> Optional[SellOrder]:
    """포지션에 COMPLETED 매도 이력이 없으면 생성."""
    existing = (
        session.query(SellOrder)
        .filter(SellOrder.position_id == pos.id, SellOrder.status == "COMPLETED")
        .first()
    )
    if existing:
        return existing

    qty = int(pos.buy_quantity or 0)
    if qty <= 0:
        return None

    price = int(sell_price) if sell_price and int(sell_price) > 0 else _infer_sell_price(pos)
    if price <= 0:
        return None

    done_at = completed_at or pos.sell_time or datetime.utcnow()
    buy_price = int(pos.buy_price or 0)
    pl = (price - buy_price) * qty if buy_price else None
    pl_rate = ((price - buy_price) / buy_price * 100) if buy_price else None

    sell = SellOrder(
        position_id=pos.id,
        stock_code=pos.stock_code,
        stock_name=pos.stock_name,
        sell_price=price,
        sell_quantity=qty,
        sell_amount=price * qty,
        sell_reason=sell_reason,
        sell_reason_detail=(sell_reason_detail or "")[:200],
        profit_loss=pl,
        profit_loss_rate=pl_rate,
        status="COMPLETED",
        created_at=done_at,
        ordered_at=done_at,
        completed_at=done_at,
    )
    session.add(sell)
    if pl is not None:
        pos.current_profit_loss = pl
        pos.current_profit_loss_rate = pl_rate
    session.flush()
    return sell


def repair_missing_sell_orders(session: Session) -> int:
    """청산 포지션 → COMPLETED sell_orders 없음 건 일괄 보정."""
    rows = (
        session.query(Position)
        .filter(Position.status != "HOLDING", Position.sell_time.isnot(None))
        .all()
    )
    repaired = 0
    for pos in rows:
        has_done = (
            session.query(SellOrder)
            .filter(SellOrder.position_id == pos.id, SellOrder.status == "COMPLETED")
            .first()
        )
        if has_done:
            continue
        reason = pos.status if pos.status in (
            "STOP_LOSS", "TAKE_PROFIT", "MANUAL_SELL", "MANUAL", "MARKET_CLOSE",
        ) else "MANUAL"
        if reason == "MANUAL_SELL":
            reason = "MANUAL"
        detail = "DB 보정 — 청산 포지션에 매도 체결 이력 누락 복구"
        if ensure_completed_sell_order(
            session,
            pos,
            sell_reason=reason,
            sell_reason_detail=detail,
            completed_at=pos.sell_time,
        ):
            repaired += 1
    if repaired:
        session.commit()
    return repaired
