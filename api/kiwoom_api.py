"""Public Kiwoom API facade, preserving the existing import path."""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional

from api.api_rate_limiter import api_rate_limiter
from api.kiwoom_account import _AccountMixin
from api.kiwoom_api_shared import (
    SANGTTA_CHANGE_RATE_RANK_FILTERS,
    SANGTTA_UNIVERSE_MIN_CHANGE_RATE,
    SCREENER_VOLUME_RANK_FILTERS,
    _normalize_stock_code,
    _parse_kiwoom_float,
    _parse_kiwoom_int,
    _parse_kiwoom_rate,
    invalidate_account_balance_cache,
    logger,
)
from api.kiwoom_market_data import _MarketDataMixin
from api.kiwoom_portfolio import _PortfolioMixin
from api.kiwoom_realtime import _RealtimeMixin, ConditionSearchSession
from api.kiwoom_screener import _ScreenerMixin
from api.kiwoom_stock_analytics import _StockAnalyticsMixin
from api.token_manager import TokenManager, get_token_manager
from core.config import Config

__all__ = [
    "KiwoomAPI",
    "ConditionSearchSession",
    "TokenManager",
    "SANGTTA_CHANGE_RATE_RANK_FILTERS",
    "SANGTTA_UNIVERSE_MIN_CHANGE_RATE",
    "SCREENER_VOLUME_RANK_FILTERS",
    "kiwoom_api",
    "invalidate_account_balance_cache",
    "_parse_kiwoom_float",
    "_parse_kiwoom_int",
    "_parse_kiwoom_rate",
]


class KiwoomAPI(
    _RealtimeMixin,
    _MarketDataMixin,
    _ScreenerMixin,
    _PortfolioMixin,
    _StockAnalyticsMixin,
    _AccountMixin,
):
    def __init__(self):
        self.base_url = Config.KIWOOM_BASE_URL
        self.ws_url = Config.KIWOOM_WS_URL
        self.token_manager = get_token_manager()
        self.websocket = None
        self.condition_callbacks = {}
        self.running = False
        # 재연결 관련 속성 추가
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5  # 초
        self.auto_reconnect = True
        self.message_task = None
        
        # 현재가 캐시 (종목코드 -> (가격, 시간)) - API 호출 최소화
        self._price_cache = {}
        self._price_cache_ttl = 30  # 30초 캐시 (API 제한 고려)
        # 차트 캐시 (종목+주기 -> (bars, timestamp))
        self._chart_cache: Dict[str, tuple] = {}
        self._chart_cache_ttl = 300  # 분봉 등: 5분
        self._daily_chart_cache_ttl = 43200  # 일봉: 12시간 (ATR은 stop_loss에서 일 1회 캐시)

        # 실시간 체결 구독 종목 집합 (WebSocket REG/UNREG 관리)
        self._realtime_subscribed: set = set()
        # 실시간 REAL 메시지 수신 콜백 [(stock_code, values) -> Awaitable]
        self._realtime_trade_callbacks: list = []

    @staticmethod
    def normalize_stock_code(stock_code: str) -> str:
        """키움 종목코드 정규화 — A 접두사·거래소 접미사(_NX/_AL/_L/_K 등) 제거."""
        return _normalize_stock_code(stock_code)

    @staticmethod
    def minute_chart_stk_cd(stock_code: str) -> str:
        """분봉 조회용 종목코드 — KRX+NXT 통합(_AL).

        HTS 통합/NXT 차트와 MA·거래량을 맞추기 위해 분봉(ka10080)은
        기본 6자리가 아니라 `{code}_AL` 로 요청한다. 일봉·주문은 기존 6자리.
        """
        return KiwoomAPI.quote_stk_cd(stock_code, "AL")

    @staticmethod
    def quote_stk_cd(stock_code: str, venue: str = "AL") -> str:
        """시세 조회용 종목코드 — venue: KRX(6자리) | NX | AL(통합)."""
        base = KiwoomAPI.normalize_stock_code(stock_code)
        if not base or len(base) != 6:
            return base
        v = (venue or "AL").strip().upper()
        if v in ("", "KRX", "KR"):
            return base
        if v in ("NX", "NXT"):
            return f"{base}_NX"
        return f"{base}_AL"

    async def _acquire_api_slot(
        self,
        api_name: str,
        max_wait: float | None = None,
        *,
        priority=None,
    ) -> bool:
        """레이트 리미터 슬롯 확보 — 간격·분당한도·LIMITED면 대기 후 재시도."""
        from utils.api_traffic_guard import (
            APIPriority,
            effective_max_wait,
            should_yield_low_priority,
        )
        pri = priority if priority is not None else APIPriority.NORMAL
        if max_wait is None:
            max_wait = effective_max_wait(pri)
        deadline = time.time() + max_wait
        while time.time() < deadline:
            # LOW는 스캔 버스트 중 양보하되, max_wait 안에서는 대기 후 재시도
            if pri == APIPriority.LOW and should_yield_low_priority():
                wait_sec = min(0.5, deadline - time.time())
                if wait_sec <= 0:
                    return False
                await asyncio.sleep(wait_sec)
                continue
            if not api_rate_limiter.is_api_available():
                wait_sec = min(
                    max(api_rate_limiter.seconds_until_available(), 0.2),
                    5.0,
                    deadline - time.time(),
                )
                if wait_sec > 0:
                    await asyncio.sleep(wait_sec)
                    continue
                # LIMITED가 방금 풀렸을 수 있음
            if api_rate_limiter.record_api_call(api_name):
                return True
            wait_sec = min(
                max(
                    api_rate_limiter.seconds_until_available()
                    or api_rate_limiter.min_call_interval,
                    0.2,
                ),
                5.0,
                deadline - time.time(),
            )
            if wait_sec > 0:
                await asyncio.sleep(wait_sec)
        return False

    @staticmethod
    def _is_kiwoom_token_invalid(error_msg: str, data: Optional[Dict] = None) -> bool:
        msg_lower = f"{error_msg or ''} {data or {}}".lower()
        return ("8005" in msg_lower) or (
            "token" in msg_lower and ("invalid" in msg_lower or "유효하지" in msg_lower)
        )

    async def _reauthenticate_async(self) -> bool:
        """토큰 무효 시 재발급 (동기 requests가 이벤트 루프를 막지 않도록 스레드 실행)."""
        self.token_manager.access_token = None
        self.token_manager.token_expiry = None
        return await asyncio.to_thread(self.authenticate)

    def authenticate(self) -> bool:
        """키움증권 API 인증"""
        try:
            return self.token_manager.authenticate()
        except Exception as e:
            logger.error(f"키움증권 API 인증 실패: {e}")
            return False


kiwoom_api = KiwoomAPI()
