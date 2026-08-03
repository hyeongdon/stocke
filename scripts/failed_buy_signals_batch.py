"""
장마감 후 당일 FAILED 매수 신호 → 텔레그램 (별도 메시지)

사용:
  python scripts/failed_buy_signals_batch.py
  python scripts/failed_buy_signals_batch.py --date 2026-07-28
  python scripts/failed_buy_signals_batch.py --dry-run
  python scripts/failed_buy_signals_batch.py --skip-empty
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from datetime import date, datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.ensure_project_venv import ensure_project_venv  # noqa: E402

ensure_project_venv()

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from core.models import get_db, init_db  # noqa: E402
from notifications.failed_buy_signals_notify import (  # noqa: E402
    format_failed_buy_signals_html,
    notify_failed_buy_signals,
)
from utils.datetime_kst import kst_today  # noqa: E402
from utils.failed_buy_signals_report import collect_failed_buy_signals  # noqa: E402

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "failed_buy_signals_batch.log")


def setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )


def _parse_day(raw: str | None) -> date:
    if not raw:
        return kst_today()
    return datetime.strptime(raw.strip(), "%Y-%m-%d").date()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="장마감 매수 실패 신호 텔레그램 배치")
    p.add_argument("--date", default=None, help="기준일 YYYY-MM-DD (기본: KST 오늘)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="텔레그램 전송 없이 본문만 출력",
    )
    p.add_argument(
        "--skip-empty",
        action="store_true",
        help="당일 FAILED 없으면 전송 생략",
    )
    p.add_argument(
        "--no-telegram",
        action="store_true",
        help="전송 비활성 (--dry-run 과 동일하게 집계만)",
    )
    return p.parse_args()


def main() -> int:
    setup_logging()
    log = logging.getLogger(__name__)
    args = parse_args()
    day = _parse_day(args.date)
    init_db()

    report = None
    for db in get_db():
        report = collect_failed_buy_signals(db, day=day)
        break

    if report is None:
        log.error("DB 세션을 열지 못했습니다.")
        return 1

    log.info(
        "매수실패 집계 day=%s count=%s strategies=%s",
        report["day"],
        report["count"],
        dict(report.get("strategy_counts") or []),
    )

    if args.skip_empty and not report.get("has_failures"):
        log.info("당일 FAILED 없음 — --skip-empty 로 전송 생략")
        return 0

    body = format_failed_buy_signals_html(report)
    if args.dry_run or args.no_telegram:
        print(body)
        log.info("dry-run/no-telegram — 전송 생략")
        return 0

    ok = notify_failed_buy_signals(report)
    if not ok:
        log.error("텔레그램 전송 실패 (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 확인)")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
