"""종가배팅(jongga) — 거래대금순 → 테마 합산 → 사용자 선택/자동매수."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from utils.datetime_kst import as_kst, kst_today, utc_now_naive

logger = logging.getLogger(__name__)

STRATEGY_KEY = "jongga"
GATE_PACK = "jongga_closing"
UNMAPPED_THEME = "미분류"

DEFAULT_RANK_LIMIT = 50
DEFAULT_BUY_AMOUNT = 1_000_000
DEFAULT_MAX_SLOTS = 1
DEFAULT_STOP_LOSS_PCT = 3.0
DEFAULT_TRAILING_START_PCT = 5.0
DEFAULT_TRAILING_PCT = 2.0

# 자동선택 스코어 가중치 (눌림 · 대금 · 등락)
DEFAULT_W_PULLBACK = 1.0
DEFAULT_W_AMOUNT = 1.0
DEFAULT_W_CHANGE = 1.0

_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs",
    "_jongga_state.json",
)


def _norm_code(code: Any) -> str:
    s = str(code or "").replace("A", "").strip()
    if not s:
        return ""
    return s.zfill(6) if s.isdigit() else s


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _trade_amount(row: Dict[str, Any]) -> float:
    for key in ("trade_amount", "trading_value", "trde_prica", "trde_amt"):
        try:
            v = row.get(key)
            if v is not None and str(v).strip() != "":
                return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def pullback_from_day_high_pct(
    current_price: float,
    day_high: float,
) -> Optional[float]:
    """당일 고가 대비 하락률(%). 높을수록 눌림이 큼."""
    try:
        px = float(current_price)
        hi = float(day_high)
    except (TypeError, ValueError):
        return None
    if hi <= 0 or px <= 0:
        return None
    return max(0.0, (hi - px) / hi * 100.0)


def primary_theme(themes: Optional[Sequence[str]]) -> str:
    if not themes:
        return UNMAPPED_THEME
    for t in themes:
        name = str(t or "").strip()
        if name:
            return name
    return UNMAPPED_THEME


def aggregate_theme_trade_amounts(
    items: Sequence[Dict[str, Any]],
    theme_map: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    """종목 리스트 + 테마맵 → 테마별 대금 합산, enrichment된 행 목록."""
    totals: Dict[str, float] = {}
    enriched: List[Dict[str, Any]] = []
    for raw in items or []:
        code = _norm_code(raw.get("stock_code"))
        if not code:
            continue
        info = theme_map.get(code) or {}
        themes = list(info.get("themes") or [])
        theme = primary_theme(themes)
        amt = _trade_amount(raw)
        totals[theme] = totals.get(theme, 0.0) + amt
        row = dict(raw)
        row["stock_code"] = code
        row["theme"] = theme
        row["themes"] = themes
        row["trade_amount"] = amt
        try:
            row["change_rate"] = float(raw.get("change_rate"))
        except (TypeError, ValueError):
            row["change_rate"] = _f(raw.get("change_rate"), 0.0)
        try:
            row["current_price"] = int(raw.get("current_price") or 0)
        except (TypeError, ValueError):
            row["current_price"] = 0
        enriched.append(row)
    return totals, enriched


def strongest_theme(totals: Dict[str, float]) -> Optional[str]:
    if not totals:
        return None
    return max(totals.items(), key=lambda kv: (kv[1], kv[0] != UNMAPPED_THEME, kv[0]))[0]


def candidates_for_theme(
    enriched: Sequence[Dict[str, Any]],
    theme: str,
) -> List[Dict[str, Any]]:
    theme = str(theme or UNMAPPED_THEME)
    rows = [r for r in enriched if str(r.get("theme") or UNMAPPED_THEME) == theme]
    rows.sort(key=lambda r: float(r.get("trade_amount") or 0), reverse=True)
    return rows


def _minmax_norm(values: List[float]) -> List[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi <= lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def score_candidates(
    candidates: Sequence[Dict[str, Any]],
    *,
    w_pullback: float = DEFAULT_W_PULLBACK,
    w_amount: float = DEFAULT_W_AMOUNT,
    w_change: float = DEFAULT_W_CHANGE,
) -> List[Dict[str, Any]]:
    """눌림↑·대금↑·등락↑ 가중합. score 내림차순 정렬 복사본."""
    rows = [dict(c) for c in candidates]
    if not rows:
        return []
    pulls = [_f(r.get("pullback_pct"), 0.0) for r in rows]
    amts = [_f(r.get("trade_amount"), 0.0) for r in rows]
    chgs = [_f(r.get("change_rate"), 0.0) for r in rows]
    np_ = _minmax_norm(pulls)
    na = _minmax_norm(amts)
    nc = _minmax_norm(chgs)
    wp, wa, wc = float(w_pullback), float(w_amount), float(w_change)
    wsum = wp + wa + wc
    if wsum <= 0:
        wp = wa = wc = 1.0
        wsum = 3.0
    for i, r in enumerate(rows):
        r["score"] = round(
            (wp * np_[i] + wa * na[i] + wc * nc[i]) / wsum,
            6,
        )
        r["score_parts"] = {
            "pullback_n": round(np_[i], 4),
            "amount_n": round(na[i], 4),
            "change_n": round(nc[i], 4),
        }
    rows.sort(
        key=lambda r: (
            float(r.get("score") or 0),
            float(r.get("trade_amount") or 0),
            float(r.get("change_rate") or 0),
        ),
        reverse=True,
    )
    return rows


def pick_auto_candidate(scored: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return scored[0] if scored else None


def load_jongga_state(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or _STATE_PATH
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("jongga state load 실패: %s", e)
        return {}


def save_jongga_state(state: Dict[str, Any], path: Optional[str] = None) -> None:
    p = path or _STATE_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    payload = dict(state or {})
    payload["updated_at"] = utc_now_naive().isoformat()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def today_state_or_empty(path: Optional[str] = None) -> Dict[str, Any]:
    st = load_jongga_state(path)
    today = kst_today().isoformat()
    if str(st.get("biz_date") or "") != today:
        return {
            "biz_date": today,
            "status": "idle",
            "strongest_theme": None,
            "theme_totals": {},
            "candidates": [],
            "picked_code": None,
            "auto_fired": False,
        }
    return st


def _extract_day_high(row: Dict[str, Any]) -> int:
    for key in ("day_high", "high", "high_price", "high_pric", "stck_hgpr"):
        try:
            v = row.get(key)
            if v is not None and str(v).strip() != "":
                return int(float(str(v).replace(",", "")))
        except (TypeError, ValueError):
            continue
    return 0


def _ohlc_from_bars(bars: Optional[Sequence[Dict[str, Any]]]) -> Tuple[int, int]:
    """분봉/일봉에서 (당일고가, 마지막종가). high 없으면 close로 고가 보정."""
    day_high = 0
    last_close = 0
    for b in bars or []:
        try:
            c = int(b.get("close") or 0)
        except (TypeError, ValueError):
            c = 0
        try:
            h = int(b.get("high") or 0)
        except (TypeError, ValueError):
            h = 0
        if c > 0:
            last_close = c
            if c > day_high:
                day_high = c
        if h > day_high:
            day_high = h
    return day_high, last_close


async def _resolve_day_high_price(
    kiwoom_api: Any,
    code: str,
    px: int,
) -> Tuple[int, int, List[Dict[str, Any]]]:
    """당일 고가·눌림용 종가·사용 봉. 분봉 → 일봉. 종가는 차트 마지막 close 우선."""
    day_high = 0
    last_close = 0
    used_bars: List[Dict[str, Any]] = []

    try:
        from utils.intraday_sparkline import today_kst_date

        result = await kiwoom_api.get_intraday_chart_for_date(
            code, today_kst_date(), tic_scope="15", max_pages=1,
        )
        used_bars = list((result or {}).get("bars") or [])
        day_high, last_close = _ohlc_from_bars(used_bars)
    except Exception as e:
        logger.warning("jongga day_high 분봉 실패 %s: %s", code, e)

    if not day_high:
        try:
            bars = await kiwoom_api.get_stock_chart_data(
                code, "1D", max_bars=3, allow_off_hours=True,
            )
        except Exception as e:
            logger.warning("jongga day_high 일봉 실패 %s: %s", code, e)
            bars = None
        if bars:
            used_bars = [bars[-1]]
            day_high, last_close = _ohlc_from_bars(used_bars)

    if not last_close and px:
        last_close = px
    return day_high, last_close, used_bars


async def fill_pullbacks_from_daily_chart(
    kiwoom_api: Any,
    rows: Sequence[Dict[str, Any]],
) -> Dict[str, float]:
    """후보에 차트 고가 기준 눌림을 채운다.

    눌림 = (당일 분봉 max(high) − 마지막 close) / max(high) × 100
    """
    from utils.intraday_sparkline import bars_to_sparkline

    pullbacks: Dict[str, float] = {}
    for r in rows or []:
        code = _norm_code(r.get("stock_code"))
        if not code:
            continue
        try:
            px_rank = int(r.get("current_price") or 0)
        except (TypeError, ValueError):
            px_rank = 0

        day_high = 0
        last_close = 0
        used_bars: List[Dict[str, Any]] = []
        if kiwoom_api is not None:
            day_high, last_close, used_bars = await _resolve_day_high_price(
                kiwoom_api, code, px_rank,
            )
        else:
            day_high = _extract_day_high(r)

        px = last_close or px_rank
        if last_close > 0:
            r["chart_last"] = last_close
        if day_high > 0:
            r["day_high"] = day_high
        if used_bars:
            sp = bars_to_sparkline(used_bars)
            if sp:
                r["sparkline"] = sp
                # 스파크라인과 동일 공식으로 한 번 더 맞춤
                if sp.get("day_high"):
                    r["day_high"] = int(sp["day_high"])
                    day_high = int(sp["day_high"])
                if sp.get("last"):
                    r["chart_last"] = int(sp["last"])
                    px = int(sp["last"])
                if sp.get("pullback_pct") is not None:
                    r["pullback_pct"] = float(sp["pullback_pct"])
                    pullbacks[code] = r["pullback_pct"]
                    logger.info(
                        "jongga pullback(chart) code=%s high=%s last=%s pb=%.2f%%",
                        code, day_high, px, r["pullback_pct"],
                    )
                    continue

        pb = pullback_from_day_high_pct(px, day_high) if px and day_high else None
        if pb is None:
            logger.warning(
                "jongga pullback 미산출 code=%s px=%s day_high=%s rank_px=%s",
                code, px, day_high, px_rank,
            )
            r["pullback_pct"] = None
        else:
            r["pullback_pct"] = round(pb, 4)
            pullbacks[code] = r["pullback_pct"]
            logger.info(
                "jongga pullback(chart) code=%s high=%s last=%s pb=%.2f%%",
                code, day_high, px, r["pullback_pct"],
            )
    return pullbacks


def build_session_payload(
    *,
    items: Sequence[Dict[str, Any]],
    theme_map: Dict[str, Dict[str, Any]],
    pullbacks: Optional[Dict[str, float]] = None,
    w_pullback: float = DEFAULT_W_PULLBACK,
    w_amount: float = DEFAULT_W_AMOUNT,
    w_change: float = DEFAULT_W_CHANGE,
) -> Dict[str, Any]:
    """대금순 항목 → 최강 테마 후보 + 스코어.

    눌림은 pullbacks 또는 행의 day_high로 계산한다.
    고가가 없으면 pullback_pct=None (스코어 시 0 취급).
    """
    totals, enriched = aggregate_theme_trade_amounts(items, theme_map)
    theme = strongest_theme(totals)
    cands = candidates_for_theme(enriched, theme) if theme else []
    pb = pullbacks or {}
    for r in cands:
        code = r.get("stock_code")
        if code in pb and pb[code] is not None:
            r["pullback_pct"] = float(pb[code])
        elif r.get("pullback_pct") is None:
            hi = float(_extract_day_high(r) or 0)
            px = _f(r.get("current_price"), 0.0)
            calc = pullback_from_day_high_pct(px, hi) if hi and px else None
            r["pullback_pct"] = calc  # None 허용 — 가짜 0 금지
    scored = score_candidates(
        cands, w_pullback=w_pullback, w_amount=w_amount, w_change=w_change,
    )
    ranked_themes = sorted(
        ({"theme": k, "trade_amount": v} for k, v in totals.items()),
        key=lambda x: float(x["trade_amount"]),
        reverse=True,
    )
    return {
        "biz_date": kst_today().isoformat(),
        "status": "awaiting_pick",
        "strongest_theme": theme,
        "theme_totals": {k: round(v, 2) for k, v in totals.items()},
        "theme_rank": ranked_themes[:10],
        "candidates": scored,
        "auto_pick": pick_auto_candidate(scored),
        "picked_code": None,
        "auto_fired": False,
        "telegram_notified": False,
        "built_at": utc_now_naive().isoformat(),
    }


def attach_market_caps(rows: Sequence[Dict[str, Any]]) -> None:
    """기본적분석 마트 시가총액(억원)을 후보 행에 부착 (in-place)."""
    codes = [_norm_code(r.get("stock_code")) for r in (rows or [])]
    codes = [c for c in codes if c]
    if not codes:
        return
    try:
        from utils.fundamental_mart_store import get_latest_map_by_codes
        fmap = get_latest_map_by_codes(codes) or {}
    except Exception as e:
        logger.warning("jongga 시총 조회 실패: %s", e)
        return
    for r in rows or []:
        code = _norm_code(r.get("stock_code"))
        fund = fmap.get(code) or {}
        mcap = fund.get("market_cap")
        if mcap is not None:
            try:
                r["market_cap"] = float(mcap)
            except (TypeError, ValueError):
                r["market_cap"] = mcap


async def build_session_payload_async(
    kiwoom_api: Any,
    *,
    items: Sequence[Dict[str, Any]],
    theme_map: Dict[str, Dict[str, Any]],
    w_pullback: float = DEFAULT_W_PULLBACK,
    w_amount: float = DEFAULT_W_AMOUNT,
    w_change: float = DEFAULT_W_CHANGE,
) -> Dict[str, Any]:
    """테마 집계 후 최강테마 후보만 차트 고가로 눌림을 채운 세션 페이로드."""
    totals, enriched = aggregate_theme_trade_amounts(items, theme_map)
    theme = strongest_theme(totals)
    cands = candidates_for_theme(enriched, theme) if theme else []
    pullbacks = await fill_pullbacks_from_daily_chart(kiwoom_api, cands)
    by_code = {_norm_code(r.get("stock_code")): r for r in cands}
    meta_keys = ("day_high", "chart_last", "pullback_pct", "sparkline")
    for it in items:
        code = _norm_code(it.get("stock_code"))
        src = by_code.get(code)
        if not src:
            continue
        for k in meta_keys:
            if src.get(k) is not None:
                it[k] = src[k]
    payload = build_session_payload(
        items=items,
        theme_map=theme_map,
        pullbacks=pullbacks,
        w_pullback=w_pullback,
        w_amount=w_amount,
        w_change=w_change,
    )
    for r in payload.get("candidates") or []:
        src = by_code.get(_norm_code(r.get("stock_code")))
        if not src:
            continue
        for k in ("day_high", "chart_last", "sparkline"):
            if src.get(k) is not None:
                r[k] = src[k]
        if src.get("pullback_pct") is not None:
            r["pullback_pct"] = src["pullback_pct"]
    payload["candidates"] = score_candidates(
        payload.get("candidates") or [],
        w_pullback=w_pullback,
        w_amount=w_amount,
        w_change=w_change,
    )
    attach_market_caps(payload["candidates"])
    payload["auto_pick"] = pick_auto_candidate(payload["candidates"])
    auto = payload.get("auto_pick")
    if auto:
        src = by_code.get(_norm_code(auto.get("stock_code")))
        if src:
            for k in ("day_high", "chart_last", "sparkline", "market_cap"):
                if src.get(k) is not None:
                    auto[k] = src[k]
        if auto.get("market_cap") is None:
            for r in payload["candidates"]:
                if _norm_code(r.get("stock_code")) == _norm_code(auto.get("stock_code")):
                    if r.get("market_cap") is not None:
                        auto["market_cap"] = r["market_cap"]
                    break
    return payload


def in_pick_window(
    settings: Any,
    now: Optional[datetime] = None,
) -> Tuple[bool, Optional[str]]:
    """14:30~(pick_end|trade_end) 사용자 선택 창."""
    kst = as_kst(now)
    try:
        start_s = getattr(settings, "jongga_trade_start_time", None) or "14:30"
        end_s = (
            getattr(settings, "jongga_pick_end_time", None)
            or getattr(settings, "jongga_trade_end_time", None)
            or "14:40"
        )
        sh, sm = map(int, str(start_s).split(":"))
        eh, em = map(int, str(end_s).split(":"))
        from datetime import time as dt_time
        if not (dt_time(sh, sm) <= kst.time() <= dt_time(eh, em)):
            return False, f"종가배팅 선택 창 외 ({start_s}~{end_s})"
    except Exception:
        return False, "종가배팅 시간 판정 오류"
    return True, None


def past_pick_end(settings: Any, now: Optional[datetime] = None) -> bool:
    kst = as_kst(now)
    try:
        end_s = (
            getattr(settings, "jongga_pick_end_time", None)
            or getattr(settings, "jongga_trade_end_time", None)
            or "14:40"
        )
        eh, em = map(int, str(end_s).split(":"))
        from datetime import time as dt_time
        return kst.time() >= dt_time(eh, em)
    except Exception:
        return False


def is_exit_management_day(buy_time, now: Optional[datetime] = None) -> bool:
    """익일부터 고정손절·트레일 적용 (매수 당일은 청산 모니터 스킵)."""
    from utils.position_peak_since_buy import buy_time_utc_naive_to_kst

    buy_kst = buy_time_utc_naive_to_kst(buy_time)
    if buy_kst is None:
        return True
    return as_kst(now).date() > buy_kst.date()


def find_candidate(state: Dict[str, Any], stock_code: str) -> Optional[Dict[str, Any]]:
    code = _norm_code(stock_code)
    for row in state.get("candidates") or []:
        if _norm_code(row.get("stock_code")) == code:
            return row
    return None


# ----- 돼지물량 반응형 분할매수 -----

DEFAULT_LEG_PCTS = (20.0, 30.0, 50.0)
DEFAULT_LEG2_START = "14:50"
DEFAULT_LEG3_START = "15:20"
DEFAULT_LEG3_END = "15:28"
DEFAULT_PIG_RATIO = 1.5
DEFAULT_PIG_LEVELS = 5
DEFAULT_LOW_HOLD_BARS = 5


def pig_split_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "jongga_pig_split", True))


def jongga_leg_pcts(settings: Any) -> Tuple[float, float, float]:
    """1·2·3차 비중(%). 합이 0이면 기본 20/30/50."""
    try:
        a = float(getattr(settings, "jongga_leg1_pct", None) or DEFAULT_LEG_PCTS[0])
        b = float(getattr(settings, "jongga_leg2_pct", None) or DEFAULT_LEG_PCTS[1])
        c = float(getattr(settings, "jongga_leg3_pct", None) or DEFAULT_LEG_PCTS[2])
    except (TypeError, ValueError):
        return DEFAULT_LEG_PCTS
    if a + b + c <= 0:
        return DEFAULT_LEG_PCTS
    return (a, b, c)


def leg_fraction(settings: Any, entry_leg: int) -> float:
    """해당 차수 비중 비율(0~1). 분할 OFF면 1차=1.0."""
    if not pig_split_enabled(settings):
        return 1.0 if int(entry_leg or 1) <= 1 else 0.0
    pcts = jongga_leg_pcts(settings)
    total = sum(pcts) or 100.0
    leg = max(1, min(3, int(entry_leg or 1)))
    return max(0.0, pcts[leg - 1] / total)


def _parse_hm(s: str, default: str) -> Tuple[int, int]:
    raw = str(s or default).strip() or default
    h, m = map(int, raw.split(":")[:2])
    return h, m


def past_hm(settings_time: str, default: str, now: Optional[datetime] = None) -> bool:
    kst = as_kst(now)
    try:
        h, m = _parse_hm(settings_time, default)
        from datetime import time as dt_time
        return kst.time() >= dt_time(h, m)
    except Exception:
        return False


def in_hm_window(
    start_s: str,
    end_s: str,
    *,
    default_start: str,
    default_end: str,
    now: Optional[datetime] = None,
) -> bool:
    kst = as_kst(now)
    try:
        from datetime import time as dt_time
        sh, sm = _parse_hm(start_s, default_start)
        eh, em = _parse_hm(end_s, default_end)
        return dt_time(sh, sm) <= kst.time() <= dt_time(eh, em)
    except Exception:
        return False


def jongga_buy_window_end(settings: Any) -> str:
    """종가배팅 매수 허용 종료 시각(분할 시 3차 종료)."""
    if pig_split_enabled(settings):
        return (
            getattr(settings, "jongga_leg3_end_time", None)
            or DEFAULT_LEG3_END
        )
    return (
        getattr(settings, "jongga_pick_end_time", None)
        or getattr(settings, "jongga_trade_end_time", None)
        or "14:40"
    )


def pig_orderbook_verdict(
    orderbook: Sequence[Dict[str, Any]],
    *,
    levels: int = DEFAULT_PIG_LEVELS,
    min_ratio: float = DEFAULT_PIG_RATIO,
) -> Tuple[str, Dict[str, Any]]:
    """호가 잔량비로 돼지(매수벽/매도벽) 판정.

    Returns:
        ('buy'|'sell'|'neutral', detail)
    """
    n = max(1, int(levels or DEFAULT_PIG_LEVELS))
    rows = list(orderbook or [])[:n]
    bid = 0
    ask = 0
    for r in rows:
        try:
            bid += int(r.get("bid_qty") or 0)
        except (TypeError, ValueError):
            pass
        try:
            ask += int(r.get("ask_qty") or 0)
        except (TypeError, ValueError):
            pass
    ratio = None
    if ask > 0:
        ratio = bid / ask
    elif bid > 0:
        ratio = float("inf")
    detail = {
        "bid_qty": bid,
        "ask_qty": ask,
        "ratio": ratio,
        "levels": n,
        "min_ratio": float(min_ratio),
    }
    thr = float(min_ratio or DEFAULT_PIG_RATIO)
    if thr <= 0:
        thr = DEFAULT_PIG_RATIO
    if ratio is None:
        return "neutral", detail
    if ratio >= thr:
        return "buy", detail
    if ratio <= (1.0 / thr):
        return "sell", detail
    return "neutral", detail


def investor_net_ok(
    foreign_net: Optional[int],
    institution_net: Optional[int],
) -> Tuple[bool, str]:
    """외인·기관 순매수 유지: 둘 다 >=0 이고 합 > 0. (장중 미집계로 비권장)"""
    try:
        f = int(foreign_net or 0)
        i = int(institution_net or 0)
    except (TypeError, ValueError):
        return False, "수급 수치 없음"
    if f < 0:
        return False, f"외인 순매도({f})"
    if i < 0:
        return False, f"기관 순매도({i})"
    if f + i <= 0:
        return False, "외인·기관 순매수 없음"
    return True, f"외인 {f:+d} · 기관 {i:+d}"


def program_net_ok(net_qty: Optional[int]) -> Tuple[bool, str]:
    """프로그램 매수세: 당일(또는 최신) 순매수수량 > 0."""
    try:
        n = int(net_qty or 0)
    except (TypeError, ValueError):
        return False, "프로그램 수급 수치 없음"
    if n > 0:
        return True, f"프로그램 순매수 {n:+d}"
    if n < 0:
        return False, f"프로그램 순매도({n})"
    return False, "프로그램 순매수 없음"


def low_support_ok(
    bars: Sequence[Dict[str, Any]],
    current_price: float,
    *,
    lookback: int = DEFAULT_LOW_HOLD_BARS,
) -> Tuple[bool, str]:
    """당일 저점 지지: 저가 미이탈 + 최근 N봉 저가가 붕괴하지 않음."""
    try:
        px = float(current_price)
    except (TypeError, ValueError):
        return False, "현재가 없음"
    if px <= 0:
        return False, "현재가 없음"
    lows: List[float] = []
    for b in bars or []:
        try:
            lo = float(b.get("low") if b.get("low") is not None else b.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if lo > 0:
            lows.append(lo)
    if not lows:
        return False, "분봉 저가 없음"
    day_low = min(lows)
    if px < day_low * 0.998:
        return False, f"당일저가 이탈(px={px:.0f} low={day_low:.0f})"
    n = max(2, int(lookback or DEFAULT_LOW_HOLD_BARS))
    recent = lows[-n:]
    older = lows[-(n * 2):-n] if len(lows) >= n * 2 else lows[:-n] or recent
    recent_low = min(recent)
    older_low = min(older) if older else recent_low
    # 최근 저가가 직전 구간보다 크게 깨지면 지지 실패
    if recent_low < older_low * 0.995:
        return False, f"저점 붕괴(recent={recent_low:.0f} prev={older_low:.0f})"
    return True, f"저점지지(day_low={day_low:.0f} recent={recent_low:.0f})"


def ensure_leg_state(state: Dict[str, Any]) -> Dict[str, Any]:
    legs = state.get("legs")
    if not isinstance(legs, dict):
        legs = {}
    for i in (1, 2, 3):
        key = str(i)
        if key not in legs or not isinstance(legs[key], dict):
            legs[key] = {"done": False, "skipped": False, "reason": None}
    state["legs"] = legs
    return state


def mark_leg(
    state: Dict[str, Any],
    leg: int,
    *,
    done: bool = False,
    skipped: bool = False,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    ensure_leg_state(state)
    entry = state["legs"][str(int(leg))]
    entry["done"] = bool(done)
    entry["skipped"] = bool(skipped)
    if reason is not None:
        entry["reason"] = reason
    entry["at"] = utc_now_naive().isoformat()
    save_jongga_state(state)
    return state
