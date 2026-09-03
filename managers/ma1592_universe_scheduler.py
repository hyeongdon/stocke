"""MA1592 관찰 장부 주기 정리 — 3분봉 DC(EMA15≤EMA92) · TTL 만료 (2분 주기)."""

from __future__ import annotations

import asyncio
import logging
import time

from core.config import Config
from utils.market_hours import is_krx_session

logger = logging.getLogger(__name__)

_MAINTAIN_INTERVAL_SEC = 120  # 2분 — ledger_purge_tf(기본 3분봉) DC 정리


class Ma1592UniverseScheduler:
    def __init__(self) -> None:
        self.is_running = False
        self._task = None
        self._last_run = 0.0
        self._fail_until = 0.0

    async def start_scheduler(self) -> None:
        if self.is_running:
            logger.warning("[MA1592_UNIVERSE] 스케줄러가 이미 실행 중입니다")
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"📈 [MA1592_UNIVERSE] 장부 정리 스케줄러 시작 ({_MAINTAIN_INTERVAL_SEC // 60}분)"
        )

    async def stop_scheduler(self) -> None:
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("📈 [MA1592_UNIVERSE] 장부 정리 스케줄러 중지")

    async def _loop(self) -> None:
        while self.is_running:
            try:
                now = time.time()
                if now >= self._fail_until and now - self._last_run >= _MAINTAIN_INTERVAL_SEC:
                    if is_krx_session():
                        await self._run_maintenance()
                    self._last_run = now
            except Exception as e:
                self._fail_until = time.time() + 300
                logger.warning(f"📈 [MA1592_UNIVERSE] 장부 정리 실패, 5분 후 재시도: {e}")
            await asyncio.sleep(30)

    async def _run_maintenance(self) -> None:
        from api.kiwoom_api import kiwoom_api
        from core.models import AutoTradeSettings, get_db
        from utils.ma1592 import get_universe_store, maintain_ma1592_universe, params_from_settings

        store = get_universe_store()
        if not store.l3_codes():
            store.expire_stale()
            return

        settings = None
        for db in get_db():
            settings = db.query(AutoTradeSettings).first()
            break

        chart_ttl = float(getattr(Config, "MA1592_CHART_CACHE_TTL", 60) or 60)
        result = await maintain_ma1592_universe(
            kiwoom_api,
            params=params_from_settings(settings),
            store=store,
            cache_ttl_sec=chart_ttl,
        )
        purged = result.get("purged") or []
        expired = result.get("expired") or []
        trimmed = result.get("trimmed") or []
        closed_manage = result.get("closed_manage") or []
        if purged or expired or trimmed or closed_manage:
            parts = []
            if purged:
                parts.append(f"추세전환 {len(purged)}({', '.join(purged)})")
            if trimmed:
                parts.append(f"상한초과 {len(trimmed)}({', '.join(trimmed)})")
            if expired:
                parts.append(f"만료 {len(expired)}({', '.join(expired)})")
            if closed_manage:
                parts.append(f"청산정리 {len(closed_manage)}({', '.join(closed_manage)})")
            logger.info(f"📈 [MA1592_UNIVERSE] 장부 정리 — {' · '.join(parts)}")


ma1592_universe_scheduler = Ma1592UniverseScheduler()
