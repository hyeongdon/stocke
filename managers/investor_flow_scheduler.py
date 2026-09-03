"""장중 외국인·기관 수급 스냅샷 (5분). 대시보드 요청과 분리한다."""

from __future__ import annotations

import asyncio
import logging
import time

from utils.market_indices import investor_refresh_due, refresh_investor_flow_snapshot

logger = logging.getLogger(__name__)


class InvestorFlowScheduler:
    def __init__(self) -> None:
        self.is_running = False
        self._task = None
        self._poll_sec = 30
        self._fail_until = 0.0

    async def start_scheduler(self) -> None:
        if self.is_running:
            logger.warning("[INVESTOR_FLOW] 스케줄러가 이미 실행 중입니다")
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("📊 [INVESTOR_FLOW] 수급 스냅샷 스케줄러 시작 (5분)")

    async def stop_scheduler(self) -> None:
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("📊 [INVESTOR_FLOW] 수급 스냅샷 스케줄러 중지")

    async def _loop(self) -> None:
        while self.is_running:
            try:
                now = time.time()
                if now >= self._fail_until and investor_refresh_due():
                    await asyncio.to_thread(refresh_investor_flow_snapshot)
                    logger.info("📊 [INVESTOR_FLOW] 수급 스냅샷 갱신")
            except Exception as e:
                self._fail_until = time.time() + 300
                logger.warning(f"📊 [INVESTOR_FLOW] 스냅샷 갱신 실패, 5분 후 재시도: {e}")
            await asyncio.sleep(self._poll_sec)


investor_flow_scheduler = InvestorFlowScheduler()
