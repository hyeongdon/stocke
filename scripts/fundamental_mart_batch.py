"""
기본적분석 마트 배치 — 네이버 시가총액 페이지 → DB upsert

서버(FastAPI) 없이 단독 실행. 장 마감 후 작업 스케줄러 등록 권장.

사용 예:
  python scripts/fundamental_mart_batch.py
  python scripts/fundamental_mart_batch.py --market kospi
  python scripts/fundamental_mart_batch.py --as-of 2026-07-05 --page-delay 1.0
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from utils.naver_market_sum_crawler import crawl_all_markets  # noqa: E402
from utils.fundamental_mart_store import upsert_many  # noqa: E402

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "fundamental_mart_batch.log")


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
    parser = argparse.ArgumentParser(description="네이버 재무지표 → 기본적분석 마트 DB 적재")
    parser.add_argument(
        "--market",
        choices=["all", "kospi", "kosdaq"],
        default="all",
        help="수집 시장 (기본: all)",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="기준일 YYYY-MM-DD (기본: 오늘)",
    )
    parser.add_argument(
        "--page-delay",
        type=float,
        default=0.6,
        help="페이지 간 대기(초) — 네이버 부하·차단 완화 (기본: 0.6)",
    )
    return parser.parse_args()


def main() -> int:
    setup_logging()
    args = parse_args()
    log = logging.getLogger(__name__)

    if args.as_of:
        as_of_date = datetime.strptime(args.as_of.strip()[:10], "%Y-%m-%d").date()
    else:
        as_of_date = datetime.now().date()

    markets = None if args.market == "all" else [args.market]
    log.info("기본적분석 마트 배치 시작 — as_of=%s market=%s", as_of_date, args.market)

    def on_page(page: int, last_page: int, row_count: int) -> None:
        log.info("페이지 %d/%d — %d건", page, last_page, row_count)

    rows = crawl_all_markets(
        markets=markets,
        page_delay_sec=args.page_delay,
        on_page=on_page,
    )
    if not rows:
        log.error("수집된 데이터가 없습니다.")
        return 1

    # source 태그
    for r in rows:
        r["source"] = "naver"

    saved = upsert_many(rows, as_of_date=as_of_date)
    log.info("완료 — %d건 upsert (as_of=%s)", saved, as_of_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
