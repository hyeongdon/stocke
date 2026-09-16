"""
WebSocket 실시간 체결 데이터 → 3분봉 로컬 집계

REST API(ka10080) 호출 없이 WebSocket REAL 메시지를 구독해
메모리에서 직접 N분봉을 만든다. API 제한 없음.

사용법:
    # KiwoomAPI connect 후
    await kiwoom_api.subscribe_realtime_stock("005930")
    bars = realtime_candle_manager.get_candles("005930", period_min=3, count=20)
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from typing import Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
#  상수
# ──────────────────────────────────────────────────────────────────
_MAX_BARS_PER_STOCK = 200   # 종목당 최대 보관 봉 수
_MARKET_OPEN  = dt_time(9, 0)
_MARKET_CLOSE = dt_time(15, 30)


@dataclass
class Bar:
    """OHLCV 봉"""
    ts: str      # 'HHMM' (봉 시작 시각, KST)
    open:   int = 0
    high:   int = 0
    low:    int = 0
    close:  int = 0
    volume: int = 0
    trades: int = 0   # 체결 횟수

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "open":   self.open,
            "high":   self.high,
            "low":    self.low,
            "close":  self.close,
            "volume": self.volume,
        }


def _round_to_period(hhmmss: str, period_min: int) -> str:
    """HHMMSS → 봉 시작 시각 'HHMM' (period_min 분 단위 내림)."""
    h = int(hhmmss[:2])
    m = int(hhmmss[2:4])
    m_aligned = (m // period_min) * period_min
    return f"{h:02d}{m_aligned:02d}"


def _parse_price(raw) -> int:
    """키움 가격 필드(부호 포함 문자열 또는 숫자) → 절댓값 정수."""
    try:
        return abs(int(str(raw or "0").replace(",", "").strip()))
    except (ValueError, TypeError):
        return 0


# ──────────────────────────────────────────────────────────────────
#  RealtimeCandleManager
# ──────────────────────────────────────────────────────────────────
class RealtimeCandleManager:
    """
    WebSocket REAL 체결 메시지를 수신해 N분봉을 인메모리로 집계한다.

    KiwoomAPI._message_handler()가 REAL 메시지를 받으면
    on_realtime_trade()를 호출해야 한다.
    """

    def __init__(self, period_min: int = 3):
        self.period_min = period_min
        # 구독 종목 코드 집합
        self._subscribed: Set[str] = set()
        # 완성된 봉 히스토리: {stock_code: deque[Bar]}
        self._history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=_MAX_BARS_PER_STOCK)
        )
        # 현재 진행 중인 봉: {stock_code: Bar}
        self._current: Dict[str, Bar] = {}
        # 콜백: 봉 완성 시 호출 [(stock_code, bar)]
        self._bar_callbacks: List[Callable] = []
        # KiwoomAPI 참조 (subscribe 시 REG 전송용)
        self._kiwoom_api = None
        self._lock = asyncio.Lock()

    # ── 설정 ────────────────────────────────────────────────────────

    def set_kiwoom_api(self, api):
        """KiwoomAPI 인스턴스 주입 (REG/UNREG 전송용)."""
        self._kiwoom_api = api

    def on_bar_complete(self, callback: Callable):
        """봉 완성 시 호출될 콜백 등록. callback(stock_code: str, bar: Bar)."""
        self._bar_callbacks.append(callback)

    # ── 구독 관리 ────────────────────────────────────────────────────

    async def subscribe(self, stock_code: str) -> bool:
        """
        종목 실시간 체결 구독.
        KiwoomAPI 연결 후 호출해야 REG 명령이 전송된다.
        """
        code = stock_code.strip().lstrip("A")
        if code in self._subscribed:
            return True
        self._subscribed.add(code)
        logger.info(f"📡 [REALTIME_CANDLE] 구독 등록: {code}")
        if self._kiwoom_api:
            return await self._kiwoom_api.subscribe_realtime_stock(code)
        return True

    async def unsubscribe(self, stock_code: str) -> bool:
        code = stock_code.strip().lstrip("A")
        self._subscribed.discard(code)
        self._current.pop(code, None)
        logger.info(f"📡 [REALTIME_CANDLE] 구독 해제: {code}")
        if self._kiwoom_api:
            return await self._kiwoom_api.unsubscribe_realtime_stock(code)
        return True

    async def resubscribe_all(self):
        """WebSocket 재연결 후 구독 복구."""
        if not self._subscribed:
            return
        codes = list(self._subscribed)
        logger.info(f"📡 [REALTIME_CANDLE] 재구독 {len(codes)}종목")
        for code in codes:
            if self._kiwoom_api:
                await self._kiwoom_api.subscribe_realtime_stock(code)
            await asyncio.sleep(0.05)

    # ── 체결 데이터 수신 ─────────────────────────────────────────────

    async def on_realtime_trade(self, stock_code: str, values: dict):
        """
        KiwoomAPI._message_handler()에서 REAL(0D) 메시지 수신 시 호출.

        values 예시 (키움 WebSocket REAL 0D):
            "20"  : "093012"   체결시간 HHMMSS
            "10"  : "70500"    현재가(체결가)
            "16"  : "70100"    시가
            "17"  : "71000"    고가
            "18"  : "70100"    저가
            "561" : "1234"     체결량
        """
        code = stock_code.strip().lstrip("A")
        if code not in self._subscribed:
            return

        hhmmss = str(values.get("20") or values.get("체결시간") or "")
        if len(hhmmss) < 6:
            return

        price  = _parse_price(values.get("10") or values.get("13") or values.get("현재가"))
        volume = _parse_price(values.get("561") or values.get("체결량"))
        if price <= 0:
            return

        bar_ts = _round_to_period(hhmmss, self.period_min)

        async with self._lock:
            cur = self._current.get(code)

            if cur is None or cur.ts != bar_ts:
                # 봉 전환
                if cur is not None:
                    self._history[code].append(cur)
                    asyncio.create_task(self._fire_callbacks(code, cur))

                # 새 봉 시작
                self._current[code] = Bar(
                    ts=bar_ts,
                    open=price, high=price, low=price, close=price,
                    volume=volume, trades=1,
                )
            else:
                # 진행 중인 봉 업데이트
                cur.high   = max(cur.high, price)
                cur.low    = min(cur.low, price)
                cur.close  = price
                cur.volume += volume
                cur.trades += 1

    async def _fire_callbacks(self, code: str, bar: Bar):
        for cb in self._bar_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(code, bar)
                else:
                    cb(code, bar)
            except Exception as e:
                logger.warning(f"📡 [REALTIME_CANDLE] 콜백 오류: {e}")

    # ── 조회 API ────────────────────────────────────────────────────

    def get_candles(
        self,
        stock_code: str,
        count: int = 20,
        include_current: bool = True,
    ) -> List[dict]:
        """
        최근 N개 3분봉 반환.
        include_current=True면 아직 완성되지 않은 현재 봉도 포함.

        반환: [{"ts":"0900","open":...,"high":...,"low":...,"close":...,"volume":...}, ...]
        """
        code = stock_code.strip().lstrip("A")
        bars: List[Bar] = list(self._history.get(code, []))

        if include_current and code in self._current:
            bars = bars + [self._current[code]]

        return [b.to_dict() for b in bars[-count:]]

    def get_latest_bar(self, stock_code: str) -> Optional[dict]:
        """가장 최신 봉(완성 또는 진행 중) 반환."""
        bars = self.get_candles(stock_code, count=1, include_current=True)
        return bars[-1] if bars else None

    def get_subscribed_codes(self) -> List[str]:
        return list(self._subscribed)

    def is_subscribed(self, stock_code: str) -> bool:
        return stock_code.strip().lstrip("A") in self._subscribed

    def get_stats(self) -> dict:
        return {
            "subscribed_count": len(self._subscribed),
            "subscribed": sorted(self._subscribed),
            "period_min": self.period_min,
            "history_counts": {
                code: len(bars) for code, bars in self._history.items()
            },
        }

    # ── 유틸 ────────────────────────────────────────────────────────

    def flush_old_bars(self):
        """장 종료 후 당일 봉 히스토리 정리 (메모리 관리용)."""
        self._history.clear()
        self._current.clear()
        logger.info("📡 [REALTIME_CANDLE] 봉 히스토리 초기화 완료")


# 전역 인스턴스 (3분봉)
realtime_candle_manager = RealtimeCandleManager(period_min=3)
