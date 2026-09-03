"""장세(시장 지수) 악화·급등 시 전략별 신규 매수 제한."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from utils.market_indices import fetch_market_indices

logger = logging.getLogger(__name__)

_STRATEGY_LABEL = {
    "legacy": "레거시",
    "sangtta": "상따",
    "breakout": "돌파",
    "jongga": "종가배팅",
    "fractal": "프랙탈 스캘핑",
}


def normalize_strategy_key(strategy: Optional[str]) -> str:
    key = (strategy or "legacy").strip().lower()
    if key == "sangtta":
        return "sangtta"
    if key == "breakout":
        return "breakout"
    if key in ("jongga", "jongga_closing", "closing_bet"):
        return "jongga"
    if key in ("fractal", "ema_fractal_pullback"):
        return "fractal"
    if key in ("ma1592", "ma1592_hold", "ma1590", "ma1590_hold"):
        return "ma1592"
    return "legacy"


def _as_bool(settings: Any, name: str, default: bool = False) -> bool:
    raw = getattr(settings, name, None) if settings is not None else None
    if raw is None:
        return default
    return bool(raw)


def _as_float(settings: Any, name: str, default: float) -> float:
    raw = getattr(settings, name, None) if settings is not None else None
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _as_int(settings: Any, name: str, default: int) -> int:
    raw = getattr(settings, name, None) if settings is not None else None
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _index_mode(
    settings: Any,
    attr: str = "market_risk_index",
    default: str = "kospi",
) -> str:
    raw = str(getattr(settings, attr, None) or default).strip().lower()
    if raw in ("kospi", "kosdaq", "either", "both", "per_market"):
        return raw
    return default


def _pick_index(indices: List[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    for row in indices or []:
        if str(row.get("key") or "").lower() == key:
            return row
    return None


_STOCK_MARKET_CACHE: Dict[str, Optional[str]] = {}
_MARKET_LABEL = {"kospi": "코스피", "kosdaq": "코스닥"}


def normalize_stock_market(raw: Any) -> Optional[str]:
    """종목 시장 → kospi|kosdaq. 미상은 None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    low = s.lower()
    if low in ("kospi", "001"):
        return "kospi"
    if low in ("kosdaq", "101"):
        return "kosdaq"
    up = s.upper()
    if up == "KOSPI":
        return "kospi"
    if up == "KOSDAQ":
        return "kosdaq"
    return None


def _normalize_stock_code(stock_code: Optional[str]) -> str:
    code = str(stock_code or "").strip()
    if not code or code.startswith("SAMPLE_"):
        return ""
    digits = "".join(c for c in code if c.isdigit())
    if not digits:
        return code
    return digits[-6:].zfill(6) if len(digits) >= 6 else digits.zfill(6)


def resolve_stock_market(
    stock_code: Optional[str] = None,
    *,
    stock_market: Optional[str] = None,
    session: Any = None,
    cache: Optional[Dict[str, Optional[str]]] = None,
) -> Optional[str]:
    """종목코드 → kospi|kosdaq. Fundamental/Technical 마트 조회, 없으면 None."""
    direct = normalize_stock_market(stock_market)
    if direct:
        return direct
    code = _normalize_stock_code(stock_code)
    if not code:
        return None
    store = cache if cache is not None else _STOCK_MARKET_CACHE
    if code in store:
        return store[code]
    market: Optional[str] = None
    try:
        if session is not None:
            from core.models import FundamentalSnapshot, TechnicalSnapshot

            row = (
                session.query(FundamentalSnapshot.market)
                .filter(FundamentalSnapshot.stock_code == code)
                .order_by(FundamentalSnapshot.as_of_date.desc())
                .first()
            )
            if row is not None:
                market = normalize_stock_market(row[0])
            if market is None:
                row = (
                    session.query(TechnicalSnapshot.market)
                    .filter(TechnicalSnapshot.stock_code == code)
                    .order_by(TechnicalSnapshot.as_of_date.desc())
                    .first()
                )
                if row is not None:
                    market = normalize_stock_market(row[0])
        if market is None:
            from utils.fundamental_mart_store import get_latest_by_code

            snap = get_latest_by_code(code)
            if snap:
                market = normalize_stock_market(snap.get("market"))
    except Exception as exc:
        logger.warning("종목 시장 조회 실패 %s: %s", code, exc)
        market = None
    store[code] = market
    return market


