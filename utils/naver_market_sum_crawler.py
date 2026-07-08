"""네이버 금융 시가총액 페이지 재무 지표 크롤러 (오프라인 배치용)."""

from __future__ import annotations

import re
import time
from typing import Callable, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://finance.naver.com/sise/sise_market_sum.naver"
FIELD_SUBMIT_URL = "https://finance.naver.com/sise/field_submit.naver"

# field_submit으로 요청하는 추가 재무 컬럼 (기본 페이지에 없는 항목)
EXTENDED_FIELD_IDS = [
    "property_total",   # 자산총계
    "debt_total",       # 부채총계
    "pbr",
    "sales",            # 매출액
    "operating_profit", # 영업이익
    "eps",              # 주당순이익
    "dividend",         # 보통주배당금
]

# 테이블 헤더(한글) → 내부 키 (있는 컬럼만 매핑)
HEADER_MAP = {
    "현재가": "current_price",
    "시가총액": "market_cap",
    "거래량": "volume",
    "거래대금": "trading_value",
    "PER": "per",
    "ROE": "roe",
    "PBR": "pbr",
    "주당순이익": "eps",
    "보통주배당금": "dividend_per_share",
    "매출액": "revenue",
    "영업이익": "operating_profit",
    "자산총계": "total_assets",
    "부채총계": "total_debt",
    "상장주식수": "listed_shares",
    "외국인비율": "foreign_ratio",
}

INT_FIELDS = {"current_price", "volume", "listed_shares"}
SOSOK_LABEL = {"0": "KOSPI", "1": "KOSDAQ"}


def parse_last_page(soup: BeautifulSoup) -> int:
    last_page_link = soup.select_one("td.pgRR a")
    if last_page_link and last_page_link.get("href"):
        match = re.search(r"page=(\d+)", last_page_link["href"])
        if match:
            return int(match.group(1))
    return 1


def _extract_stock_code(name_tag) -> Optional[str]:
    href = name_tag.get("href") or ""
    match = re.search(r"code=(\d{6})", href)
    return match.group(1) if match else None


def parse_numeric(raw: str) -> Optional[float]:
    """네이버 표시값 → float. N/A, -, 빈값은 None."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text or text in ("-", "N/A", "nan"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_korean_amount(raw: str) -> Optional[float]:
    """한글 금액 표기(조/억) 또는 일반 숫자 문자열을 억원 단위 float로 변환."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace(" ", "")
    if not text or text in ("-", "N/A", "nan"):
        return None

    # 예: "1조2345억", "2조", "8450억", "12345"
    match = re.match(r"^(?:(\d+(?:\.\d+)?)조)?(?:(\d+(?:\.\d+)?)억)?$", text)
    if match:
        jo = float(match.group(1)) if match.group(1) else 0.0
        eok = float(match.group(2)) if match.group(2) else 0.0
        total = jo * 10000.0 + eok
        return total if total > 0 else None

    # 혹시 숫자만 오는 경우(이미 억원 단위)
    return parse_numeric(text)


