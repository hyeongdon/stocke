"""MA1592 (15/92 홀드) 순수 로직.

유니버스: HTS 조건식(1592매매) 편입(관찰).
지표: **3분봉 EMA15 / EMA92** (기본). 1차는 **GC(EMA15>EMA92)** 확인 후 3단 분할(15%→35%→50%).
  T2: 15분봉 이격 (종가−EMA15)/EMA15 ≥ 1% → 35%
  T3: 2차 후 EMA92 유지 + EMA15 눌림 반등(기본) → 잔여 50%
    (scale_leg3_mode=hold 시 레거시: N개 15분봉 유지)
전고 50% 익절 후 잔량은 impulse 뒤 급락+큰이탈로만 청산.
시세 전 손절은 급락+DC(EMA15≤EMA92) 또는 %손절.

외부 I/O 없음(유니버스 JSON 제외). 유닛테스트·게이트·StopLoss에서 재사용.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from utils.ema_fractal import ema_series

DEFAULT_PARAMS: Dict[str, Any] = {
    "ma_fast": 15,
    "ma_slow": 92,
    "ma_type": "ema",
    "ma_source": "bar",
    "exec_tf": "3M",
    "gc_confirm": "cross_close",
    "require_ma_slope_up": True,
    "min_trading_value": 5_000_000_000,
    # 기본: 관찰 후 3분봉 GC(EMA15>EMA92)로 1차. 레거시 터치매수는 hold_mode=no_break_then_touch
    "hold_mode": "scale_in_gc",
    # gc_above(기본) | price_lead — 1차 트리거
    "entry_trigger": "gc_above",
    # gc_above: 교차 봉 포함 최대 허용 경과 봉 (0=교차봉만, 2=교차+2봉)
    "gc_entry_max_bars": 2,
    # gc_above: GC 직후 종가가 EMA15 대비 이격 상한(%). 미설정 시 price_lead_near_pct 사용
    # EMA15가 EMA92 대비 이 이격(%) 이내(아래)이거나 이미 상회 → 근접
    "price_lead_near_pct": 1.5,
    # 이격이 이보다 크면 관찰 장부 폐기 (너무 먼 데드크로스)
    "price_lead_far_pct": 3.0,
    # 장부 DC 정리 봉주기 — 키움 API는 2분봉 없음(1·3·5·10·15분). 기본 3분.
    "ledger_purge_tf": "3M",
    # L3 관찰 장부 상한 (편입·스캔·DC정리 대상)
    "l1_limit": 10,
    "hold_bars": 6,
    "touch_mode": "wick",
    "touch_buffer_pct": 0.15,
    "entry_confirm": "bounce_candle",
    "require_bullish_candle": True,
    "break_before_entry_pct": 0.4,
    "leg1_pct": 15.0,
    "leg2_pct": 35.0,
    "leg3_pct": 50.0,
    "scale_tf": "15M",
    "scale_gap_pct": 1.0,
    "scale_leg3_mode": "pullback",  # pullback | hold
    "scale_hold_bars": 2,  # T3 hold 모드: 2차 후 유지 봉수(기본 2≈30분)
    "prev_high_mode": "swing_lookback",
    "prev_high_lookback_bars": 200,
    "prev_high_lookback_days": 20,
    "tp1_frac": 0.5,
    "take_profit_mode": "prev_high_half",
    "take_profit_pct": 4.0,
    "tp_trigger": "last",
    "tp_fill": "market",
    "tp_same_bar_priority": "tp",
    "tp_fallback": "hard_pct",
    "stop_mode": "ma_or_pct",
    "stop_pct": 4.0,
    "hard_break_pct": 1.0,
    "bearish_exit_pct": 1.0,
    "large_break_pct": 0.7,
    "impulse_min_pct": 2.0,
    "crash_pct": 1.8,
    "crash_bars": 3,
    "setup_expire_days": 8,
    "setup_expire_bars": 0,
    "max_hold_days": 10,
    "flatten_eod": True,
    "entry_fill": "next_open",
    "risk_per_trade_pct": 2.0,
    "max_invest_amount_cap": True,
}

SKIP_REASONS = frozenset({
    "NO_GC", "SLOPE_DOWN", "LOW_VALUE", "MA15_BREAK_PRE", "MA92_BREAK_PRE", "TREND_LOST", "NO_BOUNCE",
    "SETUP_EXPIRED", "ALREADY_IN_POSITION", "RISK_LIMIT",
    "WAIT_PRICE_LEAD", "NOT_NEAR", "BELOW_MA", "BELOW_MA92", "FAR_FROM_GC",
    "GC_STALE", "GC_CHASE",
    "L1_LIMIT",
})
EXIT_REASONS = frozenset({
    "TP1_HIGH", "TP1_GAP", "TP1_FALLBACK", "TP1_SKIP_QTY",
    "STOP_MA_DC_WIDEN", "STOP_MA_DC_CRASH", "STOP_MA_CRASH", "STOP_PCT", "MAX_HOLD", "EOD",
})

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_UNIVERSE_PATH = _PROJECT_ROOT / "logs" / "_ma1592_universe.json"


def merge_params(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = dict(DEFAULT_PARAMS)
    if overrides:
        out.update({k: v for k, v in overrides.items() if v is not None})
    return out


def sma(values: Sequence[float], period: int) -> Optional[float]:
    if period <= 0 or len(values) < period:
        return None
    chunk = values[-period:]
    return sum(float(x) for x in chunk) / float(period)


def sma_series(values: Sequence[float], period: int) -> List[Optional[float]]:
    n = len(values)
    out: List[Optional[float]] = [None] * n
    if period <= 0 or n < period:
        return out
    running = sum(float(values[i]) for i in range(period))
    out[period - 1] = running / period
    for i in range(period, n):
        running += float(values[i]) - float(values[i - period])
        out[i] = running / period
    return out


@dataclass(frozen=True)
class OverlayMA:
    ma15_live: float
    ma92_live: float
    ma15_yest: float
    ma92_yest: float

    @property
    def slope92(self) -> float:
        return self.ma92_live - self.ma92_yest


def compute_daily_overlay(
    daily_closes: Sequence[float],
    dayclose: float,
    *,
    fast: int = 15,
    slow: int = 92,
) -> Optional[OverlayMA]:
    """키움형: yest=확정봉 SMA, live=(직전 N-1 확정 + dayclose)."""
    closes = [float(c) for c in daily_closes if c is not None]
    if len(closes) < slow:
        return None
    ma15_yest = sma(closes, fast)
    ma92_yest = sma(closes, slow)
    if ma15_yest is None or ma92_yest is None:
        return None
    prev14 = closes[-(fast - 1):] if fast > 1 else []
    prev89 = closes[-(slow - 1):] if slow > 1 else []
    if len(prev14) < fast - 1 or len(prev89) < slow - 1:
        return None
    ma15_live = (sum(prev14) + float(dayclose)) / float(fast)
    ma92_live = (sum(prev89) + float(dayclose)) / float(slow)
    return OverlayMA(ma15_live, ma92_live, ma15_yest, ma92_yest)


def compute_bar_ma(
    closes_5m: Sequence[float],
    *,
    fast: int = 15,
    slow: int = 92,
    ma_type: str = "ema",
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """5분봉 MA. 기본 EMA. 반환: fast[t], slow[t], fast[t-1], slow[t-1]."""
    kind = str(ma_type or "ema").lower()
    if kind == "sma":
        s_fast = sma_series(closes_5m, fast)
        s_slow = sma_series(closes_5m, slow)
    else:
        s_fast = ema_series(closes_5m, fast)
        s_slow = ema_series(closes_5m, slow)
    if not s_fast or s_fast[-1] is None or s_slow[-1] is None:
        return None, None, None, None
    prev_f = s_fast[-2] if len(s_fast) >= 2 else None
    prev_s = s_slow[-2] if len(s_slow) >= 2 else None
    return s_fast[-1], s_slow[-1], prev_f, prev_s


def ema15_above_ema90(
    closes_5m: Sequence[float],
    *,
    fast: int = 15,
    slow: int = 92,
) -> Tuple[bool, Optional[float], Optional[float]]:
    """현재 확정봉 기준 EMA15 > EMA92 여부."""
    f, s, _, _ = compute_bar_ma(closes_5m, fast=fast, slow=slow, ma_type="ema")
    if f is None or s is None:
        return False, f, s
    return float(f) > float(s), float(f), float(s)


def is_golden_cross_overlay(
    overlay: OverlayMA,
    *,
    require_slope_up: bool = True,
) -> Tuple[bool, str]:
    if overlay.ma15_yest > overlay.ma92_yest:
        return False, "NO_GC"
    if not (overlay.ma15_live > overlay.ma92_live):
        return False, "NO_GC"
    if require_slope_up and overlay.slope92 < 0:
        return False, "SLOPE_DOWN"
    return True, "GC"


def is_golden_cross_bar(
    ma_fast_t: float,
    ma_slow_t: float,
    ma_fast_prev: Optional[float],
    ma_slow_prev: Optional[float],
    *,
    require_slope_up: bool = True,
) -> Tuple[bool, str]:
    if ma_fast_prev is None or ma_slow_prev is None:
        return False, "NO_GC"
    if not (ma_fast_prev <= ma_slow_prev and ma_fast_t > ma_slow_t):
        return False, "NO_GC"
    if require_slope_up and (ma_slow_t - ma_slow_prev) < 0:
        return False, "SLOPE_DOWN"
    return True, "GC"


def bars_since_golden_cross(
    closes: Sequence[float],
    *,
    fast: int = 15,
    slow: int = 92,
    ma_type: str = "ema",
    require_slope_up: bool = True,
    lookback: int = 8,
) -> Optional[int]:
    """최근 GC 교차 확정봉 이후 경과 봉 수. 교차 봉=0, 없으면 None."""
    vals = [float(c) for c in closes if c is not None and float(c) > 0]
    if len(vals) < slow + 1:
        return None
    kind = str(ma_type or "ema").lower()
    if kind == "sma":
        s_fast = sma_series(vals, fast)
        s_slow = sma_series(vals, slow)
    else:
        s_fast = ema_series(vals, fast)
        s_slow = ema_series(vals, slow)
    n = len(vals)
    start = max(slow, n - max(lookback, fast + 2))
    for i in range(n - 1, start - 1, -1):
        f_prev, s_prev = s_fast[i - 1], s_slow[i - 1]
        f_cur, s_cur = s_fast[i], s_slow[i]
        if f_prev is None or s_prev is None or f_cur is None or s_cur is None:
            continue
        ok, _ = is_golden_cross_bar(
            float(f_cur),
            float(s_cur),
            float(f_prev),
            float(s_prev),
            require_slope_up=require_slope_up,
        )
        if ok:
            return (n - 1) - i
    return None


def check_fresh_gc_entry(
    close: float,
    ma15: float,
    ma92: float,
    closes: Optional[Sequence[float]],
    *,
    ma15_prev: Optional[float] = None,
    ma92_prev: Optional[float] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str, str]:
    """gc_above 1차 매수 — 교차 봉 ±N봉, 이평 위, 과도 이격 거부.

    Returns:
        (ok, reason_code, detail_message)
    """
    p = merge_params(params)
    try:
        max_bars = max(0, int(p.get("gc_entry_max_bars") or 2))
    except (TypeError, ValueError):
        max_bars = 2
    try:
        max_gap = float(
            p.get("gc_entry_max_price_gap_pct")
            if p.get("gc_entry_max_price_gap_pct") is not None
            else p.get("price_lead_near_pct") or 1.5
        )
    except (TypeError, ValueError):
        max_gap = 1.5
    require_slope = bool(p.get("require_ma_slope_up"))
    fast = int(p.get("ma_fast") or 15)
    slow = int(p.get("ma_slow") or 92)
    ma_type = str(p.get("ma_type") or "ema")

    if float(ma15) <= float(ma92):
        return False, "NO_GC", "GC 대기 (EMA15≤EMA92)"

    c = float(close)
    f = float(ma15)
    s = float(ma92)
    if c <= f or c <= s:
        return False, "BELOW_MA", "종가가 EMA15·92 미만"

    chase_gap = gap_pct_above_ema(c, f)
    if chase_gap > max_gap:
        return (
            False,
            "GC_CHASE",
            f"GC 직후 이격 과다 {chase_gap:.2f}% (>{max_gap:.2f}%)",
        )

    bars_since: Optional[int] = None
    if closes:
        bars_since = bars_since_golden_cross(
            closes,
            fast=fast,
            slow=slow,
            ma_type=ma_type,
            require_slope_up=require_slope,
            lookback=max_bars + 6,
        )
    elif ma15_prev is not None and ma92_prev is not None:
        ok_cross, _ = is_golden_cross_bar(
            f, s, float(ma15_prev), float(ma92_prev),
            require_slope_up=require_slope,
        )
        bars_since = 0 if ok_cross else None

    if bars_since is None:
        return False, "GC_STALE", "최근 교차 봉 없음"

    if bars_since > max_bars:
        return (
            False,
            "GC_STALE",
            f"교차 후 {bars_since}봉 경과 (허용 {max_bars}봉)",
        )

    return True, "GC_FRESH", f"GC 교차 구간 ({bars_since}봉 전)"


def normalize_ledger_stock_code(stock_code: str) -> Tuple[Optional[str], str]:
    """장부용 6자리 종목코드. 유효하지 않으면 (None, reason)."""
    raw = str(stock_code or "").replace("A", "").strip()
    if not raw.isdigit() or len(raw) != 6:
        return None, "INVALID_CODE"
    return raw, ""


def effective_l1_limit(params: Optional[Dict[str, Any]] = None) -> int:
    """L3 관찰 장부 상한 (편입·스캔)."""
    p = merge_params(params)
    try:
        n = int(p.get("l1_limit") or 10)
    except (TypeError, ValueError):
        n = 10
    return max(1, n)


def _l3_row_sort_ts(row: "UniverseRow") -> float:
    for key in (row.gc_at, row.in_at):
        if not key:
            continue
        try:
            s = str(key).strip().replace("Z", "+00:00")
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            continue
    return 0.0


def l3_rows_sorted(
    store: "Ma1592UniverseStore",
    *,
    newest_first: bool = True,
) -> List["UniverseRow"]:
    rows = [store.get(c) for c in store.l3_codes()]
    rows = [r for r in rows if r]
    rows.sort(key=_l3_row_sort_ts, reverse=newest_first)
    return rows


def select_l3_codes_for_scan(
    store: "Ma1592UniverseStore",
    *,
    params: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """스캔·DC정리 대상 L3 — 최신 편입 우선, l1_limit까지."""
    limit = effective_l1_limit(params)
    return [r.stock_code for r in l3_rows_sorted(store)[:limit]]


def is_l3_at_capacity(
    store: "Ma1592UniverseStore",
    *,
    params: Optional[Dict[str, Any]] = None,
) -> bool:
    return len(store.l3_codes()) >= effective_l1_limit(params)


def trim_l3_over_limit(
    store: "Ma1592UniverseStore",
    *,
    params: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """관찰 상한 초과분 — 오래된 종목부터 장부 제거(차트 조회 없음)."""
    limit = effective_l1_limit(params)
    rows = l3_rows_sorted(store, newest_first=True)
    removed: List[str] = []
    for row in rows[limit:]:
        code = str(row.stock_code or "").replace("A", "").strip()
        if not code:
            continue
        store.set_state(code, "DONE")
        removed.append(code)
    return removed


def validate_condition_ledger_insert(
    ma15: Optional[float],
    ma92: Optional[float],
    *,
    close: Optional[float] = None,
    closes: Optional[Sequence[float]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """조건식 편입 장부 등록 전 EMA 검증.

    - EMA15/92 계산 불가 → NO_MA
    - EMA15 ≤ EMA92 (데드크로스) → NO_GC
    - 이평 이격 > price_lead_far_pct → FAR_FROM_GC (역배열 근접만)
    - closes 제공 시 gc_above 신선도(교차 봉 ±N, 이평 위, 과도 이격) 추가 검증
    """
    try:
        f = float(ma15)
        s = float(ma92)
    except (TypeError, ValueError):
        return False, "NO_MA"
    if f <= 0 or s <= 0:
        return False, "NO_MA"
    if f <= s:
        return False, "NO_GC"
    p = merge_params(params)
    far = float(p.get("price_lead_far_pct") or 3.0)
    gap = ema_gap_pct_below(f, s)
    if gap > far:
        return False, "FAR_FROM_GC"

    trigger = str(p.get("entry_trigger") or "gc_above").strip().lower()
    if trigger in ("gc_above", "gc", "gc_confirm") and closes:
        try:
            px = float(close if close is not None else (closes[-1] if closes else 0))
        except (TypeError, ValueError):
            px = 0.0
        if px > 0:
            ok, code, _ = check_fresh_gc_entry(px, f, s, closes, params=p)
            if not ok:
                return False, code
    return True, "OK"


def ema_gap_pct_below(ma15: float, ma92: float) -> float:
    """EMA15가 EMA92 아래일 때 이격%(양수). 이미 상회면 0."""
    if float(ma92 or 0) <= 0:
        return 0.0
    if float(ma15) >= float(ma92):
        return 0.0
    return (float(ma92) - float(ma15)) / float(ma92) * 100.0


def preview_entry_gate_status(
    close: float,
    ma15: float,
    ma92: float,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """대시보드·후보 API — entry_trigger에 맞는 1차 게이트 미리보기."""
    p = merge_params(params)
    trigger = str(p.get("entry_trigger") or "gc_above").strip().lower()
    if trigger in ("gc_above", "gc", "gc_confirm"):
        try:
            f = float(ma15)
            s = float(ma92)
            c = float(close)
        except (TypeError, ValueError):
            return {
                "gate_ok": False,
                "reason_code": "NO_MA",
                "gate_reason": "이평 계산 불가",
                "ema_gap_pct": None,
                "price_vs_ma15_pct": None,
                "price_vs_ma92_pct": None,
                "ema15_above_ema92": None,
            }
        above = f > s
        price_vs_ma15 = round(gap_pct_above_ema(c, f), 2)
        gate_ok = above
        reason_code = "GC_ABOVE" if above else "NO_GC"
        gate_reason = "GC(EMA15>EMA92)" if above else "GC 대기 (EMA15≤EMA92)"
        if above and c <= f:
            gate_ok = False
            reason_code = "BELOW_MA"
            gate_reason = "종가가 EMA15·92 미만"
        elif above:
            try:
                max_gap = float(
                    p.get("gc_entry_max_price_gap_pct")
                    if p.get("gc_entry_max_price_gap_pct") is not None
                    else p.get("price_lead_near_pct") or 1.5
                )
            except (TypeError, ValueError):
                max_gap = 1.5
            if price_vs_ma15 > max_gap:
                gate_ok = False
                reason_code = "GC_CHASE"
                gate_reason = f"GC 직후 이격 과다 {price_vs_ma15:.2f}% (>{max_gap:.2f}%)"
        return {
            "gate_ok": gate_ok,
            "reason_code": reason_code,
            "gate_reason": gate_reason,
            "ema_gap_pct": round(ema_gap_pct_below(f, s), 2),
            "price_vs_ma15_pct": price_vs_ma15,
            "price_vs_ma92_pct": round(gap_pct_above_ema(c, s), 2),
            "ema15_above_ema92": above,
        }
    return preview_price_lead_status(close, ma15, ma92, params=p)


def preview_price_lead_status(
    close: float,
    ma15: float,
    ma92: float,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """대시보드·후보 API용 — 가격선행 돌파 게이트 미리보기."""
    p = merge_params(params)
    near = float(p.get("price_lead_near_pct") or 1.5)
    far = float(p.get("price_lead_far_pct") or 3.0)
    try:
        c = float(close)
        f = float(ma15)
        s = float(ma92)
    except (TypeError, ValueError):
        return {
            "gate_ok": False,
            "reason_code": "NO_MA",
            "gate_reason": "이평 계산 불가",
            "ema_gap_pct": None,
            "price_vs_ma15_pct": None,
            "price_vs_ma90_pct": None,
            "ema15_above_ema90": None,
        }
    gap = ema_gap_pct_below(f, s)
    ok, code = is_price_lead_breakout(c, f, s, near_pct=near, far_pct=far)
    reason_map = {
        "PRICE_LEAD": "돌파조건 충족",
        "NOT_NEAR": f"이평 이격 {gap:.2f}% (>{near}%)",
        "BELOW_MA": "종가가 EMA15·90 미만",
        "FAR_FROM_GC": f"이평 이격 과다 {gap:.2f}% (>{far}%)",
        "NO_MA": "이평 없음",
    }
    return {
        "gate_ok": ok,
        "reason_code": "PRICE_LEAD" if ok else (code or "WAIT_PRICE_LEAD"),
        "gate_reason": reason_map.get(code or "", str(code or "대기")),
        "ema_gap_pct": round(gap, 2),
        "price_vs_ma15_pct": round(gap_pct_above_ema(c, f), 2),
        "price_vs_ma92_pct": round(gap_pct_above_ema(c, s), 2),
        "ema15_above_ema92": f > s,
    }


def is_price_lead_breakout(
    close: float,
    ma15: float,
    ma92: float,
    *,
    near_pct: float = 1.0,
    far_pct: float = 3.0,
) -> Tuple[bool, str]:
    """가격 선행 돌파: 종가가 EMA15·EMA92 위 + 이평 근접.

    - 종가 > EMA15 이고 종가 > EMA92 (가격이 이평대를 상회)
    - EMA15가 EMA92 대비 near_pct% 이내(아래)이거나 이미 상회
    - 이격이 far_pct% 초과면 FAR_FROM_GC (장부 폐기 후보)
    """
    try:
        c = float(close)
        f = float(ma15)
        s = float(ma92)
    except (TypeError, ValueError):
        return False, "NO_MA"
    if c <= 0 or f <= 0 or s <= 0:
        return False, "NO_MA"

    gap = ema_gap_pct_below(f, s)
    far = max(0.0, float(far_pct or 0))
    near = max(0.0, float(near_pct or 0))
    if far > 0 and gap > far:
        return False, "FAR_FROM_GC"
    if gap > near:
        return False, "NOT_NEAR"
    if c <= s or c <= f:
        return False, "BELOW_MA"
    return True, "PRICE_LEAD"


def hard_break_below_ma15(close: float, ma15: float, break_pct: float) -> bool:
    if ma15 <= 0:
        return False
    return float(close) < float(ma15) * (1.0 - float(break_pct) / 100.0)


def hard_break_below_ma92(close: float, ma92: float, break_pct: float) -> bool:
    """확정봉 종가가 EMA92 대비 break_pct% 이상 하향 이탈."""
    if float(ma92 or 0) <= 0:
        return False
    return float(close) < float(ma92) * (1.0 - float(break_pct) / 100.0)


def hard_break_below_ma90(close: float, ma92: float, break_pct: float) -> bool:
    """Deprecated alias — use hard_break_below_ma92."""
    return hard_break_below_ma92(close, ma92, break_pct)


def structural_stop_ma(ma15: float, ma92: float) -> float:
    """보유 손절·사이징 기준선 — EMA92 우선."""
    if float(ma92 or 0) > 0:
        return float(ma92)
    return float(ma15 or 0)


def normalize_chart_tf(tf: Optional[str], *, default: str = "5M") -> str:
    """키움 분봉 코드 정규화 (2분봉 미지원 → 3M·1M 등)."""
    raw = str(tf or default).strip().upper()
    aliases = {
        "1": "1M", "1M": "1M", "M1": "1M", "1MIN": "1M",
        "3": "3M", "3M": "3M", "M3": "3M", "3MIN": "3M",
        "2": "3M", "2M": "3M", "M2": "3M", "2MIN": "3M",
        "5": "5M", "5M": "5M", "M5": "5M", "5MIN": "5M",
        "10": "10M", "10M": "10M", "M10": "10M", "10MIN": "10M",
        "15": "15M", "15M": "15M", "M15": "15M", "15MIN": "15M",
    }
    return aliases.get(raw, default)


def chart_tf_interval_minutes(tf: Optional[str], *, default: str = "5M") -> int:
    mapping = {"1M": 1, "3M": 3, "5M": 5, "10M": 10, "15M": 15}
    return mapping.get(normalize_chart_tf(tf, default=default), 5)


def is_far_from_gc_zone(
    ma15: float,
    ma92: float,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> bool:
    """price_lead L3 루프 — 이평 이격이 far_pct% 초과일 때만 즉시 폐기."""
    if float(ma92 or 0) <= 0 or float(ma15 or 0) <= 0:
        return False
    p = merge_params(params)
    far = float(p.get("price_lead_far_pct") or 3.0)
    return ema_gap_pct_below(float(ma15), float(ma92)) > far


def is_trend_lost(
    ma15: float,
    ma92: float,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> bool:
    """관찰 장부 제거용 추세 전환 — EMA15 ≤ EMA92 (데드크로스·역배열)."""
    del params  # 하위 호환 시그니처 유지
    if float(ma92 or 0) <= 0 or float(ma15 or 0) <= 0:
        return False
    return float(ma15) <= float(ma92)


def should_exit_ma_dc_after_scale(
    ma15: float,
    ma92: float,
    *,
    entry_leg: int = 1,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """2차(물타기) 반영 후 — 데드크로스 + 이평 이격 확대 시 즉시 청산."""
    if int(entry_leg or 1) < 2:
        return False, ""
    if not is_trend_lost(ma15, ma92, params=params):
        return False, ""
    if not is_far_from_gc_zone(ma15, ma92, params=params):
        return False, ""
    p = merge_params(params)
    far = float(p.get("price_lead_far_pct") or 3.0)
    gap = ema_gap_pct_below(ma15, ma92)
    return True, f"EMA15≤92·이격 {gap:.2f}%≥{far:.1f}%"


def below_ma90(close: float, ma92: float) -> bool:
    """확정봉 종가가 EMA92 아래인지."""
    if float(ma92 or 0) <= 0:
        return False
    return float(close) < float(ma92)


def gap_pct_above_ema(close: float, ema: float) -> float:
    """(종가 − EMA) / EMA × 100. EMA 위 이격(양수)."""
    if float(ema or 0) <= 0:
        return 0.0
    return (float(close) - float(ema)) / float(ema) * 100.0


def is_scale_gap_open(close: float, ema: float, gap_pct: float = 1.0) -> bool:
    """15분봉 이격 벌어짐: (종가−EMA15)/EMA15 ≥ gap_pct%."""
    return gap_pct_above_ema(close, ema) >= float(gap_pct)


def is_scale_hold_bar(close: float, ema: float, gap_pct: float = 1.0) -> bool:
    """T3 hold 모드 유지봉: 종가 ≥ EMA15 또는 이격 ≥ gap_pct%."""
    return float(close) >= float(ema) or is_scale_gap_open(close, ema, gap_pct)


def is_touch_bounce(
    bar: Dict[str, Any],
    ma15: float,
    *,
    touch_buffer_pct: float = 0.15,
    require_bullish: bool = True,
) -> bool:
    if ma15 <= 0:
        return False
    low = float(bar.get("low") or 0)
    close = float(bar.get("close") or 0)
    open_ = float(bar.get("open") or 0)
    touch_ceil = ma15 * (1.0 + float(touch_buffer_pct) / 100.0)
    if low > touch_ceil:
        return False
    if close <= ma15:
        return False
    if require_bullish and close <= open_:
        return False
    return True


def is_scale_pullback_bar(
    bar: Dict[str, Any],
    ma15: float,
    ma92: float,
    *,
    touch_buffer_pct: float = 0.15,
    break_pct: float = 0.4,
    require_bullish: bool = True,
) -> bool:
    """T3 눌림: EMA92 미이탈 + EMA15 터치 후 종가 반등(확정 15분봉)."""
    if float(ma15 or 0) <= 0 or float(ma92 or 0) <= 0:
        return False
    close = float(bar.get("close") or 0)
    low = float(bar.get("low") or 0)
    if hard_break_below_ma92(close, ma92, break_pct):
        return False
    ma_floor = float(ma92) * (1.0 - float(break_pct) / 100.0)
    if low < ma_floor:
        return False
    return is_touch_bounce(
        bar,
        ma15,
        touch_buffer_pct=touch_buffer_pct,
        require_bullish=require_bullish,
    )


def scale_leg_quantities(
    full_qty: int,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int, int]:
    """의도 전량 full_qty를 15/35/50(기본)으로 나눔. 합 = full_qty."""
    full = int(full_qty or 0)
    if full <= 0:
        return 0, 0, 0
    p = merge_params(params)
    f1 = max(0.0, float(p.get("leg1_pct") or 15.0) / 100.0)
    f2 = max(0.0, float(p.get("leg2_pct") or 35.0) / 100.0)
    if full == 1:
        return 1, 0, 0
    if full == 2:
        return 1, 1, 0
    q1 = max(1, int(math.floor(full * f1)))
    q2 = max(1, int(math.floor(full * f2)))
    if q1 + q2 >= full:
        q1 = max(1, full // 3)
        q2 = max(1, (full - q1) // 2)
    q3 = full - q1 - q2
    if q3 < 1:
        q3 = 1
        leftover = full - q3
        q1 = max(1, leftover // 2)
        q2 = leftover - q1
    return int(q1), int(q2), int(q3)


def scale_leg_qty(
    full_qty: int,
    entry_leg: int,
    params: Optional[Dict[str, Any]] = None,
) -> int:
    q1, q2, q3 = scale_leg_quantities(full_qty, params)
    leg = max(1, min(3, int(entry_leg or 1)))
    return (q1, q2, q3)[leg - 1]


def _bar_key(bar: Dict[str, Any]) -> str:
    for k in ("datetime", "time", "dt", "date"):
        v = bar.get(k)
        if v:
            return str(v)
    return f"{bar.get('open')}|{bar.get('close')}|{bar.get('high')}|{bar.get('low')}"


def evaluate_scale_add_on_15m(
    row: "UniverseRow",
    bars_15m: Sequence[Dict[str, Any]],
    ma15: float,
    *,
    ma92: Optional[float] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """보유 중 2·3차 평가 (확정 15분봉 + EMA15).

    T2: 이격 ≥ scale_gap_pct → leg2
    T3 (기본 pullback): EMA92 유지 + EMA15 터치 반등 → leg3
    T3 (hold): leg2 이후 유지 조건 scale_hold_bars회 누적 → leg3
    """
    p = merge_params(params)
    out: Dict[str, Any] = {
        "status": "wait",
        "buy": False,
        "entry_leg": 0,
        "reason": "",
        "reason_code": None,
        "gap_pct": 0.0,
        "scale_ok_bars": int(row.scale_ok_bars or 0),
    }
    entry_leg = int(row.entry_leg or 0)
    if entry_leg < 1:
        out.update(reason="1차 미체결", reason_code="NO_LEG1")
        return out
    if entry_leg >= 3:
        out.update(reason="3차 완료", reason_code="SCALE_DONE")
        return out
    if not bars_15m or float(ma15 or 0) <= 0:
        out.update(reason="15분봉/EMA 부족", reason_code="NO_BARS")
        return out

    gap_need = float(p.get("scale_gap_pct") or 1.0)
    hold_need = max(1, int(p.get("scale_hold_bars") or 2))
    last_key = str(row.scale_last_bar_at or "")

    # 미처리 확정봉만 (오래된 순)
    pending: List[Dict[str, Any]] = []
    for b in bars_15m:
        key = _bar_key(b)
        if last_key and key <= last_key:
            continue
        pending.append(b)

    if entry_leg == 1:
        bar = bars_15m[-1]
        close = float(bar.get("close") or 0)
        gap = gap_pct_above_ema(close, ma15)
        out["gap_pct"] = gap
        if is_scale_gap_open(close, ma15, gap_need):
            # 이 봉은 2차 트리거 — 3차 유지는 이후 봉부터
            row.scale_last_bar_at = _bar_key(bar)
            row.scale_ok_bars = 0
            out.update(
                status="pass",
                buy=True,
                entry_leg=2,
                reason=f"SCALE_LEG2 이격 {gap:.2f}%≥{gap_need}%",
            )
            return out
        out.update(reason=f"2차 이격 대기 ({gap:.2f}%<{gap_need}%)", reason_code="WAIT_GAP")
        return out

    leg3_mode = str(p.get("scale_leg3_mode") or "pullback").strip().lower()

    if leg3_mode in ("hold", "maintain", "scale_hold"):
        # entry_leg == 2 → T3 유지 카운트 (레거시)
        if not pending:
            bar = bars_15m[-1]
            close = float(bar.get("close") or 0)
            out["gap_pct"] = gap_pct_above_ema(close, ma15)
            out.update(
                reason=f"3차 유지 대기 ({row.scale_ok_bars}/{hold_need})",
                reason_code="WAIT_HOLD_SCALE",
            )
            return out

        for bar in pending:
            close = float(bar.get("close") or 0)
            out["gap_pct"] = gap_pct_above_ema(close, ma15)
            row.scale_last_bar_at = _bar_key(bar)
            if is_scale_hold_bar(close, ma15, gap_need):
                row.scale_ok_bars = int(row.scale_ok_bars or 0) + 1
            else:
                row.scale_ok_bars = 0
            out["scale_ok_bars"] = row.scale_ok_bars
            if row.scale_ok_bars >= hold_need:
                out.update(
                    status="pass",
                    buy=True,
                    entry_leg=3,
                    reason=f"SCALE_LEG3 유지 {row.scale_ok_bars}/{hold_need}",
                )
                return out

        out.update(
            reason=f"3차 유지 대기 ({row.scale_ok_bars}/{hold_need})",
            reason_code="WAIT_HOLD_SCALE",
        )
        return out

    # entry_leg == 2 → T3 눌림 (EMA92 유지 + EMA15 터치 반등)
    ma92_eff = float(ma92 if ma92 is not None else (getattr(row, "ma92", None) or 0))
    if ma92_eff <= 0:
        out.update(reason="3차 눌림 대기 (EMA92 없음)", reason_code="NO_MA92")
        return out

    touch_buf = float(p.get("touch_buffer_pct") or 0.15)
    break_pct = float(p.get("break_before_entry_pct") or 0.4)
    req_bull = bool(p.get("require_bullish_candle", True))

    if not pending:
        bar = bars_15m[-1]
        close = float(bar.get("close") or 0)
        out["gap_pct"] = gap_pct_above_ema(close, ma15)
        out.update(reason="3차 눌림 대기", reason_code="WAIT_PULLBACK")
        return out

    for bar in pending:
        close = float(bar.get("close") or 0)
        out["gap_pct"] = gap_pct_above_ema(close, ma15)
        row.scale_last_bar_at = _bar_key(bar)
        if is_scale_pullback_bar(
            bar,
            ma15,
            ma92_eff,
            touch_buffer_pct=touch_buf,
            break_pct=break_pct,
            require_bullish=req_bull,
        ):
            out.update(
                status="pass",
                buy=True,
                entry_leg=3,
                reason="SCALE_LEG3 EMA92유지+15선눌림",
            )
            return out

    out.update(reason="3차 눌림 대기", reason_code="WAIT_PULLBACK")
    return out


def compute_prev_high_daily(
    daily_highs: Sequence[Tuple[date, float]],
    gc_date: date,
    entry_date: date,
    lookback_days: int = 20,
) -> Optional[int]:
    start = gc_date - timedelta(days=max(0, int(lookback_days)))
    highs: List[float] = []
    for d, h in daily_highs:
        if d is None or h is None:
            continue
        if start <= d < entry_date:
            highs.append(float(h))
    if not highs:
        return None
    return int(max(highs))


def compute_prev_high_bars(
    highs_5m: Sequence[float],
    lookback_bars: int = 90,
) -> Optional[int]:
    if not highs_5m:
        return None
    n = max(1, int(lookback_bars))
    chunk = highs_5m[-n:] if len(highs_5m) >= n else highs_5m
    return int(max(float(h) for h in chunk))


def extract_bar_closes_highs(
    bars: Sequence[Dict[str, Any]],
) -> Tuple[List[float], List[float]]:
    closes: List[float] = []
    highs: List[float] = []
    for b in bars:
        try:
            c = float(b.get("close") or 0)
            h = float(b.get("high") or 0)
        except (TypeError, ValueError):
            c, h = 0.0, 0.0
        if c > 0:
            closes.append(c)
        if h > 0:
            highs.append(h)
    return closes, highs


def compute_live_metrics_from_closes(
    closes: Sequence[float],
    highs: Sequence[float],
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[float]]:
    """5분봉 종가·고가 시퀀스에서 EMA15/92·전고(lookback) 계산."""
    p = merge_params(params)
    ma_type = str(p.get("ma_type") or "ema")
    ma15, ma92, _, _ = compute_bar_ma(
        closes,
        fast=int(p.get("ma_fast") or 15),
        slow=int(p.get("ma_slow") or 92),
        ma_type=ma_type,
    )
    prev_high = compute_prev_high_bars(
        highs, int(p.get("prev_high_lookback_bars") or 90),
    )
    return {
        "ma15": float(ma15) if ma15 is not None else None,
        "ma92": float(ma92) if ma92 is not None else None,
        "prev_high": int(prev_high) if prev_high else None,
    }


async def fetch_live_universe_metrics(
    kiwoom_api,
    stock_code: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    cache_ttl_sec: float = 60.0,
    max_bars: int = 150,
    chart_tf: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    """분봉 조회 후 장부 표시용 EMA15/92·전고 계산 (기본 5분봉)."""
    from utils.ema_fractal import drop_forming_minute_bar

    p = merge_params(params)
    tf = normalize_chart_tf(chart_tf or p.get("exec_tf") or "5M")
    interval_min = chart_tf_interval_minutes(tf)
    code = str(stock_code or "").replace("A", "").strip()
    if not code:
        return {"ma15": None, "ma92": None, "prev_high": None}
    norm = getattr(kiwoom_api, "normalize_stock_code", lambda c: c)
    raw = await kiwoom_api.get_stock_chart_data(
        norm(code), tf, max_bars=max_bars, cache_ttl_sec=cache_ttl_sec,
    )
    bars = drop_forming_minute_bar(raw or [], now=now, interval_minutes=interval_min)
    slow = int(p.get("ma_slow") or 92)
    if not bars or len(bars) < slow:
        return {"ma15": None, "ma92": None, "prev_high": None}
    closes, highs = extract_bar_closes_highs(bars)
    out = compute_live_metrics_from_closes(closes, highs, params=p)
    bar_close = None
    if bars:
        try:
            bar_close = int(float(bars[-1].get("close") or 0))
        except (TypeError, ValueError):
            bar_close = None
    out["bar_close"] = bar_close if bar_close and bar_close > 0 else None
    out["closes"] = closes
    return out


def row_has_display_metrics(row: "UniverseRow") -> bool:
    return bool(
        row.ma15 and row.ma92 and row.prev_high
        and float(row.ma15) > 0 and float(row.ma92) > 0 and int(row.prev_high) > 0
    )


async def enrich_ma1592_display_row(
    kiwoom_api,
    row: "UniverseRow",
    *,
    params: Optional[Dict[str, Any]] = None,
    chart_ttl_sec: float = 60.0,
    force_chart: bool = False,
    condition_price: Optional[int] = None,
) -> Dict[str, Any]:
    """대시보드 표시용 — 장부 값 우선, 부족 시 5분봉 보강.

    실시간 현재가 TR은 호출하지 않는다. 표시·게이트용 가격은
    조건식 스냅샷(condition_price) → 5분봉 종가(bar_close) 순으로만 쓴다.
    """
    ma15 = float(row.ma15) if row.ma15 else None
    ma92 = float(row.ma92) if row.ma92 else None
    prev_high = int(row.prev_high) if row.prev_high else None
    bar_close = None
    metrics_updated = False

    if force_chart or not row_has_display_metrics(row):
        code = str(row.stock_code or "").replace("A", "").strip()
        live = await fetch_live_universe_metrics(
            kiwoom_api, code, params=params, cache_ttl_sec=chart_ttl_sec,
        )
        if live.get("ma15"):
            ma15 = live["ma15"]
            row.ma15 = float(ma15)
            metrics_updated = True
        if live.get("ma92"):
            ma92 = live["ma92"]
            row.ma92 = float(ma92)
            metrics_updated = True
        if live.get("prev_high"):
            prev_high = int(live["prev_high"])
            row.prev_high = prev_high
            metrics_updated = True
        bar_close = live.get("bar_close")

    current_price = None
    try:
        cp = int(condition_price or 0)
        if cp > 0:
            current_price = cp
    except (TypeError, ValueError):
        pass
    if not current_price and bar_close:
        current_price = int(bar_close)

    return {
        "ma15": ma15,
        "ma92": ma92,
        "prev_high": prev_high,
        "current_price": current_price,
        "bar_close": bar_close,
        "metrics_updated": metrics_updated,
    }


def tp1_price(
    prev_high: Optional[int], entry: int, take_profit_pct: float = 4.0,
) -> Tuple[int, str]:
    entry = int(entry)
    ph = int(prev_high or 0)
    if ph <= entry:
        px = int(round(entry * (1.0 + float(take_profit_pct) / 100.0)))
        return px, "TP1_FALLBACK"
    return ph, "TP1_HIGH"


def size_position(
    equity: float,
    entry: int,
    ma15: float,
    *,
    ma92: float = 0.0,
    risk_per_trade_pct: float = 2.0,
    stop_pct: float = 4.0,
    hard_break_pct: float = 1.0,
    max_invest_amount: int = 0,
    tp1_frac: float = 0.5,
) -> Dict[str, Any]:
    entry = int(entry)
    risk_amount = float(equity or 0) * (float(risk_per_trade_pct) / 100.0)
    stop_by_pct = entry * (1.0 - float(stop_pct) / 100.0)
    ma_stop = structural_stop_ma(ma15, ma92)
    stop_by_ma = (
        ma_stop * (1.0 - float(hard_break_pct) / 100.0) if ma_stop > 0 else stop_by_pct
    )
    stop_price = int(min(stop_by_pct, stop_by_ma))
    per_share = entry - stop_price
    qty = int(risk_amount // per_share) if per_share > 0 and risk_amount > 0 else 0
    if max_invest_amount and max_invest_amount > 0 and entry > 0:
        qty = min(qty, int(max_invest_amount // entry))
    qty = max(0, qty)
    if qty < 2:
        return {
            "qty": qty,
            "qty_tp1": 0,
            "qty_remain": qty,
            "stop_price": stop_price,
            "tp1_skip": True,
            "reason": "TP1_SKIP_QTY" if qty > 0 else "RISK_LIMIT",
        }
    qty_tp1 = max(1, int(math.floor(qty * float(tp1_frac))))
    qty_tp1 = min(qty_tp1, qty - 1)
    return {
        "qty": qty,
        "qty_tp1": qty_tp1,
        "qty_remain": qty - qty_tp1,
        "stop_price": stop_price,
        "tp1_skip": False,
        "reason": None,
    }


def mfe_pct(entry: int, peak: int) -> float:
    if entry <= 0:
        return 0.0
    return (float(peak) / float(entry) - 1.0) * 100.0


def update_impulse_seen(
    impulse_seen: bool,
    *,
    tp1_filled: bool,
    entry: int,
    peak: int,
    impulse_min_pct: float = 2.0,
) -> bool:
    if impulse_seen or tp1_filled:
        return True
    return mfe_pct(entry, peak) >= float(impulse_min_pct)


def is_crash(
    peak: int,
    close: float,
    bars_since_peak: int,
    *,
    crash_pct: float = 1.8,
    crash_bars: int = 3,
) -> bool:
    if peak <= 0:
        return False
    drop = (float(peak) - float(close)) / float(peak) * 100.0
    return drop >= float(crash_pct) and int(bars_since_peak) <= int(crash_bars)


def evaluate_exit(
    *,
    state: str,
    entry: int,
    last: float,
    close: float,
    open_: float,
    high: float,
    ma15: float,
    tp1_price_val: int,
    ma92: float = 0.0,
    tp1_filled: bool,
    impulse_seen: bool,
    peak: int,
    bars_since_peak: int,
    hold_days: int,
    session_end: bool = False,
    entry_leg: int = 1,
    params: Optional[Dict[str, Any]] = None,
    same_bar_backtest: bool = False,
    bar_open_3m: Optional[float] = None,
    bar_close_3m: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    p = merge_params(params)
    entry = int(entry)
    tp1 = int(tp1_price_val or 0)
    peak = max(int(peak or 0), int(high or 0), int(last or 0))
    impulse = update_impulse_seen(
        impulse_seen,
        tp1_filled=tp1_filled,
        entry=entry,
        peak=peak,
        impulse_min_pct=float(p["impulse_min_pct"]),
    )

    dc_wide, dc_detail = should_exit_ma_dc_after_scale(
        ma15, ma92, entry_leg=entry_leg, params=p,
    )
    if dc_wide:
        return {
            "reason": "STOP_MA_DC_WIDEN",
            "qty_frac": 1.0,
            "new_state": "DONE",
            "impulse_seen": impulse,
            "tp1_filled": tp1_filled,
            "peak": peak,
            "detail": dc_detail,
        }

    if not tp1_filled and tp1 > 0 and state in ("MANAGE_FULL",):
        hit = False
        reason = "TP1_HIGH"
        fill_px = tp1
        if same_bar_backtest:
            if float(open_) >= tp1:
                hit, reason, fill_px = True, "TP1_GAP", int(open_)
            elif float(high) >= tp1:
                hit, reason, fill_px = True, "TP1_HIGH", tp1
        else:
            if float(last) >= tp1:
                if float(open_) >= tp1:
                    reason = "TP1_GAP"
                hit = True
                fill_px = int(last) if reason == "TP1_GAP" else tp1
        if hit:
            return {
                "reason": reason,
                "qty_frac": float(p["tp1_frac"]),
                "new_state": "MANAGE_HALF",
                "impulse_seen": True,
                "tp1_filled": True,
                "fill_price": fill_px,
                "peak": peak,
            }

    if hold_days >= int(p["max_hold_days"]):
        return {
            "reason": "MAX_HOLD",
            "qty_frac": 1.0,
            "new_state": "DONE",
            "impulse_seen": impulse,
            "tp1_filled": tp1_filled,
            "peak": peak,
        }
    if p.get("flatten_eod") and session_end:
        return {
            "reason": "EOD",
            "qty_frac": 1.0,
            "new_state": "DONE",
            "impulse_seen": impulse,
            "tp1_filled": tp1_filled,
            "peak": peak,
        }

    bearish_open = float(bar_open_3m or 0)
    bearish_close = float(bar_close_3m or 0)
    ma15_value = float(ma15 or 0)
    bearish_drop = (
        (bearish_close - bearish_open) / bearish_open * 100.0
        if bearish_open > 0 else 0.0
    )
    if (
        bearish_open > 0
        and bearish_close > 0
        and ma15_value > 0
        and bearish_drop <= -float(p["bearish_exit_pct"])
        and bearish_close < ma15_value
    ):
        return {
            "reason": "STOP_3M_BEARISH_BELOW_MA15",
            "qty_frac": 1.0,
            "new_state": "DONE",
            "impulse_seen": impulse,
            "tp1_filled": tp1_filled,
            "peak": peak,
        }

    large = float(p["large_break_pct"])
    stop_pct = float(p["stop_pct"])
    ma_stop = structural_stop_ma(ma15, ma92)
    crash = is_crash(
        peak, close, bars_since_peak,
        crash_pct=float(p["crash_pct"]),
        crash_bars=int(p["crash_bars"]),
    )

    if not impulse:
        if crash and is_trend_lost(ma15, ma92, params=p):
            return {
                "reason": "STOP_MA_DC_CRASH",
                "qty_frac": 1.0,
                "new_state": "DONE",
                "impulse_seen": False,
                "tp1_filled": tp1_filled,
                "peak": peak,
            }
        if float(last) <= entry * (1.0 - stop_pct / 100.0):
            return {
                "reason": "STOP_PCT",
                "qty_frac": 1.0,
                "new_state": "DONE",
                "impulse_seen": False,
                "tp1_filled": tp1_filled,
                "peak": peak,
            }
        return None

    large_break = ma_stop > 0 and hard_break_below_ma92(close, ma_stop, large)
    if crash and large_break:
        return {
            "reason": "STOP_MA_CRASH",
            "qty_frac": 1.0,
            "new_state": "DONE",
            "impulse_seen": True,
            "tp1_filled": tp1_filled,
            "peak": peak,
        }
    if float(last) <= entry * (1.0 - stop_pct / 100.0):
        return {
            "reason": "STOP_PCT",
            "qty_frac": 1.0,
            "new_state": "DONE",
            "impulse_seen": True,
            "tp1_filled": tp1_filled,
            "peak": peak,
        }
    return None


@dataclass
class UniverseRow:
    stock_code: str
    stock_name: str = ""
    gc_at: str = ""
    gc_date: str = ""
    gc_price: int = 0
    # 조건식 편입(돌파 전) 스냅샷 — gc_at/gc_price는 1차 돌파 시 덮어쓸 수 있음
    in_at: str = ""
    in_price: int = 0
    in_ma15: float = 0.0
    in_ma92: float = 0.0
    ma15: float = 0.0
    ma92: float = 0.0
    prev_high: int = 0
    source: str = "volume_rank"
    state: str = "GC_WATCH"
    impulse_seen: bool = False
    tp1_filled: bool = False
    expire_date: str = ""
    ma15_broke: bool = False
    hold_ok_bars: int = 0
    bars_since_gc: int = 0
    peak_since_entry: int = 0
    bars_since_peak: int = 0
    entry_price: int = 0
    tp1_price: int = 0
    qty_tp1: int = 0
    remain_qty: int = 0
    strategy_id: int = 0
    # 3단 분할 (1=씨드 체결 후, 2=이격 추가, 3=유지 완료)
    entry_leg: int = 0
    planned_qty: int = 0
    scale_ok_bars: int = 0
    scale_last_bar_at: str = ""
    leg2_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "UniverseRow":
        raw = dict(d or {})
        if raw.get("ma92") is None and raw.get("ma90") is not None:
            raw["ma92"] = raw["ma90"]
        if raw.get("in_ma92") is None and raw.get("in_ma90") is not None:
            raw["in_ma92"] = raw["in_ma90"]
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in known})


class Ma1592UniverseStore:
    """P0: 프로세스 메모리 + logs/_ma1592_universe.json."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or _UNIVERSE_PATH
        self._rows: Dict[str, UniverseRow] = {}
        self.load()

    def load(self) -> None:
        try:
            path = self.path
            if not path.exists():
                legacy = path.with_name("_ma1590_universe.json")
                if legacy.exists():
                    path = legacy
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                rows = data.get("rows") if isinstance(data, dict) else data
                self._rows = {}
                for item in rows or []:
                    row = UniverseRow.from_dict(item)
                    if row.state != "DONE":
                        self._rows[row.stock_code] = row
        except Exception:
            self._rows = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"rows": [r.to_dict() for r in self._rows.values()]}
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except Exception:
            pass

    def get(self, stock_code: str) -> Optional[UniverseRow]:
        return self._rows.get(stock_code)

    def l3_codes(self) -> List[str]:
        return [c for c, r in self._rows.items() if r.state in ("GC_WATCH", "WAIT_HOLD")]

    def manage_codes(self) -> List[str]:
        return [
            c for c, r in self._rows.items()
            if r.state in ("MANAGE_FULL", "MANAGE_HALF")
        ]

    def all_rows(self) -> List[UniverseRow]:
        """활성 장부 전체 (DONE 제외 — load 시 이미 필터)."""
        return list(self._rows.values())

    def upsert(self, row: UniverseRow) -> None:
        existing = self._rows.get(row.stock_code)
        if (
            existing
            and existing.state in ("MANAGE_FULL", "MANAGE_HALF", "GC_WATCH", "WAIT_HOLD")
            and existing.gc_at
            and row.gc_at != existing.gc_at
        ):
            return
        self._rows[row.stock_code] = row
        self.save()

    def set_state(self, stock_code: str, state: str, **fields: Any) -> Optional[UniverseRow]:
        row = self._rows.get(stock_code)
        if not row:
            return None
        row.state = state
        for k, v in fields.items():
            if hasattr(row, k):
                setattr(row, k, v)
        if state == "DONE":
            self._rows.pop(stock_code, None)
        self.save()
        return row

    def expire_stale(self, today: Optional[date] = None) -> List[str]:
        today = today or date.today()
        expired = []
        for code, row in list(self._rows.items()):
            if not row.expire_date:
                continue
            try:
                exp = date.fromisoformat(row.expire_date[:10])
            except Exception:
                continue
            if today > exp and row.state in ("GC_WATCH", "WAIT_HOLD"):
                expired.append(code)
                self.set_state(code, "DONE")
        return expired


