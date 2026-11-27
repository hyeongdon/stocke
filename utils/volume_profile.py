"""매물대(가격대별 거래량) 계산 — OHLCV 봉 리스트만 받는 순수 함수."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

DEFAULT_BINS = 40
ZONE_THRESHOLD = 1.3  # 평균 구간 거래량 대비 배수 이상이면 매물대 구간
MAX_ZONES = 5

LOOKBACKS = {
    "1D": (60, 120, 240),
    "1W": (52, 156),
}


def _sma(values: Sequence[float], n: int, end: Optional[int] = None) -> Optional[float]:
    end = len(values) if end is None else end
    if n <= 0 or end < n:
        return None
    window = values[end - n : end]
    return sum(window) / n


def is_downtrend(closes: Sequence[float], slope_bars: int = 3) -> bool:
    """MA5 < MA20 < MA60 이고 MA20 이 slope_bars 전보다 낮으면 하락추세."""
    ma5 = _sma(closes, 5)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)
    ma20_prev = _sma(closes, 20, len(closes) - slope_bars)
    if None in (ma5, ma20, ma60, ma20_prev):
        return False
    return ma5 < ma20 < ma60 and ma20 < ma20_prev


def _histogram(bars: Sequence[Dict[str, Any]], lo: float, hi: float, bins: int) -> List[float]:
    """봉 거래량을 저가~고가에 걸친 구간에 겹친 길이 비율로 분배."""
    width = (hi - lo) / bins
    hist = [0.0] * bins
    for b in bars:
        vol = float(b.get("volume") or 0)
        if vol <= 0:
            continue
        b_lo, b_hi = float(b["low"]), float(b["high"])
        if b_hi <= b_lo:
            idx = min(bins - 1, max(0, int((float(b["close"]) - lo) / width)))
            hist[idx] += vol
            continue
        first = max(0, int((b_lo - lo) / width))
        last = min(bins - 1, int((b_hi - lo) / width))
        span = b_hi - b_lo
        for i in range(first, last + 1):
            seg_lo = lo + i * width
            overlap = min(b_hi, seg_lo + width) - max(b_lo, seg_lo)
            if overlap > 0:
                hist[i] += vol * overlap / span
    return hist


def compute_profile(
    bars: Sequence[Dict[str, Any]],
    lookback: int,
    *,
    bins: int = DEFAULT_BINS,
    threshold: float = ZONE_THRESHOLD,
) -> Optional[Dict[str, Any]]:
    """최근 lookback 봉 매물대 요약. 봉이 부족하거나 가격 범위가 없으면 None."""
    window = [b for b in bars if int(b.get("close") or 0) > 0][-lookback:]
    if len(window) < min(lookback, 20):
        return None
    lo = float(min(int(b["low"] or b["close"]) for b in window))
    hi = float(max(int(b["high"] or b["close"]) for b in window))
    if hi <= lo:
        return None

    hist = _histogram(window, lo, hi, bins)
    total = sum(hist)
    if total <= 0:
        return None
    width = (hi - lo) / bins
    mean = total / bins

    zones: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for i, v in enumerate(hist):
        if v >= mean * threshold:
            if cur is None:
                cur = {"lo_idx": i, "hi_idx": i, "volume": 0.0}
            cur["hi_idx"] = i
            cur["volume"] += v
        elif cur is not None:
            zones.append(cur)
            cur = None
    if cur is not None:
        zones.append(cur)

    for z in zones:
        z["low"] = int(round(lo + z["lo_idx"] * width))
        z["high"] = int(round(lo + (z["hi_idx"] + 1) * width))
        z["share"] = round(z["volume"] / total * 100, 2)
        del z["lo_idx"], z["hi_idx"], z["volume"]

    close = int(window[-1]["close"])
    poc_idx = max(range(bins), key=lambda i: hist[i])
    poc_price = int(round(lo + (poc_idx + 0.5) * width))

    above = [z for z in zones if z["low"] > close]
    below = [z for z in zones if z["high"] < close]
    resist = min(above, key=lambda z: z["low"]) if above else None
    support = max(below, key=lambda z: z["high"]) if below else None
    in_zone = any(z["low"] <= close <= z["high"] for z in zones)

    def _gap(a: float, b: float) -> float:
        return round((a - b) / close * 100, 2)

    closes = [float(b["close"]) for b in bars if int(b.get("close") or 0) > 0]
    top = sorted(zones, key=lambda z: z["share"], reverse=True)[:MAX_ZONES]
    return {
        "lookback": lookback,
        "last_bar_date": str(window[-1].get("timestamp") or "")[:10] or None,
        "close_price": close,
        "range_low": int(lo),
        "range_high": int(hi),
        "poc_price": poc_price,
        "poc_gap_pct": _gap(poc_price, close),
        "resist_low": resist["low"] if resist else None,
        "resist_high": resist["high"] if resist else None,
        "resist_share": resist["share"] if resist else None,
        "resist_gap_pct": _gap(resist["low"], close) if resist else None,
        "support_low": support["low"] if support else None,
        "support_high": support["high"] if support else None,
        "support_share": support["share"] if support else None,
        "support_gap_pct": _gap(close, support["high"]) if support else None,
        "in_zone": in_zone,
        "downtrend": is_downtrend(closes),
        "zones": sorted(top, key=lambda z: z["low"]),
    }


def compute_profiles(bars: Sequence[Dict[str, Any]], interval: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for lb in LOOKBACKS.get(interval, ()):
        p = compute_profile(bars, lb)
        if p:
            out.append(p)
    return out
