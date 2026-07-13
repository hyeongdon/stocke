"""테마/키워드 추출 — KeyBERT(기본) + 규칙 기반 빈도 fallback."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
import re
from collections import Counter
from typing import Iterable, List

from core.config import Config

logger = logging.getLogger(__name__)

_PREFIX_RE = re.compile(r"^\[(.*?)\]\s*")
_NON_WORD_RE = re.compile(r"[^\w가-힣]+")

_DEFAULT_STOPWORDS = {
    "속보", "단독", "종합", "기자", "인터뷰", "영상", "포토",
    "상승", "하락", "급등", "급락", "강세", "약세", "마감", "장중",
    "관련", "대해", "통해", "위해", "전망", "가능성", "확대",
}

_DEFAULT_SYNONYMS = {
    "이차전지": "2차전지",
    "배터리": "2차전지",
    "2차전지": "2차전지",
    "고대역폭메모리": "HBM",
    "고대역폭": "HBM",
    "hbm": "HBM",
    "원자력": "원전",
    "smr": "원전",
}

KEEP_UPPER = {"HBM", "SMR", "AI"}
RULES_DIR = Path(Config.PROJECT_ROOT) / "config" / "keyword_rules"


def _read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    out: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


@lru_cache(maxsize=1)
def load_stopwords() -> set[str]:
    words = set(_DEFAULT_STOPWORDS)
    words.update(_read_lines(RULES_DIR / "stopwords_ko.txt"))
    words.update(_read_lines(RULES_DIR / "stopwords_market.txt"))
    return words


@lru_cache(maxsize=1)
def load_synonyms() -> dict[str, str]:
    syn = dict(_DEFAULT_SYNONYMS)
    file_path = RULES_DIR / "synonyms.yml"
    if file_path.exists():
        for line in file_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or ":" not in s:
                continue
            left, right = s.split(":", 1)
            k = left.strip().strip('"').strip("'").lower()
            v = right.strip().strip('"').strip("'")
            if k and v:
                syn[k] = v
    return syn


@lru_cache(maxsize=1)
def load_whitelist() -> set[str]:
    return set(_read_lines(RULES_DIR / "keyword_whitelist.txt"))


def normalize_text(text: str) -> str:
    if not text:
        return ""
    t = _PREFIX_RE.sub("", text.strip())
    t = t.replace("·", " ").replace("/", " ").replace("-", " ").replace("_", " ")
    t = re.sub(r"\s+", " ", t)
    return t


def tokenize(text: str) -> List[str]:
    if not text:
        return []
    cleaned = _NON_WORD_RE.sub(" ", normalize_text(text))
    tokens = []
    for tok in cleaned.split():
        if re.fullmatch(r"A?\d{6}", tok):
            continue
        if tok.upper() in KEEP_UPPER:
            tokens.append(tok.upper())
            continue
        tokens.append(tok.lower())
    return tokens


def canonicalize(token: str) -> str:
    if not token:
        return ""
    key = token.lower()
    mapped = load_synonyms().get(key, token)
    return mapped.upper() if mapped.upper() in KEEP_UPPER else mapped


def extract_keywords_rules(texts: Iterable[str], *, min_len: int = 2, top_n: int = 30) -> List[dict]:
    """공백 분리 + 빈도 집계 (KeyBERT 미사용 시 fallback)."""
    stopwords = load_stopwords()
    whitelist = load_whitelist()
    counter: Counter[str] = Counter()
    for text in texts:
        for tok in tokenize(text):
            if len(tok) < min_len:
                continue
            canon = canonicalize(tok)
            if not canon:
                continue
            if canon not in whitelist and canon in stopwords:
                continue
            if canon.isdigit():
                continue
            counter[canon] += 1
    return [{"keyword": k, "mention_count": int(v)} for k, v in counter.most_common(top_n)]


def extract_keywords(texts: Iterable[str], *, min_len: int = 2, top_n: int = 30) -> List[dict]:
    """KeyBERT 우선, 실패 시 규칙 기반 빈도 추출."""
    materialized = [t for t in texts if (t or "").strip()]
    if not materialized:
        return []

    if Config.KEYWORD_USE_KEYBERT:
        try:
            from utils.theme_keyword_keybert import extract_keywords_keybert

            rows = extract_keywords_keybert(materialized, min_len=min_len, top_n=top_n)
            if rows:
                return rows
        except Exception as e:
            logger.warning("KeyBERT 키워드 추출 실패 — Kiwi 후보 fallback: %s", e)
            try:
                from utils.theme_keyword_keybert import extract_keywords_kiwi_freq

                rows = extract_keywords_kiwi_freq(materialized, min_len=min_len, top_n=top_n)
                if rows:
                    return rows
            except Exception as e2:
                logger.warning("Kiwi 후보 fallback 실패 — 규칙 fallback: %s", e2)

    return extract_keywords_rules(materialized, min_len=min_len, top_n=top_n)
