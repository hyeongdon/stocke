"""보유 포지션용 당일 분봉 스파크라인."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.datetime_kst import kst_date_str


def bars_to_sparkline(bars: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not bars:
        return None
    closes: List[int] = []
    highs: List[int] = []
    timestamps: List[str] = []
    for b in bars:
        try:
            c = int(b.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if c > 0:
            closes.append(c)
            try:
                h = int(b.get("high") or 0)
            except (TypeError, ValueError):
                h = 0
            highs.append(h if h > 0 else c)
            ts = b.get("timestamp")
            if ts:
                timestamps.append(str(ts)[:19])
            elif timestamps:
                timestamps.append(timestamps[-1])
            else:
                timestamps.append("")
    if len(closes) < 2:
        return None
    try:
        day_open = int(bars[0].get("open") or closes[0])
    except (TypeError, ValueError):
        day_open = closes[0]
    last = closes[-1]
    day_high = max(highs) if highs else max(closes)
    chg = (last - day_open) / day_open * 100 if day_open else 0.0
    pullback = max(0.0, (day_high - last) / day_high * 100.0) if day_high else None
    return {
        "closes": closes,
        "timestamps": timestamps[: len(closes)],
        "open": day_open,
        "last": last,
        "day_high": day_high,
        "pullback_pct": round(pullback, 4) if pullback is not None else None,
        "change_pct": round(chg, 2),
        "bars": len(closes),
    }


def today_kst_date() -> str:
    return kst_date_str()
