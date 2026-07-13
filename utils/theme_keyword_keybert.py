"""KeyBERT 기반 뉴스 키워드 추출 (한국어 SBERT + 후보 명사/구)."""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from core.config import Config

logger = logging.getLogger(__name__)

_kw_model = None
_kiwi = None
_kiwi_failed = False

_HANGUL_RUN = re.compile(r"[가-힣]{2,}")


def _get_keybert():
    global _kw_model
    if _kw_model is not None:
        return _kw_model
    from keybert import KeyBERT
    from sentence_transformers import SentenceTransformer

    model_name = Config.KEYBERT_MODEL
    logger.info("KeyBERT 모델 로드 중: %s", model_name)
    st = SentenceTransformer(model_name)
    _kw_model = KeyBERT(model=st)
    return _kw_model


def _get_kiwi():
    global _kiwi, _kiwi_failed
    if _kiwi_failed or not Config.KEYBERT_USE_KIWI:
        return None
    if _kiwi is not None:
        return _kiwi
    try:
        from kiwipiepy import Kiwi

        _kiwi = Kiwi()
        return _kiwi
    except Exception as e:
        logger.warning("Kiwi 로드 실패 — 토큰 후보만 사용: %s", e)
        _kiwi_failed = True
        return None


def _kiwi_noun_candidates(text: str, min_len: int) -> List[str]:
    kiwi = _get_kiwi()
    if not kiwi:
        return []
    out: List[str] = []
    try:
        for tok in kiwi.tokenize(text):
            tag = tok.tag or ""
            if not (tag.startswith("NN") or tag in ("SN", "SL")):
                continue
            form = (tok.form or "").strip()
            if len(form) >= min_len:
                out.append(form)
    except Exception as e:
        logger.debug("Kiwi 후보 추출 오류: %s", e)
    return out


def _token_ngram_candidates(text: str, min_len: int) -> List[str]:
    from utils.theme_keyword_rules import normalize_text, tokenize

    norm = normalize_text(text)
    toks = [t for t in tokenize(norm) if len(t) >= min_len]
    cands: List[str] = []
    for t in toks:
        cands.append(t)
    for i in range(len(toks) - 1):
        cands.append(f"{toks[i]} {toks[i + 1]}")
        if re.search(r"[가-힣]", toks[i]) and re.search(r"[가-힣]", toks[i + 1]):
            cands.append(f"{toks[i]}{toks[i + 1]}")
    for run in _HANGUL_RUN.findall(norm):
        if len(run) >= min_len:
            cands.append(run)
    return cands


def build_candidates(text: str, *, min_len: int = 2) -> List[str]:
    from utils.theme_keyword_rules import load_whitelist, normalize_text

    norm = normalize_text(text)
    if not norm:
        return []

    raw: set[str] = set()
    raw.update(_kiwi_noun_candidates(norm, min_len))
    raw.update(_token_ngram_candidates(norm, min_len))

    lower_norm = norm.lower()
    for phrase in load_whitelist():
        p = phrase.strip()
        if not p:
            continue
        if p in norm or p.lower() in lower_norm:
            raw.add(p)

    from utils.theme_keyword_rules import canonicalize, load_stopwords, load_whitelist

    stopwords = load_stopwords()
    whitelist = load_whitelist()
    filtered: List[str] = []
    seen: set[str] = set()
    for cand in raw:
        c = cand.strip()
        if len(c) < min_len or c.isdigit():
            continue
        canon = canonicalize(c)
        if not canon:
            continue
        key = canon.lower()
        if key in seen:
            continue
        if canon not in whitelist and canon in stopwords:
            continue
        seen.add(key)
        filtered.append(canon if canon != c else c)
    return filtered