_STORE: Optional[Ma1592UniverseStore] = None


def get_universe_store() -> Ma1592UniverseStore:
    global _STORE
    if _STORE is None:
        _STORE = Ma1592UniverseStore()
    return _STORE


def evaluate_setup_on_bar(
    row: UniverseRow,
    bar: Dict[str, Any],
    ma15: float,
    *,
    ma92: Optional[float] = None,
    ma90: Optional[float] = None,
    ma15_prev: Optional[float] = None,
    ma92_prev: Optional[float] = None,
    closes: Optional[Sequence[float]] = None,
    params: Optional[Dict[str, Any]] = None,
    already_in_position: bool = False,
) -> Dict[str, Any]:
    """L3 한 봉 처리. T1(1차) BUY 시 status=pass.

    hold_mode=scale_in_gc(기본):
      entry_trigger=price_lead → 종가>EMA15·90 + 이평 근접 시 1차
      entry_trigger=gc_above   → 교차 봉 ±N봉 + 이평 위 + 과도 이격 거부
    hold_mode=no_break_then_touch: 기존 홀드→터치 반등.
    """
    if ma92 is None:
        ma92 = ma90
    p = merge_params(params)
    close = float(bar.get("close") or 0)
    out: Dict[str, Any] = {
        "status": "wait",
        "reason": "",
        "reason_code": None,
        "row": row,
        "buy": False,
        "entry_leg": 1,
    }

    if row.expire_date:
        try:
            if date.today() > date.fromisoformat(row.expire_date[:10]):
                row.state = "DONE"
                out.update(status="fail", reason="셋업 만료", reason_code="SETUP_EXPIRED")
                return out
        except Exception:
            pass

    if already_in_position:
        out.update(status="fail", reason="이미 보유/대기", reason_code="ALREADY_IN_POSITION")
        return out

    if row.state in ("GC_WATCH", "WAIT_HOLD"):
        if ma92 is not None:
            hold_mode = str(p.get("hold_mode") or "scale_in_gc").strip().lower()
            trigger = str(p.get("entry_trigger") or "price_lead").strip().lower()
            drop = False
            reason_code = None
            reason = ""
            # price_lead 5분 L3: 근접 역배열은 유지. DC 정리는 ledger_purge_tf 주기 루프.
            if hold_mode == "scale_in_gc" and trigger in (
                "price_lead", "price_lead_breakout", "lead",
            ):
                if is_far_from_gc_zone(float(ma15), float(ma92), params=p):
                    drop = True
                    reason_code = "FAR_FROM_GC"
                    reason = "이평 이격 과다 — 장부 제거"
            elif is_trend_lost(float(ma15), float(ma92), params=p):
                drop = True
                reason_code = "TREND_LOST"
                reason = "추세 전환(EMA15≤EMA92) — 장부 제거"
            if drop:
                row.state = "DONE"
                out.update(status="fail", reason=reason, reason_code=reason_code)
                return out

    row.bars_since_gc = int(row.bars_since_gc or 0) + 1
    hold_mode = str(p.get("hold_mode") or "scale_in_gc").strip().lower()

    if hold_mode == "scale_in_gc":
        if row.state == "GC_WATCH":
            trigger = str(p.get("entry_trigger") or "price_lead").strip().lower()
            if trigger in ("price_lead", "price_lead_breakout", "lead"):
                if ma92 is None or float(ma92 or 0) <= 0:
                    out.update(
                        reason="가격선행 대기 (EMA92 없음)",
                        reason_code="WAIT_PRICE_LEAD",
                    )
                    return out
                ok, code = is_price_lead_breakout(
                    close,
                    float(ma15),
                    float(ma92),
                    near_pct=float(p.get("price_lead_near_pct") or 1.5),
                    far_pct=float(p.get("price_lead_far_pct") or 3.0),
                )
                if not ok:
                    if code == "FAR_FROM_GC":
                        row.state = "DONE"
                        out.update(
                            status="fail",
                            reason="이평 이격 과다 — 장부 제거",
                            reason_code="FAR_FROM_GC",
                        )
                        return out
                    out.update(
                        reason=f"가격선행 대기 ({code})",
                        reason_code=code or "WAIT_PRICE_LEAD",
                    )
                    return out
                # 실제 진입 시각 스탬프 (조건식 편입 시각과 구분)
                row.gc_at = datetime.now().isoformat(timespec="seconds")
                try:
                    row.gc_price = int(close)
                except (TypeError, ValueError):
                    pass
                out.update(
                    status="pass",
                    buy=True,
                    entry_leg=1,
                    reason="SCALE_LEG1 가격선행돌파",
                    reason_code=None,
                )
                return out
            # gc_above: 교차 봉 ±N봉 + 이평 위 + 과도 이격 거부
            if ma92 is None or float(ma92 or 0) <= 0:
                out.update(
                    reason="GC 대기 (EMA92 없음)",
                    reason_code="NO_GC",
                )
                return out
            ok_gc, code_gc, msg_gc = check_fresh_gc_entry(
                close,
                float(ma15),
                float(ma92),
                closes,
                ma15_prev=ma15_prev,
                ma92_prev=ma92_prev,
                params=p,
            )
            if not ok_gc:
                if code_gc == "GC_STALE":
                    row.state = "DONE"
                    out.update(
                        status="fail",
                        reason=msg_gc,
                        reason_code=code_gc,
                    )
                else:
                    out.update(
                        reason=msg_gc,
                        reason_code=code_gc,
                    )
                return out
            row.gc_at = datetime.now().isoformat(timespec="seconds")
            try:
                row.gc_price = int(close)
            except (TypeError, ValueError):
                pass
            out.update(
                status="pass",
                buy=True,
                entry_leg=1,
                reason="SCALE_LEG1 GC교차",
                reason_code=None,
            )
            return out
        if row.state == "WAIT_HOLD":
            out.update(reason="1차 체결 대기", reason_code="LEG1_PENDING")
            return out
        out["reason"] = f"상태 {row.state}"
        return out

    # ----- legacy: no_break_then_touch -----
    if row.state == "GC_WATCH":
        if not hard_break_below_ma15(close, ma15, float(p["break_before_entry_pct"])):
            row.hold_ok_bars = int(row.hold_ok_bars or 0) + 1
        if row.hold_ok_bars >= int(p["hold_bars"]):
            row.state = "WAIT_HOLD"
            out["reason"] = "홀드 완료 — 15선 터치 대기"
        else:
            out["reason"] = f"GC 관찰 중 ({row.hold_ok_bars}/{p['hold_bars']})"
        return out

    if row.state == "WAIT_HOLD":
        if is_touch_bounce(
            bar,
            ma15,
            touch_buffer_pct=float(p["touch_buffer_pct"]),
            require_bullish=bool(p["require_bullish_candle"]),
        ):
            out.update(status="pass", buy=True, reason="HOLD_MA15", reason_code=None)
            return out
        out["reason"] = "15선 반등 대기"
        out["reason_code"] = "NO_BOUNCE"
        return out

    out["reason"] = f"상태 {row.state}"
    return out


