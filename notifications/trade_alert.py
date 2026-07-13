"""
매수/매도 체결 텔레그램 알림

자동매매 실행기(buy_order_executor, stop_loss_manager)에서 체결 시 호출합니다.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from notifications.telegram_notifier import TelegramNotifier

from core.config import Config
from utils.datetime_kst import now_kst

logger = logging.getLogger(__name__)

SELL_REASON_KO = {
    "STOP_LOSS": "손절",
    "TAKE_PROFIT": "익절",
    "TRAILING": "트레일링 스탑",
    "PROFIT_LOCK": "수익 잠금",
    "MARKET_CLOSE": "장마감 청산",
    "MANUAL": "수동 매도",
    "INDICATOR": "지표 매도",
}


def _fmt_price(value: Optional[int]) -> str:
    if value is None:
        return "N/A"
    return f"{int(value):,}원"


def _fmt_pnl(amount: Optional[int], rate: Optional[float]) -> str:
    if amount is None:
        return "N/A"
    sign = "+" if amount > 0 else ""
    rate_str = ""
    if rate is not None:
        rate_str = f" ({sign}{rate:.2f}%)"
    return f"{sign}{amount:,}원{rate_str}"


def build_buy_message(
    *,
    stock_name: str,
    stock_code: str,
    quantity: int,
    price: int,
    is_add_buy: bool = False,
    order_id: str = "",
) -> str:
    now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    buy_type = "추가매수" if is_add_buy else "신규매수"
    total = price * quantity
    lines = [
        "🟢 매수 체결",
        f"종목: {stock_name}({stock_code})",
        f"유형: {buy_type}",
        f"수량: {quantity:,}주",
        f"가격: {_fmt_price(price)}",
        f"금액: {_fmt_price(total)}",
        f"시각: {now}",
    ]
    if order_id:
        lines.insert(-1, f"주문번호: {order_id}")
    return "\n".join(lines)


def build_sell_message(
    *,
    stock_name: str,
    stock_code: str,
    quantity: int,
    sell_price: int,
    buy_price: Optional[int],
    sell_reason: str,
    sell_reason_detail: str = "",
    profit_loss: Optional[int] = None,
    remaining_qty: Optional[int] = None,
) -> str:
    now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    reason_ko = SELL_REASON_KO.get(sell_reason, sell_reason)
    total = sell_price * quantity

    pnl_rate = None
    if profit_loss is not None and buy_price and quantity:
        cost = buy_price * quantity
        if cost > 0:
            pnl_rate = profit_loss / cost * 100

    header = "🟠 부분 매도 체결" if remaining_qty is not None else "🔴 매도 체결"
    lines = [
        header,
        f"종목: {stock_name}({stock_code})",
        f"사유: {reason_ko}",
        f"수량: {quantity:,}주",
        f"매도가: {_fmt_price(sell_price)}",
        f"금액: {_fmt_price(total)}",
    ]
    if buy_price:
        lines.append(f"매입가: {_fmt_price(buy_price)}")
    if profit_loss is not None:
        lines.append(f"손익: {_fmt_pnl(profit_loss, pnl_rate)}")
    if remaining_qty is not None:
        lines.append(f"잔량: {remaining_qty:,}주")
    if sell_reason_detail:
        lines.append(f"상세: {sell_reason_detail}")
    lines.append(f"시각: {now}")
    return "\n".join(lines)


def send_trade_alert(text: str, *, skip_market_hours_check: bool = False) -> bool:
    """텔레그램 설정이 있을 때만 매매 알림 전송."""
    if Config.TELEGRAM_ALERT_MARKET_HOURS_ONLY and not skip_market_hours_check:
        from utils.market_hours import telegram_market_alert_block_reason
        block = telegram_market_alert_block_reason()
        if block:
            logger.debug(f"매매 텔레그램 알림 스킵: {block}")
            return False
    notifier = TelegramNotifier()
    if not notifier.is_configured():
        return False
    return notifier.send_message(text)


async def send_trade_alert_async(text: str) -> bool:
    return await asyncio.to_thread(send_trade_alert, text)


def notify_buy(
    *,
    stock_name: str,
    stock_code: str,
    quantity: int,
    price: int,
    is_add_buy: bool = False,
    order_id: str = "",
) -> bool:
    try:
        msg = build_buy_message(
            stock_name=stock_name,
            stock_code=stock_code,
            quantity=quantity,
            price=price,
            is_add_buy=is_add_buy,
            order_id=order_id,
        )
        ok = send_trade_alert(msg)
        if ok:
            logger.info(f"매수 텔레그램 알림 전송 — {stock_name}({stock_code})")
        return ok
    except Exception as e:
        logger.warning(f"매수 텔레그램 알림 오류 — {stock_name}: {e}")
        return False


async def notify_buy_async(**kwargs) -> bool:
    return await asyncio.to_thread(notify_buy, **kwargs)


def sell_fill_snapshot(sell, position) -> dict:
    """세션 종료 전 매도 알림용 필드 스냅샷."""
    qty = sell.sell_quantity or position.buy_quantity or 0
    sell_price = sell.sell_price or position.current_price or position.buy_price or 0
    buy_price = position.buy_price
    profit_loss = sell.profit_loss
    if profit_loss is None and buy_price and sell_price and qty:
        profit_loss = (sell_price - buy_price) * qty
    return {
        "stock_name": sell.stock_name or position.stock_name,
        "stock_code": sell.stock_code or position.stock_code,
        "quantity": int(qty),
        "sell_price": int(sell_price),
        "buy_price": int(buy_price) if buy_price else None,
        "sell_reason": sell.sell_reason or "UNKNOWN",
        "sell_reason_detail": sell.sell_reason_detail or "",
        "profit_loss": int(profit_loss) if profit_loss is not None else None,
    }


def notify_sell_filled(
    sell,
    position,
    *,
    remaining_qty: Optional[int] = None,
) -> bool:
    """SellOrder + Position 기준 매도 체결 알림."""
    try:
        snap = sell_fill_snapshot(sell, position)
        msg = build_sell_message(
            stock_name=snap["stock_name"],
            stock_code=snap["stock_code"],
            quantity=snap["quantity"],
            sell_price=snap["sell_price"],
            buy_price=snap["buy_price"],
            sell_reason=snap["sell_reason"],
            sell_reason_detail=snap["sell_reason_detail"],
            profit_loss=snap["profit_loss"],
            remaining_qty=remaining_qty,
        )
        ok = send_trade_alert(msg)
        if ok:
            logger.info(f"매도 텔레그램 알림 전송 — {snap['stock_name']}")
        return ok
    except Exception as e:
        name = getattr(sell, "stock_name", None) or getattr(position, "stock_name", "?")
        logger.warning(f"매도 텔레그램 알림 오류 — {name}: {e}")
        return False


async def notify_sell_filled_async(snap: dict, *, remaining_qty: Optional[int] = None) -> bool:
    return await asyncio.to_thread(
        notify_sell_filled_from_snapshot,
        snap,
        remaining_qty=remaining_qty,
    )


def notify_sell_filled_from_snapshot(snap: dict, *, remaining_qty: Optional[int] = None) -> bool:
    try:
        msg = build_sell_message(
            stock_name=snap["stock_name"],
            stock_code=snap["stock_code"],
            quantity=snap["quantity"],
            sell_price=snap["sell_price"],
            buy_price=snap["buy_price"],
            sell_reason=snap["sell_reason"],
            sell_reason_detail=snap["sell_reason_detail"],
            profit_loss=snap["profit_loss"],
            remaining_qty=remaining_qty,
        )
        ok = send_trade_alert(msg)
        if ok:
            logger.info(f"매도 텔레그램 알림 전송 — {snap['stock_name']}")
        return ok
    except Exception as e:
        logger.warning(f"매도 텔레그램 알림 오류 — {snap.get('stock_name', '?')}: {e}")
        return False
