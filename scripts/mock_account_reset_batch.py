"""
모의투자 계좌 교체(앱키/시크릿/계좌번호 변경) 감지 → 이전 계좌 데이터 백업 후 초기화

사용:
  python scripts/mock_account_reset_batch.py              # 변경 시에만 초기화
  python scripts/mock_account_reset_batch.py --dry-run    # 삭제 대상만 출력
  python scripts/mock_account_reset_batch.py --force      # 변경 없어도 강제 초기화
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.ensure_project_venv import ensure_project_venv  # noqa: E402

ensure_project_venv()

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from core.models import get_db, init_db  # noqa: E402
from notifications.trade_alert import send_trade_alert  # noqa: E402
from utils.mock_account_reset import check_and_reset, format_reset_report  # noqa: E402

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "mock_account_reset_batch.log")


def setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="모의투자 계좌 교체 감지 및 데이터 초기화")
    p.add_argument("--dry-run", action="store_true", help="삭제 없이 대상만 출력")
    p.add_argument("--force", action="store_true", help="계좌 변경이 없어도 강제 초기화")
    p.add_argument("--no-telegram", action="store_true", help="텔레그램 전송 생략")
    return p.parse_args()


def main() -> int:
    setup_logging()
    args = parse_args()
    log = logging.getLogger(__name__)

    init_db()
    report = None
    for db in get_db():
        report = check_and_reset(db, apply=not args.dry_run, force=args.force)
        break
    if report is None:
        log.error("DB 세션을 열지 못했습니다.")
        return 1

    msg = format_reset_report(report)
    log.info("%s", msg.replace("\n", " | "))
    print(msg)

    if report.get("action") == "reset" and not args.no_telegram:
        if not send_trade_alert(msg, skip_market_hours_check=True):
            log.warning("텔레그램 전송 실패 또는 미설정 (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