def try_insert_l2_from_overlay(
    stock_code: str,
    stock_name: str,
    overlay: OverlayMA,
    *,
    dayclose: float,
    trading_value: float,
    source: str = "volume_rank",
    now: Optional[datetime] = None,
    params: Optional[Dict[str, Any]] = None,
    store: Optional[Ma1592UniverseStore] = None,
) -> Tuple[bool, str, Optional[UniverseRow]]:
    p = merge_params(params)
    store = store or get_universe_store()
    ok, code = is_golden_cross_overlay(
        overlay, require_slope_up=bool(p["require_ma_slope_up"]),
    )
    if not ok:
        return False, code, None
    if trading_value < float(p["min_trading_value"]):
        return False, "LOW_VALUE", None

    existing = store.get(stock_code)
    if existing and existing.state not in ("DONE",):
        return False, "ALREADY_IN_LEDGER", existing

    if is_l3_at_capacity(store, params=p):
        return False, "L1_LIMIT", None

    now = now or datetime.now()
    gc_date = now.date()
    expire = gc_date + timedelta(days=int(p["setup_expire_days"]))
    row = UniverseRow(
        stock_code=stock_code,
        stock_name=stock_name or stock_code,
        gc_at=now.isoformat(timespec="seconds"),
        gc_date=gc_date.isoformat(),
        gc_price=int(dayclose),
        ma15=float(overlay.ma15_live),
        ma92=float(overlay.ma92_live),
        source=source,
        state="GC_WATCH",
        expire_date=expire.isoformat(),
    )
    store.upsert(row)
    return True, "GC", row


