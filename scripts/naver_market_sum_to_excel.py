"""Naver 시가총액 페이지에서 재무 지표를 수집해 엑셀로 저장."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# 프로젝트 루트 import
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.naver_market_sum_crawler import crawl_all_markets  # noqa: E402

EXCEL_COLUMNS = [
    "stock_code", "stock_name", "market",
    "current_price", "market_cap", "volume", "trading_value",
    "per", "roe", "pbr", "eps", "dividend_per_share",
    "revenue", "operating_profit", "total_assets", "total_debt",
    "listed_shares", "foreign_ratio",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="네이버 시가총액 페이지를 순회해 재무 지표를 엑셀로 저장합니다."
    )
    parser.add_argument(
        "--market",
        choices=["all", "kospi", "kosdaq"],
        default="all",
        help="수집 시장 선택 (기본: all)",
    )
    parser.add_argument(
        "--output",
        default="naver_market_financials.xlsx",
        help="저장할 엑셀 파일 경로 (기본: naver_market_financials.xlsx)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    markets = None if args.market == "all" else [args.market]

    def on_page(page: int, last_page: int, row_count: int) -> None:
        print(f"[진행] page={page}/{last_page} ({row_count}건)")

    all_rows = crawl_all_markets(markets=markets, on_page=on_page)

    if not all_rows:
        raise RuntimeError("수집된 데이터가 없습니다.")

    df = pd.DataFrame(all_rows, columns=EXCEL_COLUMNS)

    output_path = Path(args.output).resolve()
    try:
        df.to_excel(output_path, index=False)
    except ImportError as exc:
        raise RuntimeError(
            "엑셀 저장을 위해 openpyxl이 필요합니다. `pip install openpyxl` 후 다시 실행하세요."
        ) from exc

    print(f"[완료] 총 {len(df)}건 저장: {output_path}")


if __name__ == "__main__":
    main()
