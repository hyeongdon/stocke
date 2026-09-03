"""
NXT 마감 후 키움 당일(또는 기간) 실현손익·수수료·잔고와 DB 동기화

사용:
  python scripts/kiwoom_db_pnl_sync_batch.py --dry-run
  python scripts/kiwoom_db_pnl_sync_batch.py --apply
  python scripts/kiwoom_db_pnl_sync_batch.py --days 90 --apply
  python scripts/kiwoom_db_pnl_sync_batch.py --date 2026-08-18 --apply
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
from notifications.kiwoom_db_pnl_sync_notify import (  # noqa: E402
    format_kiwoom_db_pnl_sync_html,
    notify_kiwoom_db_pnl_sync,
)
from utils.datetime_kst import kst_today  # noqa: E402
from utils.kiwoom_db_pnl_sync import collect_and_sync, format_diff_table, summarize_report  # noqa: E402

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "kiwoom_db_pnl_sync_batch.log")


def setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )


def _parse_day(raw: str | None) -> date | None:
    if not raw:
        return None
    return datetime.strptime(raw.strip(), "%Y-%m-%d").date()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="키움 실현손익·잔고 ↔ DB 동기화")
    p.add_argument("--date", default=None, help="기준일 YYYY-MM-DD (지정 시 해당일만)")
    p.add_argument("--days", type=int, default=1, help="오늘 포함 최근 N일 (기본 1, --date 있으면 무시)")
    p.add_argument("--dry-run", action="store_true", help="DB 반영 없이 차이만 출력")
    p.add_argument("--apply", action="store_true", help="키움 순손익으로 DB 손익 갱신")
    p.add_argument("--no-reconcile", action="store_true", help="매도 체결/잔고 reconcile 생략")
    p.add_argument("--no-holdings", action="store_true", help="보유 평가손익 동기화 생략")
    p.add_argument("--skip-empty", action="store_true", help="차이 없으면 텔레그램 생략")
    p.add_argument("--no-telegram", action="store_true", help="텔레그램 전송 생략")
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    log = logging.getLogger(__name__)
    apply = bool(args.apply) and not bool(args.dry_run)
    day = _parse_day(args.date)
    if day is None and int(args.days) <= 1:
        day = kst_today()

    init_db()
    api = KiwoomAPI()
    report = None
    for db in get_db():
        report = await collect_and_sync(
            db,
            api,
            day=day,
            days=int(args.days),
            apply=apply,
            reconcile=not args.no_reconcile,
            sync_holdings=not args.no_holdings,
        )
        break

    if report is None:
        log.error("DB 세션을 열지 못했습니다.")
        return 1

    log.info("%s", summarize_report(report).replace("\n", " | "))
    print(summarize_report(report))
    print()
    print(format_diff_table(report.get("realized_diffs") or []))
    holding_diffs = report.get("holding_diffs") or []
    if holding_diffs:
        print(f"\n보유 차이 {len(holding_diffs)}건")
        for row in holding_diffs[:30]:
            print(
                f"  {row.get('stock_name')}({row.get('stock_code')}) "
                f"키움PL={row.get('kiwoom_pl')} DB={row.get('db_pl')} "
                f"qty {row.get('kiwoom_qty')}/{row.get('db_qty')} {row.get('kind')}"
            )

    diffs = report.get("realized_diffs") or []
    skipped = ((report.get("apply_result") or {}).get("skipped") or [])
    has_work = bool(diffs or holding_diffs or skipped)

    if args.dry_run or args.no_telegram:
        if args.dry_run:
            print("\n" + format_kiwoom_db_pnl_sync_html(report))
        log.info("dry-run/no-telegram — 전송 생략 applied=%s", apply)
        return 0

    if args.skip_empty and not has_work:
        log.info("차이 없음 — --skip-empty 로 전송 생략")
        return 0

    ok = notify_kiwoom_db_pnl_sync(report)
    if not ok:
        log.error("텔레그램 전송 실패 (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 확인)")
        return 2
    return 0


def main() -> int:
    setup_logging()
    args = parse_args()
    if not args.dry_run and not args.apply:
        # 스케줄러 기본: 당일 반영
        args.apply = True
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
