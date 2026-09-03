"""테마/키워드 ↔ 종목 매핑 스토어 (스파이크용)."""
from __future__ import annotations

import io
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
from utils.theme_alphasquare_crawler import crawl_alphasquare_theme_snapshot_sync
from utils.theme_kiwoom_crawler import crawl_kiwoom_theme_snapshot_sync
from utils.theme_naver_crawler import crawl_theme_list, crawl_theme_stocks

THEME_EDGE_SOURCES = ("naver_theme", "kiwoom_theme", "alphasquare_theme")
SOURCE_KIWOOM_THEME = "kiwoom_theme"
SOURCE_NAVER_THEME = "naver_theme"
SOURCE_ALPHASQUARE_THEME = "alphasquare_theme"

_SOURCE_LABELS = {
    "naver_theme": "네이버",
    "kiwoom_theme": "키움",
    "alphasquare_theme": "알파스퀘어",
    "news_title": "뉴스",
    "news_keyword": "뉴스",
    "manual": "수동",
}


def source_label(source: Optional[str]) -> str:
    s = str(source or "").strip()
    return _SOURCE_LABELS.get(s, s or "기타")


def source_short(source: Optional[str]) -> str:
    s = str(source or "").strip()
    return {
        "naver_theme": "N",
        "kiwoom_theme": "K",
        "alphasquare_theme": "AS",
        "news_title": "뉴스",
        "news_keyword": "뉴스",
        "manual": "수동",
    }.get(s, (s[:4] if s else "?"))



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


def _store_kiwoom_theme_edges(
    session: Session,
    *,
    biz: date,
    now: datetime,
    top_n: int = 0,
) -> Dict:
    """키움 테마 스냅샷 수집 후 source=kiwoom_theme 엣지 저장 (네이버와 별도 태그키)."""
    snap = crawl_kiwoom_theme_snapshot_sync(limit=top_n)
    if not snap.get("ok"):
        return {
            "ok": False,
            "error": snap.get("error") or "키움 테마 수집 실패",
            "themes": 0,
            "edges": 0,
            "api_calls": int(snap.get("api_calls") or 0),
        }

    session.query(ThemeTagEdge).filter(
        ThemeTagEdge.source == SOURCE_KIWOOM_THEME,
        ThemeTagEdge.biz_date == biz,
    ).delete(synchronize_session=False)

    inserted = 0
    themes = snap.get("themes") or []
    for t in themes:
        theme_code = str(t.get("theme_code") or "").strip()
        theme_name = str(t.get("theme_name") or "").strip()
        if not theme_code or not theme_name:
            continue
        tag = _upsert_tag(
            session,
            tag_key=f"kiwoom_theme_{theme_code}_{_slug(theme_name)}",
            name_ko=theme_name,
            tag_type="theme",
            source=SOURCE_KIWOOM_THEME,
            meta_json={
                "kiwoom_theme_code": theme_code,
                "change_rate": t.get("change_rate"),
                "period_return": t.get("period_return"),
                "stock_count": t.get("stock_count"),
                "main_stocks": t.get("main_stocks"),
                "valid_from": biz.isoformat(),
            },
        )
        stocks = t.get("stocks") or []
        for idx, stock in enumerate(stocks):
            code = str(stock.get("stock_code") or "").strip().zfill(6)
            if not code or len(code) != 6:
                continue
            session.add(
                ThemeTagEdge(
                    stock_code=code,
                    stock_name=stock.get("stock_name") or "",
                    tag_id=tag.id,
                    source=SOURCE_KIWOOM_THEME,
                    role="leader" if idx == 0 else "member",
                    weight=1.0,
                    biz_date=biz,
                    rank=idx + 1,
                    inclusion_flag=True,
                    reason_text=f"키움 테마 '{theme_name}' 편입",
                    observed_at=now,
                    meta_json={
                        "kiwoom_theme_code": theme_code,
                        "change_rate": stock.get("change_rate"),
                        "period_return": stock.get("period_return"),
                    },
                )
            )
            inserted += 1

    return {
        "ok": True,
        "themes": len(themes),
        "edges": inserted,
        "api_calls": int(snap.get("api_calls") or 0),
        "error_count": int(snap.get("error_count") or 0),
        "errors": list(snap.get("errors") or [])[:10],
    }


