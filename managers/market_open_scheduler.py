"""
장 시작 시 자동매매 엔진 자동 기동 스케줄러.
거래일 — 레거시·상따·돌파 시간창 합집합 구간에 스캐너·매수 실행기를 자동 기동한다.
손절/익절 모니터도 동일 합집합 매매 시간에 연동된다.
"""

import asyncio
import logging
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from core.models import AutoTradeSettings, get_db
from managers.auto_trade_scanner import auto_trade_scanner
from managers.buy_order_executor import buy_order_executor
from utils.auto_trade_activity_log import log_activity
from utils.datetime_kst import as_kst, kst_today, utc_now_naive
from utils.market_hours import in_auto_trade_engine_session, is_krx_trading_day

logger = logging.getLogger(__name__)


def is_weekday(now: Optional[datetime] = None) -> bool:
    """하위 호환 — KRX 거래일(평일·비휴장)과 동일."""
    return is_krx_trading_day(now)


def in_engine_session(settings: AutoTradeSettings, now: Optional[datetime] = None) -> bool:
    """거래일 trade_start ~ trade_end — 엔진(스캐너·매수기) 가동 구간."""
    return in_auto_trade_engine_session(settings, now)


def should_auto_start_now(settings: Optional[AutoTradeSettings], now: Optional[datetime] = None) -> bool:
    """서버 기동 시 거래일·엔진 세션 안이면 자동매매 실행기를 올린다."""
    if not settings:
        return False
    now = as_kst(now)
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
        # 실시간 봉 초기화(HOLDING 재구독)는 엔진 세션과 독립적으로
        # 손절 모니터 시작 시각(08:00)에 일 1회 실행
        self._realtime_init_date: Optional[date] = None

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
        # 야간 자동 종료(기본 19:00) — 매매 루프보다 우선
        try:
            from utils.server_auto_shutdown import maybe_request_auto_shutdown
            if await maybe_request_auto_shutdown():
                self.is_running = False
                return
        except Exception as e:
            logger.warning(f"🕗 [MARKET_OPEN] 서버 자동종료 점검 경고: {e}")

        settings = None
        for db in get_db():
            session: Session = db
            settings = session.query(AutoTradeSettings).first()
            break
        if not settings:
            return
        if not is_weekday():
            return

        now = as_kst()
        today = now.date()

        # ── 실시간 봉 08:00 초기화 (엔진 세션과 독립) ──────────────────
        # 손절 모니터가 08:00에 시작하므로, 그 전에 HOLDING 재구독 완료.
        # 엔진이 아직 안 떠도, 서버가 08:00에 켜져 있기만 하면 실행됨.
        from utils.market_hours import STOP_LOSS_MONITOR_START
        realtime_ready = now.time() >= STOP_LOSS_MONITOR_START  # 08:00 이후
        if realtime_ready and self._realtime_init_date != today:
            self._realtime_init_date = today
            try:
                from managers.realtime_candle_manager import realtime_candle_manager
                await realtime_candle_manager.on_market_open()
                logger.info("🕗 [MARKET_OPEN] 08:00 실시간 봉 초기화 + HOLDING 재구독 완료")
            except Exception as e:
                logger.warning(f"🕗 [MARKET_OPEN] 실시간 봉 초기화 오류: {e}")

        if not in_engine_session(settings, now):
            if engines_running():
                from core.main import apply_auto_trade_state

                await apply_auto_trade_state(False)
                if self._last_auto_stop_date != today:
                    log_activity("SYSTEM", "매매 종료 시각 이후 자동매매 루프 자동 중지", "warn")
                    logger.info("🕗 [MARKET_OPEN] 매매 종료 이후 자동매매 루프 중지")
                    # ※ 실시간 구독은 여기서 해제하지 않는다.
                    # 애프터장(NXT 야간장)이 20:00까지 운영되므로
                    # 손절 모니터가 20:00까지 3분봉 데이터를 사용할 수 있어야 함.
                    # 실시간 구독 해제는 20:00 배치(run_realtime_candle_cleanup.bat)
                    # 또는 서버 자동 종료(20:00) 시 메모리 소멸로 처리한다.
                self._last_auto_stop_date = today
            return

        # 장중: 일일 한도 OFF 등으로 신규매수가 꺼져 있어도 손절/동기화 루프는 유지
        try:
            from core.main import _schedule_stop_loss_monitoring
            from managers.stop_loss_manager import stop_loss_manager

            if not stop_loss_manager.monitoring_task_running():
                _schedule_stop_loss_monitoring()
                log_activity(
                    "SYSTEM",
                    "장중 손절/동기화 루프 재기동 (자동매매 ON/OFF 무관)",
                    "warn",
                )
                logger.info("🕗 [MARKET_OPEN] 손절/동기화 루프 재기동")
        except Exception as e:
            logger.warning(f"🕗 [MARKET_OPEN] 손절 루프 점검 경고: {e}")

        if self._last_auto_start_date != today:
            # on_market_open()은 08:00 블록에서 이미 처리됨 (중복 호출 없음)
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
                row.updated_at = utc_now_naive()
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
        self._last_auto_start_date = kst_today()


market_open_scheduler = MarketOpenScheduler()
