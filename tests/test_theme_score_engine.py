"""테마 점수 엔진 — 배치 계산 단위 테스트."""

from utils.theme_score_engine import (
    _market_cohesion_from_cache,
    _news_rel_score,
    _score_tier,
    _theme_keyword_set,
)


def test_news_rel_score_exact_and_partial():
    theme_kw = {"반도체", "hbm"}
    assert _news_rel_score(theme_kw, {"반도체": 2.0}) > 0
    assert _news_rel_score(theme_kw, {"hbm장비": 1.0}) > 0
    assert _news_rel_score(theme_kw, {"조선": 1.0}) == 0.0


def test_market_cohesion_uses_peer_overlap():
    score = _market_cohesion_from_cache(
        "005930",
        1,
        {"반도체": 1.0, "hbm": 1.0},
        peers_by_tag={1: ["005930", "000660", "035420"]},
        stock_kw_cache={
            "005930": {"반도체": 1.0, "hbm": 1.0},
            "000660": {"반도체": 1.0},
            "035420": {"광고": 1.0},
        },
        keyword_mentions={},
        theme_name="반도체",
    )
    assert 0 < score <= 1.0


def test_market_cohesion_fallback_mention():
    score = _market_cohesion_from_cache(
        "005930",
        1,
        {},
        peers_by_tag={1: ["005930"]},
        stock_kw_cache={},
        keyword_mentions={"반도체": 25},
        theme_name="반도체",
    )
    assert score == 0.5


def test_theme_keyword_set_and_tier():
    kws = _theme_keyword_set("AI / 반도체", {})
    assert "ai" in kws
    assert "반도체" in kws
    assert _score_tier(0.85) == "core"
    assert _score_tier(0.55) == "related"
