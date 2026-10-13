"""
만료된 조건식 관심종목 정리 (DB 전용 · 키움 API 미호출)

크론에서 서버 API(/watchlist/sync/cleanup)를 부르면 로그인 미들웨어에 막혀 401 이 나므로
서버 프로세스를 거치지 않고 같은 정리 로직을 직접 실행한다.

사용:
  python scripts/watchlist_cleanup_batch.py
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "watchlist_cleanup_batch.log")


def setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _counts() -> tuple[int, int]:
    from core.models import ConditionWatchlistSync, WatchlistStock, get_db

    for db in get_db():
        watch = db.query(WatchlistStock).filter(WatchlistStock.source_type == "CONDITION").count()
        sync = db.query(ConditionWatchlistSync).count()
        return watch, sync
    return 0, 0


def main() -> int:
    setup_logging()
    log = logging.getLogger("watchlist_cleanup")
    from managers.watchlist_sync_manager import watchlist_sync_manager

    log.info("=== 관심종목 만료 정리 시작 ===")
    before = _counts()
    try:
        asyncio.run(watchlist_sync_manager._cleanup_expired_stocks())
    except Exception as e:
        log.error("정리 실패: %s", e)
        return 1
    after = _counts()
    log.info(
        "조건식 관심종목 %d → %d · 동기화 기록 %d → %d",
        before[0], after[0], before[1], after[1],
    )
    log.info("=== 관심종목 만료 정리 종료 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
