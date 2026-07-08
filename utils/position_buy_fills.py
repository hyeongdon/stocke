"""포지션 매수 체결 이력 기록."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.models import PositionBuyFill

KST = timezone(timedelta(hours=9))


def _fmt_kst(v: Any) -> Optional[str]:
    if not v:
        return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "")[:19])
        except ValueError:
            return str(v)[:19]
    if not isinstance(v, datetime):
        return str(v)[:19]
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


def record_buy_fill(
    session: Session,
    *,
    position_id: int,
    stock_code: str,
    stock_name: str,
    fill_type: str,
    price: int,
    quantity: int,
    signal_id: Optional[int] = None,
    order_id: str = "",
    planned_amount: Optional[int] = None,
    change_rate: Optional[float] = None,
    sizing_method: Optional[str] = None,
    note: Optional[str] = None,
    condition_checks: Optional[List[Dict[str, Any]]] = None,
    filled_at: Optional[datetime] = None,
    order_quantity: Optional[int] = None,
) -> PositionBuyFill:
    qty = int(quantity)
    ord_qty = int(order_quantity if order_quantity is not None else qty)
    amount = int(price) * qty
    row = PositionBuyFill(
        position_id=position_id,
        stock_code=stock_code,
        stock_name=stock_name,
        fill_type=fill_type.upper() if fill_type else "INITIAL",
        signal_id=signal_id,
        order_id=order_id or None,
        price=int(price),
        quantity=qty,
        order_quantity=ord_qty,
        amount=amount,
        planned_amount=planned_amount,
        change_rate=change_rate,
        sizing_method=sizing_method,
        filled_at=filled_at or datetime.utcnow(),
        note=note,
        condition_checks=condition_checks,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


FILL_TYPE_KO = {
    "INITIAL": "초기 매수",
    "ADD": "피라미딩 추가매수",
}


def fill_type_label(
    fill_type: str,
    sizing_method: Optional[str] = None,
    is_backfill: bool = False,
) -> str:
    ft = (fill_type or "INITIAL").upper()
    sizing = (sizing_method or "").upper()
    if ft == "ADD":
        return FILL_TYPE_KO["ADD"]
    if sizing == "PYRAMIDING":
        base = "초기 매수 (역피라미딩 사이징)"
    else:
        base = FILL_TYPE_KO["INITIAL"]
    if is_backfill:
        return f"{base} · 추정"
    return base


def serialize_buy_fill(row: PositionBuyFill, settings: Dict[str, Any]) -> Dict[str, Any]:
    sizing = row.sizing_method or settings.get("sizing_method") or "FIXED"
    is_backfill = bool(row.note and "마이그레이션" in row.note)
    label = fill_type_label(row.fill_type, sizing, is_backfill)
    ord_qty = int(getattr(row, "order_quantity", None) or row.quantity or 0)
    fill_qty = int(row.quantity or 0)
    detail_parts: List[str] = []
    if ord_qty > 0 and fill_qty > 0 and ord_qty != fill_qty:
        detail_parts.append(f"주문 {ord_qty:,}주 → 체결 {fill_qty:,}주")
    if row.change_rate is not None:
        detail_parts.append(f"등락 {row.change_rate:+.2f}%")
    if row.planned_amount:
        detail_parts.append(f"계획 {int(row.planned_amount):,}원")
    if row.fill_type == "ADD" and settings.get("add_buy_trigger") is not None:
        detail_parts.append(f"트리거 +{settings.get('add_buy_trigger')}%")
    if row.note and not is_backfill:
        detail_parts.append(row.note)

    return {
        "id": row.id,
        "fill_type": row.fill_type,
        "label": label,
        "time": _fmt_kst(row.filled_at),
        "price": int(row.price),
        "quantity": fill_qty,
        "order_quantity": ord_qty,
        "amount": int(row.amount),
        "planned_amount": int(row.planned_amount) if row.planned_amount else None,
        "change_rate": float(row.change_rate) if row.change_rate is not None else None,
        "sizing_method": sizing,
        "signal_id": row.signal_id,
        "order_id": row.order_id,
        "detail": " · ".join(detail_parts) if detail_parts else None,
        "is_backfill": is_backfill,
    }


def aggregate_buy_fills_from_rows(rows: List[PositionBuyFill]) -> Optional[Dict[str, Any]]:
    """체결 이력 합산 — 체결 수량·금액·가중평균 단가."""
    if not rows:
        return None
    total_qty = sum(int(r.quantity or 0) for r in rows)
    total_ord = sum(int(getattr(r, "order_quantity", None) or r.quantity or 0) for r in rows)
    total_amt = sum(int(r.amount or 0) for r in rows)
    if total_qty <= 0 or total_amt <= 0:
        return None
    times = [r.filled_at for r in rows if r.filled_at]
    return {
        "quantity": total_qty,
        "order_quantity": total_ord,
        "amount": total_amt,
        "avg_price": int(round(total_amt / total_qty)),
        "fill_count": len(rows),
        "first_filled_at": min(times) if times else None,
    }


def aggregate_buy_fills_from_dicts(fills: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not fills:
        return None
    total_qty = sum(int(f.get("quantity") or 0) for f in fills)
    total_ord = sum(int(f.get("order_quantity") or f.get("quantity") or 0) for f in fills)
    total_amt = sum(int(f.get("amount") or 0) for f in fills)
    if total_qty <= 0 or total_amt <= 0:
        return None
    return {
        "quantity": total_qty,
        "order_quantity": total_ord,
        "amount": total_amt,
        "avg_price": int(round(total_amt / total_qty)),
        "fill_count": len(fills),
    }


def order_and_filled_totals(
    position,
    buy_fills: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[int, int]:
    """(주문 수량 합계, 체결 수량 합계). 체결은 포지션(API)·이력 병합."""
    order_qty = int(getattr(position, "order_quantity", None) or 0)
    if buy_fills:
        ord_from_fills = sum(int(f.get("order_quantity") or f.get("quantity") or 0) for f in buy_fills)
        if ord_from_fills > order_qty:
            order_qty = ord_from_fills

    filled_qty = int(position.buy_quantity or 0)
    if buy_fills:
        fill_from_rows = sum(int(f.get("quantity") or 0) for f in buy_fills)
        if filled_qty <= 0:
            filled_qty = fill_from_rows
        elif fill_from_rows > 0 and filled_qty != fill_from_rows:
            # API(포지션) 체결 우선 — 부분체결
            filled_qty = min(filled_qty, fill_from_rows) if filled_qty < fill_from_rows else filled_qty

    return order_qty, filled_qty


def reconcile_position_buy_with_fills(
    session: Session,
    position,
    holding: Optional[Dict[str, Any]] = None,
) -> None:
    """키움 잔고 체결 수량 반영 + 주문 수량 유지 + 체결 이력 갱신."""
    from api.kiwoom_api import _parse_kiwoom_int
    from utils.eval_pnl import pl_from_holding

    rows = (
        session.query(PositionBuyFill)
        .filter(PositionBuyFill.position_id == position.id)
        .order_by(PositionBuyFill.filled_at.asc())
        .all()
    )
    agg = aggregate_buy_fills_from_rows(rows)

    if not getattr(position, "order_quantity", None):
        if agg and agg.get("order_quantity"):
            position.order_quantity = agg["order_quantity"]
        elif agg and agg.get("quantity"):
            position.order_quantity = agg["quantity"]

    api_qty = _parse_kiwoom_int(holding.get("qty")) if holding else 0
    api_pur = _parse_kiwoom_int(holding.get("pur_amt")) if holding else 0
    api_avg = _parse_kiwoom_int(holding.get("avg_pr")) if holding else 0

    if api_qty > 0:
        filled_qty = api_qty
        filled_amt = api_pur if api_pur > 0 else int(position.buy_amount or 0)
        filled_price = api_avg if api_avg > 0 else (
            int(round(filled_amt / filled_qty)) if filled_qty > 0 else int(position.buy_price or 0)
        )
        position.buy_quantity = filled_qty
        position.buy_amount = filled_amt
        position.actual_buy_amount = filled_amt
        if filled_price > 0:
            position.buy_price = filled_price

        if len(rows) == 1 and (rows[0].fill_type or "").upper() == "INITIAL":
            row = rows[0]
            if int(row.quantity or 0) != filled_qty or int(row.amount or 0) != filled_amt:
                row.quantity = filled_qty
                row.amount = filled_amt
                if filled_price > 0:
                    row.price = filled_price
    elif agg and agg["quantity"] > 0:
        position.buy_quantity = agg["quantity"]
        position.buy_amount = agg["amount"]
        position.actual_buy_amount = agg["amount"]
        position.buy_price = agg["avg_price"]

    if holding:
        cur = _parse_kiwoom_int(holding.get("cur_pr"))
        pl, rate = pl_from_holding(holding)
        if cur > 0:
            position.current_price = cur
        position.current_profit_loss = pl
        position.current_profit_loss_rate = rate


def effective_buy_stats(
    buy_fills: List[Dict[str, Any]],
    position,
) -> Dict[str, Any]:
    """표시용 — 체결 수량·금액 (포지션/API 동기화값 우선)."""
    order_qty, filled_qty = order_and_filled_totals(position, buy_fills)
    amt = int(getattr(position, "actual_buy_amount", None) or position.buy_amount or 0)
    price = int(position.buy_price or 0)
    if filled_qty > 0 and amt > 0:
        return {
            "price": price if price > 0 else int(round(amt / filled_qty)),
            "quantity": filled_qty,
            "order_quantity": order_qty,
            "amount": amt,
            "from_fills": False,
        }
    agg = aggregate_buy_fills_from_dicts(buy_fills)
    if agg:
        return {
            "price": agg["avg_price"],
            "quantity": agg["quantity"],
            "order_quantity": agg.get("order_quantity") or agg["quantity"],
            "amount": agg["amount"],
            "from_fills": True,
        }
    return {
        "price": price,
        "quantity": filled_qty,
        "order_quantity": order_qty,
        "amount": amt,
        "from_fills": False,
    }


def repair_positions_from_buy_fills(session: Session) -> int:
    """order_quantity 미설정 포지션만 체결 이력에서 보완."""
    from core.models import Position

    n = 0
    for pos in session.query(Position).filter(Position.status.in_(("HOLDING", "TRAILING"))).all():
        if getattr(pos, "order_quantity", None):
            continue
        rows = (
            session.query(PositionBuyFill)
            .filter(PositionBuyFill.position_id == pos.id)
            .all()
        )
        agg = aggregate_buy_fills_from_rows(rows)
        if agg and agg.get("order_quantity"):
            pos.order_quantity = agg["order_quantity"]
            n += 1
        elif agg and agg.get("quantity"):
            pos.order_quantity = agg["quantity"]
            n += 1
    if n:
        session.commit()
    return n


def buy_fills_summary(fills: List[Dict[str, Any]], settings: Dict[str, Any]) -> str:
    if not fills:
        return "매수 체결 이력 없음"
    n = len(fills)
    adds = sum(1 for f in fills if f.get("fill_type") == "ADD")
    total = sum(int(f.get("amount") or 0) for f in fills)
    ord_total = sum(int(f.get("order_quantity") or f.get("quantity") or 0) for f in fills)
    fill_total = sum(int(f.get("quantity") or 0) for f in fills)
    mismatch = ord_total > 0 and fill_total > 0 and ord_total != fill_total
    sizing = (settings.get("sizing_method") or "FIXED").upper()
    base = ""
    if n == 1 and adds == 0:
        if sizing == "PYRAMIDING":
            base = (
                f"1회 체결 · 역피라미딩 초기 사이징 · "
                f"{total:,}원 (등락 {settings.get('signal_min_threshold', 2)}%→"
                f"{int(settings.get('initial_max_amount') or 0):,}원 · "
                f"{settings.get('signal_max_threshold', 10)}%→"
                f"{int(settings.get('initial_min_amount') or 0):,}원)"
            )
        else:
            base = f"1회 체결 · {total:,}원"
    else:
        base = f"총 {n}회 체결 (추가매수 {adds}회) · 합계 {total:,}원"
    if mismatch:
        base += f" · 주문 {ord_total:,}주 / 체결 {fill_total:,}주"
    return base
