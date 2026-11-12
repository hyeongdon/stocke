"""Persisted one-shot Telegram price alerts."""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from core.models import SessionLocal, TelegramPriceAlert
from notifications.telegram_notifier import TelegramNotifier
from utils.datetime_kst import utc_now_naive
from utils.market_hours import is_krx_session

logger = logging.getLogger(__name__)

POLL_SECONDS = 60
MAX_ACTIVE_ALERTS = 50
MAX_LIST_ITEMS = MAX_ACTIVE_ALERTS + 10


def _serialize(alert: TelegramPriceAlert) -> Dict:
    return {
        "id": alert.id,
        "stock_code": alert.stock_code,
        "target_price": alert.target_price,
        "status": alert.status,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "last_checked_at": alert.last_checked_at.isoformat() if alert.last_checked_at else None,
        "last_price": alert.last_price,
        "last_error": alert.last_error,
        "triggered_at": alert.triggered_at.isoformat() if alert.triggered_at else None,
    }


def list_price_alerts() -> List[Dict]:
    with SessionLocal() as db:
        alerts = (
            db.query(TelegramPriceAlert)
            .order_by(TelegramPriceAlert.status.asc(), TelegramPriceAlert.created_at.desc())
            .limit(MAX_LIST_ITEMS)
            .all()
        )
        return [_serialize(alert) for alert in alerts]


def create_price_alert(stock_code: str, target_price: int) -> Dict:
    code = str(stock_code or "").strip().upper()
    if code.startswith("A"):
        code = code[1:]
    if not code.isdigit() or not 1 <= len(code) <= 6:
        raise ValueError("종목코드는 숫자 1~6자리로 입력하세요.")
    code = code.zfill(6)

    try:
        price = int(target_price)
    except (TypeError, ValueError) as exc:
        raise ValueError("알림 가격은 원 단위 정수로 입력하세요.") from exc
    if price < 1 or price > 2_000_000_000:
        raise ValueError("알림 가격은 1원 이상 20억원 이하로 입력하세요.")

    with SessionLocal() as db:
        active_count = db.query(TelegramPriceAlert).filter(
            TelegramPriceAlert.status == "active"
        ).count()
        if active_count >= MAX_ACTIVE_ALERTS:
            raise ValueError(f"활성 가격 알림은 최대 {MAX_ACTIVE_ALERTS}개까지 등록할 수 있습니다.")
        alert = TelegramPriceAlert(stock_code=code, target_price=price)
        db.add(alert)
        db.commit()
        db.refresh(alert)
        return _serialize(alert)


def delete_price_alert(alert_id: int) -> bool:
    with SessionLocal() as db:
        alert = db.get(TelegramPriceAlert, alert_id)
        if alert is None:
            return False
        db.delete(alert)
        db.commit()
        return True


def _record_check(alert_id: int, price: Optional[int], error: Optional[str] = None) -> None:
    with SessionLocal() as db:
        alert = db.get(TelegramPriceAlert, alert_id)
        if alert is None or alert.status != "active":
            return
        alert.last_checked_at = utc_now_naive()
        alert.last_price = price
        alert.last_error = error
        db.commit()


def _active_alerts() -> List[Dict]:
    with SessionLocal() as db:
        alerts = (
            db.query(TelegramPriceAlert)
            .filter(TelegramPriceAlert.status == "active")
            .order_by(TelegramPriceAlert.id.asc())
            .all()
        )
        return [_serialize(alert) for alert in alerts]


def _mark_triggered(alert_id: int) -> None:
    with SessionLocal() as db:
        alert = db.get(TelegramPriceAlert, alert_id)
        if alert is None or alert.status != "active":
            return
        alert.status = "sent"
        alert.triggered_at = utc_now_naive()
        alert.last_error = None
        db.commit()


class TelegramPriceAlertScheduler:
    def __init__(self, api, notifier: Optional[TelegramNotifier] = None):
        self.api = api
        self.notifier = notifier or TelegramNotifier()
        self._stop = asyncio.Event()
        self._run_lock = asyncio.Lock()

    async def run(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except Exception:
                logger.exception("텔레그램 가격 알림 점검 중 오류")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    async def poll_once(self) -> None:
        if not is_krx_session():
            return
        if not self.notifier.is_configured():
            logger.warning("텔레그램 가격 알림 감시 보류 — TELEGRAM_BOT_TOKEN/CHAT_ID 미설정")
            return

        async with self._run_lock:
            alerts = _active_alerts()
            by_code: Dict[str, List[Dict]] = {}
            for alert in alerts:
                by_code.setdefault(alert["stock_code"], []).append(alert)

            for code, code_alerts in by_code.items():
                try:
                    price = await self.api.get_current_price(code)
                except Exception as exc:
                    logger.exception("가격 알림 현재가 조회 실패 (code=%s)", code)
                    for alert in code_alerts:
                        _record_check(alert["id"], None, str(exc)[:255])
                    continue

                if price is None or int(price) <= 0:
                    for alert in code_alerts:
                        _record_check(alert["id"], None, "현재가 조회 실패")
                    continue

                current_price = int(price)
                for alert in code_alerts:
                    _record_check(alert["id"], current_price)
                    if current_price > int(alert["target_price"]):
                        continue

                    message = (
                        "🔔 가격 도달 알림\n"
                        f"종목코드: {code}\n"
                        f"현재가: {current_price:,}원\n"
                        f"목표가: {int(alert['target_price']):,}원 이하"
                    )
                    sent = await asyncio.to_thread(
                        self.notifier.send_message,
                        message,
                        notification_key=f"price.alert.{alert['id']}",
                    )
                    if sent:
                        _mark_triggered(alert["id"])
                    else:
                        _record_check(alert["id"], current_price, "텔레그램 전송 실패 — 다음 감시 때 재시도합니다.")
                        logger.error("텔레그램 가격 알림 전송 실패 (alert_id=%s)", alert["id"])
