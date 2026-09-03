"""5분봉 EMA 이탈 SOFT 청산 (거래대금 레거시 · 수급 돌파 · 상따 공통).

가격이 설정 기간(기본 90) 5분 EMA 대비 허용 이격(기본 1%)을 넘어 내려간 뒤,
당일·매수 이후 5분봉 종가가 연속 N분(기본 10, 2개 봉) 동안 그 선을 회복하지 못하면 청산합니다.
현재가가 허용 이격 안으로 돌아오면 연속 카운트는 리셋됩니다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Sequence, Tuple

from utils.datetime_kst import as_kst
from utils.ema_fractal import ema_series
from utils.position_peak_since_buy import parse_bar_end_kst

DEFAULT_EMA_PERIOD = 90
DEFAULT_SOFT_MINUTES = 10
DEFAULT_BAND_PCT = 1.0
BAR_MINUTES = 5


def _as_int(settings: Any, name: str, default: int) -> int:
    if settings is None:
        return default
    raw = settings.get(name) if isinstance(settings, dict) else getattr(settings, name, None)
    try:
        if raw is None or raw == "":
            return default
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _as_float(settings: Any, name: str, default: float) -> float:
    if settings is None:
        return default
    raw = settings.get(name) if isinstance(settings, dict) else getattr(settings, name, None)
    try:
        if raw is None or raw == "":
            return default
        return float(raw)
    except (TypeError, ValueError):
        return default


def ema_break_line(ema: float, band_pct: float) -> float:
    """이탈 판정선 = EMA × (1 − 허용이격%). 이격 0이면 EMA 자체."""
    b = max(0.0, abs(float(band_pct or 0)))
    return float(ema) * (1.0 - b / 100.0)


def price_breaks_ema(price: float, ema: float, band_pct: float) -> bool:
    """허용 이격을 넘어 EMA 아래로 빠진 경우만 True. 정확히 이격%는 허용."""
    if ema <= 0 or price <= 0:
        return False
    return float(price) < ema_break_line(ema, band_pct)


def legacy_ema_exit_enabled(settings: Any) -> bool:
    if settings is None:
        return True
    raw = (
        settings.get("legacy_ema_exit_enabled")
        if isinstance(settings, dict)
        else getattr(settings, "legacy_ema_exit_enabled", None)
    )
    if raw is None:
        return True
    return bool(raw)


def legacy_ema_exit_params(settings: Any) -> Tuple[bool, int, int, float]:
    """(사용여부, EMA기간, SOFT 분, 허용 이격%)."""
    period = _as_int(settings, "legacy_ema_exit_period", DEFAULT_EMA_PERIOD)
    soft = _as_int(settings, "legacy_ema_exit_soft_min", DEFAULT_SOFT_MINUTES)
    band = _as_float(settings, "legacy_ema_exit_band_pct", DEFAULT_BAND_PCT)
    period = max(5, min(300, period if period > 0 else DEFAULT_EMA_PERIOD))
    soft = max(1, min(60, soft if soft > 0 else DEFAULT_SOFT_MINUTES))
    band = max(0.0, min(10.0, band))
    return legacy_ema_exit_enabled(settings), period, soft, band


def _empty(
    *,
    period: int,
    soft_minutes: int,
    band_pct: float = DEFAULT_BAND_PCT,
    reason: str,
    ema: Optional[float] = None,
    below: bool = False,
    consecutive: int = 0,
    triggered: bool = False,
    detail: str = "",
) -> Dict[str, Any]:
    return {
        "ok": reason == "ok",
        "triggered": triggered,
        "below": below,
        "ema": ema,
        "period": period,
        "consecutive": consecutive,
        "soft_minutes": soft_minutes,
        "band_pct": band_pct,
        "reason": reason,
        "detail": detail,
    }


def evaluate_legacy_ema_soft_exit(
    bars: Sequence[Dict[str, Any]],
    current_price: float,
    *,
    now: Optional[datetime] = None,
    period: int = DEFAULT_EMA_PERIOD,
    soft_minutes: int = DEFAULT_SOFT_MINUTES,
    band_pct: float = DEFAULT_BAND_PCT,
    buy_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """5분봉 종가 기준 EMA 이탈 SOFT 판정.

    - EMA는 전체 시계열로 계산(전일 봉 포함).
    - 이탈은 EMA 대비 허용 이격(기본 1%)을 **넘는** 하락만 인정한다.
    - 연속 시간은 **당일·매수시각 이후** 확정 5분봉만 센다.
    - 현재가가 허용 이격 안이면 이탈이 아니므로 카운트를 리셋한다.
    """
    period = max(5, min(300, int(period or DEFAULT_EMA_PERIOD)))
    soft_minutes = max(1, min(60, int(soft_minutes or DEFAULT_SOFT_MINUTES)))
    band_pct = max(0.0, min(10.0, float(band_pct if band_pct is not None else DEFAULT_BAND_PCT)))
    px = float(current_price or 0)
    if px <= 0:
        return _empty(
            period=period, soft_minutes=soft_minutes, band_pct=band_pct, reason="현재가 없음",
        )

    rows = list(bars or [])
    closes = [float(b.get("close") or 0) for b in rows]
    if len(closes) < period:
        return _empty(
            period=period,
            soft_minutes=soft_minutes,
            band_pct=band_pct,
            reason=f"5분봉 부족({len(closes)}/{period})",
        )

    series = ema_series(closes, period)
    latest_ema: Optional[float] = None
    for val in reversed(series):
        if val is not None:
            latest_ema = float(val)
            break
    if latest_ema is None or latest_ema <= 0:
        return _empty(
            period=period, soft_minutes=soft_minutes, band_pct=band_pct, reason="EMA 미산출",
        )

    line = ema_break_line(latest_ema, band_pct)
    below = price_breaks_ema(px, latest_ema, band_pct)
    if not below:
        return _empty(
            period=period,
            soft_minutes=soft_minutes,
            band_pct=band_pct,
            ema=latest_ema,
            below=False,
            consecutive=0,
            reason="ok",
            detail=(
                f"현재 {int(px):,} ≥ 이탈선 {line:,.0f} "
                f"(EMA{period} {latest_ema:,.0f} · 이격 {band_pct:g}%)"
            ),
        )

    now_kst = as_kst(now)
    today = now_kst.date()
    buy_kst = as_kst(buy_time) if buy_time is not None else None

    consecutive = 0
    for i in range(len(rows) - 1, -1, -1):
        ema_i = series[i]
        close_i = closes[i]
        if ema_i is None or close_i <= 0:
            break
        ts = parse_bar_end_kst(str(rows[i].get("timestamp") or ""))
        if ts is not None:
            if ts.date() != today:
                break
            if buy_kst is not None and ts < buy_kst:
                break
        if price_breaks_ema(close_i, float(ema_i), band_pct):
            consecutive += 1
        else:
            break

    required_bars = max(1, (soft_minutes + BAR_MINUTES - 1) // BAR_MINUTES)
    elapsed_minutes = consecutive * BAR_MINUTES
    triggered = consecutive >= required_bars
    detail = (
        f"EMA{period} 이탈(SOFT≧{soft_minutes}분, 이격>{band_pct:g}%): "
        f"현재 {int(px):,} ≤ 선 {line:,.0f} "
        f"(EMA {latest_ema:,.0f}, 확정 5분봉 연속 {consecutive}개/{elapsed_minutes}분)"
    )
    return {
        "ok": True,
        "triggered": triggered,
        "below": True,
        "ema": latest_ema,
        "period": period,
        "consecutive": consecutive,
        "required_bars": required_bars,
        "elapsed_minutes": elapsed_minutes,
        "soft_minutes": soft_minutes,
        "band_pct": band_pct,
        "reason": "triggered" if triggered else "soft_wait",
        "detail": detail,
    }


def classify_legacy_ema_exit_detail(detail: Optional[str]) -> Optional[str]:
    d = str(detail or "")
    if "EMA" in d and "이탈" in d and "SOFT" in d:
        return "ema_soft"
    return None
