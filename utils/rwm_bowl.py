"""RWM 밥그릇 매매 (PRD_RWM_BOWL v0.4.0) — 순수 계산기 + 상태 저장소.

R = 주봉 RSI 과매도(1차) + 5거래일 이내 일봉 RSI 과매도, W = Williams %R −80 상향 돌파,
M = 일봉 MACD(12/26/9) 골든크로스 3거래일 이내.
이평 필터(m_type) = 20/60 골든크로스(M1_GC) 또는 GC 임박 + 종가 > 60선(M2_NEAR), 60선 하락 아님.
신호는 장 마감 후 확정 일봉, 진입은 익일 3분봉 EMA20 눌림 반등, 청산은 20일선 종가 이탈.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

STRATEGY_KEY = "rwm_bowl"
STRATEGY_TYPE = "RWM_BOWL"
STRATEGY_LABEL = "RWM 밥그릇"

DEFAULT_CONDITION_NAME = "RWM밥그릇"

DEFAULT_PARAMS: Dict[str, Any] = {
    "exec_tf": "1D",
    "entry_tf": "3M",
    "min_bars": 300,
    "max_l0_candidates": 200,
    "min_trading_value": 3_000_000_000,
    "liquidity_days": 10,
    "min_price": 1000,
    "rwm_condition_names": DEFAULT_CONDITION_NAME,
    "rwm_manual_codes": "",
    "max_manual_codes": 50,
    "max_pool_codes": 200,
    "include_global_watchlist": False,
    "intraday_preview": True,
    "preview_interval_min": 30,
    "preview_alert": True,
    "manual_daily_report": True,
    "manual_report_changes_only": False,

    "ma_fast": 20,
    "ma_slow": 60,
    "near_gap_pct": 2.0,
    "near_shrink_days": 5,
    "m2_require_close_above_ma60": True,
    "require_ma20_up": True,
    "require_ma60_not_down": True,
    "ma60_slope_days": 5,
    "ma60_slope_min_pct": -0.2,

    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "macd_fresh_days": 3,

    "bowl_lookback": 60,
    "rsi_period": 14,
    "rsi_os_weekly": 30.0,
    "rsi_os_daily": 30.0,
    "rsi_pair_days": 5,

    "w_period": 93,
    "w_os": -80.0,
    "w_min_bottom_bars": 2,

    "max_above_ma20_pct": 8.0,
    "chase_day_pct": 7.0,
    "max_new_per_day": 3,

    "pb_start": "09:06",
    "pb_end": "14:30",
    "pb_ema": 20,
    "pb_min_rise_pct": 1.0,
    "pb_touch_buf_pct": 0.2,
    "pb_min_depth_pct": 1.0,
    "pb_max_depth_pct": 5.0,
    "pb_confirm_bars": 2,
    "max_entry_above_pct": 5.0,
    "pb_valid_days": 1,
    "pb_fallback": "none",

    "exit_ma": 20,
    "ma_exit_close_days": 1,
    "ma_exit_buffer_pct": 0.0,
    "intraday_hard_buf_pct": 3.0,
    "exit_fill": "next_open",
    "max_hold_days": 60,
    "surge_day_pct": 15.0,
    "surge_exit_ma": 10,
    "surge_exit_hm": "15:10",

    "flatten_eod": False,
    "exempt_market_risk_gate": False,
    "risk_per_trade_pct": 1.0,
    "min_risk_pct": 3.0,
    "max_positions": 5,
    "max_position_pct": 20.0,
    "max_invest_amount": 0,
}

# AutoTradeSettings 컬럼 → 파라미터 키
SETTINGS_ALIASES: Dict[str, str] = {
    "rwm_condition_names": "rwm_condition_names",
    "rwm_manual_codes": "rwm_manual_codes",
    "rwm_max_slots": "max_positions",
    "rwm_max_new_per_day": "max_new_per_day",
    "rwm_risk_per_trade_pct": "risk_per_trade_pct",
    "rwm_max_position_pct": "max_position_pct",
    "rwm_max_invest_amount": "max_invest_amount",
    "rwm_rsi_os_weekly": "rsi_os_weekly",
    "rwm_rsi_os_daily": "rsi_os_daily",
    "rwm_rsi_pair_days": "rsi_pair_days",
    "rwm_bowl_lookback": "bowl_lookback",
    "rwm_gc_fresh_days": "macd_fresh_days",
    "rwm_near_gap_pct": "near_gap_pct",
    "rwm_ma60_slope_min_pct": "ma60_slope_min_pct",
    "rwm_pb_start": "pb_start",
    "rwm_pb_end": "pb_end",
    "rwm_max_entry_above_pct": "max_entry_above_pct",
    "rwm_intraday_hard_buf_pct": "intraday_hard_buf_pct",
    "rwm_max_hold_days": "max_hold_days",
    "rwm_manual_daily_report": "manual_daily_report",
}

SIGNAL_REASONS = (
    "INSUFFICIENT_BARS", "LOW_LIQUIDITY", "NO_MA_SETUP", "MA60_DOWN", "NO_MACD_GC",
    "NO_WEEKLY_OVERSOLD", "NO_DAILY_OVERSOLD_PAIR", "NO_W_SIGNAL", "NO_BOWL",
    "TOO_FAR_ABOVE_MA20", "CHASE_DAY", "CYCLE_DONE", "ALREADY_IN_POSITION",
    "SLOT_FULL", "DAILY_NEW_LIMIT", "WATCH_ADDED",
)
ENTRY_REASONS = (
    "OPEN_BELOW_MA20", "BELOW_DAILY_MA20", "NO_PULLBACK", "ENTRY_ABOVE_CAP", "PULLBACK_3M",
)
EXIT_REASONS = ("STOP_HARD", "EXIT_MA10", "EXIT_MA20", "MAX_HOLD")

REASON_KO: Dict[str, str] = {
    "INSUFFICIENT_BARS": "일봉 부족",
    "LOW_LIQUIDITY": "유동성 부족",
    "NO_MA_SETUP": "20/60 자리 아님",
    "MA60_DOWN": "60선 하락 중",
    "MA_SETUP_STALE": "20/60 자리 신선도 경과 (구 판정)",
    "NO_MACD_GC": "최근 MACD 골든크로스 없음",
    "NO_WEEKLY_OVERSOLD": "주봉 RSI 과매도 없음",
    "NO_DAILY_OVERSOLD_PAIR": "주봉 기준 5거래일 내 일봉 과매도 없음",
    "NO_W_SIGNAL": "Williams %R 반전 없음",
    "NO_BOWL": "그릇(20<60) 없음",
    "TOO_FAR_ABOVE_MA20": "20선 이격 과다",
    "CHASE_DAY": "신호일 급등",
    "CYCLE_DONE": "이번 그릇 매수 완료",
    "ALREADY_IN_POSITION": "이미 보유/대기",
    "SLOT_FULL": "최대 종목 수",
    "DAILY_NEW_LIMIT": "하루 신규 상한",
    "WATCH_ADDED": "감시 등록",
    "OPEN_BELOW_MA20": "시가 < 신호일 20선",
    "BELOW_DAILY_MA20": "3분봉 종가 < 신호일 20선",
    "NO_PULLBACK": "눌림 없음",
    "ENTRY_ABOVE_CAP": "반등가 > 매수 상한",
    "PULLBACK_3M": "3분봉 눌림 반등",
    "STOP_HARD": "하드 손절",
    "EXIT_MA20": "20일선 종가 이탈",
    "EXIT_MA10": "급등 후 10일선 이탈 익절",
    "MAX_HOLD": "보유 만기",
}

M_TYPE_KO = {"M1_GC": "20/60 골든크로스", "M2_NEAR": "20/60 GC 임박"}

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STATE_PATH = _PROJECT_ROOT / "logs" / "_rwm_bowl_state.json"


# ───────────────────────── params ─────────────────────────

def merge_params(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = dict(DEFAULT_PARAMS)
    if overrides:
        out.update({k: v for k, v in overrides.items() if v is not None})
    return out


def params_from_settings(settings: Any) -> Dict[str, Any]:
    """AutoTradeSettings → 파라미터. rwm_params(JSON) 고급값 → 컬럼값 순으로 덮어씀."""
    if settings is None:
        return merge_params()
    raw: Dict[str, Any] = {}
    adv = getattr(settings, "rwm_params", None)
    if adv:
        try:
            data = json.loads(adv) if isinstance(adv, str) else dict(adv)
            if isinstance(data, dict):
                raw.update({k: v for k, v in data.items() if k in DEFAULT_PARAMS})
        except (TypeError, ValueError):
            pass
    for col, key in SETTINGS_ALIASES.items():
        val = getattr(settings, col, None)
        if val is None:
            continue
        if isinstance(val, str) and not val.strip() and key not in ("rwm_manual_codes",):
            continue
        raw[key] = val
    return merge_params(raw)


def parse_code_list(raw: Optional[str]) -> List[str]:
    """쉼표·줄바꿈·공백 구분 종목코드 → 6자리 코드 목록 (중복 제거, 순서 유지)."""
    if not raw:
        return []
    out: List[str] = []
    seen = set()
    for part in str(raw).replace("\n", ",").replace("\r", ",").replace(" ", ",").split(","):
        code = normalize_code(part)
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def normalize_code(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    if s.startswith("A") and len(s) == 7:
        s = s[1:]
    if "_" in s:
        s = s.split("_", 1)[0]
    if not s or not s.isalnum():
        return ""
    if s.isdigit():
        s = s.zfill(6)
    return s if len(s) == 6 else ""


def _hm_to_min(hm: str, default: str = "00:00") -> int:
    try:
        h, m = str(hm or default).split(":")[:2]
        return int(h) * 60 + int(m)
    except (TypeError, ValueError):
        h, m = default.split(":")
        return int(h) * 60 + int(m)


# ───────────────────────── 지표 ─────────────────────────

def sma_series(values: Sequence[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    n = max(1, int(n))
    acc = 0.0
    for i, v in enumerate(values):
        acc += float(v)
        if i >= n:
            acc -= float(values[i - n])
        if i >= n - 1:
            out[i] = acc / n
    return out


def wilder_rsi_series(closes: Sequence[float], period: int = 14) -> List[Optional[float]]:
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    period = max(1, int(period))
    if n < period + 1:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = float(closes[i]) - float(closes[i - 1])
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period

    def _rsi(g: float, l: float) -> float:
        if l <= 1e-12:
            return 100.0 if g > 1e-12 else 50.0
        return 100.0 - (100.0 / (1.0 + g / l))

    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, n):
        diff = float(closes[i]) - float(closes[i - 1])
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def williams_r_series(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14,
) -> List[Optional[float]]:
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    period = max(1, int(period))
    for i in range(period - 1, n):
        hh = max(float(x) for x in highs[i - period + 1: i + 1])
        ll = min(float(x) for x in lows[i - period + 1: i + 1])
        if hh - ll <= 1e-12:
            out[i] = -50.0
        else:
            out[i] = (hh - float(closes[i])) / (hh - ll) * -100.0
    return out


def ema_series(values: Sequence[float], period: int = 20) -> List[Optional[float]]:
    """EMA — 첫 값으로 시작해 재귀 (alpha = 2/(n+1)). 시드 봉이 충분히 길면 HTS 와 수렴."""
    out: List[Optional[float]] = [None] * len(values)
    if not values:
        return out
    alpha = 2.0 / (max(1, int(period)) + 1.0)
    e = float(values[0])
    out[0] = e
    for i in range(1, len(values)):
        e = alpha * float(values[i]) + (1.0 - alpha) * e
        out[i] = e
    return out


def macd_series(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9,
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """(MACD 선 = EMA(fast) − EMA(slow), 시그널 = MACD 선의 EMA(signal))."""
    ef = ema_series(values, fast)
    es = ema_series(values, slow)
    line = [f - s if f is not None and s is not None else None for f, s in zip(ef, es)]
    sig = ema_series([v or 0.0 for v in line], signal) if line else []
    return line, sig


def macd_gc_idx(
    macd: Sequence[Optional[float]], sig: Sequence[Optional[float]], t: int, fresh_days: int,
) -> Optional[int]:
    """t 에서 MACD > 시그널이고, t 포함 최근 fresh_days 거래일 안에 MACD 가 시그널을 상향 돌파한 가장 최근 날."""
    if t < 1 or t >= len(macd) or not _ok(macd[t], sig[t]) or macd[t] <= sig[t]:
        return None
    for j in range(t, max(1, t - max(1, int(fresh_days)) + 1) - 1, -1):
        a0, s0, a1, s1 = macd[j - 1], sig[j - 1], macd[j], sig[j]
        if _ok(a0, s0, a1, s1) and a0 <= s0 and a1 > s1:
            return j
    return None


# ───────────────────────── 일봉 · 주봉 ─────────────────────────

def _bar_date(bar: Dict[str, Any]) -> Optional[date]:
    d = bar.get("date")
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    raw = str(bar.get("timestamp") or d or "").strip()
    if len(raw) >= 10 and raw[4] == "-":
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    if len(raw) >= 8 and raw[:8].isdigit():
        try:
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        except ValueError:
            return None
    return None


def normalize_daily_bars(raw: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """키움 일봉 dict → [{date, open, high, low, close, volume}] (날짜 오름차순, 중복·0종가 제거)."""
    by_date: Dict[date, Dict[str, Any]] = {}
    for b in raw or []:
        d = _bar_date(b)
        if d is None:
            continue
        try:
            close = float(b.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue

        def _f(key: str) -> float:
            try:
                v = float(b.get(key) or 0)
            except (TypeError, ValueError):
                v = 0.0
            return v if v > 0 else close

        by_date[d] = {
            "date": d,
            "open": _f("open"),
            "high": _f("high"),
            "low": _f("low"),
            "close": close,
            "volume": float(b.get("volume") or 0),
        }
    return [by_date[d] for d in sorted(by_date)]


def drop_unconfirmed_daily(
    bars: List[Dict[str, Any]], now: datetime, *, close_hm: str = "15:30",
) -> List[Dict[str, Any]]:
    """장 마감 전에는 당일 진행 봉을 뺀다 (신호는 확정 일봉만)."""
    if not bars:
        return bars
    last = bars[-1]["date"]
    today = now.date()
    if last > today:
        return bars[:-1]
    if last == today and (now.hour * 60 + now.minute) < _hm_to_min(close_hm):
        return bars[:-1]
    return bars


def default_is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    try:
        from utils.market_hours import is_krx_holiday

        return not is_krx_holiday(d)
    except Exception:
        return True


def is_week_closed(d: date, is_trading_day: Callable[[date], bool] = default_is_trading_day) -> bool:
    """d 가 그 ISO 주의 마지막 거래일이면 True (이후 같은 주 거래일 없음)."""
    for k in range(1, 7 - d.weekday()):
        nd = d + timedelta(days=k)
        if nd.weekday() < 5 and is_trading_day(nd):
            return False
    return True


def next_trading_day(d: date, is_trading_day: Callable[[date], bool] = default_is_trading_day) -> date:
    nd = d + timedelta(days=1)
    for _ in range(30):
        if is_trading_day(nd):
            return nd
        nd += timedelta(days=1)
    return nd


def prev_trading_day(d: date, is_trading_day: Callable[[date], bool] = default_is_trading_day) -> date:
    pd = d - timedelta(days=1)
    for _ in range(30):
        if is_trading_day(pd):
            return pd
        pd -= timedelta(days=1)
    return pd


def trading_days_back(d: date, n: int, is_trading_day: Callable[[date], bool] = default_is_trading_day) -> date:
    for _ in range(max(0, int(n))):
        d = prev_trading_day(d, is_trading_day)
    return d


def update_r_pool(
    pool: Dict[str, Dict[str, Any]],
    found: Dict[str, str],
    today: date,
    *,
    keep_from: date,
    max_codes: int,
) -> List[str]:
    """바닥 후보 풀 갱신 — 조건식(주봉 과매도) 편입 종목 누적, last_seen < keep_from 만료, 초과분은 오래된 순 삭제.

    조건식에 걸리는 날은 20선이 60선 아래라 M 이 나올 수 없으므로, 이후 GC 까지 판정 대상으로 유지한다.
    반환: 삭제된 코드.
    """
    iso = today.isoformat()
    for code, name in found.items():
        row = pool.get(code)
        if row is None:
            pool[code] = {"name": name, "first_seen": iso, "last_seen": iso}
        else:
            row["last_seen"] = iso
            if name:
                row["name"] = name
    keep = keep_from.isoformat()
    removed = [c for c, r in pool.items() if str(r.get("last_seen") or "") < keep]
    for c in removed:
        pool.pop(c, None)
    over = len(pool) - max(0, int(max_codes))
    if over > 0:
        drop = sorted(pool, key=lambda c: (str(pool[c].get("last_seen") or ""), c))[:over]
        for c in drop:
            pool.pop(c, None)
        removed += drop
    return removed


@dataclass
class WeekBar:
    key: Tuple[int, int]
    last_idx: int
    last_date: date
    close: float
    rsi: Optional[float] = None


def resample_weekly(bars: Sequence[Dict[str, Any]], *, last_week_closed: bool) -> List[WeekBar]:
    """일봉 → ISO 주봉 (주 마지막 거래일 종가). 미확정 마지막 주는 제외하고 RSI 계산."""
    weeks: List[WeekBar] = []
    for i, b in enumerate(bars):
        iso = b["date"].isocalendar()
        key = (iso[0], iso[1])
        if weeks and weeks[-1].key == key:
            weeks[-1].last_idx = i
            weeks[-1].last_date = b["date"]
            weeks[-1].close = float(b["close"])
        else:
            weeks.append(WeekBar(key=key, last_idx=i, last_date=b["date"], close=float(b["close"])))
    if weeks and not last_week_closed:
        weeks = weeks[:-1]
    return weeks


# ───────────────────────── 신호 판정 ─────────────────────────

@dataclass
class Indicators:
    dates: List[date]
    open: List[float]
    high: List[float]
    low: List[float]
    close: List[float]
    volume: List[float]
    ma20: List[Optional[float]]
    ma60: List[Optional[float]]
    gap_pct: List[Optional[float]]
    rsi_d: List[Optional[float]]
    wr: List[Optional[float]]
    weeks: List[WeekBar] = field(default_factory=list)
    macd: List[Optional[float]] = field(default_factory=list)
    macd_sig: List[Optional[float]] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.close)


def compute_indicators(
    bars: Sequence[Dict[str, Any]], p: Dict[str, Any], *, last_week_closed: bool,
) -> Indicators:
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    ma20 = sma_series(closes, int(p["ma_fast"]))
    ma60 = sma_series(closes, int(p["ma_slow"]))
    gap: List[Optional[float]] = []
    for f, s in zip(ma20, ma60):
        gap.append(((s - f) / s * 100.0) if (f is not None and s) else None)
    weeks = resample_weekly(bars, last_week_closed=last_week_closed)
    rsi_w = wilder_rsi_series([w.close for w in weeks], int(p["rsi_period"]))
    for w, r in zip(weeks, rsi_w):
        w.rsi = r
    macd, macd_sig = macd_series(closes, int(p["macd_fast"]), int(p["macd_slow"]), int(p["macd_signal"]))
    return Indicators(
        dates=[b["date"] for b in bars],
        open=[float(b["open"]) for b in bars],
        high=highs,
        low=lows,
        close=closes,
        volume=[float(b.get("volume") or 0) for b in bars],
        ma20=ma20,
        ma60=ma60,
        gap_pct=gap,
        rsi_d=wilder_rsi_series(closes, int(p["rsi_period"])),
        wr=williams_r_series(highs, lows, closes, int(p["w_period"])),
        weeks=weeks,
        macd=macd,
        macd_sig=macd_sig,
    )


def _ok(*vals: Any) -> bool:
    return all(v is not None for v in vals)


def ma60_slope_pct(ind: Indicators, t: int, days: int) -> Optional[float]:
    if t - days < 0:
        return None
    a, b = ind.ma60[t], ind.ma60[t - days]
    if not _ok(a, b) or not b:
        return None
    return (a / b - 1.0) * 100.0


def trading_value_ratio(ind: Indicators, t: int, short: int, long: int) -> Optional[float]:
    """최근 short 일 평균 거래대금 ÷ 최근 long 일 평균 (t 포함). 바닥 이후 거래 회복 정도."""
    if t + 1 < long:
        return None
    tv = [ind.close[i] * ind.volume[i] for i in range(t - long + 1, t + 1)]
    base = sum(tv) / len(tv)
    if base <= 0:
        return None
    return round((sum(tv[-short:]) / short) / base, 3)


TV_RATIO_TIERS = (1.0, 1.5, 2.0)


def tv_ratio_tier(ratio: Optional[float]) -> int:
    """거래대금 증가율 등급: <1.0 → 0, 1.0~1.5 → 1, 1.5~2.0 → 2, ≥2.0 → 3."""
    if ratio is None:
        return 0
    return sum(1 for th in TV_RATIO_TIERS if float(ratio) >= th)


def _ma20_up(ind: Indicators, i: int) -> bool:
    return i >= 1 and _ok(ind.ma20[i], ind.ma20[i - 1]) and ind.ma20[i] > ind.ma20[i - 1]


def _is_gc(ind: Indicators, j: int) -> bool:
    if j < 1:
        return False
    a0, b0, a1, b1 = ind.ma20[j - 1], ind.ma60[j - 1], ind.ma20[j], ind.ma60[j]
    return _ok(a0, b0, a1, b1) and a0 <= b0 and a1 > b1


def ma_state_at(ind: Indicators, i: int, p: Dict[str, Any]) -> Dict[str, Any]:
    """i 일의 20/60 이평 상태 (교차 시점 무관): m_type = M1_GC(20선 > 60선) / M2_NEAR(교차 임박) / None.

    gc_idx 는 bowl_lookback 안 가장 최근 20/60 골든크로스 (표시용).
    """
    out: Dict[str, Any] = {"m_type": None, "gc_idx": None}
    if i < 1 or not _ok(ind.ma20[i], ind.ma60[i]):
        return out
    ma20, ma60, close = ind.ma20[i], ind.ma60[i], ind.close[i]
    up_ok = _ma20_up(ind, i) or not p.get("require_ma20_up", True)

    if ma20 > ma60 and close > ma20 and up_ok:
        out["m_type"] = "M1_GC"
        for j in range(i, max(0, i - int(p["bowl_lookback"])) - 1, -1):
            if _is_gc(ind, j):
                out["gc_idx"] = j
                break
        return out

    if ma20 < ma60:
        k = int(p["near_shrink_days"])
        gap, gap_prev = ind.gap_pct[i], (ind.gap_pct[i - k] if i - k >= 0 else None)
        above60 = close > ma60 or not p.get("m2_require_close_above_ma60", True)
        if (
            _ok(gap, gap_prev)
            and gap <= float(p["near_gap_pct"])
            and gap < gap_prev
            and up_ok
            and above60
            and close > ma20
        ):
            out["m_type"] = "M2_NEAR"
    return out


def _fmt_d(d: Optional[date]) -> str:
    return d.isoformat() if d else ""


def find_daily_pair(
    rsi_d: Sequence[Optional[float]],
    os_weeks: Sequence[WeekBar],
    ws: int,
    t: int,
    th_d: float,
    pair_n: int,
) -> Optional[Tuple[int, WeekBar]]:
    """주봉 과매도 주 마지막 거래일(E) 기준 ±pair_n 거래일 안 일봉 RSI ≤ th_d. 가장 이른 날."""
    best: Optional[Tuple[int, WeekBar]] = None
    for w in os_weeks:
        for d in range(max(ws, w.last_idx - pair_n), min(t, w.last_idx + pair_n) + 1):
            r = rsi_d[d]
            if r is not None and r <= th_d:
                if best is None or d < best[0]:
                    best = (d, w)
                break
    return best


def williams_turns(
    wr: Sequence[Optional[float]],
    since: int,
    ws: int,
    t: int,
    w_os: float,
    min_bottom: int,
) -> List[Tuple[int, int]]:
    """since(r_date) 이후 %R 이 w_os 아래 min_bottom 봉 이상 머문 뒤 상향 돌파한 날 전부 (idx, 체류 봉 수)."""
    out: List[Tuple[int, int]] = []
    for i in range(max(since, ws, 1), t + 1):
        prev, cur = wr[i - 1], wr[i]
        if prev is None or cur is None or not (prev <= w_os < cur):
            continue
        cnt = 0
        j = i - 1
        while j >= 0 and wr[j] is not None and wr[j] <= w_os:
            cnt += 1
            j -= 1
        if cnt >= max(1, min_bottom):
            out.append((i, cnt))
    return out


def find_williams_turn(
    wr: Sequence[Optional[float]],
    since: int,
    ws: int,
    t: int,
    w_os: float,
    min_bottom: int,
) -> Tuple[Optional[int], int]:
    """williams_turns 중 가장 최근 날."""
    turns = williams_turns(wr, since, ws, t, w_os, min_bottom)
    return turns[-1] if turns else (None, 0)


def evaluate_signal(
    bars: Sequence[Dict[str, Any]],
    p: Dict[str, Any],
    *,
    last_week_closed: bool,
    cycle_bought: Optional[Callable[[date], bool]] = None,
) -> Dict[str, Any]:
    """확정 일봉 마지막 봉(t)을 신호일로 판정. 모든 단계를 계산해 checks 로 남기고 첫 실패 reason 반환."""
    n = len(bars)
    res: Dict[str, Any] = {"ok": False, "reason": None, "bars": n, "checks": {}}
    min_bars = int(p["min_bars"])
    if n < max(min_bars, int(p["ma_slow"]) + int(p["ma60_slope_days"]) + 2):
        res["reason"] = "INSUFFICIENT_BARS"
        return res

    ind = compute_indicators(bars, p, last_week_closed=last_week_closed)
    t = n - 1
    lookback = max(5, int(p["bowl_lookback"]))
    ws = max(1, t - lookback + 1)
    ck: Dict[str, Any] = {}

    close_t = ind.close[t]
    ma20_t, ma60_t = ind.ma20[t], ind.ma60[t]
    gap_t = ind.gap_pct[t]
    k = int(p["near_shrink_days"])
    slope = ma60_slope_pct(ind, t, int(p["ma60_slope_days"]))
    day_ret = (close_t / ind.close[t - 1] - 1.0) * 100.0 if ind.close[t - 1] else 0.0
    above20 = (close_t / ma20_t - 1.0) * 100.0 if ma20_t else None
    res.update(
        signal_date=_fmt_d(ind.dates[t]),
        signal_close=int(round(close_t)),
        ma20=round(ma20_t, 2) if ma20_t else None,
        ma60=round(ma60_t, 2) if ma60_t else None,
        gap_pct=round(gap_t, 3) if gap_t is not None else None,
        gap_prev_pct=(round(ind.gap_pct[t - k], 3) if t - k >= 0 and ind.gap_pct[t - k] is not None else None),
        ma60_slope_pct=round(slope, 3) if slope is not None else None,
        day_return_pct=round(day_ret, 2),
        above_ma20_pct=round(above20, 2) if above20 is not None else None,
    )

    # 유동성 (조건식 J 재확인)
    liq_n = max(1, int(p["liquidity_days"]))
    tv = [ind.close[i] * ind.volume[i] for i in range(max(0, t - liq_n + 1), t + 1)]
    tv_avg = sum(tv) / len(tv) if tv else 0.0
    liq_ok = tv_avg >= float(p["min_trading_value"]) and close_t >= float(p["min_price"])
    ck["liquidity"] = liq_ok
    res["trading_value_avg"] = int(tv_avg)
    res["tv_ratio_5_60"] = trading_value_ratio(ind, t, 5, 60)

    # 이평 필터 (20/60 상태)
    st = ma_state_at(ind, t, p)
    m_type = st["m_type"]
    ck["m_type"] = m_type
    res["m_type"] = m_type
    res["gc_date"] = _fmt_d(ind.dates[st["gc_idx"]]) if st["gc_idx"] is not None else ""
    slope_ok = (slope is not None and slope >= float(p["ma60_slope_min_pct"])) or not p.get(
        "require_ma60_not_down", True,
    )
    ck["ma60_ok"] = slope_ok

    # 그릇
    win_ma20 = [(i, ind.ma20[i]) for i in range(ws, t + 1) if ind.ma20[i] is not None]
    bowl_ok = any(
        ind.ma20[i] is not None and ind.ma60[i] is not None and ind.ma20[i] < ind.ma60[i]
        for i in range(ws, t + 1)
    )
    bowl_idx = min(win_ma20, key=lambda x: x[1])[0] if win_ma20 else ws
    ck["bowl"] = bowl_ok
    res["bowl_date"] = _fmt_d(ind.dates[bowl_idx])
    res["bowl_low"] = int(round(min(ind.low[ws: t + 1])))

    # M 트리거 — 일봉 MACD 가 시그널선을 상향 돌파 (최근 macd_fresh_days 거래일, 오늘도 MACD > 시그널)
    macd_gc = macd_gc_idx(ind.macd, ind.macd_sig, t, int(p["macd_fresh_days"]))
    ck["macd_gc"] = macd_gc is not None
    res["macd_gc_date"] = _fmt_d(ind.dates[macd_gc]) if macd_gc is not None else ""
    res["macd"] = round(ind.macd[t], 2) if ind.macd[t] is not None else None
    res["macd_signal"] = round(ind.macd_sig[t], 2) if ind.macd_sig[t] is not None else None

    # R — 주봉 1차
    th_w = float(p["rsi_os_weekly"])
    win_weeks = [w for w in ind.weeks if ws <= w.last_idx <= t and w.rsi is not None]
    os_weeks = [w for w in win_weeks if w.rsi <= th_w]
    res["rsi_w_min"] = round(min(w.rsi for w in win_weeks), 2) if win_weeks else None
    ck["r_weekly"] = bool(os_weeks)

    # R — 일봉 짝 (주봉 마지막 거래일 기준 ±rsi_pair_days 거래일)
    best = find_daily_pair(
        ind.rsi_d, os_weeks, ws, t, float(p["rsi_os_daily"]), int(p["rsi_pair_days"]),
    )
    win_rsi_d = [ind.rsi_d[i] for i in range(ws, t + 1) if ind.rsi_d[i] is not None]
    res["rsi_d_min"] = round(min(win_rsi_d), 2) if win_rsi_d else None
    ck["r_daily"] = best is not None
    r_idx = best[0] if best else None
    if best:
        res["r_date"] = _fmt_d(ind.dates[best[0]])
        res["r_week"] = _fmt_d(best[1].last_date)
        res["r_week_rsi"] = round(best[1].rsi, 2)
        res["rsi_d_at_r"] = round(ind.rsi_d[best[0]], 2)
        res["r_pair_offset"] = best[0] - best[1].last_idx
    else:
        res["r_date"] = ""
        res["r_week"] = _fmt_d(os_weeks[0].last_date) if os_weeks else ""

    # W — r_date 이후 %R −80 상향 돌파 (바닥 체류 ≥ N봉). 횟수는 우선순위에 사용
    turns: List[Tuple[int, int]] = []
    if r_idx is not None:
        turns = williams_turns(ind.wr, r_idx, ws, t, float(p["w_os"]), int(p["w_min_bottom_bars"]))
    w_idx, w_bottom = turns[-1] if turns else (None, 0)
    ck["w"] = w_idx is not None
    res["w_date"] = _fmt_d(ind.dates[w_idx]) if w_idx is not None else ""
    res["w_bottom_bars"] = w_bottom
    res["w_count"] = len(turns)
    if w_idx is not None:
        res["w_from"] = round(ind.wr[w_idx - 1], 1)
        res["w_to"] = round(ind.wr[w_idx], 1)

    # 추격 방지
    too_far = ma20_t is not None and close_t > ma20_t * (1.0 + float(p["max_above_ma20_pct"]) / 100.0)
    chase = day_ret >= float(p["chase_day_pct"])
    ck["too_far"] = too_far
    ck["chase"] = chase

    bowl_d = ind.dates[bowl_idx]
    cycle_done = bool(cycle_bought(bowl_d)) if cycle_bought else False
    ck["cycle_done"] = cycle_done

    res["checks"] = ck
    if not liq_ok:
        res["reason"] = "LOW_LIQUIDITY"
    elif m_type is None:
        res["reason"] = "NO_MA_SETUP"
    elif not slope_ok:
        res["reason"] = "MA60_DOWN"
    elif macd_gc is None:
        res["reason"] = "NO_MACD_GC"
    elif not os_weeks:
        res["reason"] = "NO_WEEKLY_OVERSOLD"
    elif best is None:
        res["reason"] = "NO_DAILY_OVERSOLD_PAIR"
    elif w_idx is None:
        res["reason"] = "NO_W_SIGNAL"
    elif not bowl_ok:
        res["reason"] = "NO_BOWL"
    elif too_far:
        res["reason"] = "TOO_FAR_ABOVE_MA20"
    elif chase:
        res["reason"] = "CHASE_DAY"
    elif cycle_done:
        res["reason"] = "CYCLE_DONE"
    else:
        res["ok"] = True
        res["reason"] = "WATCH_ADDED"
    return res


def signal_priority_key(sig: Dict[str, Any]) -> Tuple[int, int, int, float, float]:
    """20/60 GC(M1) > 임박(M2), W 신호 많은 순, 거래대금 증가율(5일/60일) 등급 높은 순, 주봉 RSI 최저 낮은 순, MA20 이격 작은 순."""
    m_rank = 0 if sig.get("m_type") == "M1_GC" else 1
    w_count = int(sig.get("w_count") or 0)
    tv_tier = tv_ratio_tier(sig.get("tv_ratio_5_60"))
    rsi_w = sig.get("r_week_rsi")
    if rsi_w is None:
        rsi_w = sig.get("rsi_w_min")
    rsi_w = float(rsi_w) if rsi_w is not None else 100.0
    above = sig.get("above_ma20_pct")
    above = abs(float(above)) if above is not None else 100.0
    return (m_rank, -w_count, -tv_tier, rsi_w, above)


def entry_cap(signal_close: float, p: Dict[str, Any]) -> int:
    return int(math.floor(float(signal_close) * (1.0 + float(p["max_entry_above_pct"]) / 100.0)))


def hard_stop_price(ma20: float, p: Dict[str, Any]) -> int:
    return int(round(float(ma20) * (1.0 - float(p["intraday_hard_buf_pct"]) / 100.0)))


def size_position(
    equity: float,
    entry: float,
    stop_ref: float,
    p: Dict[str, Any],
    *,
    max_invest_amount: int = 0,
) -> Dict[str, Any]:
    """리스크 1%/거래 · 종목당 20% · 손절 거리 최소 min_risk_pct."""
    out = {"qty": 0, "risk_amount": 0, "risk_dist": 0.0}
    try:
        equity = float(equity or 0)
        entry = float(entry or 0)
        stop_ref = float(stop_ref or 0)
    except (TypeError, ValueError):
        return out
    if equity <= 0 or entry <= 0:
        return out
    risk_amount = equity * float(p["risk_per_trade_pct"]) / 100.0
    risk_dist = max(entry - stop_ref, entry * float(p["min_risk_pct"]) / 100.0)
    if risk_dist <= 0:
        return out
    qty = int(math.floor(risk_amount / risk_dist))
    qty = min(qty, int(math.floor(equity * float(p["max_position_pct"]) / 100.0 / entry)))
    cap_amt = int(max_invest_amount or p.get("max_invest_amount") or 0)
    if cap_amt > 0:
        qty = min(qty, int(math.floor(cap_amt / entry)))
    out.update(qty=max(0, qty), risk_amount=int(risk_amount), risk_dist=round(risk_dist, 2))
    return out


# ───────────────────────── 3분봉 눌림 ─────────────────────────

def _bar_dt(bar: Dict[str, Any]) -> Optional[datetime]:
    raw = str(bar.get("timestamp") or "").strip()[:19]
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def normalize_minute_bars(
    raw: Iterable[Dict[str, Any]], *, session_start: str = "09:00", session_end: str = "15:30",
) -> List[Dict[str, Any]]:
    """분봉 dict → 정규장(09:00~15:30) 봉만, 시각 오름차순, 중복 제거. NXT 프리·애프터 봉 제외."""
    s_min, e_min = _hm_to_min(session_start), _hm_to_min(session_end)
    by_ts: Dict[datetime, Dict[str, Any]] = {}
    for b in raw or []:
        dt = _bar_dt(b)
        if dt is None:
            continue
        hm = dt.hour * 60 + dt.minute
        if hm < s_min or hm >= e_min:
            continue
        try:
            c = float(b.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        o = float(b.get("open") or 0) or c
        h = float(b.get("high") or 0) or max(o, c)
        lo = float(b.get("low") or 0) or min(o, c)
        by_ts[dt] = {
            "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "dt": dt,
            "open": o,
            "high": max(h, o, c),
            "low": min(lo, o, c),
            "close": c,
            "volume": float(b.get("volume") or 0),
        }
    return [by_ts[k] for k in sorted(by_ts)]


def evaluate_pullback(
    bars: Sequence[Dict[str, Any]],
    *,
    today: date,
    now: datetime,
    ma20_d: float,
    signal_close: float,
    cap: float,
    p: Dict[str, Any],
    interval_min: int = 3,
) -> Dict[str, Any]:
    """D+1 장중 3분봉 눌림 판정 (확정 봉만, 전일 봉 포함 EMA20).

    Returns: {action: BUY|WAIT|EXPIRE, reason, ...}
    BUY 는 가장 최근 확정 봉에서 반등이 확정됐을 때만 (놓친 과거 확정은 소비하고 계속 감시).
    """
    out: Dict[str, Any] = {"action": "WAIT", "reason": None, "events": []}
    now_naive = now.replace(tzinfo=None)
    done = [b for b in bars if b["dt"] + timedelta(minutes=interval_min) <= now_naive]
    if not done:
        return out
    ema = ema_series([b["close"] for b in done], int(p["pb_ema"]))
    idx_today = [i for i, b in enumerate(done) if b["dt"].date() == today]
    pb_end_min = _hm_to_min(p["pb_end"])
    if not idx_today:
        if now.hour * 60 + now.minute >= pb_end_min:
            out.update(action="EXPIRE", reason="NO_PULLBACK")
        return out

    day_open = done[idx_today[0]]["open"]
    out["day_open"] = int(round(day_open))
    if day_open < ma20_d:
        out.update(action="EXPIRE", reason="OPEN_BELOW_MA20")
        return out

    pb_start_min = _hm_to_min(p["pb_start"])
    base = max(day_open, float(signal_close)) * (1.0 + float(p["pb_min_rise_pct"]) / 100.0)
    buf = 1.0 + float(p["pb_touch_buf_pct"]) / 100.0
    dmin, dmax = float(p["pb_min_depth_pct"]), float(p["pb_max_depth_pct"])
    confirm_n = max(0, int(p["pb_confirm_bars"]))

    day_high = 0.0
    had_above = False
    pending: Optional[int] = None
    pending_info: Dict[str, Any] = {}
    last_i = idx_today[-1]
    for pos, i in enumerate(idx_today):
        b = done[i]
        e = ema[i]
        hm = b["dt"].hour * 60 + b["dt"].minute
        if b["close"] < ma20_d:
            out.update(action="EXPIRE", reason="BELOW_DAILY_MA20", at=b["timestamp"])
            return out
        day_high = max(day_high, b["high"])
        eligible = hm >= pb_start_min and hm + interval_min <= pb_end_min
        if eligible:
            if pending is not None and pos - pending > confirm_n:
                pending = None
            depth = (day_high - b["low"]) / day_high * 100.0 if day_high > 0 else 0.0
            touch = (
                had_above
                and day_high >= base
                and e is not None
                and b["low"] <= e * buf
                and dmin <= depth <= dmax
            )
            if touch:
                pending = pos
                pending_info = {
                    "pb_day_high": int(round(day_high)),
                    "pb_low": int(round(b["low"])),
                    "pb_depth_pct": round(depth, 2),
                    "pb_touch_time": b["timestamp"],
                }
            if pending is not None and pos >= 1:
                prev = done[idx_today[pos - 1]]
                confirm = b["close"] > b["open"] and e is not None and b["close"] > e and b["close"] > prev["high"]
                if confirm:
                    if b["close"] > cap:
                        out["events"].append({"reason": "ENTRY_ABOVE_CAP", "at": b["timestamp"], "close": b["close"]})
                        pending = None
                    elif i == last_i:
                        out.update(
                            action="BUY",
                            reason="PULLBACK_3M",
                            entry_price=int(round(b["close"])),
                            pb_ema20_3m=round(e, 2),
                            pb_confirm_time=b["timestamp"],
                            **pending_info,
                        )
                        return out
                    else:
                        out["events"].append({"reason": "MISSED_CONFIRM", "at": b["timestamp"]})
                        pending = None
        if e is not None and b["close"] > e:
            had_above = True

    out["day_high"] = int(round(day_high))
    out["ema20_3m"] = round(ema[last_i], 2) if ema[last_i] is not None else None
    out["rise_ok"] = day_high >= base
    if now.hour * 60 + now.minute >= pb_end_min:
        out.update(action="EXPIRE", reason="NO_PULLBACK")
    elif out["events"] and out["events"][-1]["reason"] == "ENTRY_ABOVE_CAP":
        out["reason"] = "ENTRY_ABOVE_CAP"
    return out


# ───────────────────────── 청산 ─────────────────────────

def surge_day(bars: Sequence[Dict[str, Any]], entry_date: Optional[date], p: Dict[str, Any]) -> Optional[date]:
    """매수일 이후(당일 포함) 첫 급등 양봉일: 전일 종가 대비 +surge_day_pct% 이상 · 종가 > 시가."""
    pct = float(p.get("surge_day_pct") or 0.0)
    if pct <= 0 or entry_date is None:
        return None
    for i in range(1, len(bars)):
        b = bars[i]
        if b["date"] < entry_date:
            continue
        prev = float(bars[i - 1]["close"])
        if prev > 0 and float(b["close"]) > float(b["open"]) and float(b["close"]) >= prev * (1.0 + pct / 100.0):
            return b["date"]
    return None


def evaluate_daily_exit(
    bars: Sequence[Dict[str, Any]],
    p: Dict[str, Any],
    *,
    entry_date: Optional[date],
) -> Dict[str, Any]:
    """일봉 기준 청산 판정. EXIT_MA10(급등 후) > EXIT_MA20 > MAX_HOLD. hard_price = 오늘 MA20 × (1 − 3%).

    급등 양봉(surge_day) 이후로는 surge_exit_ma(10)일선 종가 이탈도 청산. 진행 중 봉을 넣으면 장중 판정.
    """
    out: Dict[str, Any] = {
        "reason": None, "hard_price": None, "ma20": None, "close": None, "hold_days": 0,
        "surge_date": None, "exit_ma_n": int(p["exit_ma"]), "exit_ma": None,
    }
    if not bars:
        return out
    closes = [float(b["close"]) for b in bars]
    ma = sma_series(closes, int(p["exit_ma"]))
    t = len(bars) - 1
    if ma[t] is None:
        return out
    out["ma20"] = round(ma[t], 2)
    out["exit_ma"] = out["ma20"]
    out["close"] = int(round(closes[t]))
    out["hard_price"] = hard_stop_price(ma[t], p)
    if entry_date is not None:
        out["hold_days"] = sum(1 for b in bars if b["date"] > entry_date)
    s_day = surge_day(bars, entry_date, p)
    ma_s: List[Optional[float]] = []
    if s_day is not None:
        out["surge_date"] = _fmt_d(s_day)
        ma_s = sma_series(closes, int(p["surge_exit_ma"]))
        if ma_s[t] is not None:
            out["exit_ma_n"] = int(p["surge_exit_ma"])
            out["exit_ma"] = round(ma_s[t], 2)
    # 매수 당일 종가는 판정 대상 아님 (매수 다음 날부터)
    if entry_date is not None and bars[t]["date"] <= entry_date:
        return out

    need = max(1, int(p["ma_exit_close_days"]))
    buf = float(p.get("ma_exit_buffer_pct") or 0.0) / 100.0

    def _below_days(series: List[Optional[float]], since: Optional[date]) -> int:
        n = 0
        for i in range(t, -1, -1):
            if since is not None and bars[i]["date"] <= since:
                break
            if series[i] is not None and closes[i] < series[i] * (1.0 - buf):
                n += 1
            else:
                break
        return n

    if s_day is not None and _below_days(ma_s, s_day) >= need:
        out["reason"] = "EXIT_MA10"
        return out
    if _below_days(ma, entry_date) >= need:
        out["reason"] = "EXIT_MA20"
        return out
    max_hold = int(p.get("max_hold_days") or 0)
    if max_hold > 0 and out["hold_days"] >= max_hold:
        out["reason"] = "MAX_HOLD"
    return out


def check_hard_stop(current_price: float, hard_price: Optional[float]) -> bool:
    return bool(hard_price) and float(current_price) > 0 and float(current_price) <= float(hard_price)


# ───────────────────────── 리포트 문자열 ─────────────────────────

def _md_short(iso: Optional[str]) -> str:
    return f" {iso[5:7]}/{iso[8:10]}" if iso and len(iso) >= 10 else ""


def manual_status_line(code: str, name: str, sig: Dict[str, Any], p: Dict[str, Any]) -> str:
    """수동 종목 단계별 현황 한 줄."""
    reason = sig.get("reason") or ""
    if reason == "INSUFFICIENT_BARS":
        return f"{code} {name}  일봉 부족 ({sig.get('bars', 0)}봉)"
    ck = sig.get("checks") or {}
    parts: List[str] = []
    rw = "✓" if ck.get("r_weekly") else f"✗ (최저 {sig.get('rsi_w_min')})"
    rd = "✓" if ck.get("r_daily") else "✗"
    parts.append(f"R 주봉 {rw} 일봉 {rd}")
    parts.append(f"W ✓{sig.get('w_count') or 1}회" if ck.get("w") else "W ✗")
    parts.append(f"M(MACD) ✓{_md_short(sig.get('macd_gc_date'))}" if ck.get("macd_gc") else "M(MACD) ✗")
    m = ck.get("m_type")
    if m:
        parts.append(f"20/60 {'GC' if m == 'M1_GC' else '임박'} ✓")
    else:
        gap = sig.get("gap_pct")
        gap_s = f" 이격 {gap:.1f}%" if isinstance(gap, (int, float)) else ""
        parts.append(f"20/60 ✗{gap_s}")
    slope = sig.get("ma60_slope_pct")
    if slope is not None:
        parts.append(f"60선 {slope:+.1f}% {'✓' if ck.get('ma60_ok') else '✗'}")
    tv_ratio = sig.get("tv_ratio_5_60")
    if isinstance(tv_ratio, (int, float)):
        parts.append(f"거래대금 5/60일 ×{tv_ratio:.2f}")
    tail = "→ 감시 등록" if sig.get("ok") else f"→ {REASON_KO.get(reason, reason)} ({reason})"
    return f"{code} {name}  " + " · ".join(parts) + f"  {tail}"


# ───────────────────────── 상태 저장소 ─────────────────────────

class RwmStateStore:
    """logs/_rwm_bowl_state.json — 감시 목록·대기 청산·장후 리포트.

    그릇·R·W 상태는 저장하지 않는다 (매일 일봉으로 재계산).
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = path or _STATE_PATH
        self.data: Dict[str, Any] = {}
        self.load()

    def _empty(self) -> Dict[str, Any]:
        return {
            "watch": {},
            "exits": {},
            "last_batch": {},
            "manual_report": {},
            "near": [],
            "condition_cache": {},
            "warned": {},
            "r_pool": {},
        }

    def load(self) -> None:
        data = self._empty()
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data.update(raw)
        except Exception:
            pass
        self.data = data

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    # watch
    def watch_rows(self) -> Dict[str, Dict[str, Any]]:
        return self.data.setdefault("watch", {})

    def active_watch(self, today: date) -> Dict[str, Dict[str, Any]]:
        iso = today.isoformat()
        return {
            c: w for c, w in self.watch_rows().items()
            if w.get("status") == "WATCH" and w.get("valid_date") == iso
        }

    def set_watch(self, code: str, row: Dict[str, Any]) -> None:
        self.watch_rows()[code] = row
        self.save()

    def update_watch(self, code: str, **fields: Any) -> Optional[Dict[str, Any]]:
        row = self.watch_rows().get(code)
        if row is None:
            return None
        row.update(fields)
        self.save()
        return row

    def prune_watch(self, keep_from: date) -> None:
        """valid_date 가 keep_from 이전인 감시 행 삭제 (이력은 리포트로 충분)."""
        iso = keep_from.isoformat()
        rows = self.watch_rows()
        for c in [c for c, w in rows.items() if str(w.get("valid_date") or "") < iso]:
            rows.pop(c, None)

    # 바닥 후보 풀 (code → name, first_seen, last_seen, 마지막 판정)
    def r_pool(self) -> Dict[str, Dict[str, Any]]:
        return self.data.setdefault("r_pool", {})

    # exits
    def exits(self) -> Dict[str, Dict[str, Any]]:
        return self.data.setdefault("exits", {})

    def pending_exit(self, position_id: int) -> Optional[Dict[str, Any]]:
        return self.exits().get(str(position_id))

    def set_exit(self, position_id: int, row: Dict[str, Any]) -> None:
        self.exits()[str(position_id)] = row
        self.save()

    def clear_exit(self, position_id: int) -> None:
        if self.exits().pop(str(position_id), None) is not None:
            self.save()


_STORE: Optional[RwmStateStore] = None


def get_state_store() -> RwmStateStore:
    global _STORE
    if _STORE is None:
        _STORE = RwmStateStore()
    return _STORE
