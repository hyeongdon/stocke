"""종목×테마 연관도 점수 — 정적·뉴스·시장동조 가중합."""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from datetime import date
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

logger = logging.getLogger(__name__)

# final = w_static*Static + w_news*News + w_market*CoMove + w_supply*Supply
W_STATIC = 0.40
W_NEWS = 0.35
W_MARKET = 0.15
W_SUPPLY = 0.10

TIER_CORE = 0.80
TIER_RELATED = 0.50
TIER_EVENT = 0.20

_SYNONYMS_PATH = Path(__file__).resolve().parent.parent / "config" / "keyword_rules" / "synonyms.yml"
_THEME_EDGE_SOURCES = ("naver_theme", "kiwoom_theme")


def _norm_code(code: str) -> str:
    return str(code or "").replace("A", "").strip().zfill(6)


def _code_aliases(code: str) -> List[str]:
    c = _norm_code(code)
    raw = c.lstrip("0") or "0"
    return list(dict.fromkeys([c, raw]))


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
    """종목 당일 뉴스 키워드 → 가중치 (단건)."""
    return _bulk_stock_news_keywords(session, [_norm_code(stock_code)], biz_date).get(
        _norm_code(stock_code), {}
    )


def _bulk_stock_news_keywords(
    session: Session,
    stock_codes: List[str],
    biz_date: date,
) -> Dict[str, Dict[str, float]]:
    """여러 종목 뉴스 키워드를 한두 번 조회로 적재."""
    codes = list(dict.fromkeys(_norm_code(c) for c in stock_codes if c))
    out: Dict[str, Dict[str, float]] = {c: defaultdict(float) for c in codes}
    if not codes:
        return {}

    variants: List[str] = []
    variant_to_norm: Dict[str, str] = {}
    for c in codes:
        for v in _code_aliases(c):
            variants.append(v)
            variant_to_norm[v] = c

    edges = (
        session.query(ThemeTagEdge, ThemeTag)
        .join(ThemeTag, ThemeTag.id == ThemeTagEdge.tag_id)
        .filter(ThemeTagEdge.stock_code.in_(variants))
        .filter(ThemeTagEdge.source == "news_title")
        .filter(ThemeTag.tag_type.in_(["news_keyword", "keyword"]))
        .all()
    )
    biz_iso = biz_date.isoformat()
    for edge, tag in edges:
        code = variant_to_norm.get(str(edge.stock_code))
        if not code:
            continue
        meta = edge.meta_json or {}
        edge_date = meta.get("biz_date")
        if edge_date and str(edge_date) != biz_iso:
            if edge.observed_at and edge.observed_at.date() != biz_date:
                continue
        kw = (tag.name_ko or "").strip().lower()
        if kw:
            out[code][kw] += float(edge.weight or 1.0)

    article_rows = (
        session.query(TagArticleKeywordEdge, ThemeTag, TagArticle)
        .join(ThemeTag, ThemeTag.id == TagArticleKeywordEdge.tag_id)
        .join(TagArticle, TagArticle.id == TagArticleKeywordEdge.article_id)
        .filter(TagArticle.stock_code.in_(variants))
        .filter(TagArticle.biz_date == biz_date)
        .filter(TagArticle.source == "naver_news")
        .all()
    )
    for edge, tag, article in article_rows:
        code = variant_to_norm.get(str(article.stock_code))
        if not code:
            continue
        kw = (tag.name_ko or "").strip().lower()
        if kw:
            out[code][kw] += float(edge.weight or 1.0)

    return {c: dict(v) for c, v in out.items()}


def _news_rel_score(theme_keywords: Set[str], stock_kw: Dict[str, float]) -> float:
    if not theme_keywords or not stock_kw:
        return 0.0
    hit = 0.0
    total = sum(stock_kw.values()) or 1.0
    for kw, w in stock_kw.items():
        if kw in theme_keywords:
            hit += w
            continue
        for tk in theme_keywords:
            if tk in kw or kw in tk:
                hit += w * 0.8
                break
    return min(1.0, hit / total)