def refresh_kiwoom_theme_mapping_snapshot(
    session: Session,
    *,
    top_n: int = 0,
    recompute_scores: bool = True,
) -> Dict:
    """키움 테마만 수집 (네이버 스냅샷은 유지)."""
    biz = kst_today()
    now = utc_now_naive()
    kiwoom_result = _store_kiwoom_theme_edges(
        session,
        biz=biz,
        now=now,
        top_n=top_n,
    )
    if not kiwoom_result.get("ok"):
        return {
            "ok": False,
            "error": kiwoom_result.get("error") or "키움 테마 수집 실패",
            "themes": 0,
            "edges": 0,
            "keywords": 0,
            "biz_date": biz.isoformat(),
            "scores": {"ok": False, "skipped": True},
            "kiwoom": kiwoom_result,
            "kiwoom_ok": False,
            "mode": "kiwoom_only",
        }

    session.commit()
    score_result: Dict = {"ok": True, "skipped": True}
    if recompute_scores:
        score_result = compute_theme_scores_for_date(session, biz_date=biz)

    return {
        "ok": True,
        "themes": 0,
        "edges": 0,
        "keywords": 0,
        "biz_date": biz.isoformat(),
        "scores": score_result,
        "kiwoom": kiwoom_result,
        "kiwoom_ok": True,
        "mode": "kiwoom_only",
    }


def _store_alphasquare_theme_edges(
    session: Session,
    *,
    biz: date,
    now: datetime,
    top_n: int = 0,
    fetch_reasons: Optional[bool] = None,
) -> Dict:
    """알파스퀘어 테마 스냅샷 → source=alphasquare_theme 엣지 저장."""
    snap = crawl_alphasquare_theme_snapshot_sync(
        limit=top_n,
        fetch_reasons=fetch_reasons,
    )
    if not snap.get("ok"):
        return {
            "ok": False,
            "error": snap.get("error") or "알파스퀘어 테마 수집 실패",
            "themes": 0,
            "edges": 0,
            "api_calls": int(snap.get("api_calls") or 0),
            "skipped": bool(snap.get("skipped")),
        }

    session.query(ThemeTagEdge).filter(
        ThemeTagEdge.source == SOURCE_ALPHASQUARE_THEME,
        ThemeTagEdge.biz_date == biz,
    ).delete(synchronize_session=False)

    inserted = 0
    themes = snap.get("themes") or []
    for t in themes:
        theme_id = t.get("theme_id")
        theme_name = str(t.get("theme_name") or "").strip()
        if theme_id is None or not theme_name:
            continue
        tid = int(theme_id)
        tag = _upsert_tag(
            session,
            tag_key=f"alphasquare_theme_{tid}_{_slug(theme_name)}",
            name_ko=theme_name,
            tag_type="theme",
            source=SOURCE_ALPHASQUARE_THEME,
            meta_json={
                "alphasquare_theme_id": tid,
                "description": t.get("description") or "",
                "key_point": t.get("key_point"),
                "stock_count": t.get("stock_count"),
                "big_theme_id": t.get("big_theme_id"),
                "category_name": t.get("category_name"),
                "collected_via": "internal_api",
                "valid_from": biz.isoformat(),
            },
        )
        stocks = t.get("stocks") or []
        for idx, stock in enumerate(stocks):
            code = str(stock.get("stock_code") or "").strip().zfill(6)
            if not code or len(code) != 6:
                continue
            reason = str(stock.get("reason") or "").strip()
            session.add(
                ThemeTagEdge(
                    stock_code=code,
                    stock_name=stock.get("stock_name") or "",
                    tag_id=tag.id,
                    source=SOURCE_ALPHASQUARE_THEME,
                    role="leader" if idx == 0 else "member",
                    weight=1.0,
                    biz_date=biz,
                    rank=idx + 1,
                    inclusion_flag=True,
                    reason_text=reason or f"알파스퀘어 테마 '{theme_name}' 편입",
                    observed_at=now,
                    meta_json={
                        "alphasquare_theme_id": tid,
                        "alphasquare_stock_id": stock.get("alphasquare_stock_id"),
                        "reason": reason or None,
                        "market": stock.get("market"),
                    },
                )
            )
            inserted += 1

    return {
        "ok": True,
        "themes": len(themes),
        "edges": inserted,
        "api_calls": int(snap.get("api_calls") or 0),
        "error_count": int(snap.get("error_count") or 0),
        "errors": list(snap.get("errors") or [])[:10],
        "fetch_reasons": bool(snap.get("fetch_reasons")),
        "reason_count": int(snap.get("reason_count") or 0),
    }


