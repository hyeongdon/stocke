"""
종목×테마 연관도 일별 점수 재계산 배치.

사용:
  python scripts/theme_score_daily_batch.py
  python scripts/theme_score_daily_batch.py --biz-date 2026-07-11
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from core.models import get_db  # noqa: E402
from utils.theme_score_engine import compute_theme_scores_for_date  # noqa: E402

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "theme_score_daily_batch.log")


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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="종목×테마 연관도 점수 배치")
    p.add_argument("--biz-date", type=str, default=None, help="YYYY-MM-DD (기본: KST 오늘)")
    return p.parse_args()


def main() -> int:
    setup_logging()
    args = parse_args()
    log = logging.getLogger(__name__)
    biz: date | None = None
    if args.biz_date:
        biz = date.fromisoformat(args.biz_date)
    log.info("theme_score_daily_batch 시작 biz_date=%s", biz or "today")
    for db in get_db():
        result = compute_theme_scores_for_date(db, biz_date=biz)
        log.info("theme_score_daily_batch 결과: %s", result)
        return 0 if result.get("ok") else 1
    log.error("DB 세션 없음")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