def upsert_from_condition(
    stock_code: str,
    stock_name: str = "",
    *,
    price: int = 0,
    in_ma15: Optional[float] = None,
    in_ma92: Optional[float] = None,
    in_ma90: Optional[float] = None,
    closes: Optional[Sequence[float]] = None,
    source: str = "condition",
    condition_label: str = "",
    now: Optional[datetime] = None,
    params: Optional[Dict[str, Any]] = None,
    store: Optional[Ma1592UniverseStore] = None,
) -> Tuple[bool, str, Optional[UniverseRow]]:
    """HTS 조건식 편입 → L2 장부(GC_WATCH).

    5분봉 EMA15/92 스냅샷 필수. EMA15>EMA92(골든크로스)·이격 검증 통과 시에만 등록.
    """
    p = merge_params(params)
    store = store or get_universe_store()
    code, code_err = normalize_ledger_stock_code(stock_code)
    if not code:
        return False, code_err or "NO_CODE", None
    existing = store.get(code)
    if existing and existing.state not in ("DONE",):
        # 이름만 갱신
        if stock_name and existing.stock_name != stock_name:
            existing.stock_name = stock_name
            store.upsert(existing)
        return False, "ALREADY_IN_LEDGER", existing

    if is_l3_at_capacity(store, params=p):
        return False, "L1_LIMIT", None

    if in_ma92 is None and in_ma90 is not None:
        in_ma92 = in_ma90

    ok, vreason = validate_condition_ledger_insert(
        in_ma15,
        in_ma92,
        close=float(price or 0) if price else None,
        closes=closes,
        params=p,
    )
    if not ok:
        return False, vreason, None

    now = now or datetime.now()
    gc_date = now.date()
    expire = gc_date + timedelta(days=int(p["setup_expire_days"]))
    snap15 = float(in_ma15) if in_ma15 else 0.0
    snap90 = float(in_ma92) if in_ma92 else 0.0
    row = UniverseRow(
        stock_code=code,
        stock_name=stock_name or code,
        gc_at=now.isoformat(timespec="seconds"),
        gc_date=gc_date.isoformat(),
        gc_price=int(price or 0),
        in_at=now.isoformat(timespec="seconds"),
        in_price=int(price or 0),
        in_ma15=snap15,
        in_ma92=snap90,
        ma15=snap15,
        ma92=snap90,
        source=source,
        state="GC_WATCH",
        expire_date=expire.isoformat(),
    )
    store.upsert(row)
    try:
        from utils.auto_trade_activity_log import log_ma1592_ledger_insert
        log_ma1592_ledger_insert(
            code,
            stock_name or code,
            condition_label=condition_label,
            insert_source=source,
        )
    except Exception:
        pass
    return True, "CONDITION_IN", row


