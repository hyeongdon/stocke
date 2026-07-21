"""테마/키워드 ↔ 종목 매핑 스토어 (스파이크용)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional

import requests
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.config import Config
from core.models import (
    FundamentalSnapshot,
    KeywordDailyStat,
    TagArticle,
    TagArticleKeywordEdge,
    ThemeScoreDaily,
    ThemeTag,
    ThemeTagEdge,
)
from utils.theme_score_engine import compute_theme_scores_for_date
from utils.batch_scheduler_status import get_batch_jobs_status
from utils.datetime_kst import kst_today, utc_now_naive
from utils.stock_news_progress import get_stock_news_progress
from utils.theme_keyword_rules import extract_keywords
from utils.theme_naver_crawler import crawl_theme_list, crawl_theme_stocks


def _slug(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "_")


def _upsert_tag(
    session: Session,
    *,
    tag_key: str,
    name_ko: str,
    tag_type: str,
    source: str,
    meta_json: Optional[dict] = None,
) -> ThemeTag:
    row = session.query(ThemeTag).filter(ThemeTag.tag_key == tag_key).first()
    if row:
        row.name_ko = name_ko
        row.tag_type = tag_type
        row.source = source
        if meta_json is not None:
            row.meta_json = meta_json
        row.updated_at = utc_now_naive()
        return row
    row = ThemeTag(
        tag_key=tag_key,
        name_ko=name_ko,
        tag_type=tag_type,
        source=source,
        meta_json=meta_json,
        created_at=utc_now_naive(),
        updated_at=utc_now_naive(),
    )
    session.add(row)
    session.flush()
    return row


def _strip_naver_news_html(text: str) -> str:
    return (
        (text or "")
        .replace("<b>", "")
        .replace("</b>", "")
        .replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .strip()
    )


def _fetch_news_titles(query: str, display: int = 8) -> List[str]:
    """네이버 뉴스 검색 API에서 제목+요약(description) 수집.

    본문 크롤 없음 — 검색 응답에 포함된 description만 사용(추가 API 호출 없음).
    """
    if not query or not Config.NAVER_CLIENT_ID or not Config.NAVER_CLIENT_SECRET:
        return []
    try:
        resp = requests.get(
            Config.NAVER_NEWS_API_URL,
            params={"query": query, "display": max(1, min(display, 50)), "sort": "date"},
            headers={
                "X-Naver-Client-Id": Config.NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": Config.NAVER_CLIENT_SECRET,
            },
            timeout=12,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        out: List[str] = []
        seen = set()
        for it in data.get("items") or []:
            for key in ("title", "description"):
                raw = _strip_naver_news_html(str(it.get(key) or ""))
                if not raw or raw in seen:
                    continue
                seen.add(raw)
                out.append(raw)
        return out
    except Exception:
        return []


def refresh_theme_mapping_snapshot(
    session: Session,
    *,
    top_n: int = 0,
    include_news_keywords: bool = True,
    news_stock_limit_per_theme: int = 2,
) -> Dict:
    now = utc_now_naive()
    biz = kst_today()

    themes = crawl_theme_list(limit=top_n)
    if not themes:
        return {"ok": False, "error": "테마 목록이 비어 있습니다."}

    # 당일 정적 편입 스냅샷 교체
    session.query(ThemeTagEdge).filter(
        ThemeTagEdge.source == "naver_theme",
        ThemeTagEdge.biz_date == biz,
    ).delete(synchronize_session=False)

    inserted_edges = 0
    theme_names: List[str] = []
    keyword_stock_sets: Dict[str, set] = defaultdict(set)
    keyword_counter = defaultdict(int)
    keyword_stock_sets: Dict[str, set] = defaultdict(set)
    for t in themes:
        theme_name = t["theme_name"]
        theme_no = t["theme_no"]
        theme_names.append(theme_name)
        tag = _upsert_tag(
            session,
            tag_key=f"theme_{theme_no}_{_slug(theme_name)}",
            name_ko=theme_name,
            tag_type="theme",
            source="naver_theme",
            meta_json={
                "naver_theme_no": theme_no,
                "valid_from": biz.isoformat(),
            },
        )

        stocks = crawl_theme_stocks(theme_no)
        for idx, stock in enumerate(stocks):
            edge = ThemeTagEdge(
                stock_code=stock["stock_code"],
                stock_name=stock["stock_name"],
                tag_id=tag.id,
                source="naver_theme",
                role="leader" if idx == 0 else "member",
                weight=1.0,
                biz_date=biz,
                rank=idx + 1,
                inclusion_flag=True,
                reason_text=f"네이버 테마 '{theme_name}' 편입",
                observed_at=now,
                meta_json={"naver_theme_no": theme_no},
            )
            session.add(edge)
            inserted_edges += 1
        # 키워드 집계(기본): 테마명 자체
        base_kws = extract_keywords([theme_name], top_n=10)
        for kw in base_kws:
            keyword_counter[kw["keyword"]] += int(kw["mention_count"])
            for stock in stocks:
                keyword_stock_sets[kw["keyword"]].add(stock["stock_code"])

        # 키워드 집계(확장): 종목 뉴스 제목+요약(description) 기반 (본문 크롤 없음)
        if include_news_keywords and stocks:
            for stock in stocks[: max(1, news_stock_limit_per_theme)]:
                titles = _fetch_news_titles(stock["stock_name"], display=8)
                if not titles:
                    continue
                kws = extract_keywords(titles, top_n=8)
                for kw in kws:
                    k = kw["keyword"]
                    keyword_counter[k] += int(kw["mention_count"])
                    keyword_stock_sets[k].add(stock["stock_code"])
                    kw_tag = _upsert_tag(
                        session,
                        tag_key=f"kw_{_slug(k)}",
                        name_ko=k,
                        tag_type="news_keyword",
                        source="news_title",
                    )
                    session.add(
                        ThemeTagEdge(
                            stock_code=stock["stock_code"],
                            stock_name=stock["stock_name"],
                            tag_id=kw_tag.id,
                            source="news_title",
                            role="peer",
                            weight=0.5,
                            observed_at=now,
                            meta_json={"from": "naver_news_title", "theme": theme_name},
                        )
                    )
                    inserted_edges += 1

    if keyword_counter:
        kw_rows = [
            {"keyword": k, "mention_count": int(v)}
            for k, v in sorted(keyword_counter.items(), key=lambda x: x[1], reverse=True)[:50]
        ]
    else:
        kw_rows = extract_keywords(theme_names, top_n=50)
    prev_date = (
        session.query(func.max(KeywordDailyStat.biz_date))
        .filter(KeywordDailyStat.biz_date < biz)
        .scalar()
    )
    prev_map = {}
    if prev_date:
        for row in session.query(KeywordDailyStat).filter(KeywordDailyStat.biz_date == prev_date).all():
            prev_map[row.keyword] = int(row.mention_count or 0)

    for row in kw_rows:
        kw = row["keyword"]
        cnt = int(row["mention_count"])
        prev = prev_map.get(kw, 0)
        delta = cnt - prev
        trend = "new" if prev == 0 else ("up" if delta > 0 else ("down" if delta < 0 else "flat"))
        existing = session.query(KeywordDailyStat).filter(
            KeywordDailyStat.biz_date == biz,
            KeywordDailyStat.keyword == kw,
        ).first()
        stock_count = len(keyword_stock_sets.get(kw, set()))
        if existing:
            existing.mention_count = cnt
            existing.stock_count = stock_count
            existing.delta_vs_prev = delta
            existing.trend_label = trend
            existing.updated_at = utc_now_naive()
        else:
            session.add(
                KeywordDailyStat(
                    keyword=kw,
                    biz_date=biz,
                    mention_count=cnt,
                    stock_count=stock_count,
                    delta_vs_prev=delta,
                    trend_label=trend,
                    source="theme_name",
                    updated_at=utc_now_naive(),
                )
            )

    session.commit()

    score_result = compute_theme_scores_for_date(session, biz_date=biz)

    return {
        "ok": True,
        "themes": len(themes),
        "edges": inserted_edges,
        "keywords": len(kw_rows),
        "biz_date": biz.isoformat(),
        "scores": score_result,
    }


def get_theme_tags(session: Session, limit: int = 100) -> List[Dict]:
    rows = (
        session.query(ThemeTag)
        .filter(ThemeTag.tag_type == "theme")
        .order_by(ThemeTag.name_ko.asc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    out = []
    for r in rows:
        cnt = session.query(func.count(ThemeTagEdge.id)).filter(ThemeTagEdge.tag_id == r.id).scalar() or 0
        out.append({
            "id": r.id,
            "tag_key": r.tag_key,
            "name_ko": r.name_ko,
            "edge_count": int(cnt),
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        })
    return out


def get_tags_by_stock(session: Session, stock_code: str, limit: int = 50) -> List[Dict]:
    code = _norm_code(stock_code)
    if not code:
        return []
    query_codes = list(dict.fromkeys([code, code.lstrip("0") or "0"]))
    q = (
        session.query(ThemeTagEdge, ThemeTag)
        .join(ThemeTag, ThemeTag.id == ThemeTagEdge.tag_id)
        .filter(ThemeTagEdge.stock_code.in_(query_codes))
        .order_by(ThemeTagEdge.observed_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    seen = set()
    out: List[Dict] = []
    for edge, tag in q:
        key = tag.id
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "tag_id": tag.id,
            "tag_name": tag.name_ko,
            "tag_type": tag.tag_type,
            "source": edge.source,
            "role": edge.role,
            "observed_at": edge.observed_at.isoformat() if edge.observed_at else None,
            "stock_code": edge.stock_code,
            "stock_name": edge.stock_name,
        })
    return out


def get_stocks_by_tag(session: Session, tag_id: int, limit: int = 200) -> List[Dict]:
    q = (
        session.query(ThemeTagEdge)
        .filter(ThemeTagEdge.tag_id == int(tag_id))
        .order_by(ThemeTagEdge.observed_at.desc())
        .limit(max(1, min(limit, 1000)))
        .all()
    )
    seen = set()
    out: List[Dict] = []
    for edge in q:
        if edge.stock_code in seen:
            continue
        seen.add(edge.stock_code)
        out.append({
            "stock_code": edge.stock_code,
            "stock_name": edge.stock_name,
            "source": edge.source,
            "role": edge.role,
            "weight": edge.weight,
            "observed_at": edge.observed_at.isoformat() if edge.observed_at else None,
        })
    return out


def get_keywords_today(session: Session, limit: int = 20) -> List[Dict]:
    biz = session.query(func.max(KeywordDailyStat.biz_date)).scalar()
    if not biz:
        return []
    rows = (
        session.query(KeywordDailyStat)
        .filter(KeywordDailyStat.biz_date == biz)
        .order_by(KeywordDailyStat.mention_count.desc(), KeywordDailyStat.stock_count.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    return [
        {
            "keyword": r.keyword,
            "biz_date": r.biz_date.isoformat() if r.biz_date else None,
            "mention_count": int(r.mention_count or 0),
            "stock_count": int(r.stock_count or 0),
            "delta_vs_prev": int(r.delta_vs_prev or 0),
            "trend_label": r.trend_label,
        }
        for r in rows
    ]


def get_theme_batch_status(session: Session) -> Dict:
    """대시보드용 테마/뉴스 배치 현황 요약."""
    latest_theme_at = (
        session.query(func.max(ThemeTagEdge.observed_at))
        .filter(ThemeTagEdge.source == "naver_theme")
        .scalar()
    )
    latest_news_at = (
        session.query(func.max(TagArticle.collected_at))
        .filter(TagArticle.source == "naver_news")
        .scalar()
    )
    latest_keyword_at = session.query(func.max(KeywordDailyStat.updated_at)).scalar()
    latest_keyword_biz = session.query(func.max(KeywordDailyStat.biz_date)).scalar()
    latest_article_biz = session.query(func.max(TagArticle.biz_date)).scalar()

    article_count_today = 0
    article_stock_count_today = 0
    keyword_count_today = 0
    if latest_article_biz:
        article_count_today = int(
            session.query(func.count(TagArticle.id))
            .filter(
                TagArticle.source == "naver_news",
                TagArticle.biz_date == latest_article_biz,
                ~TagArticle.url.like("stocke://empty-news/%"),
            )
            .scalar()
            or 0
        )
        article_stock_count_today = int(
            session.query(func.count(func.distinct(TagArticle.stock_code)))
            .filter(
                TagArticle.source == "naver_news",
                TagArticle.biz_date == latest_article_biz,
            )
            .scalar()
            or 0
        )
    if latest_keyword_biz:
        keyword_count_today = int(
            session.query(func.count(KeywordDailyStat.id))
            .filter(KeywordDailyStat.biz_date == latest_keyword_biz)
            .scalar()
            or 0
        )

    return {
        "theme_snapshot_last_at": latest_theme_at.isoformat() if latest_theme_at else None,
        "news_batch_last_at": latest_news_at.isoformat() if latest_news_at else None,
        "keyword_stats_last_at": latest_keyword_at.isoformat() if latest_keyword_at else None,
        "theme_snapshot_cadence": "매일 18:00",
        "news_batch_cadence": "장 마감 후 순차/분할 실행",
        "keyword_stats_cadence": "뉴스 배치 완료 시 재집계",
        "latest_article_biz_date": latest_article_biz.isoformat() if latest_article_biz else None,
        "latest_keyword_biz_date": latest_keyword_biz.isoformat() if latest_keyword_biz else None,
        "article_count_today": article_count_today,
        "article_stock_count_today": article_stock_count_today,
        "keyword_count_today": keyword_count_today,
        "stock_news_progress": get_stock_news_progress(session),
        "batch_jobs": get_batch_jobs_status(),
    }


def get_stocks_by_keyword(session: Session, keyword: str, limit: int = 200) -> List[Dict]:
    kw = (keyword or "").strip()
    if not kw:
        return []
    tag = (
        session.query(ThemeTag)
        .filter(ThemeTag.tag_type == "news_keyword")
        .filter(func.lower(ThemeTag.name_ko) == kw.lower())
        .order_by(ThemeTag.updated_at.desc())
        .first()
    )
    if not tag:
        return []
    return get_stocks_by_tag(session, tag.id, limit=limit)


def list_articles_by_stock(
    session: Session,
    stock_code: str,
    *,
    biz_date: date | None = None,
    limit: int = 50,
) -> List[Dict]:
    """종목에 연결된 네이버 뉴스 기사 목록(tag_articles)."""
    code = _norm_code(stock_code).lstrip("0") or "0"
    code_z = _norm_code(stock_code)
    q = session.query(TagArticle).filter(TagArticle.source == "naver_news")
    if biz_date:
        q = q.filter(TagArticle.biz_date == biz_date)
    else:
        # 가장 최근 수집된 biz_date
        latest_biz = session.query(func.max(TagArticle.biz_date)).scalar()
        if latest_biz:
            q = q.filter(TagArticle.biz_date == latest_biz)

    rows = (
        q.filter((TagArticle.stock_code == code_z) | (TagArticle.stock_code == code))
        .filter(~TagArticle.url.like("stocke://empty-news/%"))
        .order_by(TagArticle.published_at.desc(), TagArticle.collected_at.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return [
        {
            "id": r.id,
            "title": r.title,
            "url": r.url,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "biz_date": r.biz_date.isoformat() if r.biz_date else None,
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "collected_at": r.collected_at.isoformat() if r.collected_at else None,
        }
        for r in rows
    ]


def list_articles_by_keyword(
    session: Session,
    keyword: str,
    *,
    biz_date: date | None = None,
    limit: int = 50,
) -> List[Dict]:
    """키워드(news_keyword) 태그에 연결된 기사 목록."""
    kw = (keyword or "").strip()
    if not kw:
        return []
    tag = (
        session.query(ThemeTag)
        .filter(ThemeTag.tag_type == "news_keyword")
        .filter(func.lower(ThemeTag.name_ko) == kw.lower())
        .order_by(ThemeTag.updated_at.desc())
        .first()
    )
    if not tag:
        return []

    q = (
        session.query(TagArticle, TagArticleKeywordEdge)
        .join(TagArticleKeywordEdge, TagArticleKeywordEdge.article_id == TagArticle.id)
        .filter(TagArticleKeywordEdge.tag_id == tag.id)
        .filter(TagArticle.source == "naver_news")
        .filter(~TagArticle.url.like("stocke://empty-news/%"))
    )
    if biz_date:
        q = q.filter(TagArticle.biz_date == biz_date)
    else:
        latest_biz = session.query(func.max(TagArticle.biz_date)).scalar()
        if latest_biz:
            q = q.filter(TagArticle.biz_date == latest_biz)

    rows = (
        q.order_by(TagArticle.published_at.desc(), TagArticle.collected_at.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    out = []
    for article, edge in rows:
        out.append(
            {
                "id": article.id,
                "title": article.title,
                "url": article.url,
                "published_at": article.published_at.isoformat() if article.published_at else None,
                "biz_date": article.biz_date.isoformat() if article.biz_date else None,
                "stock_code": article.stock_code,
                "stock_name": article.stock_name,
                "matched_keyword_weight": float(edge.weight or 1.0),
            }
        )
    return out


_SOURCE_RANK = {
    "naver_theme": 0,
    "news_title": 1,
    "news_keyword": 1,
    "manual": 2,
}

_THEME_TYPES = frozenset({"theme"})
_KEYWORD_TYPES = frozenset({"news_keyword", "keyword"})


def _norm_code(code: str) -> str:
    return str(code or "").replace("A", "").strip().zfill(6)


def _load_universe_rows(
    session: Session,
    market: str,
) -> tuple[Optional[str], str, List[Dict]]:
    """코스피/코스닥 유니버스 — FundamentalSnapshot 최신일 우선."""
    mkt = (market or "all").strip().lower()
    as_of = session.query(func.max(FundamentalSnapshot.as_of_date)).scalar()
    if as_of:
        q = session.query(
            FundamentalSnapshot.stock_code,
            FundamentalSnapshot.stock_name,
            FundamentalSnapshot.market,
        ).filter(FundamentalSnapshot.as_of_date == as_of)
        if mkt in ("kospi", "kosdaq"):
            q = q.filter(FundamentalSnapshot.market == mkt.upper())
        rows = q.order_by(FundamentalSnapshot.stock_code.asc()).all()
        if rows:
            items = [
                {
                    "stock_code": _norm_code(r.stock_code),
                    "stock_name": r.stock_name or "",
                    "market": (r.market or "").upper(),
                }
                for r in rows
            ]
            return as_of.isoformat(), "fundamental_snapshot", items

    edge_rows = session.query(ThemeTagEdge.stock_code, ThemeTagEdge.stock_name).all()
    article_rows = (
        session.query(TagArticle.stock_code, TagArticle.stock_name)
        .filter(TagArticle.stock_code.isnot(None))
        .all()
    )
    by_code: Dict[str, Dict] = {}
    for code_raw, name in edge_rows + article_rows:
        code = _norm_code(str(code_raw or ""))
        if not code or len(code) != 6:
            continue
        by_code.setdefault(code, {"stock_code": code, "stock_name": name or "", "market": ""})
    items = sorted(by_code.values(), key=lambda x: x["stock_code"])
    return None, "mapped_stocks_union", items


def _theme_mapped_codes(session: Session) -> set[str]:
    codes: set[str] = set()
    latest_score_date = session.query(func.max(ThemeScoreDaily.biz_date)).scalar()
    if latest_score_date:
        for (code,) in (
            session.query(ThemeScoreDaily.stock_code)
            .filter(ThemeScoreDaily.biz_date == latest_score_date)
            .distinct()
            .all()
        ):
            codes.add(_norm_code(code))
    for (code,) in (
        session.query(ThemeTagEdge.stock_code)
        .join(ThemeTag, ThemeTag.id == ThemeTagEdge.tag_id)
        .filter(ThemeTag.tag_type.in_(list(_THEME_TYPES)))
        .distinct()
        .all()
    ):
        codes.add(_norm_code(code))
    return codes


def _keyword_mapped_codes(session: Session) -> set[str]:
    """실제 키워드 태그(news_keyword/keyword edge)가 있는 종목만."""
    codes: set[str] = set()
    for (code,) in (
        session.query(ThemeTagEdge.stock_code)
        .join(ThemeTag, ThemeTag.id == ThemeTagEdge.tag_id)
        .filter(ThemeTag.tag_type.in_(list(_KEYWORD_TYPES)))
        .distinct()
        .all()
    ):
        codes.add(_norm_code(code))
    return codes


def _news_scanned_codes(session: Session) -> set[str]:
    """뉴스 배치가 조회한 종목 (기사 없음 placeholder 포함)."""
    codes: set[str] = set()
    for (code,) in (
        session.query(TagArticle.stock_code)
        .filter(TagArticle.stock_code.isnot(None))
        .distinct()
        .all()
    ):
        if code:
            codes.add(_norm_code(code))
    return codes


def _news_with_article_codes(session: Session) -> set[str]:
    """실제 네이버 기사가 수집된 종목."""
    codes: set[str] = set()
    for (code,) in (
        session.query(TagArticle.stock_code)
        .filter(
            TagArticle.stock_code.isnot(None),
            TagArticle.source == "naver_news",
            ~TagArticle.url.like("stocke://empty-news/%"),
        )
        .distinct()
        .all()
    ):
        if code:
            codes.add(_norm_code(code))
    return codes


def get_theme_universe_coverage(
    session: Session,
    *,
    market: str = "all",
    gap: str = "any",
    q: str = "",
    limit: int = 200,
    offset: int = 0,
) -> Dict:
    """코스피/코스닥 전체 대비 테마·키워드 매핑 커버리지 및 미매핑 목록."""
    mkt = (market or "all").strip().lower()
    if mkt not in ("all", "kospi", "kosdaq"):
        mkt = "all"
    gap_mode = (gap or "any").strip().lower()
    if gap_mode not in ("theme", "keyword", "both", "any", "all"):
        gap_mode = "any"

    as_of_str, universe_source, universe = _load_universe_rows(session, mkt)
    theme_codes = _theme_mapped_codes(session)
    keyword_codes = _keyword_mapped_codes(session)
    news_scanned_codes = _news_scanned_codes(session)
    news_article_codes = _news_with_article_codes(session)

    latest_score_date = session.query(func.max(ThemeScoreDaily.biz_date)).scalar()
    latest_article_biz = session.query(func.max(TagArticle.biz_date)).scalar()

    def _matches_gap(has_theme: bool, has_keyword: bool) -> bool:
        if gap_mode == "all":
            return True
        if gap_mode == "theme":
            return not has_theme
        if gap_mode == "keyword":
            return not has_keyword
        if gap_mode == "both":
            return not has_theme and not has_keyword
        return not has_theme or not has_keyword

    query = (q or "").strip().lower()
    enriched: List[Dict] = []
    stats = {
        "total": 0,
        "kospi": 0,
        "kosdaq": 0,
        "theme_mapped": 0,
        "keyword_mapped": 0,
        "both_mapped": 0,
        "unmapped_theme": 0,
        "unmapped_keyword": 0,
        "unmapped_any": 0,
        "news_scanned": 0,
        "news_with_article": 0,
    }

    for row in universe:
        code = row["stock_code"]
        has_theme = code in theme_codes
        has_keyword = code in keyword_codes
        has_news_scanned = code in news_scanned_codes
        has_news_article = code in news_article_codes
        stats["total"] += 1
        m = (row.get("market") or "").upper()
        if m == "KOSPI":
            stats["kospi"] += 1
        elif m == "KOSDAQ":
            stats["kosdaq"] += 1
        if has_theme:
            stats["theme_mapped"] += 1
        else:
            stats["unmapped_theme"] += 1
        if has_keyword:
            stats["keyword_mapped"] += 1
        else:
            stats["unmapped_keyword"] += 1
        if has_theme and has_keyword:
            stats["both_mapped"] += 1
        if not has_theme or not has_keyword:
            stats["unmapped_any"] += 1
        if has_news_scanned:
            stats["news_scanned"] += 1
        if has_news_article:
            stats["news_with_article"] += 1

        if query and query not in code.lower() and query not in (row.get("stock_name") or "").lower():
            continue
        if not _matches_gap(has_theme, has_keyword):
            continue
        enriched.append({
            "stock_code": code,
            "stock_name": row.get("stock_name") or "",
            "market": m,
            "has_theme": has_theme,
            "has_keyword": has_keyword,
            "has_news_scanned": has_news_scanned,
            "has_news_article": has_news_article,
        })

    total_filtered = len(enriched)
    page = enriched[offset: offset + max(1, min(limit, 500))]

    if page:
        codes = [p["stock_code"] for p in page]
        tag_map = get_latest_map_by_codes(
            codes,
            theme_limit=8,
            keyword_limit=8,
            session=session,
        )
        for item in page:
            payload = tag_map.get(item["stock_code"]) or _empty_tag_payload()
            item["themes"] = payload.get("themes") or []
            item["keywords"] = payload.get("keywords") or []
            item["theme_text"] = payload.get("theme_text") or ""
            item["keyword_text"] = payload.get("keyword_text") or ""

    total = stats["total"] or 1
    return {
        "as_of_date": as_of_str,
        "universe_source": universe_source,
        "market": mkt,
        "gap": gap_mode,
        "latest_score_biz_date": latest_score_date.isoformat() if latest_score_date else None,
        "latest_article_biz_date": latest_article_biz.isoformat() if latest_article_biz else None,
        "summary": {
            **stats,
            "coverage_theme_pct": round(stats["theme_mapped"] * 100.0 / total, 1),
            "coverage_keyword_pct": round(stats["keyword_mapped"] * 100.0 / total, 1),
            "coverage_both_pct": round(stats["both_mapped"] * 100.0 / total, 1),
            "coverage_news_scanned_pct": round(stats["news_scanned"] * 100.0 / total, 1),
            "coverage_news_article_pct": round(stats["news_with_article"] * 100.0 / total, 1),
        },
        "filtered_total": total_filtered,
        "limit": limit,
        "offset": offset,
        "items": page,
    }


def add_manual_stock_mapping(
    session: Session,
    *,
    stock_code: str,
    tag_name: str,
    tag_type: str = "theme",
    stock_name: Optional[str] = None,
) -> Dict:
    """종목에 테마/키워드를 수동 연결 (source=manual, 네이버 배치에서 삭제되지 않음)."""
    code = _norm_code(stock_code)
    if not code or len(code) != 6:
        return {"ok": False, "error": "종목코드 6자리가 필요합니다."}

    tag_label = (tag_name or "").strip()
    if not tag_label:
        return {"ok": False, "error": "테마/키워드 이름이 필요합니다."}

    raw_type = (tag_type or "theme").strip().lower()
    if raw_type in ("keyword", "news_keyword", "kw"):
        db_tag_type = "news_keyword"
    elif raw_type == "theme":
        db_tag_type = "theme"
    else:
        return {"ok": False, "error": "tag_type은 theme 또는 keyword 여야 합니다."}

    name = (stock_name or "").strip()
    if not name:
        from utils.fundamental_mart_store import get_latest_by_code

        row = get_latest_by_code(code)
        name = (row or {}).get("stock_name") or code

    tag = (
        session.query(ThemeTag)
        .filter(ThemeTag.tag_type == db_tag_type)
        .filter(func.lower(ThemeTag.name_ko) == tag_label.lower())
        .order_by(ThemeTag.updated_at.desc())
        .first()
    )
    if not tag:
        tag = _upsert_tag(
            session,
            tag_key=f"manual_{db_tag_type}_{_slug(tag_label)}",
            name_ko=tag_label,
            tag_type=db_tag_type,
            source="manual",
        )

    now = utc_now_naive()
    biz = kst_today()
    edge = (
        session.query(ThemeTagEdge)
        .filter(
            ThemeTagEdge.stock_code.in_([code, code.lstrip("0") or "0"]),
            ThemeTagEdge.tag_id == tag.id,
            ThemeTagEdge.source == "manual",
        )
        .order_by(ThemeTagEdge.observed_at.desc())
        .first()
    )
    if edge:
        edge.stock_code = code
        edge.stock_name = name
        edge.observed_at = now
        edge.biz_date = biz
        edge.reason_text = "수동 매핑 (갱신)"
    else:
        session.add(
            ThemeTagEdge(
                stock_code=code,
                stock_name=name,
                tag_id=tag.id,
                source="manual",
                role="member",
                weight=1.0,
                biz_date=biz,
                inclusion_flag=True,
                reason_text="수동 매핑",
                observed_at=now,
            )
        )
    session.commit()
    return {
        "ok": True,
        "stock_code": code,
        "stock_name": name,
        "tag_id": tag.id,
        "tag_name": tag.name_ko,
        "tag_type": tag.tag_type,
        "source": "manual",
    }


def _empty_tag_payload() -> Dict:
    return {
        "themes": [],
        "theme_items": [],
        "keywords": [],
        "theme_text": "",
        "keyword_text": "",
        "tag_freshness": None,
    }


def _pick_top_names(
    rows: List[tuple],
    *,
    limit: int,
) -> List[str]:
    """(name, weight, source, observed_at) → 중복 제거 후 top-N 이름."""
    best: Dict[str, tuple] = {}
    for name, weight, source, observed_at in rows:
        key = (name or "").strip()
        if not key:
            continue
        rank = _SOURCE_RANK.get(str(source or ""), 9)
        w = float(weight if weight is not None else 0.0)
        prev = best.get(key.lower())
        score = (w, -rank, observed_at or datetime.min)
        if prev is None or score > prev[0]:
            best[key.lower()] = (score, key)
    ordered = sorted(best.values(), key=lambda x: x[0], reverse=True)
    return [name for _, name in ordered[: max(0, limit)]]


def _query_keywords_from_edges(
    sess: Session,
    codes: List[str],
    query_codes: List[str],
    keyword_limit: int,
) -> Dict[str, Dict]:
    rows = (
        sess.query(ThemeTagEdge, ThemeTag)
        .join(ThemeTag, ThemeTag.id == ThemeTagEdge.tag_id)
        .filter(ThemeTagEdge.stock_code.in_(query_codes))
        .filter(ThemeTag.tag_type.in_(list(_KEYWORD_TYPES)))
        .order_by(ThemeTagEdge.observed_at.desc())
        .all()
    )
    keyword_rows: Dict[str, List[tuple]] = defaultdict(list)
    freshness_keyword: Dict[str, datetime] = {}
    for edge, tag in rows:
        code = _norm_code(edge.stock_code)
        item = (tag.name_ko, edge.weight, edge.source, edge.observed_at)
        if edge.observed_at and (
            code not in freshness_keyword or edge.observed_at > freshness_keyword[code]
        ):
            freshness_keyword[code] = edge.observed_at
        keyword_rows[code].append(item)

    out: Dict[str, Dict] = {}
    for code in codes:
        obs_kw = freshness_keyword.get(code)
        keywords_src = (
            [it for it in keyword_rows.get(code, []) if it[3] == obs_kw] if obs_kw else []
        )
        keywords = _pick_top_names(keywords_src, limit=keyword_limit)
        out[code] = {
            "keywords": keywords,
            "keyword_text": " · ".join(keywords),
            "tag_freshness": obs_kw.isoformat() if obs_kw else None,
        }
    return out


def _query_themes_from_edges(
    sess: Session,
    codes: List[str],
    query_codes: List[str],
    theme_limit: int,
) -> Dict[str, Dict]:
    rows = (
        sess.query(ThemeTagEdge, ThemeTag)
        .join(ThemeTag, ThemeTag.id == ThemeTagEdge.tag_id)
        .filter(ThemeTagEdge.stock_code.in_(query_codes))
        .filter(ThemeTag.tag_type.in_(list(_THEME_TYPES)))
        .order_by(ThemeTagEdge.observed_at.desc())
        .all()
    )
    theme_rows: Dict[str, List[tuple]] = defaultdict(list)
    freshness_theme: Dict[str, datetime] = {}
    for edge, tag in rows:
        code = _norm_code(edge.stock_code)
        item = (tag.name_ko, edge.weight, edge.source, edge.observed_at)
        if edge.observed_at and (
            code not in freshness_theme or edge.observed_at > freshness_theme[code]
        ):
            freshness_theme[code] = edge.observed_at
        theme_rows[code].append(item)

    out: Dict[str, Dict] = {}
    for code in codes:
        obs_theme = freshness_theme.get(code)
        themes_src = (
            [it for it in theme_rows.get(code, []) if it[3] == obs_theme] if obs_theme else []
        )
        themes = _pick_top_names(themes_src, limit=theme_limit)
        out[code] = {
            "themes": themes,
            "theme_items": [{"name": n, "score": None, "tier": "legacy"} for n in themes],
            "theme_text": ", ".join(themes),
            "tag_freshness": obs_theme.isoformat() if obs_theme else None,
        }
    return out


def get_latest_map_by_codes(
    stock_codes: List[str],
    *,
    theme_limit: int = 3,
    keyword_limit: int = 3,
    session: Session | None = None,
) -> Dict[str, Dict]:
    """후보 종목코드 → 테마/키워드 enrichment 맵 (fundamental get_latest_map_by_codes 동형).

    반환 예:
      {
        "005930": {
          "themes": ["반도체", "AI반도체"],
          "keywords": ["HBM"],
          "theme_text": "반도체, AI반도체",
          "keyword_text": "HBM",
          "tag_freshness": "2026-07-08T06:30:00",
        }
      }
    """
    codes: List[str] = []
    for c in stock_codes:
        s = str(c or "").replace("A", "").strip()
        if not s:
            continue
        codes.append(_norm_code(s))
    codes = list(dict.fromkeys(codes))
    if not codes:
        return {}

    # DB에 zero-pad되어 있거나 비어 있을 수 있어 둘 다 조회
    query_codes = list(dict.fromkeys(codes + [c.lstrip("0") or "0" for c in codes]))

    def _query(sess: Session) -> Dict[str, Dict]:
        out: Dict[str, Dict] = {}
        latest_score_date = sess.query(func.max(ThemeScoreDaily.biz_date)).scalar()

        if latest_score_date:
            score_rows = (
                sess.query(ThemeScoreDaily, ThemeTag)
                .join(ThemeTag, ThemeTag.id == ThemeScoreDaily.tag_id)
                .filter(ThemeScoreDaily.biz_date == latest_score_date)
                .filter(ThemeScoreDaily.stock_code.in_(query_codes))
                .filter(ThemeTag.tag_type == "theme")
                .order_by(ThemeScoreDaily.final_score.desc())
                .all()
            )
            by_code: Dict[str, List[Dict]] = defaultdict(list)
            for score, tag in score_rows:
                code = _norm_code(score.stock_code)
                by_code[code].append({
                    "name": tag.name_ko,
                    "score": round(float(score.final_score or 0), 3),
                    "tier": score.tier or "none",
                    "static_score": round(float(score.static_score or 0), 3),
                    "news_score": round(float(score.news_score or 0), 3),
                    "market_score": round(float(score.market_score or 0), 3),
                })
            for code in codes:
                items = by_code.get(code, [])[: max(0, theme_limit)]
                if not items:
                    continue
                names = [it["name"] for it in items]
                out[code] = {
                    "themes": names,
                    "theme_items": items,
                    "keywords": [],
                    "theme_text": ", ".join(names),
                    "keyword_text": "",
                    "tag_freshness": latest_score_date.isoformat(),
                }

        missing = [c for c in codes if c not in out]
        if missing:
            edge_themes = _query_themes_from_edges(sess, missing, query_codes, theme_limit)
            for code, payload in edge_themes.items():
                if payload.get("themes"):
                    out[code] = {
                        **payload,
                        "keywords": [],
                        "keyword_text": "",
                    }

        if not out:
            out = _query_themes_from_edges(sess, codes, query_codes, theme_limit)
            for code in out:
                out[code]["keywords"] = []
                out[code]["keyword_text"] = ""

        kw_part = _query_keywords_from_edges(sess, codes, query_codes, keyword_limit)
        for code in codes:
            if code not in out:
                out[code] = _empty_tag_payload()
            kw = kw_part.get(code) or {}
            if kw.get("keywords"):
                out[code]["keywords"] = kw["keywords"]
                out[code]["keyword_text"] = kw.get("keyword_text") or ""
            if not out[code].get("tag_freshness") and kw.get("tag_freshness"):
                out[code]["tag_freshness"] = kw["tag_freshness"]
        return out

    if session is not None:
        return _query(session)

    from core.models import get_db

    for db in get_db():
        return _query(db)
    return {c: _empty_tag_payload() for c in codes}