def _fetch_default_page(session: requests.Session, page: int, sosok: str, *, timeout: int = 20) -> BeautifulSoup:
    url = f"{BASE_URL}?sosok={sosok}&page={page}"
    response = session.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        timeout=timeout,
    )
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def _fetch_extended_page(session: requests.Session, page: int, sosok: str, *, timeout: int = 20) -> BeautifulSoup:
    return_url = f"{BASE_URL}?sosok={sosok}&page={page}"
    payload = [("menu", "market_sum"), ("returnUrl", return_url)]
    payload.extend([("fieldIds", field_id) for field_id in EXTENDED_FIELD_IDS])

    response = session.post(
        FIELD_SUBMIT_URL,
        data=payload,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": return_url,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def _parse_table_rows(soup: BeautifulSoup, *, market: str) -> Dict[str, Dict]:
    """파싱 결과를 stock_code → row dict 로 반환."""
    by_code: Dict[str, Dict] = {}

    headers = [th.get_text(strip=True) for th in soup.select("table.type_2 thead th")]
    if not headers or "종목명" not in headers:
        return by_code

    table_header_idx = {header: idx for idx, header in enumerate(headers)}
    col_offset = 0

    for tr in soup.select("table.type_2 tbody tr"):
        name_tag = tr.select_one("a.tltle")
        if not name_tag:
            continue

        stock_code = _extract_stock_code(name_tag)
        if not stock_code:
            continue

        tds = tr.select("td")
        if not tds:
            continue

        if col_offset == 0 and "종목명" in table_header_idx:
            name_header_idx = table_header_idx["종목명"]
            for i, td in enumerate(tds):
                if td.select_one("a.tltle"):
                    col_offset = i - name_header_idx
                    break

        row: Dict = {
            "stock_code": stock_code,
            "stock_name": name_tag.get_text(strip=True),
            "market": market,
        }
        for header, target_key in HEADER_MAP.items():
            if header not in table_header_idx:
                continue
            idx = table_header_idx[header] + col_offset
            if idx < 0 or idx >= len(tds):
                continue
            raw = tds[idx].get_text(strip=True)
            if target_key == "market_cap":
                val = parse_korean_amount(raw)
            else:
                val = parse_numeric(raw)
            if target_key in INT_FIELDS:
                row[target_key] = int(val) if val is not None else None
            else:
                row[target_key] = val

        if row.get("market_cap") is None and row.get("current_price") and row.get("listed_shares"):
            row["market_cap"] = row["current_price"] * row["listed_shares"] / 100_000_000

        by_code[stock_code] = row

    return by_code


def _merge_rows(base: Dict[str, Dict], extra: Dict[str, Dict]) -> List[Dict]:
    """기본 페이지(시총·PER)와 확장 페이지(PBR·재무) 병합."""
    base_priority = {
        "current_price", "market_cap", "volume", "trading_value",
        "per", "roe", "listed_shares", "foreign_ratio",
    }
    codes = set(base) | set(extra)
    merged: List[Dict] = []
    for code in sorted(codes):
        b = base.get(code, {})
        e = extra.get(code, {})
        row = dict(b)
        for k, v in e.items():
            if v is not None or k not in row:
                row[k] = v
        for k in base_priority:
            if b.get(k) is not None:
                row[k] = b[k]
        if "stock_code" not in row:
            row["stock_code"] = code
        if row.get("stock_name"):
            merged.append(row)
    return merged


def crawl_market(
    session: requests.Session,
    sosok: str,
    *,
    page_delay_sec: float = 0.6,
    on_page: Optional[Callable[[int, int, int], None]] = None,
) -> List[Dict]:
    """sosok: 0=KOSPI, 1=KOSDAQ. 기본 페이지 + 확장 필드 페이지를 병합."""
    market = SOSOK_LABEL.get(sosok, sosok)
    first_soup = _fetch_default_page(session, page=1, sosok=sosok)
    last_page = parse_last_page(first_soup)

    base_by_code: Dict[str, Dict] = {}
    extra_by_code: Dict[str, Dict] = {}

    # field_submit(확장 필드) 호출이 세션 상태를 바꿔 기본 페이지 시총 컬럼이 깨지므로
    # 기본 페이지를 먼저 전부 수집한 뒤 확장 페이지를 별도로 수집한다.
    for page in range(1, last_page + 1):
        default_soup = first_soup if page == 1 else _fetch_default_page(session, page, sosok)
        if page_delay_sec > 0 and page > 1:
            time.sleep(page_delay_sec)
        base = _parse_table_rows(default_soup, market=market)
        base_by_code.update(base)
        if on_page:
            on_page(page, last_page, len(base))

    for page in range(1, last_page + 1):
        if page_delay_sec > 0:
            time.sleep(page_delay_sec * 0.5)
        extended_soup = _fetch_extended_page(session, page, sosok)
        extra = _parse_table_rows(extended_soup, market=market)
        extra_by_code.update(extra)

    return _merge_rows(base_by_code, extra_by_code)


def crawl_all_markets(
    markets: Optional[List[str]] = None,
    *,
    page_delay_sec: float = 0.6,
    on_page: Optional[Callable[[int, int, int], None]] = None,
) -> List[Dict]:
    """markets: ['kospi','kosdaq'] 또는 None이면 전체."""
    market_to_sosok = {"kospi": "0", "kosdaq": "1"}
    if not markets:
        targets = ["0", "1"]
    else:
        targets = [market_to_sosok[m.lower()] for m in markets]

    all_rows: List[Dict] = []
    for sosok in targets:
        # 시장마다 세션을 분리한다. KOSPI 확장 필드(field_submit) 조회가
        # 세션 상태를 바꿔 이후 KOSDAQ 기본 페이지 시총 컬럼이 깨진다.
        session = requests.Session()
        all_rows.extend(
            crawl_market(
                session,
                sosok,
                page_delay_sec=page_delay_sec,
                on_page=on_page,
            )
        )
    return all_rows
