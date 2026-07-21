"""
종목별 네이버 뉴스 검색 API(Stage C) → tag_articles + 기사 제목·요약 키워드

핵심:
- `tag_articles`: 기사 메타(title/url/published_at) 저장
- 기사 제목 + 검색 API description(요약)에서 키워드 추출
  (`theme_tags(tag_type=news_keyword)`, `theme_tag_edges`, `tag_article_keyword_edges`)
- 본문 HTML 크롤 없음 (description은 검색 응답에 포함 → 추가 API/네트워크 부하 없음)

주의:
- "전체 종목"을 돌리면 API 호출량이 매우 커집니다. 본 스크립트는
  재시작/스킵을 위한 biz_date 중복 체크 + 429 백오프 + 커밋 주기를 포함합니다.
"""

from __future__ import annotations

import argparse
import email.utils
import io
import logging
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import requests

from sqlalchemy import func

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from core.config import Config  # noqa: E402
from core.models import (  # noqa: E402
    KeywordDailyStat,
    TagArticle,
    TagArticleKeywordEdge,
    ThemeTag,
    ThemeTagEdge,
    get_db,
)
from utils.datetime_kst import KST, kst_now_iso, kst_today, utc_now_naive  # noqa: E402
from utils.naver_market_sum_crawler import crawl_all_markets  # noqa: E402
from utils.stock_news_progress import write_stock_news_progress
from utils.theme_keyword_rules import extract_keywords  # noqa: E402

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "stock_news_daily_batch.log")


def setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="종목별 네이버 뉴스 검색 API 수집 배치(Stage C)")
    p.add_argument("--biz-date", default=None, help="기준일 YYYY-MM-DD (기본: KST 오늘)")
    p.add_argument("--display", type=int, default=15, help="네이버 API display(기본: 15)")
    p.add_argument("--sort-date", type=str, default="date", help="sort 옵션(기본: date)")
    p.add_argument("--min-call-interval", type=float, default=0.9, help="종목당 최소 요청 간격(초)")
    p.add_argument("--max-retries", type=int, default=4, help="429/일시 오류 재시도 횟수")
    p.add_argument(
        "--universe",
        type=str,
        default=None,
        choices=["theme", "all"],
        help="theme=테마 편입 종목만(기본, 미니PC), all=전종목",
    )
    p.add_argument(
        "--max-stocks-per-day",
        type=int,
        default=None,
        help="하루 최대 처리 종목 수 (기본: Config.STOCK_NEWS_MAX_STOCKS_PER_DAY)",
    )
    p.add_argument(
        "--max-stocks-per-run",
        type=int,
        default=0,
        help="0=남은 전체(일일 상한 내), N=미처리 종목 중 앞에서 N개만 (분할 실행용)",
    )
    p.add_argument(
        "--offset",
        type=int,
        default=0,
        help="미처리 종목 큐에서의 시작 오프셋(기본 0). 예: --offset 100 --max-stocks-per-run 100",
    )
    p.add_argument("--page-delay", type=float, default=0.6, help="종목 유니버스(Naver 시장) 페이지 딜레이")
    p.add_argument("--article-title-keywords-top", type=int, default=4, help="기사 1개 제목+요약 → 키워드 top")
    p.add_argument("--stock-keywords-top", type=int, default=12, help="종목 전체 제목+요약 목록 → 키워드 top")
    p.add_argument("--keyword-edge-weight-mode", type=str, default="mention_count", choices=["mention_count"], help="weight 계산 방식")
    p.add_argument("--force", action="store_true", help="이미 biz_date를 처리한 종목도 강제 재수집")
    p.add_argument("--commit-every", type=int, default=20, help="stock 처리 후 커밋 주기")
    p.add_argument("--timeout", type=int, default=18, help="네이버 API timeout(초)")
    return p.parse_args()


def _slug(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "_")


def _norm_stock_code(code: str) -> str:
    s = str(code or "").replace("A", "").strip()
    if not s:
        return ""
    return s.zfill(6)


def _parse_pubdate_to_dt(pub_date_raw: object) -> Optional[datetime]:
    if not pub_date_raw:
        return None
    try:
        s = str(pub_date_raw).strip()
        if not s:
            return None
        # Naver pubDate is usually RFC 2822.
        dt = email.utils.parsedate_to_datetime(s)
        return dt
    except Exception:
        return None


