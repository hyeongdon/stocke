"""종목×테마 연관도 점수 — 정적·뉴스·시장동조 가중합."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import yaml
from sqlalchemy.orm import Session

from core.models import (
    KeywordDailyStat,
    TagArticle,
    TagArticleKeywordEdge,
    ThemeEvidence,
    ThemeScoreDaily,
    ThemeTag,
    ThemeTagEdge,
)
from utils.datetime_kst import kst_today, utc_now_naive

# final = w_static*Static + w_news*News + w_market*CoMove + w_supply*Supply
W_STATIC = 0.40
W_NEWS = 0.35
W_MARKET = 0.15
W_SUPPLY = 0.10

TIER_CORE = 0.80
TIER_RELATED = 0.50
TIER_EVENT = 0.20

_SYNONYMS_PATH = Path(__file__).resolve().parent.parent / "config" / "keyword_rules" / "synonyms.yml"


def _norm_code(code: str) -> str:
    return str(code or "").replace("A", "").strip().zfill(6)


def _load_synonyms() -> Dict[str, str]:
    if not _SYNONYMS_PATH.is_file():
        return {}
    try:
        raw = yaml.safe_load(_SYNONYMS_PATH.read_text(encoding="utf-8")) or {}
        return {str(k).strip().lower(): str(v).strip() for k, v in raw.items() if k and v}
    except Exception:
        return {}


def _theme_keyword_set(theme_name: str, synonyms: Dict[str, str]) -> Set[str]:
    """테마명 + 동의어 사전에서 canonical이 테마 토큰과 겹치는 키워드."""
    tokens: Set[str] = set()
    name = (theme_name or "").strip()
    if not name:
        return tokens
    tokens.add(name.lower())
    for part in re.split(r"[/·,\s]+", name):
        p = part.strip().lower()
        if len(p) >= 2:
            tokens.add(p)
    rev: Dict[str, Set[str]] = defaultdict(set)
    for alias, canonical in synonyms.items():
        rev[canonical.lower()].add(alias.lower())
    for t in list(tokens):
        for alias in rev.get(t, ()):
            tokens.add(alias)
    for alias, canonical in synonyms.items():
        if canonical.lower() in tokens:
            tokens.add(alias.lower())
    return tokens


def _score_tier(final: float) -> str:
    if final >= TIER_CORE:
        return "core"
    if final >= TIER_RELATED:
        return "related"
    if final >= TIER_EVENT:
        return "event"
    return "none"


def _stock_news_keywords(
    session: Session,
    stock_code: str,
    biz_date: date,
) -> Dict[str, float]:
    """종목 당일 뉴스 키워드 → 가중치."""
    code = _norm_code(stock_code)
    out: Dict[str, float] = defaultdict(float)

    edges = (
        session.query(ThemeTagEdge, ThemeTag)
        .join(ThemeTag, ThemeTag.id == ThemeTagEdge.tag_id)
        .filter(ThemeTagEdge.stock_code.in_([code, code.lstrip("0") or "0"]))
        .filter(ThemeTagEdge.source == "news_title")
        .filter(ThemeTag.tag_type.in_(["news_keyword", "keyword"]))
        .all()
    )
    for edge, tag in edges:
        meta = edge.meta_json or {}
        edge_date = meta.get("biz_date")
        if edge_date and str(edge_date) != biz_date.isoformat():
            if edge.observed_at and edge.observed_at.date() != biz_date:
                continue
        kw = (tag.name_ko or "").strip().lower()
        if kw:
            out[kw] += float(edge.weight or 1.0)

    article_rows = (
        session.query(TagArticleKeywordEdge, ThemeTag)
        .join(ThemeTag, ThemeTag.id == TagArticleKeywordEdge.tag_id)
        .join(TagArticle, TagArticle.id == TagArticleKeywordEdge.article_id)
        .filter(TagArticle.stock_code.in_([code, code.lstrip("0") or "0"]))
        .filter(TagArticle.biz_date == biz_date)
        .filter(TagArticle.source == "naver_news")
        .all()
    )
    for edge, tag in article_rows:
        kw = (tag.name_ko or "").strip().lower()
        if kw:
            out[kw] += float(edge.weight or 1.0)

    return dict(out)


def _news_rel_score(theme_keywords: Set[str], stock_kw: Dict[str, float]) -> float:
    if not theme_keywords or not stock_kw:
        return 0.0
    hit = 0.0
    total = sum(stock_kw.values()) or 1.0
    for kw, w in stock_kw.items():
        canon = kw
        if kw in theme_keywords:
            hit += w
            continue
        for tk in theme_keywords:
            if tk in kw or kw in tk:
                hit += w * 0.8
                break
    return min(1.0, hit / total)


def _market_cohesion_score(
    session: Session,
    stock_code: str,
    tag_id: int,
    biz_date: date,
    stock_kw: Dict[str, float],
) -> float:
    """동일 테마 편입 종목군과 키워드 동조화 (v1)."""
    peers = (
        session.query(ThemeTagEdge.stock_code)
        .filter(
            ThemeTagEdge.tag_id == tag_id,
            ThemeTagEdge.source == "naver_theme",
            ThemeTagEdge.inclusion_flag.is_(True),
        )
        .filter(
            (ThemeTagEdge.biz_date == biz_date)
            | (ThemeTagEdge.biz_date.is_(None))
        )
        .all()
    )
    peer_codes = [_norm_code(p[0]) for p in peers if p[0]]
    if len(peer_codes) < 2:
        return 0.0

    overlap_counts: List[float] = []
    my_keys = set(stock_kw.keys())
    if not my_keys:
        return 0.0
    for peer in peer_codes:
        if peer == _norm_code(stock_code):
            continue
        pk = set(_stock_news_keywords(session, peer, biz_date).keys())
        if not pk:
            continue
        overlap = len(my_keys & pk) / max(len(my_keys | pk), 1)
        overlap_counts.append(overlap)
    if not overlap_counts:
        theme_tag = session.query(ThemeTag).filter(ThemeTag.id == tag_id).first()
        if theme_tag:
            stat = (
                session.query(KeywordDailyStat)
                .filter(
                    KeywordDailyStat.biz_date == biz_date,
                    KeywordDailyStat.keyword == theme_tag.name_ko,
                )
                .first()
            )
            if stat and int(stat.mention_count or 0) > 0:
                return min(1.0, int(stat.mention_count) / 50.0)
        return 0.0
    return min(1.0, sum(overlap_counts) / len(overlap_counts))


def _static_map_score(
    session: Session,
    stock_code: str,
    tag_id: int,
    biz_date: date,
) -> Tuple[float, Optional[int]]:
    code = _norm_code(stock_code)
    edge = (
        session.query(ThemeTagEdge)
        .filter(
            ThemeTagEdge.stock_code.in_([code, code.lstrip("0") or "0"]),
            ThemeTagEdge.tag_id == tag_id,
            ThemeTagEdge.source == "naver_theme",
        )
        .filter(
            (ThemeTagEdge.biz_date == biz_date)
            | (ThemeTagEdge.biz_date.is_(None))
        )
        .order_by(ThemeTagEdge.observed_at.desc())
        .first()
    )
    if not edge:
        return 0.0, None
    if edge.inclusion_flag is False:
        return 0.0, edge.id
    return 1.0, edge.id


def _upsert_evidence(
    session: Session,
    *,
    biz_date: date,
    stock_code: str,
    tag_id: int,
    evidence_type: str,
    score: float,
    raw_ref_type: Optional[str] = None,
    raw_ref_id: Optional[int] = None,
    meta: Optional[dict] = None,
) -> None:
    row = (
        session.query(ThemeEvidence)
        .filter(
            ThemeEvidence.biz_date == biz_date,
            ThemeEvidence.stock_code == _norm_code(stock_code),
            ThemeEvidence.tag_id == tag_id,
            ThemeEvidence.evidence_type == evidence_type,
        )
        .first()
    )
    if row:
        row.evidence_score = score
        row.raw_ref_type = raw_ref_type
        row.raw_ref_id = raw_ref_id
        row.meta_json = meta
    else:
        session.add(
            ThemeEvidence(
                biz_date=biz_date,
                stock_code=_norm_code(stock_code),
                tag_id=tag_id,
                evidence_type=evidence_type,
                evidence_score=score,
                raw_ref_type=raw_ref_type,
                raw_ref_id=raw_ref_id,
                meta_json=meta,
                created_at=utc_now_naive(),
            )
        )


def _upsert_score(
    session: Session,
    *,
    biz_date: date,
    stock_code: str,
    tag_id: int,
    static_score: float,
    news_score: float,
    market_score: float,
    supply_score: float,
    final_score: float,
    tier: str,
) -> None:
    code = _norm_code(stock_code)
    row = (
        session.query(ThemeScoreDaily)
        .filter(
            ThemeScoreDaily.biz_date == biz_date,
            ThemeScoreDaily.stock_code == code,
            ThemeScoreDaily.tag_id == tag_id,
        )
        .first()
    )
    now = utc_now_naive()
    if row:
        row.static_score = static_score
        row.news_score = news_score
        row.market_score = market_score
        row.supply_score = supply_score
        row.final_score = final_score
        row.tier = tier
        row.updated_at = now
    else:
        session.add(
            ThemeScoreDaily(
                biz_date=biz_date,
                stock_code=code,
                tag_id=tag_id,
                static_score=static_score,
                news_score=news_score,
                market_score=market_score,
                supply_score=supply_score,
                final_score=final_score,
                tier=tier,
                updated_at=now,
            )
        )


def compute_theme_scores_for_date(
    session: Session,
    biz_date: Optional[date] = None,
    *,
    stock_codes: Optional[Iterable[str]] = None,
    tag_ids: Optional[Iterable[int]] = None,
) -> Dict:
    """당일(또는 지정일) 종목×테마 연관도 재계산."""
    biz_date = biz_date or kst_today()
    synonyms = _load_synonyms()
    now = utc_now_naive()

    theme_q = session.query(ThemeTag).filter(ThemeTag.tag_type == "theme")
    if tag_ids:
        theme_q = theme_q.filter(ThemeTag.id.in_(list(tag_ids)))
    themes = theme_q.all()
    if not themes:
        return {"ok": False, "error": "테마 없음", "biz_date": biz_date.isoformat()}

    if stock_codes:
        codes = list(dict.fromkeys(_norm_code(c) for c in stock_codes if c))
    else:
        edge_codes = (
            session.query(ThemeTagEdge.stock_code)
            .join(ThemeTag, ThemeTag.id == ThemeTagEdge.tag_id)
            .filter(ThemeTag.tag_type == "theme")
            .filter(
                (ThemeTagEdge.biz_date == biz_date)
                | (ThemeTagEdge.biz_date.is_(None))
            )
            .distinct()
            .all()
        )
        codes = list(dict.fromkeys(_norm_code(c[0]) for c in edge_codes if c[0]))

    evidence_n = 0
    score_n = 0
    stock_kw_cache: Dict[str, Dict[str, float]] = {}

    for theme in themes:
        theme_kw = _theme_keyword_set(theme.name_ko, synonyms)
        for code in codes:
            static, edge_id = _static_map_score(session, code, theme.id, biz_date)
            if code not in stock_kw_cache:
                stock_kw_cache[code] = _stock_news_keywords(session, code, biz_date)
            stock_kw = stock_kw_cache[code]
            news = _news_rel_score(theme_kw, stock_kw)
            market = _market_cohesion_score(session, code, theme.id, biz_date, stock_kw)
            supply = 0.0

            if static <= 0 and news <= 0 and market <= 0:
                continue

            final = (
                W_STATIC * static
                + W_NEWS * news
                + W_MARKET * market
                + W_SUPPLY * supply
            )
            tier = _score_tier(final)

            _upsert_evidence(
                session, biz_date=biz_date, stock_code=code, tag_id=theme.id,
                evidence_type="static", score=static,
                raw_ref_type="edge" if edge_id else None, raw_ref_id=edge_id,
            )
            _upsert_evidence(
                session, biz_date=biz_date, stock_code=code, tag_id=theme.id,
                evidence_type="news", score=news, raw_ref_type="batch",
                meta={"keyword_hits": list(stock_kw.keys())[:10]},
            )
            _upsert_evidence(
                session, biz_date=biz_date, stock_code=code, tag_id=theme.id,
                evidence_type="comove", score=market, raw_ref_type="batch",
            )
            evidence_n += 3

            _upsert_score(
                session,
                biz_date=biz_date,
                stock_code=code,
                tag_id=theme.id,
                static_score=static,
                news_score=news,
                market_score=market,
                supply_score=supply,
                final_score=round(final, 4),
                tier=tier,
            )
            score_n += 1

    session.commit()
    return {
        "ok": True,
        "biz_date": biz_date.isoformat(),
        "themes": len(themes),
        "stocks": len(codes),
        "scores_written": score_n,
        "evidence_rows": evidence_n,
        "updated_at": now.isoformat(),
    }