def _market_cohesion_from_cache(
    stock_code: str,
    tag_id: int,
    stock_kw: Dict[str, float],
    *,
    peers_by_tag: Dict[int, List[str]],
    stock_kw_cache: Dict[str, Dict[str, float]],
    keyword_mentions: Dict[str, int],
    theme_name: str,
) -> float:
    """동일 테마 편입 종목군과 키워드 동조화 (메모리 캐시)."""
    peers = peers_by_tag.get(tag_id) or []
    my_keys = set(stock_kw.keys())
    code = _norm_code(stock_code)

    if len(peers) >= 2 and my_keys:
        overlap_counts: List[float] = []
        for peer in peers:
            if peer == code:
                continue
            pk = set((stock_kw_cache.get(peer) or {}).keys())
            if not pk:
                continue
            overlap = len(my_keys & pk) / max(len(my_keys | pk), 1)
            overlap_counts.append(overlap)
        if overlap_counts:
            return min(1.0, sum(overlap_counts) / len(overlap_counts))

    mention = int(keyword_mentions.get((theme_name or "").strip(), 0) or 0)
    if mention > 0:
        return min(1.0, mention / 50.0)
    return 0.0


def _market_cohesion_score(
    session: Session,
    stock_code: str,
    tag_id: int,
    biz_date: date,
    stock_kw: Dict[str, float],
) -> float:
    """동일 테마 편입 종목군과 키워드 동조화 (v1, 단건 호환)."""
    peers = (
        session.query(ThemeTagEdge.stock_code)
        .filter(
            ThemeTagEdge.tag_id == tag_id,
            ThemeTagEdge.source.in_(_THEME_EDGE_SOURCES),
            ThemeTagEdge.inclusion_flag.is_(True),
        )
        .filter(
            (ThemeTagEdge.biz_date == biz_date)
            | (ThemeTagEdge.biz_date.is_(None))
        )
        .all()
    )
    peer_codes = list(dict.fromkeys(_norm_code(p[0]) for p in peers if p[0]))
    stock_kw_cache = _bulk_stock_news_keywords(session, peer_codes, biz_date)
    theme_tag = session.query(ThemeTag).filter(ThemeTag.id == tag_id).first()
    theme_name = (theme_tag.name_ko if theme_tag else "") or ""
    mention = 0
    if theme_name:
        stat = (
            session.query(KeywordDailyStat)
            .filter(
                KeywordDailyStat.biz_date == biz_date,
                KeywordDailyStat.keyword == theme_name,
            )
            .first()
        )
        mention = int(stat.mention_count or 0) if stat else 0
    return _market_cohesion_from_cache(
        stock_code,
        tag_id,
        stock_kw,
        peers_by_tag={tag_id: peer_codes},
        stock_kw_cache=stock_kw_cache,
        keyword_mentions={theme_name: mention} if theme_name else {},
        theme_name=theme_name,
    )


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
            ThemeTagEdge.stock_code.in_(_code_aliases(code)),
            ThemeTagEdge.tag_id == tag_id,
            ThemeTagEdge.source.in_(_THEME_EDGE_SOURCES),
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


def _load_static_membership(
    session: Session,
    biz_date: date,
    *,
    tag_ids: Optional[Set[int]] = None,
    stock_codes: Optional[Set[str]] = None,
) -> Tuple[Dict[Tuple[str, int], int], Dict[int, List[str]]]:
    """(code, tag_id) → edge_id, tag_id → peer codes."""
    q = (
        session.query(
            ThemeTagEdge.id,
            ThemeTagEdge.stock_code,
            ThemeTagEdge.tag_id,
            ThemeTagEdge.inclusion_flag,
            ThemeTagEdge.observed_at,
        )
        .filter(ThemeTagEdge.source.in_(_THEME_EDGE_SOURCES))
        .filter(
            (ThemeTagEdge.biz_date == biz_date)
            | (ThemeTagEdge.biz_date.is_(None))
        )
    )
    if tag_ids:
        q = q.filter(ThemeTagEdge.tag_id.in_(list(tag_ids)))
    rows = q.all()

    best: Dict[Tuple[str, int], Tuple[int, object, bool]] = {}
    for edge_id, stock_code, tag_id, inclusion_flag, observed_at in rows:
        code = _norm_code(stock_code)
        if stock_codes is not None and code not in stock_codes:
            continue
        key = (code, int(tag_id))
        prev = best.get(key)
        if prev is None or (observed_at and prev[1] and observed_at > prev[1]) or (
            prev is not None and observed_at and not prev[1]
        ):
            best[key] = (int(edge_id), observed_at, bool(inclusion_flag))

    static_map: Dict[Tuple[str, int], int] = {}
    peers_by_tag: Dict[int, List[str]] = defaultdict(list)
    for (code, tag_id), (edge_id, _obs, included) in best.items():
        if not included:
            continue
        static_map[(code, tag_id)] = edge_id
        peers_by_tag[tag_id].append(code)

    for tag_id, peers in peers_by_tag.items():
        peers_by_tag[tag_id] = list(dict.fromkeys(peers))
    return static_map, dict(peers_by_tag)


