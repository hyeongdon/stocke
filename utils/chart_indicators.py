"""분석 화면 차트 지표 (PRD_ANALYSIS_WEEKLY_CHART) — 주봉 리샘플 · MA/RSI/%R · RSI 과매도 / Williams 매수 신호.

지표 기간·기준값은 RWM 밥그릇 파라미터(utils/rwm_bowl.DEFAULT_PARAMS + 사용자 설정)를 그대로 쓴다.
행(row) 형식: {"timestamp": "YYYY-MM-DD[ HH:MM:SS]", open, high, low, close, volume, ...}.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from utils.rwm_bowl import (
    default_is_trading_day,
    is_week_closed,
    merge_params,
    sma_series,
    wilder_rsi_series,
    williams_r_series,
    williams_turns,
)

MA_PERIODS: Tuple[int, ...] = (10, 20, 60, 120)
SESSION_CLOSE_MIN = 15 * 60 + 30

SIGNAL_RSI_OS = "RSI_OS"
SIGNAL_WR_BUY = "WR_BUY"

TradingDayFn = Callable[[date], bool]


def chart_params(interval: str, p: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """RWM 파라미터 → 차트 지표 설정. RSI 과매도 기준은 주봉 rsi_os_weekly / 일봉 rsi_os_daily."""
    p = p or merge_params()
    weekly = str(interval).upper() == "1W"
    return {
        "rsi_period": int(p["rsi_period"]),
        "rsi_os": float(p["rsi_os_weekly"] if weekly else p["rsi_os_daily"]),
        "wr_period": int(p["w_period"]),
        "wr_os": float(p["w_os"]),
        "wr_min_bottom": int(p["w_min_bottom_bars"]),
    }


def row_date(row: Dict[str, Any]) -> Optional[date]:
    raw = str(row.get("timestamp") or "").strip()
    try:
        if len(raw) >= 10 and raw[4] == "-":
            return date.fromisoformat(raw[:10])
        if len(raw) >= 8 and raw[:8].isdigit():
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None
    return None


def _week_key(d: date) -> Tuple[int, int]:
    iso = d.isocalendar()
    return (iso[0], iso[1])


def _after_close(d: date, now: datetime) -> bool:
    return now.date() > d or (now.date() == d and now.hour * 60 + now.minute >= SESSION_CLOSE_MIN)


def is_week_partial(last_date: date, now: datetime, is_trading_day: TradingDayFn = default_is_trading_day) -> bool:
    """last_date 가 속한 주가 아직 진행 중이면 True (그 주 거래일이 더 남았거나, 마지막 거래일 장 마감 전)."""
    if _week_key(last_date) < _week_key(now.date()):
        return False
    return not (is_week_closed(last_date, is_trading_day) and _after_close(last_date, now))


def week_label_date(d: date, today: date, is_trading_day: TradingDayFn = default_is_trading_day) -> date:
    """d 가 속한 주의 마지막 거래일 (오늘 이후는 오늘까지로 자름)."""
    monday = d - timedelta(days=d.weekday())
    x = min(monday + timedelta(days=4), today)
    while x > monday and (x.weekday() >= 5 or not is_trading_day(x)):
        x -= timedelta(days=1)
    return max(x, monday)


def resample_weekly_ohlcv(
    daily_rows: Iterable[Dict[str, Any]],
    *,
    now: datetime,
    is_trading_day: TradingDayFn = default_is_trading_day,
) -> List[Dict[str, Any]]:
    """일봉 → ISO 주봉 OHLCV. 연휴 주는 있는 거래일만 묶고, timestamp 는 그 주 마지막 거래일.

    마지막 주가 진행 중이면 partial=True (차트에는 그리고 신호는 잠정).
    """
    weeks: List[Dict[str, Any]] = []
    last_key: Optional[Tuple[int, int]] = None
    last_d: Optional[date] = None
    for row in sorted(daily_rows, key=lambda r: str(r.get("timestamp") or "")):
        d = row_date(row)
        if d is None or float(row.get("close") or 0) <= 0:
            continue
        key = _week_key(d)
        if key != last_key:
            weeks.append({
                "timestamp": d.isoformat(),
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row.get("volume") or 0,
                "partial": False,
            })
            last_key = key
        else:
            w = weeks[-1]
            w["timestamp"] = d.isoformat()
            w["high"] = max(w["high"], row["high"])
            w["low"] = min(w["low"], row["low"])
            w["close"] = row["close"]
            w["volume"] = (w["volume"] or 0) + (row.get("volume") or 0)
        last_d = d
    if weeks and last_d is not None:
        weeks[-1]["partial"] = is_week_partial(last_d, now, is_trading_day)
    return weeks


def merge_weekly_rows(
    api_weeks: Iterable[Dict[str, Any]],
    daily_weeks: Sequence[Dict[str, Any]],
    *,
    now: datetime,
    is_trading_day: TradingDayFn = default_is_trading_day,
) -> List[Dict[str, Any]]:
    """키움 주봉(ka10082, 과거 구간) + 일봉 리샘플 주봉(최근 구간) 병합. 같은 주는 일봉 리샘플 우선.

    일봉 리샘플 첫 주는 일봉 조회 시작일에 잘려 있을 수 있으므로 키움 주봉에 그 주가 있으면 키움 값을 쓴다.
    """
    today = now.date()
    merged: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for row in api_weeks:
        d = row_date(row)
        if d is None or float(row.get("close") or 0) <= 0:
            continue
        label = week_label_date(d, today, is_trading_day)
        merged[_week_key(d)] = {
            "timestamp": label.isoformat(),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row.get("volume") or 0,
            "partial": is_week_partial(label, now, is_trading_day),
        }
    for i, row in enumerate(daily_weeks):
        d = row_date(row)
        if d is None:
            continue
        key = _week_key(d)
        if i == 0 and key in merged:
            continue
        merged[key] = dict(row)
    return [merged[k] for k in sorted(merged)]


def mark_daily_partial(rows: List[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    """일봉 partial — 오늘 봉이고 장 마감 전이면 True."""
    for row in rows:
        row["partial"] = False
    if rows:
        d = row_date(rows[-1])
        if d is not None and d >= now.date() and not _after_close(d, now):
            rows[-1]["partial"] = True
    return rows


def _r2(v: Optional[float]) -> Optional[float]:
    return round(float(v), 2) if v is not None else None


def annotate_indicators(
    rows: List[Dict[str, Any]],
    cp: Dict[str, Any],
    ma_periods: Sequence[int] = MA_PERIODS,
) -> List[Dict[str, Any]]:
    """봉마다 ma{n}(원 단위 반올림) · rsi · wr 를 붙인다 (계산 불가 구간은 키 없음 / None)."""
    closes = [float(r["close"]) for r in rows]
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    mas = {n: sma_series(closes, n) for n in ma_periods}
    rsi = wilder_rsi_series(closes, int(cp["rsi_period"]))
    wr = williams_r_series(highs, lows, closes, int(cp["wr_period"]))
    for i, row in enumerate(rows):
        for n in ma_periods:
            if mas[n][i] is not None:
                row[f"ma{n}"] = int(round(mas[n][i]))
        row["rsi"] = _r2(rsi[i])
        row["wr"] = _r2(wr[i])
    return rows


def detect_signals(rows: Sequence[Dict[str, Any]], cp: Dict[str, Any], *, start: int = 0) -> List[Dict[str, Any]]:
    """RSI_OS (RSI 가 기준 초과 → 이하로 처음 내려간 봉) · WR_BUY (%R 바닥 체류 후 기준 상향 돌파 봉).

    start 이전(워밍업) 봉의 신호는 제외하고, idx 는 start 기준으로 다시 매긴다.
    """
    n = len(rows)
    start = max(0, int(start))
    out: List[Dict[str, Any]] = []
    if n == 0 or start >= n:
        return out
    th = float(cp["rsi_os"])
    rsi = [r.get("rsi") for r in rows]
    for i in range(max(start, 1), n):
        prev, cur = rsi[i - 1], rsi[i]
        if prev is not None and cur is not None and prev > th >= cur:
            out.append(_signal(rows, i, start, SIGNAL_RSI_OS, cur))
    wr = [r.get("wr") for r in rows]
    for i, bottom in williams_turns(wr, start, start, n - 1, float(cp["wr_os"]), int(cp["wr_min_bottom"])):
        sig = _signal(rows, i, start, SIGNAL_WR_BUY, wr[i])
        sig["bottom_bars"] = bottom
        out.append(sig)
    out.sort(key=lambda s: (s["idx"], s["type"]))
    return out


def _signal(rows: Sequence[Dict[str, Any]], i: int, start: int, kind: str, value: Any) -> Dict[str, Any]:
    return {
        "idx": i - start,
        "date": str(rows[i].get("timestamp") or "")[:10],
        "type": kind,
        "value": value,
        "partial": bool(rows[i].get("partial")),
    }


def warmup_warning(display_rows: Sequence[Dict[str, Any]], unit: str, ma_periods: Sequence[int] = MA_PERIODS) -> str:
    """표시 구간 앞부분에 MA(최장)·%R 이 비면 '봉 부족' 경고 문구."""
    if not display_rows:
        return ""
    ma_max = max(ma_periods)
    missing: List[str] = []
    if any(r.get(f"ma{ma_max}") is None for r in display_rows):
        missing.append(f"MA{ma_max}{unit}")
    if any(r.get("wr") is None for r in display_rows):
        missing.append("%R")
    if any(r.get("rsi") is None for r in display_rows):
        missing.append("RSI")
    if not missing:
        return ""
    return f"봉 부족 — 상장 기간이 짧아 {'·'.join(missing)} 앞부분 미표시"