async def upsert_from_condition_async(
    kiwoom_api,
    stock_code: str,
    stock_name: str = "",
    *,
    price: int = 0,
    source: str = "condition",
    condition_label: str = "",
    now: Optional[datetime] = None,
    params: Optional[Dict[str, Any]] = None,
    store: Optional[Ma1592UniverseStore] = None,
    cache_ttl_sec: float = 60.0,
) -> Tuple[bool, str, Optional[UniverseRow]]:
    """조건식 편입 — 5분봉 EMA 스냅샷 조회 후 검증·장부 등록."""
    code, code_err = normalize_ledger_stock_code(stock_code)
    if not code:
        return False, code_err or "NO_CODE", None

    store = store or get_universe_store()
    existing = store.get(code)
    if existing and existing.state not in ("DONE",):
        if stock_name and existing.stock_name != stock_name:
            existing.stock_name = stock_name
            store.upsert(existing)
        return False, "ALREADY_IN_LEDGER", existing

    p = merge_params(params)
    if is_l3_at_capacity(store, params=p):
        return False, "L1_LIMIT", None

    live = await fetch_live_universe_metrics(
        kiwoom_api,
        code,
        params=params,
        now=now,
        cache_ttl_sec=cache_ttl_sec,
    )
    ma15 = live.get("ma15")
    ma92 = live.get("ma92")
    try:
        px = int(price or 0)
    except (TypeError, ValueError):
        px = 0
    if px <= 0:
        try:
            px = int(live.get("bar_close") or 0)
        except (TypeError, ValueError):
            px = 0

    return upsert_from_condition(
        code,
        stock_name,
        price=px,
        in_ma15=float(ma15) if ma15 else None,
        in_ma92=float(ma92) if ma92 else None,
        closes=live.get("closes"),
        source=source,
        condition_label=condition_label,
        now=now,
        params=params,
        store=store,
    )