def clear_stock_market_cache() -> None:
    _STOCK_MARKET_CACHE.clear()


def max_buys_when_bad(settings: Any) -> int:
    """장세 나쁠 때 전략당 금일 신규매수 상한. 0이면 전면 차단."""
    return max(0, _as_int(settings, "market_risk_max_buys_per_strategy", 2))


def max_buys_when_surge(settings: Any) -> int:
    """급등장일 때 전략당 금일 신규매수 상한. 0이면 전면 차단."""
    return max(0, _as_int(settings, "market_surge_max_buys_per_strategy", 0))


def pullback_from_high_pp(
    day_high: Optional[float],
    current_price: Optional[float],
    prev_close: Optional[float],
) -> Optional[float]:
    """당일 고점 대비 눌림 폭(%p, 전일종가 기준)."""
    try:
        high = float(day_high or 0)
        px = float(current_price or 0)
        pc = float(prev_close or 0)
    except (TypeError, ValueError):
        return None
    if high <= 0 or px <= 0 or pc <= 0:
        return None
    if high < px:
        return 0.0
    return (high - px) / pc * 100.0


def _crash_index_pct(risk: Dict[str, Any]) -> Tuple[Optional[float], str]:
    """급락 판정에 쓸 지수 등락과 라벨."""
    mode = str(risk.get("index_mode") or "kospi")
    kospi_pct = risk.get("kospi_pct")
    kosdaq_pct = risk.get("kosdaq_pct")
    if mode == "kosdaq":
        return (
            float(kosdaq_pct) if kosdaq_pct is not None else None,
            "코스닥",
        )
    if mode == "either":
        cands: List[Tuple[float, str]] = []
        if kospi_pct is not None:
            cands.append((float(kospi_pct), "코스피"))
        if kosdaq_pct is not None:
            cands.append((float(kosdaq_pct), "코스닥"))
        if not cands:
            return None, ""
        pct, label = min(cands, key=lambda x: x[0])
        return pct, label
    if mode == "both":
        if kospi_pct is None or kosdaq_pct is None:
            return None, ""
        kospif, kosdaqf = float(kospi_pct), float(kosdaq_pct)
        if kospif <= kosdaqf:
            return kospif, "코스피"
        return kosdaqf, "코스닥"
    return (
        float(kospi_pct) if kospi_pct is not None else None,
        "코스피",
    )


