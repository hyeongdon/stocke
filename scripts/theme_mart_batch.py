"""
테마/키워드 매핑 배치 (Phase 1)

사용 예:
  python scripts/theme_mart_batch.py
  python scripts/theme_mart_batch.py --top-n 20 --no-news
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

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from core.models import get_db  # noqa: E402
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
    return p.parse_args()


def main() -> int:
    setup_logging()
    args = parse_args()
    log = logging.getLogger(__name__)
    include_news = not bool(args.no_news)
    log.info("theme_mart_batch 시작 top_n=%s include_news=%s", args.top_n, include_news)
    for db in get_db():
        top_n_arg = int(args.top_n)
        top_n = 0 if top_n_arg <= 0 else min(max(top_n_arg, 5), 200)
        result = refresh_theme_mapping_snapshot(
            db,
            top_n=top_n,
            include_news_keywords=include_news,
        )
        log.info("theme_mart_batch 결과: %s", result)
        if not result.get("ok"):
            return 1
        return 0
    log.error("DB 세션을 열지 못했습니다.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