def refresh_alphasquare_theme_mapping_snapshot(
    session: Session,
    *,
    top_n: int = 0,
    recompute_scores: bool = True,
    fetch_reasons: Optional[bool] = None,
) -> Dict:
    """알파스퀘어 테마만 수집 (네이버·키움 스냅샷은 유지)."""
    biz = kst_today()
    now = utc_now_naive()
    as_result = _store_alphasquare_theme_edges(
        session,
        biz=biz,
        now=now,
        top_n=top_n,
        fetch_reasons=fetch_reasons,
    )
    if not as_result.get("ok"):
        return {
            "ok": False,
            "error": as_result.get("error") or "알파스퀘어 테마 수집 실패",
            "themes": 0,
            "edges": 0,
            "keywords": 0,
            "biz_date": biz.isoformat(),
            "scores": {"ok": False, "skipped": True},
            "alphasquare": as_result,
            "alphasquare_ok": False,
            "mode": "alphasquare_only",
        }

    session.commit()
    score_result: Dict = {"ok": True, "skipped": True}
    if recompute_scores:
        score_result = compute_theme_scores_for_date(session, biz_date=biz)

    return {
        "ok": True,
        "themes": int(as_result.get("themes") or 0),
        "edges": int(as_result.get("edges") or 0),
        "keywords": 0,
        "biz_date": biz.isoformat(),
        "scores": score_result,
        "alphasquare": as_result,
        "alphasquare_ok": True,
        "mode": "alphasquare_only",
    }