def _upsert_evidence_cached(
    existing: Dict[Tuple[str, int, str], ThemeEvidence],
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
    code = _norm_code(stock_code)
    key = (code, int(tag_id), evidence_type)
    row = existing.get(key)
    if row:
        row.evidence_score = score
        row.raw_ref_type = raw_ref_type
        row.raw_ref_id = raw_ref_id
        row.meta_json = meta
        return
    row = ThemeEvidence(
        biz_date=biz_date,
        stock_code=code,
        tag_id=tag_id,
        evidence_type=evidence_type,
        evidence_score=score,
        raw_ref_type=raw_ref_type,
        raw_ref_id=raw_ref_id,
        meta_json=meta,
        created_at=utc_now_naive(),
    )
    session.add(row)
    existing[key] = row


def _upsert_score_cached(
    existing: Dict[Tuple[str, int], ThemeScoreDaily],
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
    key = (code, int(tag_id))
    now = utc_now_naive()
    row = existing.get(key)
    if row:
        row.static_score = static_score
        row.news_score = news_score
        row.market_score = market_score
        row.supply_score = supply_score
        row.final_score = final_score
        row.tier = tier
        row.updated_at = now
        return
    row = ThemeScoreDaily(
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
    session.add(row)
    existing[key] = row


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
    existing: Dict[Tuple[str, int, str], ThemeEvidence] = {}
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
        existing[(_norm_code(stock_code), int(tag_id), evidence_type)] = row
    _upsert_evidence_cached(
        existing,
        session,
        biz_date=biz_date,
        stock_code=stock_code,
        tag_id=tag_id,
        evidence_type=evidence_type,
        score=score,
        raw_ref_type=raw_ref_type,
        raw_ref_id=raw_ref_id,
        meta=meta,
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
    existing: Dict[Tuple[str, int], ThemeScoreDaily] = {}
    row = (
        session.query(ThemeScoreDaily)
        .filter(
            ThemeScoreDaily.biz_date == biz_date,
            ThemeScoreDaily.stock_code == _norm_code(stock_code),
            ThemeScoreDaily.tag_id == tag_id,
        )
        .first()
    )
    if row:
        existing[(_norm_code(stock_code), int(tag_id))] = row
    _upsert_score_cached(
        existing,
        session,
        biz_date=biz_date,
        stock_code=stock_code,
        tag_id=tag_id,
        static_score=static_score,
        news_score=news_score,
        market_score=market_score,
        supply_score=supply_score,
        final_score=final_score,
        tier=tier,
    )


def compute_theme_scores_for_date(
    session: Session,
    biz_date: Optional[date] = None,
    *,
    stock_codes: Optional[Iterable[str]] = None,
    tag_ids: Optional[Iterable[int]] = None,
) -> Dict:
    """당일(또는 지정일) 종목×테마 연관도 재계산 (배치 조회)."""
    t0 = time.monotonic()
    biz_date = biz_date or kst_today()
    synonyms = _load_synonyms()
    now = utc_now_naive()

    theme_q = session.query(ThemeTag).filter(ThemeTag.tag_type == "theme")
    tag_id_filter = set(int(x) for x in tag_ids) if tag_ids else None
    if tag_id_filter:
        theme_q = theme_q.filter(ThemeTag.id.in_(list(tag_id_filter)))
    themes = theme_q.all()
    if not themes:
        return {"ok": False, "error": "테마 없음", "biz_date": biz_date.isoformat()}

    theme_ids = {int(t.id) for t in themes}
    theme_kw_by_id = {int(t.id): _theme_keyword_set(t.name_ko, synonyms) for t in themes}
    theme_name_by_id = {int(t.id): (t.name_ko or "") for t in themes}

    if stock_codes:
        codes = list(dict.fromkeys(_norm_code(c) for c in stock_codes if c))
        code_set = set(codes)
    else:
        codes = None
        code_set = None

    static_map, peers_by_tag = _load_static_membership(
        session,
        biz_date,
        tag_ids=theme_ids,
        stock_codes=code_set,
    )

    if codes is None:
        codes = list(dict.fromkeys(code for code, _tag in static_map.keys()))
        # 뉴스만 있는 종목도 후보에 포함
        extra = (
            session.query(ThemeTagEdge.stock_code)
            .filter(ThemeTagEdge.source == "news_title")
            .distinct()
            .all()
        )
        for (raw,) in extra:
            c = _norm_code(raw)
            if c:
                codes.append(c)
        art = (
            session.query(TagArticle.stock_code)
            .filter(TagArticle.biz_date == biz_date, TagArticle.source == "naver_news")
            .distinct()
            .all()
        )
        for (raw,) in art:
            c = _norm_code(raw)
            if c:
                codes.append(c)
        codes = list(dict.fromkeys(codes))

    logger.info(
        "[THEME_SCORE] start biz=%s themes=%s stocks=%s static_edges=%s",
        biz_date.isoformat(),
        len(themes),
        len(codes),
        len(static_map),
    )

    stock_kw_cache = _bulk_stock_news_keywords(session, codes, biz_date)

    keyword_mentions: Dict[str, int] = {}
    for row in (
        session.query(KeywordDailyStat)
        .filter(KeywordDailyStat.biz_date == biz_date)
        .all()
    ):
        keyword_mentions[str(row.keyword or "").strip()] = int(row.mention_count or 0)

    existing_scores = {
        (_norm_code(r.stock_code), int(r.tag_id)): r
        for r in session.query(ThemeScoreDaily)
        .filter(ThemeScoreDaily.biz_date == biz_date)
        .filter(ThemeScoreDaily.tag_id.in_(list(theme_ids)))
        .all()
    }
    existing_evidence = {
        (_norm_code(r.stock_code), int(r.tag_id), str(r.evidence_type)): r
        for r in session.query(ThemeEvidence)
        .filter(ThemeEvidence.biz_date == biz_date)
        .filter(ThemeEvidence.tag_id.in_(list(theme_ids)))
        .all()
    }

    # 후보: 정적 편입 + 뉴스 키워드가 있는 종목×테마 (전체 카테시안 DB 조회 제거)
    candidate_pairs: Set[Tuple[str, int]] = set(static_map.keys())
    stocks_with_news = [c for c in codes if stock_kw_cache.get(c)]
    for code in stocks_with_news:
        sk = stock_kw_cache.get(code) or {}
        for theme in themes:
            tid = int(theme.id)
            if (code, tid) in candidate_pairs:
                continue
            if _news_rel_score(theme_kw_by_id[tid], sk) > 0:
                candidate_pairs.add((code, tid))

    evidence_n = 0
    score_n = 0
    for code, tid in candidate_pairs:
        edge_id = static_map.get((code, tid))
        static = 1.0 if edge_id else 0.0
        stock_kw = stock_kw_cache.get(code) or {}
        theme_kw = theme_kw_by_id.get(tid) or set()
        news = _news_rel_score(theme_kw, stock_kw)
        market = _market_cohesion_from_cache(
            code,
            tid,
            stock_kw,
            peers_by_tag=peers_by_tag,
            stock_kw_cache=stock_kw_cache,
            keyword_mentions=keyword_mentions,
            theme_name=theme_name_by_id.get(tid, ""),
        )
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

        _upsert_evidence_cached(
            existing_evidence,
            session,
            biz_date=biz_date,
            stock_code=code,
            tag_id=tid,
            evidence_type="static",
            score=static,
            raw_ref_type="edge" if edge_id else None,
            raw_ref_id=edge_id,
        )
        _upsert_evidence_cached(
            existing_evidence,
            session,
            biz_date=biz_date,
            stock_code=code,
            tag_id=tid,
            evidence_type="news",
            score=news,
            raw_ref_type="batch",
            meta={"keyword_hits": list(stock_kw.keys())[:10]},
        )
        _upsert_evidence_cached(
            existing_evidence,
            session,
            biz_date=biz_date,
            stock_code=code,
            tag_id=tid,
            evidence_type="comove",
            score=market,
            raw_ref_type="batch",
        )
        evidence_n += 3

        _upsert_score_cached(
            existing_scores,
            session,
            biz_date=biz_date,
            stock_code=code,
            tag_id=tid,
            static_score=static,
            news_score=news,
            market_score=market,
            supply_score=supply,
            final_score=round(final, 4),
            tier=tier,
        )
        score_n += 1

    session.commit()
    elapsed = time.monotonic() - t0
    logger.info(
        "[THEME_SCORE] done biz=%s scores=%s evidence=%s candidates=%s elapsed=%.1fs",
        biz_date.isoformat(),
        score_n,
        evidence_n,
        len(candidate_pairs),
        elapsed,
    )
    return {
        "ok": True,
        "biz_date": biz_date.isoformat(),
        "themes": len(themes),
        "stocks": len(codes),
        "scores_written": score_n,
        "evidence_rows": evidence_n,
        "candidates": len(candidate_pairs),
        "elapsed_sec": round(elapsed, 2),
        "updated_at": now.isoformat(),
    }
