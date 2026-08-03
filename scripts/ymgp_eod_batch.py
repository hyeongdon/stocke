"""
장마감 후 역매공파 단계·박스권 차이 → 텔레그램

사용:
  python scripts/ymgp_eod_batch.py
  python scripts/ymgp_eod_batch.py --dry-run
  python scripts/ymgp_eod_batch.py --skip-empty
"""
from __future__ import annotations

import argparse
import asyncio
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

from api.kiwoom_api import KiwoomAPI  # noqa: E402
from core.models import get_db, init_db  # noqa: E402
from notifications.ymgp_eod_notify import format_ymgp_eod_html, notify_ymgp_eod  # noqa: E402
from utils.datetime_kst import kst_today  # noqa: E402
from utils.ymgp_eod_report import collect_ymgp_eod_report  # noqa: E402

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "ymgp_eod_batch.log")


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
    p = argparse.ArgumentParser(description="장마감 역매공파 단계·박스권 텔레그램 배치")
    p.add_argument("--date", default=None, help="기준일 YYYY-MM-DD (표시용, 기본: KST 오늘)")
    p.add_argument("--limit", type=int, default=40, help="후보 상한 (기본 40)")
    p.add_argument("--dry-run", action="store_true", help="텔레그램 전송 없이 본문만 출력")
    p.add_argument("--skip-empty", action="store_true", help="후보 없으면 전송 생략")
    p.add_argument("--no-telegram", action="store_true", help="전송 비활성")
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    log = logging.getLogger(__name__)
    day = _parse_day(args.date)
    init_db()
    api = KiwoomAPI()

    report = None
    for db in get_db():
        report = await collect_ymgp_eod_report(
            db,
            api,
            day=day,
            limit=int(args.limit),
        )
        break

    if report is None:
        log.error("DB 세션을 열지 못했습니다.")
        return 1

    log.info(
        "YMGP EOD day=%s total=%s filtered=%s ready=%s armed=%s width_over_avg=%s to_high_avg=%s",
        report.get("day"),
        report.get("total"),
        report.get("filtered_count"),
        report.get("ready_count"),
        report.get("armed_count"),
        report.get("filtered_width_over_avg"),
        report.get("filtered_to_high_avg"),
    )

    if args.skip_empty and not report.get("has_candidates"):
        log.info("후보 없음 — --skip-empty 로 전송 생략")
        return 0

    body = format_ymgp_eod_html(report)
    if args.dry_run or args.no_telegram:
        print(body)
        log.info("dry-run/no-telegram — 전송 생략")
        return 0

    ok = notify_ymgp_eod(report)
    if not ok:
        log.error("텔레그램 전송 실패 (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 확인)")
        return 2
    return 0


def main() -> int:
    setup_logging()
    args = parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