def refresh_theme_mapping_snapshot(
    session: Session,
    *,
    top_n: int = 0,
    include_news_keywords: bool = True,
    news_stock_limit_per_theme: int = 2,
    include_kiwoom: bool = True,
    include_alphasquare: bool = True,
    fetch_reasons: Optional[bool] = None,
) -> Dict:
    now = utc_now_naive()
    biz = kst_today()

    themes = crawl_theme_list(limit=top_n)
    if not themes:
        return {"ok": False, "error": "테마 목록이 비어 있습니다."}

    # 당일 정적 편입 스냅샷 교체
    session.query(ThemeTagEdge).filter(
        ThemeTagEdge.source == SOURCE_NAVER_THEME,
        ThemeTagEdge.biz_date == biz,
    ).delete(synchronize_session=False)

    inserted_edges = 0
    theme_names: List[str] = []
    keyword_stock_sets: Dict[str, set] = defaultdict(set)
    keyword_counter = defaultdict(int)
    for t in themes:
        theme_name = t["theme_name"]
        theme_no = t["theme_no"]
        theme_names.append(theme_name)
        tag = _upsert_tag(
            session,
            tag_key=f"theme_{theme_no}_{_slug(theme_name)}",
            name_ko=theme_name,
            tag_type="theme",
            source=SOURCE_NAVER_THEME,
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
                source=SOURCE_NAVER_THEME,
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

    # 네이버 커밋 후 키움·알파스퀘어 수집 — 장후 배치에서 네이버 다음 단계
    session.commit()

    kiwoom_result: Dict = {"ok": False, "skipped": True, "themes": 0, "edges": 0, "api_calls": 0}
    if include_kiwoom:
        try:
            # 동일 스냅샷 시각으로 dual 표시(당일 biz_date) 정합
            kiwoom_now = utc_now_naive()
            kiwoom_result = _store_kiwoom_theme_edges(
                session,
                biz=biz,
                now=kiwoom_now,
                top_n=top_n,
            )
            session.commit()
        except Exception as e:
            session.rollback()
            kiwoom_result = {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "themes": 0,
                "edges": 0,
                "api_calls": 0,
            }

    alphasquare_result: Dict = {
        "ok": False,
        "skipped": True,
        "themes": 0,
        "edges": 0,
        "api_calls": 0,
    }
    if include_alphasquare:
        if not Config.ALPHASQUARE_ENABLED:
            alphasquare_result = {
                "ok": False,
                "skipped": True,
                "themes": 0,
                "edges": 0,
                "api_calls": 0,
                "error": "ALPHASQUARE_ENABLED=false",
            }
        else:
            try:
                as_now = utc_now_naive()
                alphasquare_result = _store_alphasquare_theme_edges(
                    session,
                    biz=biz,
                    now=as_now,
                    top_n=top_n,
                    fetch_reasons=fetch_reasons,
                )
                session.commit()
            except Exception as e:
                session.rollback()
                alphasquare_result = {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "themes": 0,
                    "edges": 0,
                    "api_calls": 0,
                }

    score_result = compute_theme_scores_for_date(session, biz_date=biz)

    return {
        "ok": True,
        "themes": len(themes),
        "edges": inserted_edges,
        "keywords": len(kw_rows),
        "biz_date": biz.isoformat(),
        "scores": score_result,
        "kiwoom": kiwoom_result,
        # 키움/알파스퀘어 실패해도 네이버 스냅샷은 유지 (배치 overall ok)
        "kiwoom_ok": bool(kiwoom_result.get("ok")) if include_kiwoom else None,
        "alphasquare": alphasquare_result,
        "alphasquare_ok": (
            None
            if (not include_alphasquare or alphasquare_result.get("skipped"))
            else bool(alphasquare_result.get("ok"))
        ),
    }


def get_theme_tags(session: Session, limit: int = 100, source: Optional[str] = None) -> List[Dict]:
    q = session.query(ThemeTag).filter(ThemeTag.tag_type == "theme")
    src = (source or "").strip()
    if src:
        q = q.filter(ThemeTag.source == src)
    rows = q.order_by(ThemeTag.name_ko.asc()).limit(max(1, min(limit, 500))).all()
    out = []
    for r in rows:
        cnt = session.query(func.count(ThemeTagEdge.id)).filter(ThemeTagEdge.tag_id == r.id).scalar() or 0
        meta = r.meta_json if isinstance(r.meta_json, dict) else {}
        out.append({
            "id": r.id,
            "tag_key": r.tag_key,
            "name_ko": r.name_ko,
            "source": r.source,
            "source_label": source_label(r.source),
            "source_short": source_short(r.source),
            "key_point": meta.get("key_point"),
            "description": (meta.get("description") or "")[:240] or None,
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
    # 동일 (tag_id, source) 중복만 제거 — 소스별 병행 표시
    seen = set()
    out: List[Dict] = []
    for edge, tag in q:
        key = (tag.id, str(edge.source or ""))
        if key in seen:
            continue
        seen.add(key)
        meta = edge.meta_json if isinstance(edge.meta_json, dict) else {}
        tag_meta = tag.meta_json if isinstance(tag.meta_json, dict) else {}
        reason = (edge.reason_text or "").strip() or (meta.get("reason") or None)
        out.append({
            "tag_id": tag.id,
            "tag_name": tag.name_ko,
            "tag_type": tag.tag_type,
            "source": edge.source,
            "source_label": source_label(edge.source),
            "source_short": source_short(edge.source),
            "role": edge.role,
            "reason": reason,
            "key_point": tag_meta.get("key_point"),
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
        meta = edge.meta_json if isinstance(edge.meta_json, dict) else {}
        reason = (edge.reason_text or "").strip() or (meta.get("reason") or None)
        out.append({
            "stock_code": edge.stock_code,
            "stock_name": edge.stock_name,
            "source": edge.source,
            "source_label": source_label(edge.source),
            "source_short": source_short(edge.source),
            "role": edge.role,
            "reason": reason,
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
    latest_kiwoom_at = (
        session.query(func.max(ThemeTagEdge.observed_at))
        .filter(ThemeTagEdge.source == SOURCE_KIWOOM_THEME)
        .scalar()
    )
    latest_alphasquare_at = (
        session.query(func.max(ThemeTagEdge.observed_at))
        .filter(ThemeTagEdge.source == SOURCE_ALPHASQUARE_THEME)
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
    latest_theme_biz = session.query(func.max(ThemeTagEdge.biz_date)).scalar()

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

    def _src_counts(src: str, biz: Optional[date]) -> Dict:
        if not biz:
            return {"themes": 0, "edges": 0, "stocks": 0}
        edges = int(
            session.query(func.count(ThemeTagEdge.id))
            .filter(ThemeTagEdge.source == src, ThemeTagEdge.biz_date == biz)
            .scalar()
            or 0
        )
        themes = int(
            session.query(func.count(func.distinct(ThemeTagEdge.tag_id)))
            .filter(ThemeTagEdge.source == src, ThemeTagEdge.biz_date == biz)
            .scalar()
            or 0
        )
        stocks = int(
            session.query(func.count(func.distinct(ThemeTagEdge.stock_code)))
            .filter(ThemeTagEdge.source == src, ThemeTagEdge.biz_date == biz)
            .scalar()
            or 0
        )
        return {"themes": themes, "edges": edges, "stocks": stocks}

    return {
        "theme_snapshot_last_at": latest_theme_at.isoformat() if latest_theme_at else None,
        "kiwoom_snapshot_last_at": latest_kiwoom_at.isoformat() if latest_kiwoom_at else None,
        "alphasquare_snapshot_last_at": (
            latest_alphasquare_at.isoformat() if latest_alphasquare_at else None
        ),
        "theme_snapshot_biz_date": latest_theme_biz.isoformat() if latest_theme_biz else None,
        "naver_today": _src_counts(SOURCE_NAVER_THEME, latest_theme_biz),
        "kiwoom_today": _src_counts(SOURCE_KIWOOM_THEME, latest_theme_biz),
        "alphasquare_today": _src_counts(SOURCE_ALPHASQUARE_THEME, latest_theme_biz),
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


def build_theme_source_cross_report(
    session: Session,
    *,
    biz_date: Optional[date] = None,
) -> Dict:
    """네이버·키움·알파스퀘어 테마 소스 교차 커버리지 리포트."""
    biz = biz_date or session.query(func.max(ThemeTagEdge.biz_date)).scalar()
    if not biz:
        return {
            "ok": False,
            "error": "테마 스냅샷(biz_date)이 없습니다.",
            "biz_date": None,
        }

    def _stock_theme_names(source: str) -> Dict[str, set]:
        rows = (
            session.query(ThemeTagEdge.stock_code, ThemeTag.name_ko)
            .join(ThemeTag, ThemeTag.id == ThemeTagEdge.tag_id)
            .filter(
                ThemeTagEdge.source == source,
                ThemeTagEdge.biz_date == biz,
                ThemeTag.tag_type == "theme",
            )
            .all()
        )
        out: Dict[str, set] = defaultdict(set)
        for code, name in rows:
            c = _norm_code(code)
            n = (name or "").strip()
            if c and n:
                out[c].add(n)
        return out

    naver = _stock_theme_names(SOURCE_NAVER_THEME)
    kiwoom = _stock_theme_names(SOURCE_KIWOOM_THEME)
    alphasq = _stock_theme_names(SOURCE_ALPHASQUARE_THEME)

    sn, sk, sa = set(naver), set(kiwoom), set(alphasq)
    union = sn | sk | sa

    def _name_overlap(a: Dict[str, set], b: Dict[str, set]) -> Dict[str, int]:
        both = set(a) & set(b)
        share_any = 0
        for code in both:
            if a[code] & b[code]:
                share_any += 1
        return {
            "stocks_both": len(both),
            "stocks_share_theme_name": share_any,
            "share_pct": round(100.0 * share_any / len(both), 1) if both else 0.0,
        }

    # AS가 메꾸는 네이버 미매핑
    as_fills_naver_gap = len(sa - sn)
    as_fills_any_gap = len(sa - (sn | sk))

    return {
        "ok": True,
        "biz_date": biz.isoformat(),
        "stocks": {
            "naver": len(sn),
            "kiwoom": len(sk),
            "alphasquare": len(sa),
            "union": len(union),
            "naver_and_kiwoom": len(sn & sk),
            "naver_and_alphasquare": len(sn & sa),
            "kiwoom_and_alphasquare": len(sk & sa),
            "all_three": len(sn & sk & sa),
            "alphasquare_only": len(sa - sn - sk),
            "alphasquare_fills_naver_gap": as_fills_naver_gap,
            "alphasquare_fills_nk_gap": as_fills_any_gap,
        },
        "name_overlap": {
            "naver_kiwoom": _name_overlap(naver, kiwoom),
            "naver_alphasquare": _name_overlap(naver, alphasq),
            "kiwoom_alphasquare": _name_overlap(kiwoom, alphasq),
        },
        "coverage_pct": {
            "naver_of_union": round(100.0 * len(sn) / len(union), 1) if union else 0.0,
            "kiwoom_of_union": round(100.0 * len(sk) / len(union), 1) if union else 0.0,
            "alphasquare_of_union": round(100.0 * len(sa) / len(union), 1) if union else 0.0,
        },
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
    # 같은 이름이면 키움·네이버·알파스퀘어 동등 — 서로 다른 이름이면 dual로 둘 다 노출
    "kiwoom_theme": 0,
    "naver_theme": 0,
    "alphasquare_theme": 0,
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
    commit: bool = True,
) -> Dict:
    """종목에 테마/키워드를 수동 연결 (source=manual, 네이버 배치에서 삭제되지 않음)."""
    code = _norm_code(stock_code)
    if not code or len(code) != 6 or not code.isdigit():
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
    if commit:
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


def _is_manual_mapping_header(left: str, right: str) -> bool:
    l = (left or "").strip().lower().replace(" ", "")
    r = (right or "").strip().lower().replace(" ", "")
    left_ok = l in ("종목코드", "코드", "code", "stock_code", "stockcode", "종목")
    right_ok = r in ("테마", "theme", "themes", "태그", "tag", "키워드", "keyword")
    return left_ok and right_ok


def _split_theme_labels(raw: str) -> List[str]:
    seen = set()
    out: List[str] = []
    for part in str(raw or "").replace(";", ",").split(","):
        label = part.strip()
        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(label)
    return out


def parse_manual_theme_mapping_text(text: str) -> Dict:
    """
    `종목코드 | 테마` 텍스트 파싱.

    예:
      종목코드 | 테마
      000660 | 반도체,SK,
      005935 | 반도체,우선주
    """
    rows: List[Dict] = []
    errors: List[Dict] = []
    for line_no, raw in enumerate(str(text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            left, right = line.split("|", 1)
        elif "\t" in line:
            left, right = line.split("\t", 1)
        else:
            errors.append({"line": line_no, "error": "구분자(|)가 없습니다.", "raw": line[:80]})
            continue
        left = left.strip()
        right = right.strip()
        if _is_manual_mapping_header(left, right):
            continue
        code = _norm_code(left.replace("A", "").replace("a", ""))
        if not code.isdigit() or len(code) != 6:
            errors.append({"line": line_no, "error": "종목코드 형식이 아닙니다.", "raw": line[:80]})
            continue
        themes = _split_theme_labels(right)
        if not themes:
            errors.append({"line": line_no, "error": "테마가 비어 있습니다.", "raw": line[:80]})
            continue
        rows.append({"line": line_no, "stock_code": code, "themes": themes})
    return {"ok": True, "rows": rows, "errors": errors, "row_count": len(rows)}


def parse_manual_theme_mapping_table(records: List[Dict]) -> Dict:
    """엑셀/CSV 행 목록 → 표준 rows. 키: stock_code/종목코드, themes/테마."""
    rows: List[Dict] = []
    errors: List[Dict] = []
    for idx, rec in enumerate(records or [], 1):
        if not isinstance(rec, dict):
            errors.append({"line": idx, "error": "행 형식이 올바르지 않습니다."})
            continue
        lower = {str(k).strip().lower(): v for k, v in rec.items()}
        raw_code = (
            lower.get("stock_code")
            or lower.get("종목코드")
            or lower.get("코드")
            or lower.get("code")
            or lower.get("종목")
        )
        raw_themes = (
            lower.get("themes")
            or lower.get("테마")
            or lower.get("theme")
            or lower.get("태그")
            or lower.get("tag")
            or lower.get("키워드")
            or lower.get("keyword")
        )
        if raw_code is None and raw_themes is None and len(rec) >= 2:
            vals = list(rec.values())
            raw_code, raw_themes = vals[0], vals[1]
        if raw_code is None:
            errors.append({"line": idx, "error": "종목코드 열이 없습니다."})
            continue
        code_s = str(raw_code).strip()
        if code_s.endswith(".0") and code_s.replace(".", "", 1).isdigit():
            code_s = code_s[:-2]
        if _is_manual_mapping_header(str(raw_code), str(raw_themes or "")):
            continue
        code = _norm_code(code_s.replace("A", "").replace("a", ""))
        if not code.isdigit() or len(code) != 6:
            errors.append({"line": idx, "error": "종목코드 형식이 아닙니다.", "raw": code_s[:40]})
            continue
        themes = _split_theme_labels("" if raw_themes is None else str(raw_themes))
        if not themes:
            errors.append({"line": idx, "error": "테마가 비어 있습니다.", "raw": code})
            continue
        rows.append({"line": idx, "stock_code": code, "themes": themes})
    return {"ok": True, "rows": rows, "errors": errors, "row_count": len(rows)}


def parse_manual_theme_mapping_file(filename: str, content: bytes) -> Dict:
    """엑셀(.xlsx)/CSV/텍스트 파일 → parse 결과."""
    name = (filename or "").lower()
    if not content:
        return {"ok": False, "rows": [], "errors": [{"line": 0, "error": "파일이 비어 있습니다."}], "row_count": 0}

    if name.endswith((".xlsx", ".xls")):
        import pandas as pd

        try:
            df = pd.read_excel(io.BytesIO(content), dtype=str)
        except Exception as e:
            return {
                "ok": False,
                "rows": [],
                "errors": [{"line": 0, "error": f"엑셀 읽기 실패: {e}"}],
                "row_count": 0,
            }
        df = df.fillna("")
        records = df.to_dict(orient="records")
        # 헤더가 없고 첫 열이 code|themes 한 칸인 경우
        if len(df.columns) == 1:
            col = str(df.columns[0])
            text = "\n".join(
                str(v).strip() for v in df.iloc[:, 0].tolist() if str(v).strip()
            )
            if "|" in col or any("|" in str(v) for v in df.iloc[:, 0].tolist()[:5]):
                header = col if "|" in col else "종목코드 | 테마"
                return parse_manual_theme_mapping_text(f"{header}\n{text}" if "|" not in col else f"{col}\n{text}")
        return parse_manual_theme_mapping_table(records)

    # csv / txt / 기타 → 텍스트
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return {
            "ok": False,
            "rows": [],
            "errors": [{"line": 0, "error": "파일 인코딩을 읽을 수 없습니다."}],
            "row_count": 0,
        }

    # CSV인데 | 없는 경우: 첫 두 컬럼 사용
    sample = "\n".join(text.splitlines()[:5])
    if "|" not in sample and ("\t" in sample or "," in sample):
        import csv as _csv

        try:
            dialect = _csv.Sniffer().sniff(sample, delimiters=",\t;")
        except _csv.Error:
            dialect = "excel"
        reader = _csv.DictReader(io.StringIO(text), dialect=dialect)
        if reader.fieldnames and len(reader.fieldnames) >= 2:
            return parse_manual_theme_mapping_table(list(reader))
    return parse_manual_theme_mapping_text(text)


def add_manual_stock_mappings_bulk(
    session: Session,
    *,
    rows: List[Dict],
    tag_type: str = "theme",
) -> Dict:
    """여러 종목·테마를 source=manual 로 일괄 저장."""
    added = 0
    updated = 0
    edge_errors: List[Dict] = []
    stock_codes: List[str] = []
    tag_names: List[str] = []

    for row in rows or []:
        code = str(row.get("stock_code") or "")
        themes = row.get("themes") or []
        stock_name = row.get("stock_name")
        line = row.get("line")
        if not themes:
            continue
        stock_codes.append(code)
        for theme in themes:
            before = (
                session.query(ThemeTagEdge.id)
                .join(ThemeTag, ThemeTag.id == ThemeTagEdge.tag_id)
                .filter(
                    ThemeTagEdge.stock_code.in_([code, code.lstrip("0") or "0"]),
                    ThemeTagEdge.source == "manual",
                    func.lower(ThemeTag.name_ko) == str(theme).strip().lower(),
                )
                .first()
            )
            result = add_manual_stock_mapping(
                session,
                stock_code=code,
                tag_name=str(theme),
                tag_type=tag_type,
                stock_name=stock_name,
                commit=False,
            )
            if not result.get("ok"):
                edge_errors.append(
                    {
                        "line": line,
                        "stock_code": code,
                        "theme": theme,
                        "error": result.get("error") or "저장 실패",
                    }
                )
                continue
            tag_names.append(result.get("tag_name") or theme)
            if before:
                updated += 1
            else:
                added += 1

    session.commit()
    return {
        "ok": True,
        "added": added,
        "updated": updated,
        "edge_count": added + updated,
        "stock_count": len(set(stock_codes)),
        "tag_count": len({t.lower() for t in tag_names if t}),
        "errors": edge_errors,
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
    """당일(biz_date) 기준으로 네이버·키움 소스를 함께 노출.

    observed_at 초 단위 차이로 한쪽 소스가 탈락하지 않도록 biz_date를 우선한다.
    """
    rows = (
        sess.query(ThemeTagEdge, ThemeTag)
        .join(ThemeTag, ThemeTag.id == ThemeTagEdge.tag_id)
        .filter(ThemeTagEdge.stock_code.in_(query_codes))
        .filter(ThemeTag.tag_type.in_(list(_THEME_TYPES)))
        .order_by(ThemeTagEdge.observed_at.desc())
        .all()
    )
    theme_rows: Dict[str, List[tuple]] = defaultdict(list)
    freshness_biz: Dict[str, date] = {}
    freshness_obs: Dict[str, datetime] = {}
    for edge, tag in rows:
        code = _norm_code(edge.stock_code)
        biz = edge.biz_date or (edge.observed_at.date() if edge.observed_at else None)
        item = (tag.name_ko, edge.weight, edge.source, edge.observed_at, biz)
        if biz and (code not in freshness_biz or biz > freshness_biz[code]):
            freshness_biz[code] = biz
        if edge.observed_at and (
            code not in freshness_obs or edge.observed_at > freshness_obs[code]
        ):
            freshness_obs[code] = edge.observed_at
        theme_rows[code].append(item)

    out: Dict[str, Dict] = {}
    for code in codes:
        latest_biz = freshness_biz.get(code)
        if latest_biz is not None:
            themes_src = [
                (name, w, src, obs)
                for name, w, src, obs, biz in theme_rows.get(code, [])
                if biz == latest_biz
            ]
        else:
            obs_theme = freshness_obs.get(code)
            themes_src = [
                (name, w, src, obs)
                for name, w, src, obs, _biz in theme_rows.get(code, [])
                if obs_theme is None or obs == obs_theme
            ]
        themes = _pick_top_names(themes_src, limit=theme_limit)
        fresh = freshness_obs.get(code)
        out[code] = {
            "themes": themes,
            "theme_items": [{"name": n, "score": None, "tier": "legacy"} for n in themes],
            "theme_text": ", ".join(themes),
            "tag_freshness": (
                latest_biz.isoformat() if latest_biz else (fresh.isoformat() if fresh else None)
            ),
        }
    return out


def get_trade_flow_theme_map(
    session: Session,
    stock_codes: List[str],
    *,
    theme_limit: int = 0,
) -> Dict[str, Dict]:
    """테마지도 전용 종목→전체 테마 맵.

    점수 기반 대표테마를 거치지 않고 가장 최근 스냅샷 날짜의 모든 테마
    소스 엣지를 합친다. 같은 이름은 소스가 달라도 한 번만 반환한다.
    ``theme_limit=0``이면 종목별 테마 수를 제한하지 않는다.
    """
    codes = list(dict.fromkeys(_norm_code(c) for c in stock_codes if str(c or "").strip()))
    if not codes:
        return {}
    query_codes = list(dict.fromkeys(codes + [c.lstrip("0") or "0" for c in codes]))

    latest_biz = (
        session.query(func.max(ThemeTagEdge.biz_date))
        .filter(
            ThemeTagEdge.stock_code.in_(query_codes),
            ThemeTagEdge.source.in_(THEME_EDGE_SOURCES),
        )
        .scalar()
    )
    rows = (
        session.query(ThemeTagEdge, ThemeTag)
        .join(ThemeTag, ThemeTag.id == ThemeTagEdge.tag_id)
        .filter(
            ThemeTagEdge.stock_code.in_(query_codes),
            ThemeTag.tag_type == "theme",
            ThemeTagEdge.inclusion_flag.is_(True),
            ThemeTagEdge.source.in_(THEME_EDGE_SOURCES),
        )
    )
    if latest_biz is not None:
        rows = rows.filter(ThemeTagEdge.biz_date == latest_biz)
    rows = rows.order_by(
        ThemeTagEdge.stock_code.asc(),
        ThemeTagEdge.rank.asc(),
        ThemeTag.name_ko.asc(),
    ).all()

    names_by_code: Dict[str, List[str]] = defaultdict(list)
    seen_by_code: Dict[str, set] = defaultdict(set)
    for edge, tag in rows:
        code = _norm_code(edge.stock_code)
        name = str(tag.name_ko or "").strip()
        key = name.casefold()
        if not name or key in seen_by_code[code]:
            continue
        seen_by_code[code].add(key)
        names_by_code[code].append(name)

    limit = max(0, int(theme_limit or 0))
    out: Dict[str, Dict] = {}
    for code in codes:
        names = names_by_code.get(code) or []
        if limit:
            names = names[:limit]
        out[code] = {
            "themes": names,
            "theme_items": [{"name": name, "score": None, "tier": "all_sources"} for name in names],
            "keywords": [],
            "theme_text": ", ".join(names),
            "keyword_text": "",
            "tag_freshness": latest_biz.isoformat() if latest_biz else None,
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
