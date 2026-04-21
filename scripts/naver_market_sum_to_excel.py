"""Naver 시가총액 페이지에서 재무 지표를 수집해 엑셀로 저장."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, List

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://finance.naver.com/sise/sise_market_sum.naver"
FIELD_SUBMIT_URL = "https://finance.naver.com/sise/field_submit.naver"
FIELD_IDS = ["property_total", "debt_total", "per", "roe", "pbr"]
COLUMN_MAP = {
    "종목명": "종목명",
    "자산총계": "자산총계",
    "부채총계": "부채총계",
    "PER": "PER",
    "ROE": "ROE",
    "PBR": "PBR",
}


def parse_last_page(soup: BeautifulSoup) -> int:
    last_page_link = soup.select_one("td.pgRR a")
    if last_page_link and last_page_link.get("href"):
        match = re.search(r"page=(\d+)", last_page_link["href"])
        if match:
            return int(match.group(1))
    return 1


def fetch_page(session: requests.Session, page: int, sosok: str) -> BeautifulSoup:
    return_url = f"{BASE_URL}?sosok={sosok}&page={page}"
    payload = [("menu", "market_sum"), ("returnUrl", return_url)]
    payload.extend([("fieldIds", field_id) for field_id in FIELD_IDS])

    response = session.post(
        FIELD_SUBMIT_URL,
        data=payload,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": return_url,
        },
        timeout=20,
    )
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def parse_rows(soup: BeautifulSoup) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []

    headers = [th.get_text(strip=True) for th in soup.select("table.type_2 thead th")]
    table_header_idx = {header: idx for idx, header in enumerate(headers)}
    required_headers = list(COLUMN_MAP.keys())

    missing_headers = [header for header in required_headers if header not in table_header_idx]
    if missing_headers:
        raise ValueError(f"필수 컬럼을 찾지 못했습니다: {missing_headers}")

    for tr in soup.select("table.type_2 tbody tr"):
        name_tag = tr.select_one("a.tltle")
        if not name_tag:
            continue

        tds = tr.select("td")
        if len(tds) < len(headers):
            continue

        row = {}
        for source_col, target_col in COLUMN_MAP.items():
            idx = table_header_idx[source_col]
            row[target_col] = tds[idx].get_text(strip=True)

        rows.append(row)

    return rows


def crawl_market(session: requests.Session, sosok: str) -> List[Dict[str, str]]:
    first_soup = fetch_page(session, page=1, sosok=sosok)
    last_page = parse_last_page(first_soup)

    all_rows = parse_rows(first_soup)
    for page in range(2, last_page + 1):
        page_soup = fetch_page(session, page=page, sosok=sosok)
        all_rows.extend(parse_rows(page_soup))
        print(f"[진행] sosok={sosok}, page={page}/{last_page}")

    return all_rows


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

    market_to_sosok = {
        "kospi": "0",
        "kosdaq": "1",
    }
    if args.market == "all":
        targets = ["0", "1"]
    else:
        targets = [market_to_sosok[args.market]]

    session = requests.Session()
    all_rows: List[Dict[str, str]] = []
    for sosok in targets:
        print(f"[시작] sosok={sosok} 수집 시작")
        all_rows.extend(crawl_market(session, sosok=sosok))

    if not all_rows:
        raise RuntimeError("수집된 데이터가 없습니다.")

    df = pd.DataFrame(all_rows, columns=["종목명", "자산총계", "부채총계", "PER", "ROE", "PBR"])

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
