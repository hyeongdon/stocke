"""
앱·배치 로그를 최근 7일만 남기고 정리

사용:
  python scripts/log_cleanup_batch.py
  python scripts/log_cleanup_batch.py --days 7 --dry-run
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from utils.log_cleanup import (  # noqa: E402
    DEFAULT_KEEP_DAYS,
    cleanup_logs,
    format_summary,
)

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "log_cleanup_batch.log")


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
    p = argparse.ArgumentParser(description="로그 파일을 최근 N일만 남기고 정리")
    p.add_argument("--days", type=int, default=DEFAULT_KEEP_DAYS, help="보관 일수 (기본 7)")
    p.add_argument("--dry-run", action="store_true", help="삭제·자르기 없이 대상만 출력")
    p.add_argument(
        "--root",
        default=PROJECT_ROOT,
        help="프로젝트 루트 (기본: 저장소 루트)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging()
    log = logging.getLogger("log_cleanup")
    days = max(1, int(args.days or DEFAULT_KEEP_DAYS))
    root = Path(args.root)
    skip = [Path(LOG_FILE)]
    log.info("=== 로그 정리 시작 (keep=%s일, dry_run=%s) ===", days, args.dry_run)
    summary = cleanup_logs(root, keep_days=days, dry_run=bool(args.dry_run), skip=skip)
    text = format_summary(summary, dry_run=bool(args.dry_run))
    for line in text.splitlines():
        log.info(line)
    errors = sum(1 for r in summary.results if r.action == "error")
    log.info("=== 로그 정리 종료 ===")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