def check_crash_sync_pullback(
    settings: Any,
    *,
    current_price: Optional[float] = None,
    day_high: Optional[float] = None,
    prev_close: Optional[float] = None,
    eval_cache: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """고점 눌림 상한 + 급락장 지수연동 눌림 차단.

    장세 횟수 한도(market_risk)와 별개. 고점/전일종가 없으면 통과.
    """
    if settings is None or not _as_bool(settings, "crash_sync_block_enabled", True):
        return True, ""

    crash = _as_float(settings, "crash_sync_index_pct", -1.5)
    err_max = max(0.0, _as_float(settings, "crash_sync_error_pct", 0.5))
    cap = max(0.0, _as_float(settings, "crash_sync_pullback_cap_pct", 2.0))

    pb = pullback_from_high_pp(day_high, current_price, prev_close)
    if pb is None:
        return True, ""
    if cap > 0 and pb >= cap:
        return False, (
            f"고점대비 눌림 과다 ({pb:.2f}%p ≥ {cap:g}%p)"
        )

    if eval_cache is not None and eval_cache.get("kospi_pct") is not None:
        risk = eval_cache
    else:
        risk = evaluate_market_risk(settings, force_refresh=False)
        if risk.get("kospi_pct") is None and risk.get("kosdaq_pct") is None:
            try:
                payload = fetch_market_indices(force=False)
                indices = payload.get("indices") or []
                kospi = _pick_index(indices, "kospi")
                kosdaq = _pick_index(indices, "kosdaq")
                risk = dict(risk)
                risk["index_mode"] = _index_mode(settings)
                risk["kospi_pct"] = kospi.get("change_pct") if kospi else None
                risk["kosdaq_pct"] = kosdaq.get("change_pct") if kosdaq else None
            except Exception as exc:
                logger.warning("급락 연동 지수 조회 실패 — 스킵: %s", exc)
                return True, ""
        if eval_cache is not None:
            eval_cache.clear()
            eval_cache.update(risk)

    idx_pct, idx_label = _crash_index_pct(risk)
    if idx_pct is None:
        return True, ""
    if float(idx_pct) > float(crash):
        return True, ""
    if str(risk.get("index_mode") or "") == "both":
        kp, kq = risk.get("kospi_pct"), risk.get("kosdaq_pct")
        if kp is None or kq is None:
            return True, ""
        if float(kp) > float(crash) or float(kq) > float(crash):
            return True, ""

    index_drop = -float(idx_pct)
    err = abs(pb - index_drop)
    if err > err_max:
        return True, ""
    return False, (
        f"급락장 지수연동 눌림 "
        f"(고점대비 {pb:.2f}%p · {idx_label} {float(idx_pct):+.2f}% · "
        f"오차 {err:.2f}%p ≤ {err_max:g})"
    )


def _collect_index_triggers(
    mode: str,
    kospi_pct: Optional[float],
    kosdaq_pct: Optional[float],
    is_hit,
) -> Tuple[List[str], str]:
    """지수 등락이 조건에 맞으면 라벨 목록. 판단 불가면 skip_reason.

    per_market·either: 각 지수를 독립 판정(하나라도 맞으면 트리거).
    """
    triggers: List[str] = []
    if mode == "kospi":
        if kospi_pct is None:
            return [], "코스피 등락 없음"
        if is_hit(kospi_pct):
            triggers.append(f"코스피 {float(kospi_pct):+.2f}%")
        return triggers, ""
    if mode == "kosdaq":
        if kosdaq_pct is None:
            return [], "코스닥 등락 없음"
        if is_hit(kosdaq_pct):
            triggers.append(f"코스닥 {float(kosdaq_pct):+.2f}%")
        return triggers, ""
    if mode == "both":
        if kospi_pct is None or kosdaq_pct is None:
            return [], "코스피/코스닥 등락 부족"
        if is_hit(kospi_pct) and is_hit(kosdaq_pct):
            triggers.append(f"코스피 {float(kospi_pct):+.2f}%")
            triggers.append(f"코스닥 {float(kosdaq_pct):+.2f}%")
        return triggers, ""
    # either | per_market
    if kospi_pct is None and kosdaq_pct is None:
        return [], "지수 등락 없음"
    if is_hit(kospi_pct):
        triggers.append(f"코스피 {float(kospi_pct):+.2f}%")
    if is_hit(kosdaq_pct):
        triggers.append(f"코스닥 {float(kosdaq_pct):+.2f}%")
    return triggers, ""


def _index_status_bits(kospi_pct: Optional[float], kosdaq_pct: Optional[float]) -> str:
    bits = []
    if kospi_pct is not None:
        bits.append(f"코스피 {float(kospi_pct):+.2f}%")
    if kosdaq_pct is not None:
        bits.append(f"코스닥 {float(kosdaq_pct):+.2f}%")
    return f" ({' · '.join(bits)})" if bits else ""


def evaluate_market_risk(
    settings: Any,
    *,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """현재 장세가 '나쁨'(급락) 또는 '급등'인지 판정."""
    enabled = _as_bool(settings, "market_risk_enabled", False)
    surge_enabled = _as_bool(settings, "market_surge_enabled", True)
    threshold = _as_float(settings, "market_risk_change_pct", -2.0)
    surge_threshold = _as_float(settings, "market_surge_change_pct", 3.0)
    mode = _index_mode(settings)
    surge_mode = _index_mode(settings, "market_surge_index", "either")
    out: Dict[str, Any] = {
        "enabled": enabled,
        "is_bad": False,
        "reason": "장세 게이트 OFF" if not enabled else "",
        "threshold": threshold,
        "index_mode": mode,
        "kospi_pct": None,
        "kosdaq_pct": None,
        "kospi_bad": False,
        "kosdaq_bad": False,
        "trigger_labels": [],
        "max_buys_per_strategy": max_buys_when_bad(settings),
        "surge_enabled": surge_enabled,
        "is_surge": False,
        "surge_reason": "급등장 게이트 OFF" if not surge_enabled else "",
        "surge_threshold": surge_threshold,
        "surge_index_mode": surge_mode,
        "kospi_surge": False,
        "kosdaq_surge": False,
        "surge_trigger_labels": [],
        "max_buys_when_surge": max_buys_when_surge(settings),
    }
    if not enabled and not surge_enabled:
        return out

    try:
        payload = fetch_market_indices(force=force_refresh)
    except Exception as exc:
        logger.warning("장세 지수 조회 실패 — 매수 제한 스킵: %s", exc)
        fail = f"지수 조회 실패({exc})"
        if enabled:
            out["reason"] = fail
        if surge_enabled:
            out["surge_reason"] = fail
        return out

    indices = payload.get("indices") or []
    kospi = _pick_index(indices, "kospi")
    kosdaq = _pick_index(indices, "kosdaq")
    kospi_pct = kospi.get("change_pct") if kospi else None
    kosdaq_pct = kosdaq.get("change_pct") if kosdaq else None
    out["kospi_pct"] = kospi_pct
    out["kosdaq_pct"] = kosdaq_pct
    status_bits = _index_status_bits(kospi_pct, kosdaq_pct)

    if enabled:
        out["kospi_bad"] = (
            kospi_pct is not None and float(kospi_pct) <= float(threshold)
        )
        out["kosdaq_bad"] = (
            kosdaq_pct is not None and float(kosdaq_pct) <= float(threshold)
        )
        triggers, skip = _collect_index_triggers(
            mode, kospi_pct, kosdaq_pct,
            lambda pct: pct is not None and float(pct) <= float(threshold),
        )
        if skip:
            out["reason"] = skip
        else:
            out["trigger_labels"] = triggers
            out["is_bad"] = bool(triggers)
            cap = out["max_buys_per_strategy"]
            if out["is_bad"]:
                scope = (
                    " · 시장별 한도"
                    if mode == "per_market"
                    else f" · 전략당 금일 {cap}회"
                )
                out["reason"] = (
                    f"장세 악화(≤{threshold:g}%): " + " · ".join(triggers) + scope
                )
            else:
                out["reason"] = "장세 정상" + status_bits

    if surge_enabled:
        out["kospi_surge"] = (
            kospi_pct is not None and float(kospi_pct) >= float(surge_threshold)
        )
        out["kosdaq_surge"] = (
            kosdaq_pct is not None and float(kosdaq_pct) >= float(surge_threshold)
        )
        surge_triggers, surge_skip = _collect_index_triggers(
            surge_mode, kospi_pct, kosdaq_pct,
            lambda pct: pct is not None and float(pct) >= float(surge_threshold),
        )
        if surge_skip:
            out["surge_reason"] = surge_skip
        else:
            out["surge_trigger_labels"] = surge_triggers
            out["is_surge"] = bool(surge_triggers)
            surge_cap = out["max_buys_when_surge"]
            if out["is_surge"]:
                scope = (
                    " · 시장별 한도"
                    if surge_mode == "per_market"
                    else f" · 전략당 금일 {surge_cap}회"
                )
                out["surge_reason"] = (
                    f"급등장(≥{surge_threshold:g}%): "
                    + " · ".join(surge_triggers)
                    + scope
                )
            else:
                out["surge_reason"] = "급등장 아님" + status_bits
    return out


def strategy_limited_when_bad(settings: Any, strategy: Optional[str]) -> bool:
    """장세 나쁠 때 해당 전략에 횟수 제한을 적용할지."""
    key = normalize_strategy_key(strategy)
    if key == "sangtta":
        return _as_bool(settings, "market_risk_block_sangtta", True)
    if key == "breakout":
        return _as_bool(settings, "market_risk_block_breakout", False)
    if key == "jongga":
        return _as_bool(settings, "market_risk_block_jongga", False)
    if key == "fractal":
        return _as_bool(settings, "market_risk_block_fractal", True)
    if key == "ma1592":
        return _as_bool(settings, "market_risk_block_ma1592", True)
    return _as_bool(settings, "market_risk_block_legacy", True)


def strategy_limited_when_surge(settings: Any, strategy: Optional[str]) -> bool:
    """급등장일 때 해당 전략에 횟수 제한을 적용할지. 기본은 전 전략 적용."""
    key = normalize_strategy_key(strategy)
    attr = {
        "sangtta": "market_surge_block_sangtta",
        "breakout": "market_surge_block_breakout",
        "jongga": "market_surge_block_jongga",
        "fractal": "market_surge_block_fractal",
        "ma1592": "market_surge_block_ma1592",
    }.get(key, "market_surge_block_legacy")
    return _as_bool(settings, attr, True)


# 하위 호환 별칭
strategy_blocked_when_bad = strategy_limited_when_bad


def count_strategy_new_buys_today(
    session: Any,
    strategy: Optional[str],
    *,
    stock_market: Optional[str] = None,
) -> int:
    """금일 해당 전략 신규매수 건수 (체결 포지션 + 대기 신호, 추가매수 제외).

    stock_market이 kospi|kosdaq이면 해당 시장 종목만 집계.
    시장 미상 종목은 시장별 집계에서 제외.
    """
    from core.models import PendingBuySignal, Position
    from utils.auto_trade_engine import parse_signal_meta
    from utils.datetime_kst import kst_day_end_utc_naive_exclusive, kst_day_start_utc_naive

    key = normalize_strategy_key(strategy)
    market_key = normalize_stock_market(stock_market)
    start = kst_day_start_utc_naive()
    end = kst_day_end_utc_naive_exclusive()

    counted_codes = set()
    n = 0

    def _market_ok(code: str) -> bool:
        if not market_key:
            return True
        return resolve_stock_market(code, session=session) == market_key

    positions = (
        session.query(Position)
        .filter(Position.buy_time >= start, Position.buy_time < end)
        .all()
    )
    for pos in positions:
        code = str(pos.stock_code or "")
        if code.startswith("SAMPLE_"):
            continue
        if normalize_strategy_key(pos.strategy_key) != key:
            continue
        if not _market_ok(code):
            continue
        counted_codes.add(code)
        n += 1

    signals = (
        session.query(PendingBuySignal)
        .filter(
            PendingBuySignal.detected_at >= start,
            PendingBuySignal.detected_at < end,
            PendingBuySignal.status.in_(["PENDING", "PROCESSING", "ORDERED"]),
        )
        .all()
    )
    for sig in signals:
        meta = parse_signal_meta(sig)
        if meta.get("is_add_buy"):
            continue
        if normalize_strategy_key(meta.get("strategy")) != key:
            continue
        code = str(sig.stock_code or "")
        if code in counted_codes:
            continue
        if not _market_ok(code):
            continue
        counted_codes.add(code)
        n += 1

    return n


def check_market_risk_buy_allowed(
    settings: Any,
    strategy: Optional[str],
    *,
    force_refresh: bool = False,
    eval_cache: Optional[Dict[str, Any]] = None,
    session: Any = None,
    used_today: Optional[int] = None,
    stock_code: Optional[str] = None,
    stock_market: Optional[str] = None,
) -> Tuple[bool, str]:
    """신규 매수 허용 여부. (ok, reason) — ok=False면 차단.

    급등장(기본 ≥+3%)이면 전략당 금일 매수 상한(기본 0=전면)까지.
    장세가 나쁘면 전략당 금일 매수 상한(기본 2회)까지만 허용.

    index 모드가 per_market이면 해당 시장 지수·해당 시장 매수 횟수만 적용.
    시장 미상이면 허용하고 로그를 남긴다.
    """
    if settings is None:
        return True, ""

    crash_on = _as_bool(settings, "market_risk_enabled", False)
    surge_on = _as_bool(settings, "market_surge_enabled", True)
    if not crash_on and not surge_on:
        return True, ""

    key = normalize_strategy_key(strategy)
    need_crash = crash_on and strategy_limited_when_bad(settings, key)
    need_surge = surge_on and strategy_limited_when_surge(settings, key)
    if not need_crash and not need_surge:
        return True, ""

    if eval_cache is not None and "is_bad" in eval_cache:
        risk = eval_cache
    else:
        risk = evaluate_market_risk(settings, force_refresh=force_refresh)
        if eval_cache is not None:
            eval_cache.clear()
            eval_cache.update(risk)

    crash_mode = str(risk.get("index_mode") or _index_mode(settings))
    surge_mode = str(
        risk.get("surge_index_mode")
        or _index_mode(settings, "market_surge_index", "either")
    )
    need_stock_market = (
        (need_crash and crash_mode == "per_market")
        or (need_surge and surge_mode == "per_market")
    )
    stock_mkt = normalize_stock_market(stock_market)
    if need_stock_market and not stock_mkt:
        stock_mkt = resolve_stock_market(stock_code, session=session)

    used_by_market: Dict[str, int] = {}

    def _used(for_market: Optional[str] = None) -> int:
        cache_key = for_market or ""
        if cache_key in used_by_market:
            return used_by_market[cache_key]
        if used_today is not None:
            n = int(used_today or 0)
        elif session is not None:
            n = count_strategy_new_buys_today(
                session, key, stock_market=for_market,
            )
        else:
            n = 0
        used_by_market[cache_key] = n
        return n

    label = _STRATEGY_LABEL.get(key, key)
    mkt_label = _MARKET_LABEL.get(stock_mkt or "", "")

    if need_surge:
        surge_hit = False
        risk_txt = risk.get("surge_reason") or "급등장"
        count_market: Optional[str] = None
        if surge_mode == "per_market":
            if not stock_mkt:
                logger.info(
                    "급등장 게이트: 종목 시장 미상 → 한도 스킵 (code=%s)",
                    stock_code or "?",
                )
            elif stock_mkt == "kospi" and risk.get("kospi_surge"):
                surge_hit = True
                count_market = "kospi"
                pct = risk.get("kospi_pct")
                risk_txt = (
                    f"코스피 급등({float(pct):+.2f}%)"
                    if pct is not None
                    else "코스피 급등"
                )
            elif stock_mkt == "kosdaq" and risk.get("kosdaq_surge"):
                surge_hit = True
                count_market = "kosdaq"
                pct = risk.get("kosdaq_pct")
                risk_txt = (
                    f"코스닥 급등({float(pct):+.2f}%)"
                    if pct is not None
                    else "코스닥 급등"
                )
        elif risk.get("is_surge"):
            surge_hit = True

        if surge_hit:
            cap = max_buys_when_surge(settings)
            n = _used(count_market)
            scope = f"{mkt_label} " if count_market and mkt_label else ""
            if n >= cap:
                if cap <= 0:
                    return False, f"{label} {scope}매수 제한(전면) — {risk_txt}"
                return False, f"{label} {scope}급등 매수 한도 {n}/{cap} — {risk_txt}"

    if need_crash:
        crash_hit = False
        risk_txt = risk.get("reason") or "장세 악화"
        count_market = None
        if crash_mode == "per_market":
            if not stock_mkt:
                logger.info(
                    "장세 게이트: 종목 시장 미상 → 한도 스킵 (code=%s)",
                    stock_code or "?",
                )
            elif stock_mkt == "kospi" and risk.get("kospi_bad"):
                crash_hit = True
                count_market = "kospi"
                pct = risk.get("kospi_pct")
                risk_txt = (
                    f"코스피 장세 악화({float(pct):+.2f}%)"
                    if pct is not None
                    else "코스피 장세 악화"
                )
            elif stock_mkt == "kosdaq" and risk.get("kosdaq_bad"):
                crash_hit = True
                count_market = "kosdaq"
                pct = risk.get("kosdaq_pct")
                risk_txt = (
                    f"코스닥 장세 악화({float(pct):+.2f}%)"
                    if pct is not None
                    else "코스닥 장세 악화"
                )
        elif risk.get("is_bad"):
            crash_hit = True

        if crash_hit:
            cap = max_buys_when_bad(settings)
            n = _used(count_market)
            scope = f"{mkt_label} " if count_market and mkt_label else ""
            if n >= cap:
                if cap <= 0:
                    return False, f"{label} {scope}매수 제한(전면) — {risk_txt}"
                return False, f"{label} {scope}장세 매수 한도 {n}/{cap} — {risk_txt}"
    return True, ""
