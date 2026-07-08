"""
장 시작 시 자동매매 엔진 자동 기동 스케줄러.
거래일 08:50~매매 종료(trade_end_time) 구간에 스캐너·매수 실행기를 자동 기동한다.
실제 매수는 trade_start_time 이후 in_trade_hours()에서만 수행된다.
"""

import asyncio
import logging
from datetime import date, datetime, time as dt_time
from typing import Optional

from sqlalchemy.orm import Session

from core.models import AutoTradeSettings, get_db
from managers.auto_trade_scanner import auto_trade_scanner
from managers.buy_order_executor import buy_order_executor
from utils.auto_trade_activity_log import log_activity
from utils.market_hours import is_krx_trading_day

logger = logging.getLogger(__name__)

# 엔진 자동 기동 하한(내부 고정). 사용자 설정 없음 — 매매 시작과 별개.
_ENGINE_SESSION_START = dt_time(8, 50)


def is_weekday(now: Optional[datetime] = None) -> bool:
    """하위 호환 — KRX 거래일(평일·비휴장)과 동일."""
    return is_krx_trading_day(now)


def in_engine_session(settings: AutoTradeSettings, now: Optional[datetime] = None) -> bool:
    """거래일 08:50 ~ 매매 종료 시각 — 엔진(스캐너·매수기) 가동 구간."""
    now = now or datetime.now()
    if not is_krx_trading_day(now):
        return False
    if now.time() < _ENGINE_SESSION_START:
        return False
    try:
        eh, em = map(int, (settings.trade_end_time or "15:20").split(":"))
        return now.time() <= dt_time(eh, em)
    except Exception:
        return True


def should_auto_start_now(settings: Optional[AutoTradeSettings], now: Optional[datetime] = None) -> bool:
    """서버 기동 시 거래일·엔진 세션 안이면 자동매매 실행기를 올린다."""
    if not settings:
        return False
    now = now or datetime.now()
    return in_engine_session(settings, now)


def engines_need_start() -> bool:
    return not auto_trade_scanner.is_running or not buy_order_executor.is_running


def engines_running() -> bool:
    return auto_trade_scanner.is_running or buy_order_executor.is_running


class MarketOpenScheduler:
    def __init__(self):
        self.is_running = False
        self._task = None
        self._poll_sec = 30
        self._last_auto_start_date: Optional[date] = None
        self._last_auto_stop_date: Optional[date] = None

    async def start_scheduler(self):
        if self.is_running:
            logger.warning("[MARKET_OPEN] 스케줄러가 이미 실행 중입니다")
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("🕗 [MARKET_OPEN] 장 시작 자동매매 스케줄러 시작")

    async def stop_scheduler(self):
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🕗 [MARKET_OPEN] 장 시작 자동매매 스케줄러 중지")

    async def _loop(self):
        while self.is_running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"🕗 [MARKET_OPEN] tick 오류: {e}")
            await asyncio.sleep(self._poll_sec)

    async def _tick(self):
        settings = None
        for db in get_db():
            session: Session = db
            settings = session.query(AutoTradeSettings).first()
            break
        if not settings:
            return
        if not is_weekday():
            return

        now = datetime.now()
        today = now.date()

        if not in_engine_session(settings, now):
            if (
                settings.is_enabled
                and engines_running()
                and self._last_auto_stop_date != today
            ):
                from core.main import apply_auto_trade_state

                await apply_auto_trade_state(False)
                self._last_auto_stop_date = today
                log_activity("SYSTEM", "매매 종료 시각 이후 자동매매 루프 자동 중지", "warn")
                logger.info("🕗 [MARKET_OPEN] 매매 종료 이후 자동매매 루프 중지")
            return

        if self._last_auto_start_date != today:
            await self._enable_auto_trade(settings, reason="거래일 자동 기동")
            self._last_auto_start_date = today
            self._last_auto_stop_date = None
            return

        if settings.is_enabled and engines_need_start():
            await self._apply_auto_trade_state()
            log_activity(
                "SYSTEM",
                "자동매매 엔진 재기동 (중단 감지 · 장중 세션)",
                "warn",
            )
            logger.info("🕗 [MARKET_OPEN] 자동매매 엔진 재기동")

    async def _enable_auto_trade(self, settings: AutoTradeSettings, reason: str):
        changed = False
        for db in get_db():
            session: Session = db
            row = session.query(AutoTradeSettings).first()
            if not row:
                break
            if not row.is_enabled:
                row.is_enabled = True
                row.updated_at = datetime.utcnow()
                session.commit()
                changed = True
            settings = row
            break

        await self._apply_auto_trade_state()
        msg = reason
        log_activity("SYSTEM", msg, "info")
        logger.info(f"🕗 [MARKET_OPEN] {msg}" + (" · DB is_enabled 갱신" if changed else ""))

    async def _apply_auto_trade_state(self):
        from core.main import apply_auto_trade_state

        await apply_auto_trade_state(True)

    def mark_started_today(self):
        """서버 기동 시 이미 자동시작한 경우 당일 중복 기동 방지."""
        self._last_auto_start_date = datetime.now().date()


market_open_scheduler = MarketOpenScheduler()
