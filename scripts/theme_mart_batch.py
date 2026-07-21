"""
테마/키워드 매핑 배치 (Phase 1)

사용 예:
  python scripts/theme_mart_batch.py
  python scripts/theme_mart_batch.py --top-n 20 --no-news
  python scripts/theme_mart_batch.py --notify-only
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from core.models import get_db  # noqa: E402
from notifications.theme_batch_report import (  # noqa: E402
    build_notify_only_result,
    send_theme_batch_report,
)
from utils.theme_map_store import refresh_theme_mapping_snapshot  # noqa: E402

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "theme_mart_batch.log")


def setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="테마/키워드 매핑 스냅샷 배치")
    p.add_argument("--top-n", type=int, default=0, help="수집 테마 수 (0=전체, 기본 0)")
    p.add_argument("--no-news", action="store_true", help="뉴스 키워드 추출 비활성화")
    p.add_argument(
        "--no-telegram",
        action="store_true",
        help="완료/실패 텔레그램 일일 리포트 비활성화",
    )
    p.add_argument(
        "--notify-only",
        action="store_true",
        help="배치 없이 당일 DB 스냅샷으로 텔레그램 리포트만 전송",
    )
    return p.parse_args()


def _send_report(db, result: dict, duration_sec, enabled: bool, log: logging.Logger) -> None:
    if not enabled:
        log.info("텔레그램 리포트 스킵 (--no-telegram)")
        return
    try:
        ok = send_theme_batch_report(db, result, duration_sec=duration_sec)
        log.info("텔레그램 리포트 전송: %s", "OK" if ok else "FAIL/SKIP")
    except Exception as e:
        log.exception("텔레그램 리포트 전송 중 오류: %s", e)


def main() -> int:
    setup_logging()
    args = parse_args()
    log = logging.getLogger(__name__)
    telegram_on = not bool(args.no_telegram)
    started = time.monotonic()

    for db in get_db():
        if args.notify_only:
            result = build_notify_only_result(db)
            log.info("notify-only 리포트: %s", result)
            _send_report(db, result, None, telegram_on, log)
            return 0 if result.get("ok") else 1

        include_news = not bool(args.no_news)
        log.info("theme_mart_batch 시작 top_n=%s include_news=%s", args.top_n, include_news)
        try:
            top_n_arg = int(args.top_n)
            top_n = 0 if top_n_arg <= 0 else min(max(top_n_arg, 5), 200)
            result = refresh_theme_mapping_snapshot(
                db,
                top_n=top_n,
                include_news_keywords=include_news,
            )
            elapsed = time.monotonic() - started
            log.info("theme_mart_batch 결과: %s (소요 %.1fs)", result, elapsed)
            _send_report(db, result, elapsed, telegram_on, log)
            if not result.get("ok"):
                return 1
            return 0
        except Exception as e:
            elapsed = time.monotonic() - started
            log.exception("theme_mart_batch 예외: %s", e)
            fail = {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "themes": 0,
                "edges": 0,
                "keywords": 0,
                "biz_date": None,
                "scores": {"ok": False, "error": "batch crashed"},
                "traceback": traceback.format_exc()[-800:],
            }
            _send_report(db, fail, elapsed, telegram_on, log)
            return 1

    log.error("DB 세션을 열지 못했습니다.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
