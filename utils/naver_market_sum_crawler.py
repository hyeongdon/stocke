"""네이버 증권 모바일 API 재무 지표 크롤러 (오프라인 배치용).

2026-07 네이버 개편으로 finance.naver.com 시가총액 페이지가 stock.naver.com(SPA)으로
리다이렉트되어 HTML 파싱이 불가능해졌다. 대신 m.stock.naver.com JSON API 를 사용한다.

- 목록: /api/stocks/marketValue/{KOSPI|KOSDAQ}  → 코드·이름·현재가·시총·거래량
- 종목: /api/stock/{code}/integration          → PER·EPS·PBR·주당배당금·외인소진율
- 종목: /api/stock/{code}/finance/annual       → 최근 확정 연간 매출액·영업이익·ROE·부채비율·BPS

단위는 기존 마트와 동일: 시총·매출·영업이익·자산·부채 = 억원, 상장주식수 = 천주.
자산총계·부채총계는 API 에 없어 BPS × 상장주식수(자본) 와 부채비율로 근사한다.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://m.stock.naver.com/api"
LIST_PAGE_SIZE = 100
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://m.stock.naver.com/",
}
MARKET_LABEL = {"kospi": "KOSPI", "kosdaq": "KOSDAQ"}

_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_thread_local = threading.local()


def parse_numeric(raw) -> Optional[float]:
    """'10.09배', '22,292원', '46.10%', '-' → float 또는 None."""
    if raw is None:
        return None
    m = _NUM_RE.search(str(raw))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _session() -> requests.Session:
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        _thread_local.session = s
    return s


def _get_json(url: str, *, params: Optional[dict] = None, timeout: int = 15, retries: int = 3):
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            r = _session().get(url, params=params, timeout=timeout)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    logger.warning("네이버 API 실패 %s: %s", url, last_err)
    return None


def fetch_market_list(market: str, *, page_delay_sec: float = 0.3,
                      on_page: Optional[Callable[[int, int, int], None]] = None) -> List[Dict]:
    """market: 'kospi' | 'kosdaq'. 시총 순 전 종목 기본 정보."""
    label = MARKET_LABEL[market.lower()]
    url = f"{API_BASE}/stocks/marketValue/{label}"
    rows: List[Dict] = []
    page, last_page = 1, 1
    while page <= last_page:
        if page > 1 and page_delay_sec > 0:
            time.sleep(page_delay_sec)
        data = _get_json(url, params={"page": page, "pageSize": LIST_PAGE_SIZE})
        stocks = (data or {}).get("stocks") or []
        if page == 1:
            total = int((data or {}).get("totalCount") or 0)
            last_page = max(1, -(-total // LIST_PAGE_SIZE))
        for s in stocks:
            code = str(s.get("itemCode") or "").strip()
            if not re.fullmatch(r"\d{6}", code):
                continue
            price = parse_numeric(s.get("closePriceRaw"))
            mcap_won = parse_numeric(s.get("marketValueRaw"))
            listed_k = (mcap_won / price / 1000.0) if price and mcap_won else None
            vol = parse_numeric(s.get("accumulatedTradingVolumeRaw"))
            rows.append({
                "stock_code": code,
                "stock_name": str(s.get("stockName") or "").strip(),
                "market": label,
                "current_price": int(price) if price is not None else None,
                "market_cap": round(mcap_won / 1e8, 1) if mcap_won else None,
                "volume": int(vol) if vol is not None else None,
                "listed_shares": round(listed_k) if listed_k else None,
            })
        if on_page:
            on_page(page, last_page, len(stocks))
        if not stocks:
            break
        page += 1
    return rows


def _latest_annual_column(finance_info: dict) -> Optional[str]:
    titles = finance_info.get("trTitleList") or []
    actual = [t["key"] for t in titles if t.get("isConsensus") == "N" and t.get("key")]
    return max(actual) if actual else None


def fetch_stock_details(code: str) -> Dict:
    """종목별 재무 지표. 실패한 항목은 키 생략."""
    out: Dict = {}

    integ = _get_json(f"{API_BASE}/stock/{code}/integration")
    infos = {i.get("code"): i.get("value") for i in ((integ or {}).get("totalInfos") or [])}
    for src, dst in (("per", "per"), ("eps", "eps"), ("pbr", "pbr"),
                     ("dividend", "dividend_per_share"), ("foreignRate", "foreign_ratio")):
        if src in infos:
            out[dst] = parse_numeric(infos[src])

    fin = _get_json(f"{API_BASE}/stock/{code}/finance/annual")
    info = (fin or {}).get("financeInfo") or {}
    col = _latest_annual_column(info)
    if col:
        rows = {r.get("title"): (r.get("columns") or {}).get(col, {}).get("value")
                for r in (info.get("rowList") or [])}
        out["revenue"] = parse_numeric(rows.get("매출액"))
        out["operating_profit"] = parse_numeric(rows.get("영업이익"))
        out["roe"] = parse_numeric(rows.get("ROE"))
        out["_debt_ratio"] = parse_numeric(rows.get("부채비율"))
        out["_bps"] = parse_numeric(rows.get("BPS"))
        if out.get("eps") is None:
            out["eps"] = parse_numeric(rows.get("EPS"))
    return out


def _apply_balance_estimate(row: Dict) -> None:
    bps = row.pop("_bps", None)
    debt_ratio = row.pop("_debt_ratio", None)
    shares_k = row.get("listed_shares")
    if not bps or not shares_k or bps <= 0:
        return
    equity = bps * shares_k * 1000 / 1e8
    if debt_ratio is not None and debt_ratio >= 0:
        debt = equity * debt_ratio / 100.0
        row["total_debt"] = round(debt)
        row["total_assets"] = round(equity + debt)


def crawl_all_markets(
    markets: Optional[List[str]] = None,
    *,
    page_delay_sec: float = 0.3,
    on_page: Optional[Callable[[int, int, int], None]] = None,
    with_details: bool = True,
    workers: int = 4,
    detail_delay_sec: float = 0.05,
    on_detail: Optional[Callable[[int, int], None]] = None,
) -> List[Dict]:
    """markets: ['kospi','kosdaq'] 또는 None이면 전체.

    with_details=False 면 목록 API 만 호출 (코드·이름·가격·시총, 1~2분).
    """
    targets = [m.lower() for m in markets] if markets else ["kospi", "kosdaq"]
    rows: List[Dict] = []
    for m in targets:
        rows.extend(fetch_market_list(m, page_delay_sec=page_delay_sec, on_page=on_page))

    if not with_details or not rows:
        return rows

    def _work(row: Dict) -> Dict:
        if detail_delay_sec > 0:
            time.sleep(detail_delay_sec)
        try:
            return fetch_stock_details(row["stock_code"])
        except Exception as e:  # noqa: BLE001
            logger.warning("종목 상세 실패 %s: %s", row["stock_code"], e)
            return {}

    total = len(rows)
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_work, r): r for r in rows}
        for fut in as_completed(futs):
            row = futs[fut]
            row.update(fut.result())
            _apply_balance_estimate(row)
            done += 1
            if on_detail and (done % 200 == 0 or done == total):
                on_detail(done, total)
    return rows