def remove_on_trend_lost(
    stock_code: str,
    *,
    ma15: float,
    ma92: float,
    params: Optional[Dict[str, Any]] = None,
    store: Optional[Ma1592UniverseStore] = None,
) -> bool:
    """추세 전환 → 관찰 장부 제거. 보유(MANAGE_*)는 유지.

    조건식 이탈·종가 EMA92 이탈만으로는 빼지 않는다.
    """
    store = store or get_universe_store()
    code = str(stock_code or "").replace("A", "").strip()
    row = store.get(code)
    if not row:
        return False
    if row.state in ("MANAGE_FULL", "MANAGE_HALF"):
        return False
    if row.state not in ("GC_WATCH", "WAIT_HOLD"):
        return False
    if not is_trend_lost(ma15, ma92, params=params):
        return False
    store.set_state(code, "DONE")
    return True


def remove_on_ema90_break(
    stock_code: str,
    *,
    ma15: Optional[float] = None,
    ma92: Optional[float] = None,
    ma90: Optional[float] = None,
    close: Optional[float] = None,
    break_pct: float = 0.4,
    params: Optional[Dict[str, Any]] = None,
    store: Optional[Ma1592UniverseStore] = None,
) -> bool:
    """관찰 장부 제거 — 추세 전환 기준 (remove_on_trend_lost 별칭)."""
    if ma92 is None and ma90 is not None:
        ma92 = ma90
    if ma15 is None or ma92 is None:
        return False
    return remove_on_trend_lost(
        stock_code,
        ma15=float(ma15),
        ma92=float(ma92),
        params=params,
        store=store,
    )