def _safe_text(v: object) -> str:
    s = "" if v is None else str(v)
    s = s.replace("<b>", "").replace("</b>", "").replace("&quot;", '"').replace("&amp;", "&")
    return s.strip()


def _load_theme_universe(session, biz: date) -> Tuple[List[Dict], Optional[date]]:
    """테마 편입 종목 유니버스. 당일 스냅샷이 없으면 최근 편입일을 사용."""
    today_n = int(
        session.query(func.count(ThemeTagEdge.id))
        .filter(
            ThemeTagEdge.source == "naver_theme",
            ThemeTagEdge.biz_date == biz,
        )
        .scalar()
        or 0
    )
    use_biz = biz if today_n > 0 else (
        session.query(func.max(ThemeTagEdge.biz_date))
        .filter(
            ThemeTagEdge.source == "naver_theme",
            ThemeTagEdge.biz_date.isnot(None),
        )
        .scalar()
    )
    if not use_biz:
        return [], None

    rows = (
        session.query(ThemeTagEdge.stock_code, ThemeTagEdge.stock_name)
        .filter(
            ThemeTagEdge.source == "naver_theme",
            ThemeTagEdge.biz_date == use_biz,
        )
        .distinct()
        .all()
    )
    out: List[Dict] = []
    seen = set()
    for code_raw, name_raw in rows:
        code = _norm_stock_code(code_raw)
        name = _safe_text(name_raw) or code
        if not code or code in seen:
            continue
        seen.add(code)
        out.append({"stock_code": code, "stock_name": name})
    out = sorted(out, key=lambda x: x["stock_code"])
    return out, use_biz


def _upsert_keyword_tag(
    session,
    *,
    keyword: str,
    tag_cache: Dict[str, ThemeTag],
    tag_key_cache: Dict[str, int],
    tag_key_prefix: str = "kw_",
) -> ThemeTag:
    key = f"{tag_key_prefix}{_slug(keyword)}"
    if key in tag_cache:
        return tag_cache[key]
    row = (
        session.query(ThemeTag)
        .filter(ThemeTag.tag_key == key)
        .order_by(ThemeTag.updated_at.desc())
        .first()
    )
    if row:
        row.name_ko = keyword
        row.tag_type = "news_keyword"
        row.source = "news_title"
        row.updated_at = utc_now_naive()
        session.add(row)
        session.flush()
        tag_cache[key] = row
        return row
    row = ThemeTag(
        tag_key=key,
        name_ko=keyword,
        tag_type="news_keyword",
        source="news_title",
        created_at=utc_now_naive(),
        updated_at=utc_now_naive(),
    )
    session.add(row)
    session.flush()
    tag_cache[key] = row
    return row


def _report_progress(**kwargs) -> None:
    try:
        write_stock_news_progress(kwargs)
    except Exception:
        pass