def _extract_one_document(
    text: str,
    *,
    min_len: int,
    top_n: int,
) -> List[Tuple[str, float]]:
    from utils.theme_keyword_rules import canonicalize, normalize_text

    norm = normalize_text(text)
    if not norm:
        return []

    candidates = build_candidates(norm, min_len=min_len)
    if not candidates:
        return []

    kw_model = _get_keybert()
    kwargs = {
        "candidates": candidates,
        "top_n": min(top_n, len(candidates)),
    }
    if Config.KEYBERT_USE_MMR:
        kwargs["use_mmr"] = True
        kwargs["diversity"] = Config.KEYBERT_DIVERSITY

    try:
        ranked = kw_model.extract_keywords(norm, **kwargs)
    except TypeError:
        kwargs.pop("use_mmr", None)
        kwargs.pop("diversity", None)
        ranked = kw_model.extract_keywords(norm, **kwargs)

    out: List[Tuple[str, float]] = []
    for item in ranked or []:
        if not item:
            continue
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            phrase, score = item[0], float(item[1])
        else:
            phrase, score = str(item), 1.0
        phrase = canonicalize(str(phrase).strip()) or str(phrase).strip()
        if len(phrase) < min_len:
            continue
        out.append((phrase, max(0.0, score)))
    return out


def extract_keywords_keybert(
    texts: Iterable[str],
    *,
    min_len: int = 2,
    top_n: int = 30,
) -> List[dict]:
    """문서 임베딩 ↔ 후보 키워드 임베딩 유사도(KeyBERT)로 키워드 추출."""
    score_sum: Dict[str, float] = defaultdict(float)
    doc_hits: Dict[str, int] = defaultdict(int)
    max_score: Dict[str, float] = defaultdict(float)

    doc_count = 0
    for text in texts:
        if not (text or "").strip():
            continue
        doc_count += 1
        for phrase, score in _extract_one_document(text, min_len=min_len, top_n=top_n * 2):
            score_sum[phrase] += score
            doc_hits[phrase] += 1
            if score > max_score[phrase]:
                max_score[phrase] = score

    if not score_sum:
        return []

    # 문서 수·최대 유사도·누적 유사도를 함께 반영
    ranked = sorted(
        score_sum.keys(),
        key=lambda k: (doc_hits[k], max_score[k], score_sum[k]),
        reverse=True,
    )[:top_n]

    rows: List[dict] = []
    for kw in ranked:
        score = max_score[kw]
        rows.append(
            {
                "keyword": kw,
                "mention_count": max(1, doc_hits[kw]),
                "score": round(score, 4),
                "score_sum": round(score_sum[kw], 4),
            }
        )
    if doc_count == 0:
        return []
    logger.debug("KeyBERT 키워드 %d건 (문서 %d건)", len(rows), doc_count)
    return rows


def extract_keywords_kiwi_freq(
    texts: Iterable[str],
    *,
    min_len: int = 2,
    top_n: int = 30,
) -> List[dict]:
    """Kiwi 명사 후보 + 빈도 (KeyBERT/torch 불가 시 중간 fallback)."""
    from utils.theme_keyword_rules import (
        canonicalize,
        load_stopwords,
        load_whitelist,
        normalize_text,
    )

    stopwords = load_stopwords()
    whitelist = load_whitelist()
    counter: Dict[str, int] = defaultdict(int)

    for text in texts:
        if not (text or "").strip():
            continue
        norm = normalize_text(text)
        if not norm:
            continue
        seen: set[str] = set()
        cands = list(_kiwi_noun_candidates(norm, min_len))
        lower_norm = norm.lower()
        for phrase in whitelist:
            p = phrase.strip()
            if p and (p in norm or p.lower() in lower_norm):
                cands.append(p)
        for cand in cands:
            canon = canonicalize(cand) or cand
            if not canon or canon.isdigit():
                continue
            if canon not in whitelist and canon in stopwords:
                continue
            key = canon.lower()
            if key in seen:
                continue
            seen.add(key)
            counter[canon] += 1

    if not counter:
        return []
    ranked = sorted(counter.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return [{"keyword": k, "mention_count": int(v)} for k, v in ranked]