def remove_on_below_ma90(
    stock_code: str,
    *,
    ma15: Optional[float] = None,
    ma92: Optional[float] = None,
    ma90: Optional[float] = None,
    close: Optional[float] = None,
    break_pct: float = 0.4,
    params: Optional[Dict[str, Any]] = None,
    store: Optional[Ma1592UniverseStore] = None,
) -> bool:
    """remove_on_trend_lost 별칭."""
    if ma92 is None and ma90 is not None:
        ma92 = ma90
    return remove_on_ema90_break(
        stock_code,
        ma15=ma15,
        ma92=ma92,
        close=close,
        break_pct=break_pct,
        params=params,
        store=store,
    )


def remove_on_ema15_break(
    stock_code: str,
    *,
    close: float,
    ma15: float,
    break_pct: float = 0.4,
    store: Optional[Ma1592UniverseStore] = None,
) -> bool:
    """Deprecated: EMA15 이탈은 더 이상 장부 OUT 트리거가 아님."""
    return False


# 하위 호환 별칭 (조건식 이탈로는 더 이상 제거하지 않음)
def remove_on_condition_exit(
    stock_code: str,
    *,
    store: Optional[Ma1592UniverseStore] = None,
) -> bool:
    """Deprecated: 조건식 이탈 시 장부 유지. 항상 False."""
    return False


def sync_universe_from_condition(
    present: Dict[str, Dict[str, Any]],
    *,
    source: str = "condition",
    now: Optional[datetime] = None,
    params: Optional[Dict[str, Any]] = None,
    store: Optional[Ma1592UniverseStore] = None,
) -> Dict[str, int]:
    """조건식 편입분만 장부에 추가(스티키).

    present: {stock_code: {stock_name, current_price, in_ma15, in_ma92, ...}}
    - 신규 편입 → GC_WATCH 추가
    - 조건식에서 빠져도 장부 유지 (EMA92 완전 이탈 시에만 L3에서 제거)
    """
    store = store or get_universe_store()
    now = now or datetime.now()
    present_codes = set(present.keys())
    added = kept = rejected = limit_skipped = 0

    for code, meta in present.items():
        name = str((meta or {}).get("stock_name") or code)
        try:
            price = int((meta or {}).get("current_price") or 0)
        except (TypeError, ValueError):
            price = 0
        in_ma15 = (meta or {}).get("in_ma15")
        in_ma92 = (meta or {}).get("in_ma92") or (meta or {}).get("in_ma90")
        closes = (meta or {}).get("closes")
        ok, reason, _ = upsert_from_condition(
            code,
            name,
            price=price,
            in_ma15=float(in_ma15) if in_ma15 else None,
            in_ma92=float(in_ma92) if in_ma92 else None,
            closes=closes,
            source=source,
            now=now,
            params=params,
            store=store,
        )
        if ok:
            added += 1
        elif reason == "ALREADY_IN_LEDGER":
            kept += 1
        elif reason == "L1_LIMIT":
            limit_skipped += 1
        else:
            rejected += 1

    return {
        "added": added,
        "removed": 0,
        "kept": kept,
        "rejected": rejected,
        "limit_skipped": limit_skipped,
        "present": len(present_codes),
        "l3": len(store.l3_codes()),
        "l1_limit": effective_l1_limit(params),
    }