def main() -> int:
    setup_logging()
    args = parse_args()
    log = logging.getLogger(__name__)

    if not Config.NAVER_CLIENT_ID or not Config.NAVER_CLIENT_SECRET:
        log.error("NAVER_CLIENT_ID/SECRET 설정이 필요합니다. .env 확인.")
        return 2

    if args.biz_date:
        biz = datetime.strptime(args.biz_date.strip()[:10], "%Y-%m-%d").date()
    else:
        biz = kst_today()

    log.info(
        "stock_news_daily_batch 시작 biz_date=%s display=%s universe=%s max_per_day=%s",
        biz.isoformat(),
        args.display,
        (args.universe or Config.STOCK_NEWS_UNIVERSE or "theme"),
        args.max_stocks_per_day if args.max_stocks_per_day is not None else Config.STOCK_NEWS_MAX_STOCKS_PER_DAY,
    )

    started_at = time.time()
    universe_mode = (args.universe or Config.STOCK_NEWS_UNIVERSE or "theme").strip().lower()
    if universe_mode not in ("theme", "all"):
        universe_mode = "theme"
    max_per_day = (
        int(args.max_stocks_per_day)
        if args.max_stocks_per_day is not None
        else int(Config.STOCK_NEWS_MAX_STOCKS_PER_DAY or 0)
    )
    max_per_day = max(0, max_per_day)

    session_gen = get_db()
    for session in session_gen:
        # 처리 완료 스킵(중복 방지)
        done_stock_codes = set()
        if not args.force:
            # tag_articles만 있으면 "기사 저장만 끝났고 키워드 edge는 아직"일 수 있으니,
            # tag_article_keyword_edges까지 존재하는 종목만 스킵한다.
            rows = (
                session.query(TagArticle.stock_code)
                .join(TagArticleKeywordEdge, TagArticleKeywordEdge.article_id == TagArticle.id)
                .filter(TagArticle.biz_date == biz, TagArticle.source == "naver_news")
                .distinct()
                .all()
            )
            done_stock_codes = {str(r[0] or "") for r in rows if r and r[0]}
            log.info("오늘 처리된 종목 스킵 set size=%d", len(done_stock_codes))

        if max_per_day > 0 and len(done_stock_codes) >= max_per_day and not args.force:
            log.info(
                "일일 상한 도달 — 종료 (done=%d >= max_per_day=%d)",
                len(done_stock_codes),
                max_per_day,
            )
            _report_progress(
                biz_date=biz.isoformat(),
                running=False,
                status="all_done",
                universe_total=max_per_day,
                done_count=len(done_stock_codes),
                pending_count=0,
                percent=100.0,
                day_cap=max_per_day,
                universe_mode=universe_mode,
            )
            return 0

        progress_state: Dict[str, object] = {
            "biz_date": biz.isoformat(),
            "running": True,
            "status": "running",
            "universe_total": None,
            "done_count": len(done_stock_codes),
            "done_at_start": len(done_stock_codes),
            "pending_count": None,
            "run_total": None,
            "run_done": 0,
            "ok_count": 0,
            "fail_count": 0,
            "started_at": kst_now_iso(),
            "universe_mode": universe_mode,
            "day_cap": max_per_day or None,
        }
        progress_lock = threading.Lock()

        def _emit_progress(**kwargs) -> None:
            with progress_lock:
                progress_state.update(kwargs)
                _report_progress(**dict(progress_state))

        hb_stop = threading.Event()

        def _heartbeat_loop() -> None:
            while not hb_stop.wait(8.0):
                with progress_lock:
                    if progress_state.get("running") is not True:
                        break
                    _report_progress(**dict(progress_state))

        hb_thread = threading.Thread(target=_heartbeat_loop, name="stock-news-progress-heartbeat", daemon=True)
        hb_thread.start()
        _emit_progress()

        theme_edge_biz = None
        if universe_mode == "theme":
            log.info("테마 편입 종목 유니버스 로드 중...")
            all_stock_rows, theme_edge_biz = _load_theme_universe(session, biz)
            log.info(
                "테마 유니버스 완료 n=%d edge_biz=%s",
                len(all_stock_rows),
                theme_edge_biz.isoformat() if theme_edge_biz else None,
            )
            if not all_stock_rows:
                log.error("테마 편입 종목이 없습니다. 테마 배치를 먼저 실행하거나 --universe all 을 사용하세요.")
                _report_progress(
                    biz_date=biz.isoformat(),
                    running=False,
                    status="idle",
                    universe_total=0,
                    done_count=len(done_stock_codes),
                    pending_count=0,
                    universe_mode=universe_mode,
                )
                hb_stop.set()
                hb_thread.join(timeout=1.0)
                return 1
        else:
            # 종목 유니버스(전체 종목) — 페이지 크롤이라 1~3분 걸릴 수 있음
            log.info("종목 유니버스 수집 시작 (네이버 시장 합산, 잠시 대기)...")
            stocks = crawl_all_markets(markets=None, page_delay_sec=args.page_delay)
            log.info("종목 유니버스 수집 완료 raw=%d", len(stocks or []))
            all_stock_rows = []
            for r in stocks:
                code = _norm_stock_code(r.get("stock_code"))
                name = _safe_text(r.get("stock_name"))
                if not code or not name:
                    continue
                all_stock_rows.append({"stock_code": code, "stock_name": name})
            all_stock_rows = sorted(all_stock_rows, key=lambda x: x["stock_code"])

        # 이미 처리된 종목은 큐에서 제외 → 분할 실행마다 "다음 N개"가 이어짐
        if args.force:
            pending_rows = list(all_stock_rows)
        else:
            pending_rows = [s for s in all_stock_rows if s["stock_code"] not in done_stock_codes]

        # 미니PC: 하루 상한으로 큐 자체를 자름
        day_capped = False
        if max_per_day > 0:
            budget = max(0, max_per_day - len(done_stock_codes))
            if len(pending_rows) > budget:
                pending_rows = pending_rows[:budget]
                day_capped = True
            log.info(
                "일일 상한 max_per_day=%d 이미완료=%d 오늘예산=%d 대기큐=%d",
                max_per_day,
                len(done_stock_codes),
                budget,
                len(pending_rows),
            )

        offset = max(0, int(args.offset or 0))
        pending_total = len(pending_rows)
        if offset:
            pending_rows = pending_rows[offset:]

        if args.max_stocks_per_run and args.max_stocks_per_run > 0:
            stock_rows = pending_rows[: int(args.max_stocks_per_run)]
        else:
            stock_rows = pending_rows

        # 진행률 분모: 테마/전종일 때도 일일 상한을 목표치로 표시
        progress_universe = len(all_stock_rows)
        if max_per_day > 0:
            progress_universe = min(progress_universe, max_per_day) if progress_universe else max_per_day

        log.info(
            "유니버스모드=%s 유니버스=%d 이미완료=%d 미처리=%d offset=%d 이번_실행=%d",
            universe_mode,
            len(all_stock_rows),
            len(done_stock_codes),
            pending_total,
            offset,
            len(stock_rows),
        )
        if not stock_rows:
            log.info(
                "처리할 미완료 종목이 없습니다. (biz_date=%s day_cap=%s)",
                biz.isoformat(),
                max_per_day or "none",
            )
            _report_progress(
                biz_date=biz.isoformat(),
                running=False,
                status="all_done",
                universe_total=progress_universe,
                done_count=len(done_stock_codes),
                pending_count=0,
                percent=100.0 if progress_universe else 0,
                day_cap=max_per_day or None,
                universe_mode=universe_mode,
                day_capped=day_capped,
            )
            hb_stop.set()
            hb_thread.join(timeout=1.0)
            return 0

        _emit_progress(
            biz_date=biz.isoformat(),
            running=True,
            status="running",
            universe_total=progress_universe,
            done_count=len(done_stock_codes),
            done_at_start=len(done_stock_codes),
            pending_count=pending_total,
            run_total=len(stock_rows),
            run_done=0,
            ok_count=0,
            fail_count=0,
            started_at=kst_now_iso(),
            universe_mode=universe_mode,
            day_cap=max_per_day or None,
        )

        req = requests.Session()

        tag_cache: Dict[str, ThemeTag] = {}
        keyword_edge_now = utc_now_naive()

        # 커밋/롤백 주기
        ok_count = 0
        done_count = 0  # 이번 큐에서 예상치 못한 이미처리(동시 실행 등)
        fail_count = 0
        stock_committed = 0

        for idx, s in enumerate(stock_rows):
            code = s["stock_code"]
            name = s["stock_name"]
            _emit_progress(
                biz_date=biz.isoformat(),
                running=True,
                status="running",
                universe_total=len(all_stock_rows),
                done_count=len(done_stock_codes) + ok_count,
                done_at_start=len(done_stock_codes),
                pending_count=max(0, len(all_stock_rows) - len(done_stock_codes) - ok_count),
                run_total=len(stock_rows),
                run_done=idx,
                ok_count=ok_count,
                fail_count=fail_count,
                current_stock_code=code,
                current_stock_name=name,
            )
            # force가 아니면 pending 큐만 넣었지만, 동시 실행 대비 한 번 더 가드
            if not args.force and code in done_stock_codes:
                done_count += 1
                continue

            t0 = time.time()
            query = f"{name} {code}"
            params = {"query": query, "display": max(1, min(args.display, 50)), "sort": args.sort_date}
            headers = {
                "X-Naver-Client-Id": Config.NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": Config.NAVER_CLIENT_SECRET,
            }

            # 429/일시 장애 대응
            last_err: Optional[str] = None
            resp = None
            for attempt in range(args.max_retries):
                try:
                    resp = req.get(
                        Config.NAVER_NEWS_API_URL,
                        params=params,
                        headers=headers,
                        timeout=args.timeout,
                    )
                    if resp.status_code == 429:
                        wait_s = 10 + attempt * 15
                        log.warning("429 발생 (stock=%s) wait=%ss attempt=%d/%d", code, wait_s, attempt + 1, args.max_retries)
                        time.sleep(wait_s)
                        continue
                    if resp.status_code != 200:
                        last_err = f"HTTP {resp.status_code}"
                        break
                    last_err = None
                    break
                except Exception as e:
                    last_err = str(e)
                    time.sleep(3 + attempt * 3)

            if resp is None or last_err:
                fail_count += 1
                log.error("news 조회 실패 stock=%s name=%s err=%s", code, name, last_err)
                continue

            try:
                data = resp.json()
            except Exception as e:
                fail_count += 1
                log.error("news json parse 실패 stock=%s err=%s", code, e)
                continue

            items = data.get("items") or []
            # 오늘(biz_date) 기사만 수집 (제목 + 검색 API 요약 description)
            today_articles: List[Dict] = []
            today_texts: List[str] = []
            for it in items:
                title = _safe_text(it.get("title"))
                description = _safe_text(it.get("description"))
                url = _safe_text(it.get("link") or it.get("url"))
                if not title or not url:
                    continue
                dt = _parse_pubdate_to_dt(it.get("pubDate"))
                if dt is None:
                    continue
                dt_kst_date = dt.astimezone(KST).date()
                if dt_kst_date != biz:
                    continue
                today_texts.append(title)
                if description and description != title:
                    today_texts.append(description)
                today_articles.append(
                    {
                        "title": title,
                        "description": description,
                        "url": url,
                        "published_at": dt,
                    }
                )

            if not today_articles:
                # 당일 기사 0건이어도 재시작/재실행 시 다시 API를 때리지 않도록
                # 마커를 남겨 skip set에 들어가게 한다.
                empty_url = f"stocke://empty-news/{biz.isoformat()}/{code}"
                exists_empty = (
                    session.query(TagArticle.id)
                    .filter(TagArticle.url == empty_url)
                    .first()
                )
                if not exists_empty:
                    empty_article = TagArticle(
                        source="naver_news",
                        biz_date=biz,
                        collected_at=utc_now_naive(),
                        title="(no articles today)",
                        url=empty_url,
                        published_at=None,
                        stock_code=code,
                        stock_name=name,
                        meta_json={"query": query, "empty": True},
                    )
                    session.add(empty_article)
                    session.flush()
                    # edge 없이 skip 조건에 안 걸리므로, self-tag로 가벼운 완결 표시
                    empty_tag = _upsert_keyword_tag(
                        session,
                        keyword="__empty__",
                        tag_cache=tag_cache,
                        tag_key_cache={},
                    )
                    session.add(
                        TagArticleKeywordEdge(
                            article_id=empty_article.id,
                            tag_id=empty_tag.id,
                            source="news_title",
                            weight=0.0,
                            observed_at=utc_now_naive(),
                            meta_json={"empty": True, "biz_date": biz.isoformat()},
                        )
                    )
                ok_count += 1
                stock_committed += 1
                if stock_committed >= args.commit_every:
                    try:
                        session.commit()
                    except Exception as e:
                        session.rollback()
                        log.error("커밋 실패 후 롤백 err=%s", e)
                    finally:
                        stock_committed = 0
                elapsed = time.time() - t0
                if elapsed < args.min_call_interval:
                    time.sleep(args.min_call_interval - elapsed)
                continue

            # 기사 insert (url unique)
            urls = [a["url"] for a in today_articles]
            existing_urls = set(
                u
                for (u,) in session.query(TagArticle.url).filter(
                    TagArticle.url.in_(urls),
                    TagArticle.source == "naver_news",
                ).all()
            )
            inserted_articles: List[TagArticle] = []
            for a in today_articles:
                if a["url"] in existing_urls:
                    continue
                row = TagArticle(
                    source="naver_news",
                    biz_date=biz,
                    collected_at=utc_now_naive(),
                    title=a["title"][:1000],
                    url=a["url"][:2000],
                    published_at=a["published_at"].replace(tzinfo=None) if a["published_at"] else None,
                    stock_code=code,
                    stock_name=name,
                    meta_json={
                        "query": query,
                        "description": (a.get("description") or "")[:500] or None,
                    },
                )
                session.add(row)
                inserted_articles.append(row)

            # flush to get ids for inserted rows (기존 row는 keyword edge에서 건너뛸 수 있음)
            session.flush()

            # url -> TagArticle (inserted 포함)
            article_url_to_row: Dict[str, TagArticle] = {}
            if inserted_articles:
                for r in inserted_articles:
                    article_url_to_row[r.url] = r
            # 기존 기사도 keyword edge를 만들고 싶으면 조회:
            missing_urls = [u for u in urls if u not in article_url_to_row]
            if missing_urls:
                existing_article_rows = (
                    session.query(TagArticle)
                    .filter(TagArticle.url.in_(missing_urls), TagArticle.source == "naver_news")
                    .all()
                )
                for r in existing_article_rows:
                    article_url_to_row[r.url] = r

            # 종목 키워드(기사 제목+요약 전체 기준)
            stock_kw_rows = extract_keywords(today_texts, top_n=args.stock_keywords_top)

            # keyword tag upsert cache + stock->keyword edge upsert (observed_at snapshot)
            news_edge_observed_at = utc_now_naive()
            stock_keyword_edge_count = 0
            for kw in stock_kw_rows:
                keyword = str(kw.get("keyword") or "").strip()
                if not keyword:
                    continue
                mention_count = float(kw.get("mention_count") or 0)
                if mention_count <= 0:
                    mention_count = 1.0
                tag = _upsert_keyword_tag(
                    session,
                    keyword=keyword,
                    tag_cache=tag_cache,
                    tag_key_cache={},
                )
                # 종목 ↔ 키워드 edge
                session.add(
                    ThemeTagEdge(
                        stock_code=code,
                        stock_name=name,
                        tag_id=tag.id,
                        source="news_title",
                        role="peer",
                        weight=mention_count if args.keyword_edge_weight_mode == "mention_count" else mention_count,
                        observed_at=news_edge_observed_at,
                        meta_json={"biz_date": biz.isoformat(), "stock_query": query},
                    )
                )
                stock_keyword_edge_count += 1

            # 기사(제목+요약) -> 키워드 edge
            if stock_keyword_edge_count > 0:
                article_kw_observed_at = utc_now_naive()
                for a in today_articles:
                    art_row = article_url_to_row.get(a["url"])
                    if not art_row:
                        continue
                    texts = [a["title"]]
                    desc = (a.get("description") or "").strip()
                    if desc and desc != a["title"]:
                        texts.append(desc)
                    title_kw = extract_keywords(texts, top_n=args.article_title_keywords_top)
                    if not title_kw:
                        continue
                    for kw in title_kw:
                        keyword = str(kw.get("keyword") or "").strip()
                        if not keyword:
                            continue
                        mention_count = float(kw.get("mention_count") or 0) or 1.0
                        tag = _upsert_keyword_tag(
                            session,
                            keyword=keyword,
                            tag_cache=tag_cache,
                            tag_key_cache={},
                        )
                        session.add(
                            TagArticleKeywordEdge(
                                article_id=art_row.id,
                                tag_id=tag.id,
                                source="news_title",
                                weight=mention_count,
                                observed_at=article_kw_observed_at,
                                meta_json={"biz_date": biz.isoformat()},
                            )
                        )

            stock_committed += 1
            ok_count += 1

            # 커밋 주기
            if stock_committed >= args.commit_every:
                try:
                    session.commit()
                    log.info(
                        "진행 stock=%d/%d ok=%d skip=%d fail=%d elapsed=%.1fs",
                        idx + 1,
                        len(stock_rows),
                        ok_count,
                        done_count,
                        fail_count,
                        time.time() - started_at,
                    )
                    _emit_progress(
                        biz_date=biz.isoformat(),
                        running=True,
                        status="running",
                        universe_total=len(all_stock_rows),
                        done_count=len(done_stock_codes) + ok_count,
                        done_at_start=len(done_stock_codes),
                        pending_count=max(0, len(all_stock_rows) - len(done_stock_codes) - ok_count),
                        run_total=len(stock_rows),
                        run_done=idx + 1,
                        ok_count=ok_count,
                        fail_count=fail_count,
                        current_stock_code=code,
                        current_stock_name=name,
                    )
                except Exception as e:
                    session.rollback()
                    log.error("커밋 실패 후 롤백 err=%s", e)
                finally:
                    stock_committed = 0

            # 종목당 최소 간격
            elapsed = time.time() - t0
            if elapsed < args.min_call_interval:
                time.sleep(args.min_call_interval - elapsed)

        # 마지막 커밋
        try:
            # keyword_daily_stats 재계산:
            # - source=naver_news + biz_date 기준
            # - 기사(단어) edge의 weight 합계를 mention_count로 사용
            # - keyword가 등장한 종목 수를 stock_count로 집계
            daily_rows = (
                session.query(
                    ThemeTag.name_ko.label("keyword"),
                    func.coalesce(func.sum(TagArticleKeywordEdge.weight), 0).label("mention_count"),
                    func.coalesce(func.count(func.distinct(TagArticle.stock_code)), 0).label("stock_count"),
                )
                .join(TagArticleKeywordEdge, TagArticleKeywordEdge.tag_id == ThemeTag.id)
                .join(TagArticle, TagArticle.id == TagArticleKeywordEdge.article_id)
                .filter(ThemeTag.tag_type == "news_keyword")
                .filter(TagArticle.source == "naver_news")
                .filter(TagArticle.biz_date == biz)
                .group_by(ThemeTag.id, ThemeTag.name_ko)
                .all()
            )

            prev_date = (
                session.query(func.max(KeywordDailyStat.biz_date))
                .filter(KeywordDailyStat.biz_date < biz)
                .scalar()
            )

            for r in daily_rows:
                keyword = str(r.keyword or "").strip()
                if not keyword or keyword == "__empty__":
                    continue
                cnt = int(r.mention_count or 0)
                stock_count = int(r.stock_count or 0)

                prev_cnt = 0
                if prev_date:
                    prev = (
                        session.query(KeywordDailyStat)
                        .filter(KeywordDailyStat.biz_date == prev_date, KeywordDailyStat.keyword == keyword)
                        .first()
                    )
                    prev_cnt = int(prev.mention_count or 0) if prev else 0

                delta = cnt - prev_cnt
                trend = "new" if prev_cnt == 0 else ("up" if delta > 0 else ("down" if delta < 0 else "flat"))

                existing = (
                    session.query(KeywordDailyStat)
                    .filter(KeywordDailyStat.biz_date == biz, KeywordDailyStat.keyword == keyword)
                    .first()
                )
                if existing:
                    existing.mention_count = cnt
                    existing.stock_count = stock_count
                    existing.delta_vs_prev = delta
                    existing.trend_label = trend
                    existing.updated_at = utc_now_naive()
                    existing.source = "news_title"
                    session.add(existing)
                else:
                    session.add(
                        KeywordDailyStat(
                            keyword=keyword,
                            biz_date=biz,
                            mention_count=cnt,
                            stock_count=stock_count,
                            delta_vs_prev=delta,
                            trend_label=trend,
                            source="news_title",
                            updated_at=utc_now_naive(),
                        )
                    )

            session.commit()
        except Exception as e:
            session.rollback()
            log.error("마지막 커밋 실패 err=%s", e)
            hb_stop.set()
            hb_thread.join(timeout=1.0)
            return 1

        remaining = max(0, pending_total - offset - ok_count)
        final_done = len(done_stock_codes) + ok_count
        # 일일 상한에 도달했으면 전종목 남은 게 있어도 오늘 분은 완료
        hit_day_cap = bool(max_per_day > 0 and final_done >= max_per_day)
        final_status = "all_done" if (remaining <= 0 or hit_day_cap) else "run_done"
        hb_stop.set()
        hb_thread.join(timeout=1.0)

        _emit_progress(
            biz_date=biz.isoformat(),
            running=False,
            status=final_status,
            universe_total=progress_universe,
            done_count=final_done,
            pending_count=0 if final_status == "all_done" else remaining,
            run_total=len(stock_rows),
            run_done=len(stock_rows),
            ok_count=ok_count,
            fail_count=fail_count,
            percent=round(min(100.0, (final_done / progress_universe) * 100), 1) if progress_universe else 0,
            day_cap=max_per_day or None,
            universe_mode=universe_mode,
            day_capped=day_capped or hit_day_cap,
        )
        log.info(
            "완료 biz_date=%s mode=%s 이번큐=%d ok=%d skip=%d fail=%d remaining≈%d day_cap=%s elapsed=%.1fs",
            biz.isoformat(),
            universe_mode,
            len(stock_rows),
            ok_count,
            done_count,
            fail_count,
            0 if final_status == "all_done" else remaining,
            max_per_day or "none",
            time.time() - started_at,
        )
        if remaining > 0:
            log.info(
                "이어 실행: 같은 명령 재실행 (이미 완료된 종목은 자동 스킵, 다음 미처리부터 진행)"
            )
        else:
            try:
                from utils.theme_score_engine import compute_theme_scores_for_date
                score_res = compute_theme_scores_for_date(session, biz_date=biz)
                log.info("연관도 점수 재계산: %s", score_res)
            except Exception as score_err:
                log.warning("연관도 점수 재계산 스킵: %s", score_err)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

