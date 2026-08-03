"""
장마감 후 하루 매매 일지 → 텔레그램

사용:
  python scripts/daily_trade_journal_batch.py
  python scripts/daily_trade_journal_batch.py --date 2026-07-23
  python scripts/daily_trade_journal_batch.py --dry-run
  python scripts/daily_trade_journal_batch.py --skip-empty
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
from notifications.daily_trade_journal_notify import (  # noqa: E402
    format_daily_trade_journal_html,
    notify_daily_trade_journal,
)
from utils.daily_trade_journal import collect_daily_trade_journal  # noqa: E402
from utils.datetime_kst import kst_today  # noqa: E402

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "daily_trade_journal_batch.log")


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
    p = argparse.ArgumentParser(description="장마감 매매 일지 텔레그램 배치")
    p.add_argument("--date", default=None, help="기준일 YYYY-MM-DD (기본: KST 오늘)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="텔레그램 전송 없이 본문만 출력",
    )
    p.add_argument(
        "--skip-empty",
        action="store_true",
        help="당일 매수·매도 없으면 전송 생략",
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

    journal = None
    for db in get_db():
        journal = collect_daily_trade_journal(db, day=day)
        break

    if journal is None:
        log.error("DB 세션을 열지 못했습니다.")
        return 1

    log.info(
        "일지 집계 day=%s buys=%s sells=%s holdings=%s "
        "today_buy_eval=%s holding_u=%s day_eval=%s",
        journal["day"],
        journal["buy_count"],
        journal["sell_count"],
        journal["holding_count"],
        journal.get("today_buy_eval"),
        journal.get("holding_unrealized"),
        journal.get("day_eval_total"),
    )

    if args.skip_empty and not (
        journal.get("buys") or journal.get("sells") or journal.get("today_buy_positions")
    ):
        log.info("당일 매수·매도 없음 — --skip-empty 로 전송 생략")
        return 0

    body = format_daily_trade_journal_html(journal)
    if args.dry_run or args.no_telegram:
        print(body)
        log.info("dry-run/no-telegram — 전송 생략")
        return 0

    ok = notify_daily_trade_journal(journal)
    if not ok:
        log.error("텔레그램 전송 실패 (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 확인)")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