async def sync_universe_from_condition_async(
    kiwoom_api,
    present: Dict[str, Dict[str, Any]],
    *,
    source: str = "condition",
    now: Optional[datetime] = None,
    params: Optional[Dict[str, Any]] = None,
    store: Optional[Ma1592UniverseStore] = None,
    cache_ttl_sec: float = 60.0,
) -> Dict[str, int]:
    """조건식 편입 + 신규 종목 5분봉 EMA15/92 편입 스냅샷."""
    store = store or get_universe_store()
    enriched = dict(present)
    for code, meta in present.items():
        if store.get(code):
            continue
        if is_l3_at_capacity(store, params=params):
            continue
        live = await fetch_live_universe_metrics(
            kiwoom_api,
            code,
            params=params,
            now=now,
            cache_ttl_sec=cache_ttl_sec,
        )
        row_meta = dict(meta or {})
        if live.get("ma15"):
            row_meta["in_ma15"] = live["ma15"]
        if live.get("ma92"):
            row_meta["in_ma92"] = live["ma92"]
        if live.get("closes"):
            row_meta["closes"] = live["closes"]
        enriched[code] = row_meta
    return sync_universe_from_condition(
        enriched,
        source=source,
        now=now,
        params=params,
        store=store,
    )


async def purge_l3_trend_lost(
    kiwoom_api,
    *,
    params: Optional[Dict[str, Any]] = None,
    store: Optional[Ma1592UniverseStore] = None,
    now: Optional[datetime] = None,
    cache_ttl_sec: float = 60.0,
) -> List[str]:
    """L3 관찰 장부 중 ledger_purge_tf 봉 EMA15≤EMA92(추세 전환) 종목 일괄 제거."""
    store = store or get_universe_store()
    p = merge_params(params)
    purge_tf = normalize_chart_tf(p.get("ledger_purge_tf") or "3M", default="3M")
    removed: List[str] = []
    for code in select_l3_codes_for_scan(store, params=params):
        live = await fetch_live_universe_metrics(
            kiwoom_api,
            code,
            params=params,
            now=now,
            cache_ttl_sec=cache_ttl_sec,
            chart_tf=purge_tf,
        )
        ma15 = live.get("ma15")
        ma92 = live.get("ma92")
        if ma15 is None or ma92 is None:
            continue
        if remove_on_trend_lost(
            code,
            ma15=float(ma15),
            ma92=float(ma92),
            params=params,
            store=store,
        ):
            removed.append(code)
    return removed


# 하위 호환 별칭
async def purge_l3_below_ma90(
    kiwoom_api,
    *,
    params: Optional[Dict[str, Any]] = None,
    store: Optional[Ma1592UniverseStore] = None,
    now: Optional[datetime] = None,
    cache_ttl_sec: float = 60.0,
) -> List[str]:
    return await purge_l3_trend_lost(
        kiwoom_api,
        params=params,
        store=store,
        now=now,
        cache_ttl_sec=cache_ttl_sec,
    )


def ma1592_holding_codes_from_session(session: Any) -> set:
    """MA1592 HOLDING 포지션 종목코드 (수량>0)."""
    from core.models import Position

    codes: set = set()
    for pos in session.query(Position).filter(
        Position.status == "HOLDING",
        Position.strategy_key == "ma1592",
    ):
        if int(getattr(pos, "buy_quantity", None) or 0) <= 0:
            continue
        code, _ = normalize_ledger_stock_code(getattr(pos, "stock_code", "") or "")
        if code:
            codes.add(code)
    return codes


def sync_manage_ledger_with_holdings(
    holding_codes: set,
    *,
    store: Optional[Ma1592UniverseStore] = None,
) -> List[str]:
    """DB/계좌에 보유 없는 MANAGE_* 장부 행을 DONE으로 정리."""
    store = store or get_universe_store()
    holding = {str(c or "").replace("A", "").strip().zfill(6) for c in (holding_codes or set())}
    removed: List[str] = []
    for code in list(store.manage_codes()):
        norm, _ = normalize_ledger_stock_code(code)
        if not norm:
            continue
        if norm in holding:
            continue
        row = store.get(norm)
        if not row or row.state not in ("MANAGE_FULL", "MANAGE_HALF"):
            continue
        store.set_state(norm, "DONE")
        removed.append(norm)
    return removed


def release_ma1592_ledger_if_flat(session: Any, stock_code: str) -> bool:
    """해당 종목 MA1592 보유 포지션이 없으면 MANAGE_* 장부 제거."""
    code, _ = normalize_ledger_stock_code(stock_code)
    if not code:
        return False
    holding = ma1592_holding_codes_from_session(session)
    if code in holding:
        return False
    store = get_universe_store()
    row = store.get(code)
    if not row or row.state not in ("MANAGE_FULL", "MANAGE_HALF"):
        return False
    store.set_state(code, "DONE")
    return True


async def maintain_ma1592_universe(
    kiwoom_api,
    *,
    params: Optional[Dict[str, Any]] = None,
    store: Optional[Ma1592UniverseStore] = None,
    now: Optional[datetime] = None,
    cache_ttl_sec: float = 60.0,
) -> Dict[str, Any]:
    """장부 TTL 만료 + 추세 전환 + 청산된 MANAGE_* 정리."""
    store = store or get_universe_store()
    store.load()
    expired = store.expire_stale()
    trimmed = trim_l3_over_limit(store, params=params)
    purged = await purge_l3_trend_lost(
        kiwoom_api,
        params=params,
        store=store,
        now=now,
        cache_ttl_sec=cache_ttl_sec,
    )
    closed_manage: List[str] = []
    try:
        from core.models import get_db

        for db in get_db():
            closed_manage = sync_manage_ledger_with_holdings(
                ma1592_holding_codes_from_session(db),
                store=store,
            )
            break
    except Exception:
        pass
    return {
        "expired": expired,
        "trimmed": trimmed,
        "purged": purged,
        "closed_manage": closed_manage,
        "l3": len(store.l3_codes()),
        "l1_limit": effective_l1_limit(params),
    }


def params_from_settings(settings: Any) -> Dict[str, Any]:
    if settings is None:
        return merge_params()
    aliases = {
        "ma1592_hold_bars": "hold_bars",
        "ma1592_min_trading_value": "min_trading_value",
        "ma1592_setup_expire_days": "setup_expire_days",
        "ma1592_max_hold_days": "max_hold_days",
        "ma1592_stop_pct": "stop_pct",
        "ma1592_hard_break_pct": "hard_break_pct",
        "ma1592_large_break_pct": "large_break_pct",
        "ma1592_impulse_min_pct": "impulse_min_pct",
        "ma1592_crash_pct": "crash_pct",
        "ma1592_crash_bars": "crash_bars",
        "ma1592_tp1_frac": "tp1_frac",
        "ma1592_take_profit_pct": "take_profit_pct",
        "ma1592_risk_per_trade_pct": "risk_per_trade_pct",
        "ma1592_flatten_eod": "flatten_eod",
        "ma1592_ma_source": "ma_source",
        "ma1592_break_before_entry_pct": "break_before_entry_pct",
        "ma1592_prev_high_lookback_days": "prev_high_lookback_days",
        "ma1592_require_ma_slope_up": "require_ma_slope_up",
        "ma1592_touch_buffer_pct": "touch_buffer_pct",
        "ma1592_require_bullish_candle": "require_bullish_candle",
        "ma1592_max_invest_amount": "max_invest_amount",
        "ma1592_hold_mode": "hold_mode",
        "ma1592_exec_tf": "exec_tf",
        "ma1592_entry_trigger": "entry_trigger",
        "ma1592_price_lead_near_pct": "price_lead_near_pct",
        "ma1592_price_lead_far_pct": "price_lead_far_pct",
        "ma1592_ledger_purge_tf": "ledger_purge_tf",
        "ma1592_l1_limit": "l1_limit",
        "ma1592_leg1_pct": "leg1_pct",
        "ma1592_leg2_pct": "leg2_pct",
        "ma1592_leg3_pct": "leg3_pct",
        "ma1592_scale_gap_pct": "scale_gap_pct",
        "ma1592_scale_hold_bars": "scale_hold_bars",
    }
    raw: Dict[str, Any] = {}
    for sk, pk in aliases.items():
        val = getattr(settings, sk, None)
        if val is not None:
            raw[pk] = val
    return merge_params(raw)


def build_buy_additional_data(
    row: UniverseRow,
    *,
    entry: int,
    ma15: float,
    ma92: float,
    prev_high: int,
    sizing: Dict[str, Any],
    params: Optional[Dict[str, Any]] = None,
    entry_leg: int = 1,
    suggested_qty: Optional[int] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    p = merge_params(params)
    tp_px, tp_label = tp1_price(prev_high, entry, float(p["take_profit_pct"]))
    full_qty = int(sizing.get("qty") or row.planned_qty or 0)
    leg = max(1, min(3, int(entry_leg or 1)))
    qty = int(suggested_qty) if suggested_qty is not None else scale_leg_qty(full_qty, leg, p)
    return {
        "strategy": "ma1592",
        "gate_pack": "ma1592_hold",
        "setup_state": "ENTRY",
        "ma15": ma15,
        "ma92": ma92,
        "ma_source": p["ma_source"],
        "gc_at": row.gc_at,
        "gc_price": row.gc_price,
        "prev_high": int(prev_high or 0),
        "tp1_price": tp_px,
        "tp1_label": tp_label,
        "tp1_frac": float(p["tp1_frac"]),
        "tp_mode": p["take_profit_mode"],
        "take_profit_price": tp_px,
        "stop_price": int(sizing.get("stop_price") or 0),
        "suggested_stop": int(sizing.get("stop_price") or 0),
        "suggested_qty": int(qty),
        "planned_qty": int(full_qty),
        "qty_tp1": int(sizing.get("qty_tp1") or 0),
        "entry_leg": leg,
        "ma1592_entry_leg": leg,
        "is_add_buy": leg >= 2,
        "max_hold_days": int(p["max_hold_days"]),
        "reason": reason or (f"SCALE_LEG{leg}" if str(p.get("hold_mode")) == "scale_in_gc" else "HOLD_MA15"),
        "entry_fill": p["entry_fill"],
        "order_ready": True,
    }

# --- 레거시 15/90 명칭 호환 ---
remove_on_ema92_break = remove_on_ema90_break
below_ma92 = below_ma90
hard_break_below_ma90 = hard_break_below_ma92
ema15_above_ema92 = ema15_above_ema90
