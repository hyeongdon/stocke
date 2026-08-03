"""서버(uvicorn) 야간 자동 종료."""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_exit_lock = threading.Lock()
_exit_requested = False


def is_exit_requested() -> bool:
    return _exit_requested


def request_process_exit(reason: str, *, delay_sec: float = 1.5) -> bool:
    """한 번만 프로세스 종료를 예약한다. True면 이번에 새로 예약됨."""
    global _exit_requested
    with _exit_lock:
        if _exit_requested:
            return False
        _exit_requested = True

    logger.warning(f"🛑 [SERVER_SHUTDOWN] {reason} — {delay_sec:.1f}s 후 프로세스 종료")
    try:
        from utils.auto_trade_activity_log import log_activity
        log_activity("SYSTEM", reason, "warn")
    except Exception:
        pass

    def _kill() -> None:
        try:
            time.sleep(max(0.2, float(delay_sec)))
        except Exception:
            pass
        # Hidden Start-Process 환경에서도 확실히 종료 (lifespan은 위에서 수동 정리)
        os._exit(0)

    threading.Thread(target=_kill, name="server-auto-shutdown", daemon=True).start()
    return True


async def maybe_request_auto_shutdown(now: Optional[object] = None) -> bool:
    """설정·시각 조건이면 정리 후 종료 예약. 예약했으면 True."""
    from utils.market_hours import should_auto_shutdown_server

    if not should_auto_shutdown_server(now):
        return False
    if is_exit_requested():
        return False

    try:
        from core.config import Config
        hm = str(Config.SERVER_AUTO_SHUTDOWN_TIME or "19:00")
    except Exception:
        hm = "19:00"

    try:
        from core.main import apply_auto_trade_state
        await apply_auto_trade_state(False)
    except Exception as e:
        logger.debug(f"[SERVER_SHUTDOWN] auto_trade stop skip: {e}")
    try:
        from managers.stop_loss_manager import stop_loss_manager
        await stop_loss_manager.stop_monitoring()
    except Exception as e:
        logger.debug(f"[SERVER_SHUTDOWN] stop_loss stop skip: {e}")
    try:
        from managers.auto_trade_scanner import auto_trade_scanner
        await auto_trade_scanner.stop()
    except Exception as e:
        logger.debug(f"[SERVER_SHUTDOWN] scanner stop skip: {e}")
    try:
        from managers.buy_order_executor import buy_order_executor
        await buy_order_executor.stop_processing()
    except Exception as e:
        logger.debug(f"[SERVER_SHUTDOWN] buy executor stop skip: {e}")
    try:
        import core.main as main_mod
        api = getattr(main_mod, "kiwoom_api", None)
        if api is not None:
            await api.graceful_shutdown()
    except Exception as e:
        logger.debug(f"[SERVER_SHUTDOWN] kiwoom shutdown skip: {e}")

    return request_process_exit(f"{hm} 이후 — 서버 자동 종료", delay_sec=1.5)
