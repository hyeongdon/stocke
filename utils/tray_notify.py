"""Windows 시스템 트레이 풍선 알림 큐.

서버(매수/매도)가 JSONL로 쌓으면 `scripts/server_tray.ps1`이 읽어
NotifyIcon.ShowBalloonTip 으로 표시합니다. 트레이가 꺼져 있으면 파일이
쌓이다가, 트레이 재시작 후 최근 항목만 보여 줍니다.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAY_NOTIFY_FILE = os.path.join(PROJECT_ROOT, "logs", "_tray_notify.jsonl")
# 너무 오래된 알림은 트레이가 나중에 켜져도 스킵
MAX_AGE_SEC = 15 * 60


def enqueue_tray_notify(
    *,
    title: str,
    body: str,
    kind: str = "info",
) -> bool:
    """트레이 풍선 큐에 한 건 추가. kind: info|warning|error."""
    try:
        os.makedirs(os.path.dirname(TRAY_NOTIFY_FILE), exist_ok=True)
        # Balloon tip 제한에 맞춰 짧게
        title = (title or "Stocke")[:60]
        body = (body or "").replace("\r\n", "\n").strip()
        if len(body) > 240:
            body = body[:237] + "..."
        icon = (kind or "info").strip().lower()
        if icon not in ("info", "warning", "error"):
            icon = "info"
        row = {
            "ts": time.time(),
            "title": title,
            "body": body,
            "icon": icon,
        }
        with open(TRAY_NOTIFY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        logger.debug("트레이 알림 큐 기록 실패: %s", e)
        return False


def enqueue_trade_buy_tray(
    *,
    stock_name: str,
    stock_code: str,
    quantity: int,
    price: int,
    is_add_buy: bool = False,
    strategy: Optional[str] = None,
) -> bool:
    from notifications.trade_alert import strategy_label_ko

    tag = strategy_label_ko(strategy)
    buy_type = "추가매수" if is_add_buy else "신규매수"
    title = f"Stocke · {buy_type}"
    body = (
        f"{stock_name}({stock_code})\n"
        f"{quantity:,}주 @ {int(price):,}원 · {tag}"
    )
    return enqueue_tray_notify(title=title, body=body, kind="info")


def enqueue_trade_sell_tray(
    *,
    stock_name: str,
    stock_code: str,
    quantity: int,
    sell_price: int,
    sell_reason: str,
    profit_loss: Optional[int] = None,
    profit_loss_rate: Optional[float] = None,
) -> bool:
    from notifications.trade_alert import sell_reason_ko, _fmt_pnl

    reason = sell_reason_ko(
        sell_reason,
        profit_loss=profit_loss,
        profit_loss_rate=profit_loss_rate,
    )
    title = f"Stocke · 매도 · {reason}"
    pnl = ""
    if profit_loss is not None:
        pnl = f"\n{_fmt_pnl(profit_loss, profit_loss_rate)}"
    body = (
        f"{stock_name}({stock_code})\n"
        f"{quantity:,}주 @ {int(sell_price):,}원"
        f"{pnl}"
    )
    icon = "warning" if (profit_loss is not None and profit_loss < 0) else "info"
    return enqueue_tray_notify(title=title, body=body, kind=icon)
