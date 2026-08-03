"""장세(시장 지수) 악화 시 전략별 신규 매수 제한."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from utils.market_indices import fetch_market_indices

logger = logging.getLogger(__name__)

_STRATEGY_LABEL = {
    "legacy": "레거시",
    "sangtta": "상따",
    "breakout": "돌파",
    "ymgp": "역매공파",
    "jongga": "종가배팅",
}


def normalize_strategy_key(strategy: Optional[str]) -> str:
    key = (strategy or "legacy").strip().lower()
    if key == "sangtta":
        return "sangtta"
    if key == "breakout":
        return "breakout"
    if key in ("ymgp", "yeokmaegongpa"):
        return "ymgp"
    if key in ("jongga", "jongga_closing", "closing_bet"):
        return "jongga"
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


def _index_mode(settings: Any) -> str:
    raw = str(getattr(settings, "market_risk_index", None) or "kospi").strip().lower()
    if raw in ("kospi", "kosdaq", "either", "both"):
        return raw
    return "kospi"


def _pick_index(indices: List[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    for row in indices or []:
        if str(row.get("key") or "").lower() == key:
            return row
    return None


def max_buys_when_bad(settings: Any) -> int:
    """장세 나쁠 때 전략당 금일 신규매수 상한. 0이면 전면 차단."""
    return max(0, _as_int(settings, "market_risk_max_buys_per_strategy", 2))


def evaluate_market_risk(
    settings: Any,
    *,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """현재 장세가 '나쁨'인지 판정."""
    enabled = _as_bool(settings, "market_risk_enabled", False)
    threshold = _as_float(settings, "market_risk_change_pct", -2.0)
    mode = _index_mode(settings)
    out: Dict[str, Any] = {
        "enabled": enabled,
        "is_bad": False,
        "reason": "",
        "threshold": threshold,
        "index_mode": mode,
        "kospi_pct": None,
        "kosdaq_pct": None,
        "trigger_labels": [],
        "max_buys_per_strategy": max_buys_when_bad(settings),
    }
    if not enabled:
        out["reason"] = "장세 게이트 OFF"
        return out

    try:
        payload = fetch_market_indices(force=force_refresh)
    except Exception as exc:
        logger.warning("장세 지수 조회 실패 — 매수 제한 스킵: %s", exc)
        out["reason"] = f"지수 조회 실패({exc})"
        return out

    indices = payload.get("indices") or []
    kospi = _pick_index(indices, "kospi")
    kosdaq = _pick_index(indices, "kosdaq")
    kospi_pct = kospi.get("change_pct") if kospi else None
    kosdaq_pct = kosdaq.get("change_pct") if kosdaq else None
    out["kospi_pct"] = kospi_pct
    out["kosdaq_pct"] = kosdaq_pct

    def _is_bad_pct(pct: Optional[float]) -> bool:
        return pct is not None and float(pct) <= float(threshold)

    triggers: List[str] = []
    if mode == "kospi":
        if kospi_pct is None:
            out["reason"] = "코스피 등락 없음"
            return out
        if _is_bad_pct(kospi_pct):
            triggers.append(f"코스피 {kospi_pct:+.2f}%")
    elif mode == "kosdaq":
        if kosdaq_pct is None:
            out["reason"] = "코스닥 등락 없음"
            return out
        if _is_bad_pct(kosdaq_pct):
            triggers.append(f"코스닥 {kosdaq_pct:+.2f}%")
    elif mode == "both":
        if kospi_pct is None or kosdaq_pct is None:
            out["reason"] = "코스피/코스닥 등락 부족"
            return out
        if _is_bad_pct(kospi_pct) and _is_bad_pct(kosdaq_pct):
            triggers.append(f"코스피 {kospi_pct:+.2f}%")
            triggers.append(f"코스닥 {kosdaq_pct:+.2f}%")
    else:  # either
        if kospi_pct is None and kosdaq_pct is None:
            out["reason"] = "지수 등락 없음"
            return out
        if _is_bad_pct(kospi_pct):
            triggers.append(f"코스피 {kospi_pct:+.2f}%")
        if _is_bad_pct(kosdaq_pct):
            triggers.append(f"코스닥 {kosdaq_pct:+.2f}%")

    out["trigger_labels"] = triggers
    out["is_bad"] = bool(triggers)
    cap = out["max_buys_per_strategy"]
    if out["is_bad"]:
        out["reason"] = (
            f"장세 악화(≤{threshold:g}%): " + " · ".join(triggers)
            + f" · 전략당 금일 {cap}회"
        )
    else:
        bits = []
        if kospi_pct is not None:
            bits.append(f"코스피 {kospi_pct:+.2f}%")
        if kosdaq_pct is not None:
            bits.append(f"코스닥 {kosdaq_pct:+.2f}%")
        out["reason"] = "장세 정상" + (f" ({' · '.join(bits)})" if bits else "")
    return out


def strategy_limited_when_bad(settings: Any, strategy: Optional[str]) -> bool:
    """장세 나쁠 때 해당 전략에 횟수 제한을 적용할지."""
    key = normalize_strategy_key(strategy)
    if key == "sangtta":
        return _as_bool(settings, "market_risk_block_sangtta", True)
    if key == "breakout":
        return _as_bool(settings, "market_risk_block_breakout", False)
    if key == "ymgp":
        return _as_bool(settings, "market_risk_block_ymgp", False)
    if key == "jongga":
        return _as_bool(settings, "market_risk_block_jongga", False)
    return _as_bool(settings, "market_risk_block_legacy", True)


# 하위 호환 별칭
strategy_blocked_when_bad = strategy_limited_when_bad


def count_strategy_new_buys_today(session: Any, strategy: Optional[str]) -> int:
    """금일 해당 전략 신규매수 건수 (체결 포지션 + 대기 신호, 추가매수 제외)."""
    from core.models import PendingBuySignal, Position
    from utils.auto_trade_engine import parse_signal_meta
    from utils.datetime_kst import kst_day_end_utc_naive_exclusive, kst_day_start_utc_naive

    key = normalize_strategy_key(strategy)
    start = kst_day_start_utc_naive()
    end = kst_day_end_utc_naive_exclusive()

    counted_codes = set()
    n = 0

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
) -> Tuple[bool, str]:
    """신규 매수 허용 여부. (ok, reason) — ok=False면 차단.

    장세가 나쁘면 전략당 금일 매수 상한(기본 2회)까지만 허용.
    """
    if settings is None or not _as_bool(settings, "market_risk_enabled", False):
        return True, ""

    key = normalize_strategy_key(strategy)
    if not strategy_limited_when_bad(settings, key):
        return True, ""

    if eval_cache is not None and "is_bad" in eval_cache:
        risk = eval_cache
    else:
        risk = evaluate_market_risk(settings, force_refresh=force_refresh)
        if eval_cache is not None:
            eval_cache.clear()
            eval_cache.update(risk)

    if not risk.get("is_bad"):
        return True, ""

    cap = max_buys_when_bad(settings)
    label = _STRATEGY_LABEL.get(key, key)
    risk_txt = risk.get("reason") or "장세 악화"

    if used_today is None:
        if session is not None:
            used_today = count_strategy_new_buys_today(session, key)
        else:
            used_today = 0
    used = int(used_today or 0)

    if used >= cap:
        if cap <= 0:
            return False, f"{label} 매수 제한(전면) — {risk_txt}"
        return False, f"{label} 장세 매수 한도 {used}/{cap} — {risk_txt}"
    return True, ""
