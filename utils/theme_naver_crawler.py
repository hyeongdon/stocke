"""네이버 금융 테마 크롤러 (스파이크용)."""
from __future__ import annotations

import re
from typing import Dict, List
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

LIST_URL = "https://finance.naver.com/sise/theme.naver"
DETAIL_URL = (
    "https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={theme_no}"
)
# 구 URL (네이버 구조 변경 전)
_LEGACY_DETAIL_URL = "https://finance.naver.com/sise/theme_detail.naver?no={theme_no}"

_HDRS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}

_FALLBACK_THEME_STOCKS = {
    "fb_ai": [("005930", "삼성전자"), ("000660", "SK하이닉스"), ("012450", "한화에어로스페이스")],
    "fb_power": [("267260", "HD현대일렉트릭"), ("010120", "LS ELECTRIC"), ("047810", "한국전력")],
    "fb_ship": [("042660", "한화오션"), ("009540", "HD한국조선해양"), ("010140", "삼성중공업")],
    "fb_bio": [("068270", "셀트리온"), ("196170", "알테오젠"), ("214450", "파마리서치")],
    "fb_battery": [("373220", "LG에너지솔루션"), ("003670", "포스코퓨처엠"), ("006400", "삼성SDI")],
}


def _theme_no_from_href(href: str) -> str:
    if not href:
        return ""
    parsed = urlparse(href)
    q = parse_qs(parsed.query)
    no = (q.get("no") or [""])[0]
    return no.strip()


def _decode_naver_html(resp) -> BeautifulSoup:
    if resp.encoding:
        resp.encoding = resp.encoding.upper().replace("-", "")
    if not resp.encoding or "KR" in (resp.encoding or "").upper():
        resp.encoding = "euc-kr"
    return BeautifulSoup(resp.text, "html.parser")


def _parse_theme_links_from_soup(soup: BeautifulSoup) -> List[Dict]:
    themes: List[Dict] = []
    seen: set[str] = set()
    selectors = (
        "a[href*='sise_group_detail.naver'][href*='type=theme']",
        "a[href*='theme_detail.naver']",
    )
    for sel in selectors:
        for a in soup.select(sel):
            name = a.get_text(strip=True)
            href = a.get("href", "")
            no = _theme_no_from_href(href)
            if not name or not no or no in seen:
                continue
            seen.add(no)
            themes.append({"theme_no": no, "theme_name": name})
    return themes


def _max_theme_list_page(soup: BeautifulSoup) -> int:
    max_page = 1
    for a in soup.select("table.Nnavi a, .Nnavi a"):
        href = a.get("href", "")
        m = re.search(r"page=(\d+)", href)
        if m:
            max_page = max(max_page, int(m.group(1)))
    return max_page


def crawl_theme_list(limit: int = 20) -> List[Dict]:
    """네이버 테마 목록 (전 페이지).

    limit<=0 이면 목록에 있는 전체 테마(약 260개+) 수집.
    limit>0 이면 등장 순서대로 상위 N개만 반환.
    """
    max_count = None if int(limit or 0) <= 0 else max(1, int(limit))

    try:
        resp = requests.get(LIST_URL, headers=_HDRS, timeout=15)
        resp.raise_for_status()
        first_soup = _decode_naver_html(resp)
    except Exception:
        first_soup = None

    if first_soup is None:
        themes: List[Dict] = []
    else:
        last_page = _max_theme_list_page(first_soup)
        themes = []
        seen: set[str] = set()

        for page in range(1, max(last_page, 1) + 1):
            if page == 1:
                soup = first_soup
            else:
                try:
                    page_resp = requests.get(
                        f"{LIST_URL}?&page={page}",
                        headers=_HDRS,
                        timeout=15,
                    )
                    page_resp.raise_for_status()
                    soup = _decode_naver_html(page_resp)
                except Exception:
                    break

            added = 0
            for item in _parse_theme_links_from_soup(soup):
                no = item["theme_no"]
                if no in seen:
                    continue
                seen.add(no)
                themes.append(item)
                added += 1
                if max_count is not None and len(themes) >= max_count:
                    return themes

            if page > 1 and added == 0:
                break

    if themes:
        return themes
    # 스파이크 fallback: 네이버 페이지 차단/구조 변경 시 데모 데이터로 UI 검증
    fallback = []
    names = {
        "fb_ai": "AI반도체",
        "fb_power": "전력기기",
        "fb_ship": "조선",
        "fb_bio": "바이오",
        "fb_battery": "2차전지",
    }
    for key, name in names.items():
        fallback.append({"theme_no": key, "theme_name": name})
        if len(fallback) >= max(1, int(limit)):
            break
    return fallback


def _parse_theme_stocks_html(soup: BeautifulSoup) -> List[Dict]:
    stocks: List[Dict] = []
    seen_codes = set()
    for a in soup.select("a[href*='/item/main.naver?code=']"):
        name = a.get_text(strip=True)
        href = a.get("href", "")
        m = re.search(r"code=(\d{6})", href)
        if not m:
            continue
        code = m.group(1)
        if code in seen_codes:
            continue
        seen_codes.add(code)
        stocks.append({"stock_code": code, "stock_name": name})
    return stocks


def crawl_theme_stocks(theme_no: str) -> List[Dict]:
    if not theme_no:
        return []
    if theme_no.startswith("fb_"):
        return [
            {"stock_code": code, "stock_name": name}
            for code, name in _FALLBACK_THEME_STOCKS.get(theme_no, [])
        ]
    for url in (
        DETAIL_URL.format(theme_no=theme_no),
        _LEGACY_DETAIL_URL.format(theme_no=theme_no),
    ):
        try:
            resp = requests.get(url, headers=_HDRS, timeout=15)
            if resp.status_code != 200:
                continue
            soup = _decode_naver_html(resp)
            stocks = _parse_theme_stocks_html(soup)
            if stocks:
                return stocks
        except Exception:
            continue
    return []
