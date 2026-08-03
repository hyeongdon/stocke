"""자동매매 Phase 2 공통 규칙 — 스캐너·매수 실행기에서 공유."""
from __future__ import annotations

import logging
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.config import Config
from core.models import AutoTradeSettings, SellOrder, get_db
from api.api_rate_limiter import api_rate_limiter
from utils.market_hours import (
    auto_trade_engine_block_reason,
    is_krx_trading_day,
    trading_day_block_reason,
)
from utils.datetime_kst import (
    as_kst,
    kst_date_str,
    kst_day_start_utc_naive,
    kst_today,
    utc_now_naive,
)

logger = logging.getLogger(__name__)

# 진입 게이트·체결 체크리스트 VWAP 계산에 동일 봉 주기 사용
VWAP_BAR_INTERVAL = "5M"
# 과매도 돌파 게이트 — HTS 조건식(5분 RSI)과 맞춘 분봉
BREAKOUT_BAR_INTERVAL = "5M"

# 돌파 SOFT 진입 확인 — 스캐너/관측 API 공유 (종목코드 → 연속 레벨 위 횟수)
_breakout_entry_soft_streak: Dict[str, int] = {}
# HOLD: 고가 돌파 후 다음 봉 확인 대기 (종목코드 → armed 메타)
_breakout_hold_armed: Dict[str, Dict[str, Any]] = {}

DEFAULT_BREAKOUT_HOLD_EXPIRE_BARS = 3
DEFAULT_BREAKOUT_HOLD_RSI_MIN = 30.0
DEFAULT_BREAKOUT_RSI_PERIOD = 10
# 레거시 진입 게이트 — 일봉 RSI(14)
LEGACY_RSI_PERIOD = 14


def get_breakout_entry_soft_streak(stock_code: str) -> int:
    code = str(stock_code or "").replace("A", "").zfill(6)
    return int(_breakout_entry_soft_streak.get(code) or 0)


def update_breakout_entry_soft_streak(stock_code: str, above_level: bool) -> int:
    """레벨 위면 +1, 아니면 0으로 리셋. 갱신된 streak 반환."""
    code = str(stock_code or "").replace("A", "").zfill(6)
    if not code:
        return 0
    if above_level:
        _breakout_entry_soft_streak[code] = get_breakout_entry_soft_streak(code) + 1
    else:
        _breakout_entry_soft_streak[code] = 0
    return get_breakout_entry_soft_streak(code)


def clear_breakout_entry_soft_streak(stock_code: str) -> None:
    code = str(stock_code or "").replace("A", "").zfill(6)
    _breakout_entry_soft_streak.pop(code, None)


def get_breakout_hold_armed(stock_code: str) -> Optional[Dict[str, Any]]:
    code = str(stock_code or "").replace("A", "").zfill(6)
    armed = _breakout_hold_armed.get(code)
    return dict(armed) if armed else None


def clear_breakout_hold_armed(stock_code: str) -> None:
    code = str(stock_code or "").replace("A", "").zfill(6)
    _breakout_hold_armed.pop(code, None)


def clear_breakout_entry_state(stock_code: str) -> None:
    """매수 신호 생성 후 SOFT/HOLD 메모리 초기화."""
    clear_breakout_entry_soft_streak(stock_code)
    clear_breakout_hold_armed(stock_code)


def _breakout_entry_flags(settings: AutoTradeSettings) -> Tuple[bool, bool, int, bool]:
    hard_raw = getattr(settings, "breakout_entry_hard", None)
    soft_raw = getattr(settings, "breakout_entry_soft", None)
    hold_raw = getattr(settings, "breakout_entry_hold", None)
    use_hard = True if hard_raw is None else bool(hard_raw)
    use_soft = True if soft_raw is None else bool(soft_raw)
    use_hold = True if hold_raw is None else bool(hold_raw)
    polls = int(
        getattr(settings, "breakout_entry_soft_polls", None)
        or getattr(settings, "soft_confirm_polls", None)
        or 3
    )
    return use_hard, use_soft, max(1, polls), use_hold


def compute_rsi_series(closes: List[float], period: int = 10) -> List[Optional[float]]:
    """Wilder RSI. 길이=len(closes), 초반 period개는 None."""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    period = max(1, int(period or 10))
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
    if avg_loss <= 1e-12:
        out[period] = 100.0
    else:
        out[period] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    for i in range(period + 1, n):
        diff = float(closes[i]) - float(closes[i - 1])
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss <= 1e-12:
            out[i] = 100.0
        else:
            out[i] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return out


def compute_legacy_rsi14(
    daily_bars: List[Dict[str, Any]],
    *,
    current_price: Optional[int] = None,
    asof_idx: Optional[int] = None,
) -> Optional[float]:
    """일봉 Wilder RSI(14). asof_idx면 그 봉까지, current_price면 해당 봉 종가 대체."""
    if not daily_bars:
        return None
    end = len(daily_bars) - 1 if asof_idx is None else int(asof_idx)
    if end < 0:
        return None
    bars = daily_bars[: end + 1]
    if len(bars) < LEGACY_RSI_PERIOD + 1:
        return None
    closes = [float(b.get("close") or 0) for b in bars]
    if current_price is not None and int(current_price) > 0:
        closes[-1] = float(current_price)
    rsi_vals = compute_rsi_series(closes, LEGACY_RSI_PERIOD)
    v = rsi_vals[-1]
    return round(float(v), 2) if v is not None else None


def _level_before_bar(
    completed: List[Dict[str, Any]],
    bar_idx: int,
    settings: AutoTradeSettings,
) -> Tuple[int, str]:
    """bar_idx 직전 구간으로 돌파 레벨 계산 (완성봉 기준)."""
    mode = str(getattr(settings, "breakout_level_mode", "prev_high") or "prev_high")
    n_bar = max(1, int(getattr(settings, "breakout_n_day", 10) or 10))
    prior = completed[:bar_idx]
    if not prior:
        return 0, mode
    if mode in ("n_day_high", "n_bar_high"):
        mode = "n_day_high"
        window = prior[-n_bar:]
        if len(window) < n_bar:
            return 0, mode
        return max(int(row.get("high") or 0) for row in window), mode
    mode = "prev_high"
    return int(prior[-1].get("high") or 0), mode


def _bar_ma20_signal(
    completed: List[Dict[str, Any]],
    bar_idx: int,
    settings: AutoTradeSettings,
) -> Tuple[bool, Optional[float]]:
    """완성봉 bar_idx 기준 MA20 상회/상향돌파 여부."""
    if bar_idx < 0 or bar_idx >= len(completed):
        return False, None
    closes = [float(r.get("close") or 0) for r in completed[: bar_idx + 1]]
    if len(closes) < 20:
        return False, None
    ma20 = sum(closes[-20:]) / 20.0
    bar = completed[bar_idx]
    confirm_open = int(bar.get("open") or 0)
    confirm_close = int(bar.get("close") or 0)
    confirm_low = int(bar.get("low") or 0)
    mode = str(getattr(settings, "breakout_ma20_mode", None) or "above").strip().lower()
    if mode not in ("above", "cross"):
        mode = "above"
    above_ok = bool(confirm_close > ma20)
    if mode == "above":
        return above_ok, ma20
    # cross
    classic = False
    if len(closes) >= 21:
        ma20_prev = sum(closes[-21:-1]) / 20.0
        prev_close = closes[-2]
        classic = bool(prev_close > 0 and prev_close <= ma20_prev and confirm_close > ma20)
    intrabar = bool(confirm_open > 0 and confirm_open <= ma20 < confirm_close)
    reclaim = bool(
        confirm_low > 0
        and confirm_low <= ma20 * 1.002
        and confirm_close > ma20
        and confirm_close > confirm_open
    )
    return bool(classic or intrabar or reclaim), ma20


def resolve_breakout_ma20_grace_from_minute_bars(
    bars: List[Dict[str, Any]],
    settings: AutoTradeSettings,
    *,
    exclude_forming: bool = True,
) -> Dict[str, Any]:
    """돌파 후 MA20 상회 유예창 (settings.breakout_ma20_grace_bars).

    - 카운트: **돌파 확인봉을 포함**한 5분 완성봉 N개 (기본 3 = 돌파 + 후속 2).
    - 통과: 창 안 어느 확인봉에서든 MA20 판정(mode) 충족 + 돌파 레벨 위 유지.
    - 대기: 레벨은 유지했으나 아직 MA20 미충족 → `ma20_grace_waiting`(매수는 보류).
    - 만료: N봉 지나도 미충족 → `ma20_grace_expired`.
    - 상속: 유예 활성 시 돌파봉에서 이미 통과한 장대·거래량을 후속봉에 적용
      (후속봉이 약해도 MA20만 따라오면 매수 가능).
    - `breakout_require_ma20_cross=False` 이면 이 로직은 비활성(빈 플래그만 반환).
    """
    grace = max(1, int(getattr(settings, "breakout_ma20_grace_bars", None) or 3))
    require = bool(getattr(settings, "breakout_require_ma20_cross", False))
    out: Dict[str, Any] = {
        "ma20_grace_bars": grace,
        "ma20_grace_waiting": False,
        "ma20_grace_active": False,
        "ma20_grace_slot": 0,
        "ma20_grace_expired": False,
        "ma20_grace_reason": "",
        "ma20_grace_breakout_level": 0,
        "ma20_grace_inherit_body_ok": False,
        "ma20_grace_inherit_volume_ok": False,
        "ma20_grace_breakout_body_pct": 0.0,
        "ma20_grace_breakout_day_volume": 0,
        "ma20_grace_breakout_prev_volume": 0,
    }
    if not require or not bars:
        return out

    rows = sorted(bars, key=lambda row: str(row.get("timestamp", "")))
    completed = rows[:-1] if exclude_forming and len(rows) >= 2 else rows
    if len(completed) < 2:
        return out

    confirm_idx = len(completed) - 1
    window_start = max(0, confirm_idx - grace + 1)
    breakout_idx: Optional[int] = None
    breakout_level = 0
    for i in range(window_start, confirm_idx + 1):
        lvl, _ = _level_before_bar(completed, i, settings)
        if lvl <= 0:
            continue
        c = int(completed[i].get("close") or 0)
        h = int(completed[i].get("high") or 0)
        body_min = float(getattr(settings, "breakout_body_pct", None) or 0.0)
        require_ma20 = True
        # 품질 모드와 동일: 장대/MA20 필수면 종가 돌파만 인정
        if body_min > 0 or require_ma20:
            broken = c > lvl
        else:
            broken = c > lvl or h > lvl
        if broken:
            breakout_idx = i
            breakout_level = lvl
            break

    if breakout_idx is None:
        return out

    slot = confirm_idx - breakout_idx + 1  # 1=돌파봉
    out["ma20_grace_slot"] = slot
    out["ma20_grace_breakout_level"] = breakout_level
    out["ma20_grace_active"] = bool(slot <= grace)

    brk = completed[breakout_idx]
    brk_open = int(brk.get("open") or 0)
    brk_close = int(brk.get("close") or 0)
    brk_body = 0.0
    if brk_open > 0 and brk_close > 0:
        brk_body = (brk_close / brk_open - 1.0) * 100.0
    body_min = float(getattr(settings, "breakout_body_pct", None) or 0.0)
    out["ma20_grace_breakout_body_pct"] = brk_body
    out["ma20_grace_inherit_body_ok"] = bool(body_min <= 0 or brk_body >= body_min)

    n_bar = max(1, int(getattr(settings, "breakout_n_day", 10) or 10))
    prior = completed[:breakout_idx]
    vol_window = prior[-n_bar:] if prior else []
    vols = [int(r.get("volume") or 0) for r in vol_window]
    positive = [v for v in vols if v > 0]
    avg_prev = (sum(positive) / len(positive)) if positive else 0.0
    brk_vol = int(brk.get("volume") or 0)
    vol_mult = float(getattr(settings, "breakout_vol_mult", 1.5) or 1.5)
    out["ma20_grace_breakout_day_volume"] = brk_vol
    out["ma20_grace_breakout_prev_volume"] = int(round(avg_prev)) if avg_prev > 0 else 0
    out["ma20_grace_inherit_volume_ok"] = bool(
        avg_prev > 0 and (brk_vol / avg_prev) >= vol_mult
    )

    confirm_ma20_ok, ma20_v = _bar_ma20_signal(completed, confirm_idx, settings)
    out["ma20_signal_ok"] = bool(confirm_ma20_ok)
    if ma20_v is not None:
        out["ma20"] = float(ma20_v)
    confirm_close = int(completed[confirm_idx].get("close") or 0)
    confirm_high = int(completed[confirm_idx].get("high") or 0)
    still_above = bool(
        breakout_level > 0
        and (confirm_close > breakout_level or confirm_high > breakout_level)
    )

    if slot > grace:
        out["ma20_grace_expired"] = True
        if not confirm_ma20_ok:
            out["ma20_grace_reason"] = f"MA20 유예 만료 ({grace}봉)"
        return out

    if confirm_ma20_ok and still_above:
        out["ma20_grace_reason"] = f"MA20 유예 내 상회 ({slot}/{grace}봉)"
        return out

    if still_above and not confirm_ma20_ok:
        out["ma20_grace_waiting"] = True
        out["ma20_grace_reason"] = f"MA20 유예 대기 ({slot}/{grace}봉)"
    return out


def resolve_breakout_hold_from_minute_bars(
    bars: List[Dict[str, Any]],
    settings: AutoTradeSettings,
    stock_code: str = "",
    *,
    update_armed: bool = True,
) -> Dict[str, Any]:
    """HOLD: 고가 돌파 → 다음봉 저가 유지 + 전봉 RSI 교차 + 현재봉 양봉.

    - 돌파: 완성봉 high > 직전 N봉(또는 직전봉) 고가
    - 구조 확인: 바로 다음 완성봉 low >= 돌파봉 low
    - 무효: 다음봉 low < 돌파봉 low, 또는 expire_bars 경과
    - RSI: **전봉**에서 임계(기본 30) 상향 교차 (직전≤임계 < 전봉)
    - 유지: **현재봉(확인봉)** 이 양봉(close>open) 이고 RSI가 임계 위 유지
    """
    code = str(stock_code or "").replace("A", "").zfill(6)
    expire = max(
        1,
        int(
            getattr(settings, "breakout_hold_expire_bars", None)
            or DEFAULT_BREAKOUT_HOLD_EXPIRE_BARS
        ),
    )
    rsi_period = max(
        2,
        int(getattr(settings, "breakout_rsi_period", None) or DEFAULT_BREAKOUT_RSI_PERIOD),
    )
    rsi_min = float(
        getattr(settings, "breakout_hold_rsi_min", None)
        if getattr(settings, "breakout_hold_rsi_min", None) is not None
        else DEFAULT_BREAKOUT_HOLD_RSI_MIN
    )

    out: Dict[str, Any] = {
        "hold_structure_ok": False,
        "hold_rsi_ok": False,
        "hold_bullish_ok": False,
        "entry_hold_ok": False,
        "hold_armed": False,
        "hold_breakout_low": 0,
        "hold_breakout_level": 0,
        "hold_breakout_ts": "",
        "hold_rsi": None,
        "hold_rsi_prev": None,
        "hold_rsi_cross_bar": None,
        "hold_rsi_min": rsi_min,
        "hold_rsi_period": rsi_period,
        "hold_expire_bars": expire,
        "hold_wait_reason": "",
    }

    if not bars:
        out["hold_wait_reason"] = "5분봉 없음(HOLD)"
        return out
    rows = sorted(bars, key=lambda row: str(row.get("timestamp", "")))
    if len(rows) < 3:
        out["hold_wait_reason"] = "5분봉 부족(HOLD)"
        return out

    # 최신봉은 형성 중일 수 있음 → 완성봉만 HOLD 판정
    completed = rows[:-1]
    n_bar = max(1, int(getattr(settings, "breakout_n_day", 10) or 10))
    mode = str(getattr(settings, "breakout_level_mode", "prev_high") or "prev_high")
    min_before = n_bar if mode in ("n_day_high", "n_bar_high") else 1

    closes = [float(row.get("close") or 0) for row in completed]
    rsi_vals = compute_rsi_series(closes, rsi_period)

    # 1) 최신 완성쌍: breakout=completed[-2], confirm=completed[-1]
    structure_ok = False
    breakout_low = 0
    breakout_level = 0
    breakout_ts = ""
    confirm_bar: Optional[Dict[str, Any]] = None
    if len(completed) >= min_before + 2:
        b_idx = len(completed) - 2
        c_idx = len(completed) - 1
        level, _ = _level_before_bar(completed, b_idx, settings)
        bo = completed[b_idx]
        cf = completed[c_idx]
        confirm_bar = cf
        bo_high = int(bo.get("high") or 0)
        bo_low = int(bo.get("low") or 0)
        cf_low = int(cf.get("low") or 0)
        if level > 0 and bo_high > level and bo_low > 0:
            breakout_low = bo_low
            breakout_level = level
            breakout_ts = str(bo.get("timestamp") or "")
            if cf_low >= bo_low:
                structure_ok = True
            else:
                out["hold_wait_reason"] = (
                    f"HOLD 무효(다음봉 저가 {cf_low:,} < 돌파봉 저가 {bo_low:,})"
                )
                if update_armed and code:
                    clear_breakout_hold_armed(code)
        elif level > 0 and bo_high <= level:
            out["hold_wait_reason"] = (
                f"HOLD 대기(직전완성 고가 {bo_high:,} ≤ 레벨 {level:,})"
            )

    # 2) 최신 완성봉만 고가 돌파 → armed (다음 봉 대기)
    armed_meta = get_breakout_hold_armed(code) if code else None
    if len(completed) >= min_before + 1:
        last_idx = len(completed) - 1
        level_l, _ = _level_before_bar(completed, last_idx, settings)
        last = completed[last_idx]
        last_high = int(last.get("high") or 0)
        last_low = int(last.get("low") or 0)
        last_ts = str(last.get("timestamp") or "")
        if level_l > 0 and last_high > level_l and last_low > 0:
            # 아직 confirm 봉이 없음(형성 중) — armed 갱신
            if not structure_ok:
                armed_meta = {
                    "level": level_l,
                    "breakout_low": last_low,
                    "bar_ts": last_ts,
                    "bars_waited": 0,
                }
                if update_armed and code:
                    _breakout_hold_armed[code] = dict(armed_meta)
                out["hold_armed"] = True
                out["hold_breakout_low"] = last_low
                out["hold_breakout_level"] = level_l
                out["hold_breakout_ts"] = last_ts
                out["hold_wait_reason"] = (
                    f"HOLD armed(고가 {last_high:,} > {level_l:,}, 다음봉 대기)"
                )

    # 3) armed 상태에서 confirm 봉이 도착했는지 (expire 포함)
    if armed_meta and not structure_ok:
        arm_ts = str(armed_meta.get("bar_ts") or "")
        arm_low = int(armed_meta.get("breakout_low") or 0)
        arm_level = int(armed_meta.get("level") or 0)
        after = [
            row for row in completed
            if str(row.get("timestamp") or "") > arm_ts
        ] if arm_ts else []
        if not after and arm_ts:
            for i, row in enumerate(completed):
                if str(row.get("timestamp") or "") == arm_ts and i + 1 < len(completed):
                    after = completed[i + 1:]
                    break
        if after:
            nxt = after[0]
            confirm_bar = nxt
            nxt_low = int(nxt.get("low") or 0)
            waited = len(after)
            if waited > expire:
                out["hold_wait_reason"] = f"HOLD 만료({waited}봉 > {expire})"
                if update_armed and code:
                    clear_breakout_hold_armed(code)
            elif nxt_low < arm_low:
                out["hold_wait_reason"] = (
                    f"HOLD 무효(다음봉 저가 {nxt_low:,} < 돌파봉 저가 {arm_low:,})"
                )
                if update_armed and code:
                    clear_breakout_hold_armed(code)
            else:
                structure_ok = True
                breakout_low = arm_low
                breakout_level = arm_level
                breakout_ts = arm_ts
                if update_armed and code:
                    clear_breakout_hold_armed(code)
        else:
            out["hold_armed"] = True
            out["hold_breakout_low"] = arm_low
            out["hold_breakout_level"] = arm_level
            out["hold_breakout_ts"] = arm_ts
            if not out["hold_wait_reason"]:
                out["hold_wait_reason"] = "HOLD armed(다음 완성봉 대기)"

    # RSI: 최근 expire봉 안에 임계 상향 교차가 있고, 현재봉 RSI가 임계 위 유지
    rsi_now = rsi_vals[-1] if rsi_vals else None
    rsi_prev_bar = rsi_vals[-2] if len(rsi_vals) >= 2 else None
    rsi_before_prev = rsi_vals[-3] if len(rsi_vals) >= 3 else None
    out["hold_rsi"] = round(float(rsi_now), 2) if rsi_now is not None else None
    out["hold_rsi_prev"] = (
        round(float(rsi_prev_bar), 2) if rsi_prev_bar is not None else None
    )
    out["hold_rsi_before_prev"] = (
        round(float(rsi_before_prev), 2) if rsi_before_prev is not None else None
    )

    rsi_cross_idx = None
    lookback = min(expire + 1, max(0, len(rsi_vals) - 1))
    # completed[-1]이 현재 확인봉이므로, 교차는 확인봉 이전~확인봉 구간에서 찾는다
    for i in range(len(rsi_vals) - 1, max(0, len(rsi_vals) - 1 - lookback) - 1, -1):
        if i <= 0:
            break
        a = rsi_vals[i - 1]
        b = rsi_vals[i]
        if a is None or b is None:
            continue
        if float(a) <= rsi_min < float(b):
            rsi_cross_idx = i
            out["hold_rsi_before_prev"] = round(float(a), 2)
            out["hold_rsi_cross_bar"] = round(float(b), 2)
            # 전봉 교차 표기용: 교차가 확인봉 직전이면 prev에 교차봉 RSI
            if i == len(rsi_vals) - 2:
                out["hold_rsi_prev"] = round(float(b), 2)
            break

    rsi_cross_recent = rsi_cross_idx is not None
    # 교차 후 확인봉까지 봉 수가 expire 이내
    if rsi_cross_recent and rsi_cross_idx is not None:
        bars_since_cross = (len(rsi_vals) - 1) - rsi_cross_idx
        if bars_since_cross > expire:
            rsi_cross_recent = False
    rsi_held = bool(rsi_now is not None and float(rsi_now) > rsi_min)
    rsi_ok = bool(rsi_cross_recent and rsi_held)
    out["hold_rsi_cross"] = rsi_cross_recent
    out["hold_rsi_ok"] = rsi_ok

    # 현재봉 양봉 유지 (close > open)
    bullish_ok = False
    if confirm_bar is None and completed:
        confirm_bar = completed[-1]
    if confirm_bar is not None:
        c_open = float(confirm_bar.get("open") or 0)
        c_close = float(confirm_bar.get("close") or 0)
        bullish_ok = c_close > c_open > 0
    out["hold_bullish_ok"] = bullish_ok

    out["hold_structure_ok"] = structure_ok
    if structure_ok:
        out["hold_breakout_low"] = breakout_low
        out["hold_breakout_level"] = breakout_level
        out["hold_breakout_ts"] = breakout_ts
        out["hold_armed"] = False

    out["entry_hold_ok"] = bool(structure_ok and rsi_ok and bullish_ok)
    if out["entry_hold_ok"]:
        out["hold_wait_reason"] = ""
    elif structure_ok:
        if not rsi_cross_recent:
            if rsi_prev_bar is None or rsi_before_prev is None:
                out["hold_wait_reason"] = "HOLD 구조 OK · RSI 교차 계산 불가"
            else:
                out["hold_wait_reason"] = (
                    f"HOLD 구조 OK · 최근 {expire}봉 내 RSI {rsi_min:g} 교차 없음"
                    f"(전봉 {float(rsi_before_prev):.1f}→{float(rsi_prev_bar):.1f})"
                )
        elif not rsi_held:
            out["hold_wait_reason"] = (
                f"HOLD 구조 OK · 현재봉 RSI 미유지"
                f"({float(rsi_now):.1f} ≤ {rsi_min:g})"
                if rsi_now is not None
                else "HOLD 구조 OK · 현재봉 RSI 없음"
            )
        elif not bullish_ok:
            out["hold_wait_reason"] = "HOLD 구조 OK · 현재봉 양봉 아님"
    return out

# 상따 게이트 패키지 sangtta_breakout 기본값 (PRD Phase1)
SANGTTA_CHANGE_MIN = 12.0
SANGTTA_CHANGE_MAX = 15.0
SANGTTA_MAX_MARKET_CAP_EOK = 3000.0
SANGTTA_OPEN_RISE_MIN_PCT = 3.0
SANGTTA_UPPER_LIMIT_MULT = 1.30  # 일반주 상한가(전일종가 대비)
DEFAULT_SANGTTA_BUY_AMOUNT = 500_000  # 상따 1회 매수 기본 소액
DEFAULT_SANGTTA_MAX_SLOTS = 2
DEFAULT_BREAKOUT_BUY_AMOUNT = 1_000_000
DEFAULT_BREAKOUT_MAX_SLOTS = 1
DEFAULT_YMGP_BUY_AMOUNT = 500_000
DEFAULT_YMGP_MAX_SLOTS = 1
DEFAULT_JONGGA_BUY_AMOUNT = 1_000_000
DEFAULT_JONGGA_MAX_SLOTS = 1


def is_deposit_pct_buy_unit(settings: Optional[AutoTradeSettings]) -> bool:
    unit = str(getattr(settings, "buy_amount_unit", "WON") or "WON").upper()
    return unit in ("DEPOSIT_PCT", "PCT", "PERCENT", "PERCENTAGE")


def deposit_pct_to_won(deposit: int, pct: Optional[float]) -> int:
    try:
        d = max(0, int(deposit or 0))
        p = float(pct) if pct is not None else 0.0
    except (TypeError, ValueError):
        return 0
    if d <= 0 or p <= 0:
        return 0
    return int(d * min(p, 100.0) / 100.0)


def resolve_buy_amount_won(
    settings: Optional[AutoTradeSettings],
    *,
    amount_won: Optional[int],
    deposit_pct: Optional[float],
    deposit: Optional[int],
    default_won: int = 0,
) -> int:
    """buy_amount_unit에 따라 원 단위 매수금 산출."""
    if is_deposit_pct_buy_unit(settings):
        won = deposit_pct_to_won(int(deposit or 0), deposit_pct)
        if won > 0:
            return won
        # 비중 미입력 시 기존 원화 값으로 폴백
    try:
        amt = int(amount_won or 0)
    except (TypeError, ValueError):
        amt = 0
    return amt if amt > 0 else int(default_won or 0)


def effective_sangtta_buy_amount(
    settings: Optional[AutoTradeSettings],
    deposit: Optional[int] = None,
) -> int:
    """상따 1회 매수 금액. 미설정/0이면 기본 소액."""
    if not settings:
        return DEFAULT_SANGTTA_BUY_AMOUNT
    return resolve_buy_amount_won(
        settings,
        amount_won=getattr(settings, "sangtta_buy_amount", None),
        deposit_pct=getattr(settings, "sangtta_buy_deposit_pct", None),
        deposit=deposit,
        default_won=DEFAULT_SANGTTA_BUY_AMOUNT,
    )


def effective_sangtta_max_slots(settings: Optional[AutoTradeSettings]) -> int:
    if not settings:
        return DEFAULT_SANGTTA_MAX_SLOTS
    try:
        n = int(getattr(settings, "sangtta_max_slots", 0) or 0)
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else DEFAULT_SANGTTA_MAX_SLOTS


def effective_breakout_buy_amount(
    settings: Optional[AutoTradeSettings],
    deposit: Optional[int] = None,
) -> int:
    if not settings:
        return DEFAULT_BREAKOUT_BUY_AMOUNT
    return resolve_buy_amount_won(
        settings,
        amount_won=getattr(settings, "breakout_buy_amount", None),
        deposit_pct=getattr(settings, "breakout_buy_deposit_pct", None),
        deposit=deposit,
        default_won=DEFAULT_BREAKOUT_BUY_AMOUNT,
    )


def effective_breakout_max_slots(settings: Optional[AutoTradeSettings]) -> int:
    try:
        slots = int(getattr(settings, "breakout_max_slots", 0) or 0)
    except (TypeError, ValueError):
        slots = 0
    return slots if slots > 0 else DEFAULT_BREAKOUT_MAX_SLOTS


def effective_ymgp_buy_amount(
    settings: Optional[AutoTradeSettings],
    *,
    entry_leg: int = 1,
    deposit: Optional[int] = None,
) -> int:
    if not settings:
        return DEFAULT_YMGP_BUY_AMOUNT
    leg = 2 if int(entry_leg or 1) >= 2 else 1
    if leg == 2:
        return resolve_buy_amount_won(
            settings,
            amount_won=getattr(settings, "ymgp_buy_amount_2", None),
            deposit_pct=getattr(settings, "ymgp_buy_deposit_pct_2", None),
            deposit=deposit,
            default_won=DEFAULT_YMGP_BUY_AMOUNT,
        )
    return resolve_buy_amount_won(
        settings,
        amount_won=getattr(settings, "ymgp_buy_amount_1", None),
        deposit_pct=getattr(settings, "ymgp_buy_deposit_pct_1", None),
        deposit=deposit,
        default_won=DEFAULT_YMGP_BUY_AMOUNT,
    )


def effective_ymgp_max_slots(settings: Optional[AutoTradeSettings]) -> int:
    try:
        slots = int(getattr(settings, "ymgp_max_slots", 0) or 0)
    except (TypeError, ValueError):
        slots = 0
    return slots if slots > 0 else DEFAULT_YMGP_MAX_SLOTS


def effective_jongga_buy_amount(
    settings: Optional[AutoTradeSettings],
    deposit: Optional[int] = None,
    *,
    entry_leg: int = 1,
) -> int:
    if not settings:
        return DEFAULT_JONGGA_BUY_AMOUNT
    total = resolve_buy_amount_won(
        settings,
        amount_won=getattr(settings, "jongga_buy_amount", None),
        deposit_pct=getattr(settings, "jongga_buy_deposit_pct", None),
        deposit=deposit,
        default_won=DEFAULT_JONGGA_BUY_AMOUNT,
    )
    try:
        from utils.jongga_engine import leg_fraction

        frac = leg_fraction(settings, entry_leg)
    except Exception:
        frac = 1.0 if int(entry_leg or 1) <= 1 else 0.0
    amt = int(total * frac)
    # 분할 시 최소 1원 이상(수량 계산은 executor에서)
    if frac > 0 and amt <= 0 and total > 0:
        amt = max(1, total // 100)
    return amt


def effective_jongga_max_slots(settings: Optional[AutoTradeSettings]) -> int:
    try:
        slots = int(getattr(settings, "jongga_max_slots", 0) or 0)
    except (TypeError, ValueError):
        slots = 0
    return slots if slots > 0 else DEFAULT_JONGGA_MAX_SLOTS


def effective_min_change_rate(settings: AutoTradeSettings) -> Optional[float]:
    if settings.min_change_rate_buy is not None:
        return float(settings.min_change_rate_buy)
    if settings.signal_min_threshold is not None:
        return float(settings.signal_min_threshold)
    return None


def has_buy_conditions(settings: AutoTradeSettings) -> bool:
    return (
        bool(settings.buy_below_price)
        or effective_min_change_rate(settings) is not None
        or bool(
            getattr(settings, "use_breakout", False)
            and getattr(settings, "breakout_condition_names", None)
        )
        or bool(
            getattr(settings, "use_ymgp", False)
            and getattr(settings, "ymgp_condition_names", None)
        )
        or bool(getattr(settings, "use_jongga", False))
    )


def in_trade_hours(settings: AutoTradeSettings, now: Optional[datetime] = None) -> bool:
    """레거시(거래대금·스크리너) 신규매수 시간창."""
    kst = as_kst(now)
    if not is_krx_trading_day(kst):
        return False
    try:
        sh, sm = map(int, (settings.trade_start_time or "10:00").split(":"))
        eh, em = map(int, (settings.trade_end_time or "15:20").split(":"))
        in_window = dt_time(sh, sm) <= kst.time() <= dt_time(eh, em)
    except Exception:
        in_window = True
    if in_window:
        return True
    return bool(getattr(Config, "ALLOW_OUT_OF_MARKET_TRADING", False))


def new_buy_block_reason(
    settings: Optional[AutoTradeSettings],
    now: Optional[datetime] = None,
) -> Optional[str]:
    """신규 매수가 막힌 이유(스캐너 전역 게이트). None이면 최소 1개 전략이 매수 가능.

    레거시·상따·돌파 중 하나라도 시간창이면 스캔을 돌리고,
    종목/신호 단위에서 전략별 시간창을 다시 검사한다.
    """
    if not settings:
        return "자동매매 설정 없음"
    now = as_kst(now)

    off_day = trading_day_block_reason(now)
    if off_day:
        return off_day

    if now.weekday() < 5 and getattr(settings, "liquidate_before_close", False):
        try:
            liq = getattr(settings, "liquidate_time", None) or "15:10"
            lh, lm = map(int, str(liq).split(":"))
            if now.time() >= dt_time(lh, lm):
                return f"장마감 청산 시각({liq}) 이후 — 신규 매수 중단"
        except Exception:
            pass

    if getattr(Config, "ALLOW_OUT_OF_MARKET_TRADING", False):
        return None

    from utils.market_hours import any_strategy_buy_window_open, linked_trading_session_window_str

    if any_strategy_buy_window_open(settings, now):
        return None

    legacy = f"{settings.trade_start_time or '10:00'}~{settings.trade_end_time or '15:20'}"
    sang = (
        f"{getattr(settings, 'sangtta_trade_start_time', None) or '09:05'}"
        f"~{getattr(settings, 'sangtta_trade_end_time', None) or '11:00'}"
    )
    parts = [f"레거시 {legacy}", f"상따 {sang}"]
    if getattr(settings, "use_breakout", False) or str(
        getattr(settings, "breakout_condition_names", None) or ""
    ).strip():
        br = (
            f"{getattr(settings, 'breakout_trade_start_time', None) or '11:00'}"
            f"~{getattr(settings, 'breakout_trade_end_time', None) or '14:30'}"
        )
        parts.append(f"돌파 {br}")
    if getattr(settings, "use_ymgp", False) or str(
        getattr(settings, "ymgp_condition_names", None) or ""
    ).strip():
        ym = (
            f"{getattr(settings, 'ymgp_trade_start_time', None) or '09:30'}"
            f"~{getattr(settings, 'ymgp_trade_end_time', None) or '14:30'}"
        )
        parts.append(f"역매공파 {ym}")
    if getattr(settings, "use_jongga", False):
        from utils.jongga_engine import jongga_buy_window_end

        jg = (
            f"{getattr(settings, 'jongga_trade_start_time', None) or '14:30'}"
            f"~{jongga_buy_window_end(settings)}"
        )
        parts.append(f"종가배팅 {jg}")
    engine = linked_trading_session_window_str(settings, now)
    return f"모든 전략 매매시간 외 ({', '.join(parts)} · 엔진 {engine})"


def allows_new_buy(settings: Optional[AutoTradeSettings], now: Optional[datetime] = None) -> bool:
    """신규 매수 허용 — 전략 중 하나라도 시간창이며 장마감 청산 시각 이전."""
    return new_buy_block_reason(settings, now) is None


def get_auto_trade_settings_sync() -> Optional[AutoTradeSettings]:
    """세션 판단용 — 캐시 없이 DB 최신 설정."""
    for db in get_db():
        return db.query(AutoTradeSettings).first()
    return None


def auto_trade_engines_allowed(now: Optional[datetime] = None) -> tuple[bool, Optional[str]]:
    """스캐너·매수 실행기 기동/스캔 허용 여부. (허용, 차단 사유)"""
    settings = get_auto_trade_settings_sync()
    block = auto_trade_engine_block_reason(settings, now)
    return (block is None, block)


def buy_price_skip_reason(
    settings: AutoTradeSettings,
    price: int,
    change_rate: Optional[float],
) -> Optional[str]:
    if settings.buy_below_price and price > int(settings.buy_below_price):
        return f"매수가 상한 초과 ({price:,} > {int(settings.buy_below_price):,})"
    min_rate = effective_min_change_rate(settings)
    if min_rate is not None and float(change_rate or 0) < min_rate:
        cr = float(change_rate or 0)
        return f"등락률 미달 ({cr:.2f}% < {min_rate:g}%)"
    return None


def passes_buy_price_conditions(
    settings: AutoTradeSettings,
    price: int,
    change_rate: Optional[float],
) -> bool:
    return buy_price_skip_reason(settings, price, change_rate) is None


def cash_reserve_pct(settings: AutoTradeSettings) -> float:
    pct = getattr(settings, "cash_reserve_pct", None)
    if pct is None:
        return 10.0
    return max(0.0, min(100.0, float(pct)))


def compute_investable_cash(deposit: int, settings: Optional[AutoTradeSettings] = None) -> Tuple[int, int]:
    """예수금에서 현금 보유 비율을 남기고 매수 가능 금액을 반환. (investable, reserve)"""
    deposit = max(0, int(deposit or 0))
    if not settings:
        return deposit, 0
    pct = cash_reserve_pct(settings)
    if pct <= 0:
        return deposit, 0
    reserve = int(deposit * pct / 100)
    return max(0, deposit - reserve), reserve


def cap_buy_amount_by_cash(amount: int, investable_cash: int) -> int:
    if investable_cash <= 0 or amount <= 0:
        return 0
    return min(int(amount), investable_cash)


def normalize_pyramid_amounts(
    initial_min_amount: Optional[int],
    initial_max_amount: Optional[int],
) -> Tuple[int, int]:
    """역피라미딩 금액 정규화 — initial_max=약한 신호(큰 금액), initial_min=강한 신호(작은 금액)."""
    a = int(initial_min_amount or 0)
    b = int(initial_max_amount or 0)
    if a <= 0 and b <= 0:
        return 0, 0
    if a <= 0:
        return b, b
    if b <= 0:
        return a, a
    return min(a, b), max(a, b)


def compute_buy_amount(
    settings: AutoTradeSettings,
    change_rate: Optional[float] = None,
    is_add_buy: bool = False,
    deposit: Optional[int] = None,
) -> int:
    if is_add_buy:
        return resolve_buy_amount_won(
            settings,
            amount_won=settings.add_buy_amount or settings.initial_min_amount or settings.max_invest_amount,
            deposit_pct=getattr(settings, "add_buy_deposit_pct", None),
            deposit=deposit,
            default_won=0,
        )

    method = (settings.sizing_method or "FIXED").upper()
    imin = resolve_buy_amount_won(
        settings,
        amount_won=settings.initial_min_amount or settings.max_invest_amount,
        deposit_pct=getattr(settings, "initial_min_deposit_pct", None),
        deposit=deposit,
        default_won=0,
    )
    imax = resolve_buy_amount_won(
        settings,
        amount_won=settings.initial_max_amount or settings.max_invest_amount or imin,
        deposit_pct=getattr(settings, "initial_max_deposit_pct", None),
        deposit=deposit,
        default_won=imin,
    )

    if method == "PYRAMIDING" and change_rate is not None:
        strong_amt, weak_amt = normalize_pyramid_amounts(imin, imax)
        smin = float(settings.signal_min_threshold if settings.signal_min_threshold is not None else 2)
        smax = float(settings.signal_max_threshold if settings.signal_max_threshold is not None else 10)
        rate = float(change_rate)
        if smax <= smin or weak_amt <= 0:
            return weak_amt or strong_amt
        # 역피라미딩: 등락률이 높을수록 금액 감소 (변동성·손절 리스크 완화)
        if rate <= smin:
            amount = weak_amt
        elif rate >= smax:
            amount = strong_amt
        else:
            ratio = (rate - smin) / (smax - smin)
            amount = int(weak_amt - (weak_amt - strong_amt) * ratio)
        return max(strong_amt, min(weak_amt, amount))

    strong_amt, weak_amt = normalize_pyramid_amounts(imin, imax)
    if method == "PYRAMIDING":
        return weak_amt or strong_amt
    return max(imax, imin)


def compute_quantity(amount: int, price: int, max_shares: int = 1000) -> int:
    if not price or price <= 0 or amount <= 0:
        return 0
    qty = amount // price
    if qty < 1:
        return 0
    return min(qty, max_shares)


def order_params(settings: AutoTradeSettings, current_price: int) -> Tuple[int, str]:
    """(주문가격, trde_tp) — MARKET=0원/3, LIMIT=현재가/0."""
    method = (settings.order_method or "MARKET").upper()
    if method == "LIMIT" and current_price > 0:
        return current_price, "0"
    return 0, "3"


def get_today_realized_pnl() -> int:
    start = kst_day_start_utc_naive()
    end = kst_day_start_utc_naive(kst_today() + timedelta(days=1))
    total = 0
    for db in get_db():
        session: Session = db
        rows = session.query(SellOrder).filter(
            SellOrder.status == "COMPLETED",
            SellOrder.completed_at >= start,
            SellOrder.completed_at < end,
        ).all()
        total = sum(int(r.profit_loss or 0) for r in rows)
        break
    return total


def _daily_limit_halted(
    pnl: int,
    loss_limit: Optional[int],
    profit_target: Optional[int],
) -> bool:
    if loss_limit is not None and pnl <= int(loss_limit):
        return True
    if profit_target is not None and pnl >= int(profit_target):
        return True
    return False


def check_daily_limits(settings: AutoTradeSettings) -> Optional[str]:
    """일일 손실/이익 한도 초과 시 사유 문자열, 아니면 None."""
    pnl = get_today_realized_pnl()
    loss_limit = settings.daily_loss_limit
    if loss_limit is not None and pnl <= int(loss_limit):
        return f"일일 손실 한도 도달: {pnl:,}원 (한도 {int(loss_limit):,}원)"
    profit_target = settings.daily_profit_target
    if profit_target is not None and pnl >= int(profit_target):
        return f"일일 이익 목표 달성: {pnl:,}원 (목표 {int(profit_target):,}원)"
    return None


# 일일 한도 킬스위치 잔존 표시 — 한도만 먼저 완화해 저장한 뒤에도 재개 가능
_daily_limit_halt_reason: Optional[str] = None
_daily_limit_halt_day: Optional[str] = None


def mark_daily_limit_halt(reason: str) -> None:
    global _daily_limit_halt_reason, _daily_limit_halt_day
    _daily_limit_halt_reason = reason
    _daily_limit_halt_day = kst_date_str()


def clear_daily_limit_halt() -> None:
    global _daily_limit_halt_reason, _daily_limit_halt_day
    _daily_limit_halt_reason = None
    _daily_limit_halt_day = None


def is_daily_limit_halt_pending() -> bool:
    """당일 일일 한도 중단 플래그가 남아 있으면 True."""
    if not _daily_limit_halt_reason:
        return False
    if _daily_limit_halt_day and _daily_limit_halt_day != kst_date_str():
        clear_daily_limit_halt()
        return False
    return True


def should_resume_after_daily_limit_change(
    *,
    old_loss_limit: Optional[int],
    old_profit_target: Optional[int],
    new_loss_limit: Optional[int],
    new_profit_target: Optional[int],
    pnl: int,
    currently_enabled: bool,
) -> bool:
    """일일 한도 완화·잔존 OFF 상태에서 자동매매 재개 여부.

    - 한도 값 변경으로 당일 손익이 다시 허용되면 True
    - 한도는 이미 완화됐는데 is_enabled만 OFF인 잔존(킬스위치 플래그)도 True
    - 수동 OFF(한도 미도달·플래그 없음)는 False
    """
    if currently_enabled:
        clear_daily_limit_halt()
        return False
    still_halted = _daily_limit_halted(pnl, new_loss_limit, new_profit_target)
    if still_halted:
        return False

    limits_changed = (
        old_loss_limit != new_loss_limit or old_profit_target != new_profit_target
    )
    if limits_changed:
        was_halted = _daily_limit_halted(pnl, old_loss_limit, old_profit_target)
        if was_halted:
            return True

    # 한도 저장은 이미 반영됐고 OFF만 남은 경우 (재저장·서버 재시작 후 등)
    if is_daily_limit_halt_pending():
        return True

    # 플래그 없이 남은 잔존: 현재 한도로는 OK인데, 당일 실현손실이
    # 현재 손실한도보다 타이트한 구간에서 막혔을 법한 경우
    # 예) 한도 -34만·실현 -23만 → 예전에 -20만 한도로 끊긴 뒤 한도만 올린 상태
    if (
        new_loss_limit is not None
        and pnl < 0
        and pnl > int(new_loss_limit)
        and old_loss_limit == new_loss_limit
    ):
        # 손실한도가 음수이고, 실현손실이 한도의 절반 이상이면 한도중단 잔존으로 간주
        limit_abs = abs(int(new_loss_limit))
        if limit_abs > 0 and abs(pnl) >= limit_abs * 0.5:
            return True

    return False


def disable_auto_trade(reason: str) -> bool:
    """신규 매수 중단(is_enabled=False). 손절 모니터는 유지해야 한다.

    Returns:
        DB에서 실제로 OFF로 바꿨으면 True.
    """
    changed = False
    if "일일 손실" in reason or "일일 이익" in reason:
        mark_daily_limit_halt(reason)
    for db in get_db():
        session: Session = db
        settings = session.query(AutoTradeSettings).first()
        if settings and settings.is_enabled:
            settings.is_enabled = False
            settings.updated_at = utc_now_naive()
            session.commit()
            changed = True
            logger.warning(
                f"🛑 [AUTO_TRADE] 자동매매 OFF — {reason} "
                "(신규 매수 중단 · 손절/익절 모니터는 유지)"
            )
        break
    return changed


def estimate_upper_limit_price(prev_close: int, *, mult: float = SANGTTA_UPPER_LIMIT_MULT) -> Optional[int]:
    """전일종가 기준 상한가 근사 (일반주 ±30%)."""
    pc = int(prev_close or 0)
    if pc <= 0:
        return None
    return max(1, int(round(pc * float(mult))))


def sangtta_band_bounds(settings: Optional[AutoTradeSettings] = None) -> Tuple[float, float]:
    lo = float(getattr(settings, "sangtta_change_min", None) or SANGTTA_CHANGE_MIN) if settings else SANGTTA_CHANGE_MIN
    hi = float(getattr(settings, "sangtta_change_max", None) or SANGTTA_CHANGE_MAX) if settings else SANGTTA_CHANGE_MAX
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def evaluate_sangtta_breakout_from_ctx(
    settings: AutoTradeSettings,
    current_price: int,
    change_rate: Optional[float],
    ctx: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
    skip_time_check: bool = False,
) -> Tuple[bool, str]:
    """sangtta_breakout AND 패키지 (동기·컨텍스트 주입 가능).

    S1 시간 / S2 등락밴드 / S3 시가 / S4·S7 상한가 미도달 / S6 유니버스는 호출측 /
    S8 VWAP은 상따에서 기본 미적용.
    """
    if ctx is None:
        ctx = {}
    if not current_price or current_price <= 0:
        return False, "현재가 없음"

    if not skip_time_check:
        ok, reason = allows_strategy_new_buy(settings, "sangtta", now)
        if not ok:
            return False, reason or "상따 시간대 외"

    band_lo, band_hi = sangtta_band_bounds(settings)
    cr = change_rate
    if cr is None and ctx.get("prev_close") and int(ctx["prev_close"]) > 0:
        cr = (current_price - int(ctx["prev_close"])) / int(ctx["prev_close"]) * 100
    if cr is None:
        return False, "등락률 없음(상따 밴드)"
    cr = float(cr)
    if cr < band_lo or cr > band_hi:
        return False, f"상따 등락 밴드 이탈 ({cr:.2f}% / {band_lo:g}~{band_hi:g}%)"

    day_open = int(ctx.get("day_open") or 0)
    if day_open > 0:
        open_chg = (current_price - day_open) / day_open * 100
        open_rise_min = float(
            getattr(settings, "sangtta_open_rise_min_pct", None) or SANGTTA_OPEN_RISE_MIN_PCT
        )
        if not (open_chg >= open_rise_min or current_price >= day_open):
            return False, f"시가 조건 미달 (시가대비 {open_chg:.2f}%, 시가 {day_open:,})"

    prev_close = int(ctx.get("prev_close") or 0)
    ul = ctx.get("upper_limit_price")
    if ul is None and prev_close > 0:
        ul = estimate_upper_limit_price(prev_close)
    if ul:
        ul = int(ul)
        if current_price >= ul:
            return False, f"상한가 진입 금지 ({current_price:,} ≥ 상한가 {ul:,})"

    max_cap = float(
        getattr(settings, "sangtta_max_market_cap", None) or SANGTTA_MAX_MARKET_CAP_EOK
    )
    mcap = ctx.get("market_cap")
    if mcap is not None:
        try:
            mcap_f = float(mcap)
        except (TypeError, ValueError):
            mcap_f = None
        if mcap_f is not None and mcap_f > max_cap:
            return False, f"시총 초과 ({mcap_f:.0f}억 > {max_cap:.0f}억)"

    return True, "상따 게이트 통과"


def evaluate_oversold_breakout_from_ctx(
    settings: AutoTradeSettings,
    current_price: int,
    change_rate: Optional[float],
    ctx: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
    skip_time_check: bool = False,
) -> Tuple[bool, str]:
    """조건식 유니버스 후보의 돌파·거래량·과열·진입확인 AND 게이트.

    진입 확인 (켜진 모드 OR):
    - HARD: 직전 완성 5분봉 종가 > 돌파 레벨 → 즉시
    - SOFT: 현재가 > 레벨이 연속 N스캔 → 통과
    - HOLD: 고가 돌파 후 다음봉 저가 ≥ 돌파봉 저가
      + 전봉 RSI 교차 + 현재봉 양봉·RSI 유지
      (HOLD 시 가격 하한은 돌파봉 저가)
    - 전부 끄면: 레벨 위 터치(기존) 동작

    ctx['gate_checks'] 에 UI용 조건별 충족 여부 목록을 채운다.
    """
    if ctx is None:
        ctx = {}
    if not current_price or current_price <= 0:
        ctx["gate_checks"] = [_gate_check("현재가", False, "없음")]
        return False, "현재가 없음"
    if not skip_time_check:
        allowed, reason = allows_strategy_new_buy(settings, "breakout", now)
        if not allowed:
            ctx["gate_checks"] = [_gate_check("시간대", False, reason or "돌파 시간대 외")]
            return False, reason or "돌파 시간대 외"

    level_price = int(ctx.get("level_price") or 0)
    level_kind = str(
        ctx.get("level_kind")
        or getattr(settings, "breakout_level_mode", "prev_high")
        or "prev_high"
    )
    if level_kind not in ("prev_high", "n_day_high", "prev_bar_high", "n_bar_high"):
        ctx["gate_checks"] = [_gate_check("레벨모드", False, level_kind)]
        return False, f"지원하지 않는 돌파 레벨 ({level_kind})"
    if level_price <= 0:
        ctx["gate_checks"] = [_gate_check("돌파레벨", False, "계산 불가")]
        return False, "돌파 레벨 계산 불가"

    use_hard, use_soft, soft_polls, use_hold = _breakout_entry_flags(settings)
    confirm_close = int(ctx.get("confirm_close") or 0)
    confirm_high = int(ctx.get("confirm_high") or 0)
    soft_streak = int(ctx.get("entry_soft_streak") or 0)
    soft_bar_streak = int(ctx.get("soft_bar_streak") or 0)
    body_min = float(getattr(settings, "breakout_body_pct", None) or 0.0)
    range_min = float(getattr(settings, "breakout_range_mult", None) or 0.0)
    require_ma20 = bool(getattr(settings, "breakout_require_ma20_cross", False))
    # 장대/MA20 품질 모드: HARD는 종가 돌파만 (고가 터치 가짜 완화)
    if body_min > 0 or require_ma20:
        hard_ok = confirm_close > level_price
    else:
        hard_ok = confirm_close > level_price or confirm_high > level_price
    # MA20 유예창: 돌파 당시 레벨 위를 유지하면 HARD 유지
    # (후속봉에서 직전고·N봉고가 레벨이 상향되어 HARD만 깨지는 것 완화)
    grace_brk_lvl = int(ctx.get("ma20_grace_breakout_level") or 0)
    if bool(ctx.get("ma20_grace_active")) and grace_brk_lvl > 0 and confirm_close > grace_brk_lvl:
        hard_ok = True
    # SOFT: 스캔 연속 OR 레벨 위 완성봉 연속 (차트 유지)
    soft_ok = soft_streak >= soft_polls or soft_bar_streak >= soft_polls
    hold_structure = bool(ctx.get("hold_structure_ok"))
    hold_rsi_ok = bool(ctx.get("hold_rsi_ok"))
    hold_bullish_ok = bool(ctx.get("hold_bullish_ok"))
    hold_rsi_cross = bool(ctx.get("hold_rsi_cross"))
    hold_ok = bool(ctx.get("entry_hold_ok")) or (
        hold_structure and hold_rsi_ok and hold_bullish_ok
    )
    hold_breakout_low = int(ctx.get("hold_breakout_low") or 0)

    body_pct = float(ctx.get("body_pct") or 0.0)
    range_ratio = float(ctx.get("range_ratio") or 0.0)
    body_ok = body_min <= 0 or body_pct >= body_min
    range_ok = range_min <= 0 or range_ratio >= range_min
    ma20_mode = str(
        ctx.get("ma20_mode")
        or getattr(settings, "breakout_ma20_mode", None)
        or "above"
    ).strip().lower()
    if ma20_mode not in ("above", "cross"):
        ma20_mode = "above"
    if "ma20_signal_ok" in ctx:
        ma20_signal_ok = bool(ctx.get("ma20_signal_ok"))
    elif ma20_mode == "cross":
        ma20_signal_ok = bool(ctx.get("ma20_cross_ok"))
    else:
        ma20_v = ctx.get("ma20")
        ma20_signal_ok = bool(
            ctx.get("ma20_above_ok")
            if "ma20_above_ok" in ctx
            else (ma20_v is not None and confirm_close > float(ma20_v))
        )
    grace_active = bool(ctx.get("ma20_grace_active"))
    grace_waiting = bool(ctx.get("ma20_grace_waiting"))
    grace_bars = int(ctx.get("ma20_grace_bars") or getattr(settings, "breakout_ma20_grace_bars", 3) or 3)
    ma20_ok = (not require_ma20) or ma20_signal_ok
    ctx["body_pct"] = body_pct
    ctx["body_min"] = body_min
    ctx["body_ok"] = body_ok
    ctx["range_ratio"] = range_ratio
    ctx["range_min"] = range_min
    ctx["range_ok"] = range_ok
    ctx["ma20_mode"] = ma20_mode
    ctx["ma20_signal_ok"] = ma20_signal_ok
    ctx["ma20_ok"] = ma20_ok
    ctx["require_ma20_cross"] = require_ma20
    ctx["ma20_grace_waiting"] = grace_waiting
    ctx["ma20_grace_bars"] = grace_bars
    ctx["ma20_grace_active"] = grace_active

    ctx["confirm_close"] = confirm_close
    ctx["confirm_high"] = confirm_high
    ctx["entry_soft_streak"] = soft_streak
    ctx["soft_bar_streak"] = soft_bar_streak
    ctx["entry_soft_polls"] = soft_polls
    ctx["entry_hard_ok"] = hard_ok
    ctx["entry_soft_ok"] = soft_ok
    ctx["entry_hold_ok"] = hold_ok
    ctx["hold_bullish_ok"] = hold_bullish_ok
    ctx["entry_hard_enabled"] = use_hard
    ctx["entry_soft_enabled"] = use_soft
    ctx["entry_hold_enabled"] = use_hold

    # 가격 하한: HOLD 구조 성립 시 돌파봉 저가, 그 외 레벨
    price_floor = level_price
    floor_label = "레벨"
    if use_hold and hold_structure and hold_breakout_low > 0:
        price_floor = hold_breakout_low
        floor_label = "돌파봉저가"
    price_ok = current_price > price_floor
    ctx["price_floor"] = price_floor
    ctx["price_floor_label"] = floor_label
    ctx["price_ok"] = price_ok

    max_change = float(getattr(settings, "breakout_max_change_pct", 12.0) or 12.0)
    overheat_ok = not (
        change_rate is not None and float(change_rate) >= max_change
    )
    ctx["overheat_ok"] = overheat_ok
    ctx["max_change_pct"] = max_change

    day_volume = int(ctx.get("day_volume") or 0)
    prev_volume = int(ctx.get("prev_volume") or 0)
    vol_mult = float(getattr(settings, "breakout_vol_mult", 1.5) or 1.5)
    volume_ratio = (day_volume / prev_volume) if prev_volume > 0 else None
    if volume_ratio is not None:
        ctx["volume_ratio"] = volume_ratio
    volume_ok = bool(volume_ratio is not None and volume_ratio >= vol_mult)

    # MA20 유예창(대기·상회 후속봉 공통): 돌파봉 장대·거래량 상속
    # → 후속봉 거래량 부족으로 대기 전에 탈락하지 않도록
    if require_ma20 and grace_active:
        if not body_ok and ctx.get("ma20_grace_inherit_body_ok"):
            body_ok = True
            body_pct = float(ctx.get("ma20_grace_breakout_body_pct") or body_pct)
            ctx["body_ok"] = body_ok
            ctx["body_pct"] = body_pct
        if not volume_ok and ctx.get("ma20_grace_inherit_volume_ok"):
            brk_day = int(ctx.get("ma20_grace_breakout_day_volume") or 0)
            brk_prev = int(ctx.get("ma20_grace_breakout_prev_volume") or 0)
            if brk_prev > 0:
                day_volume = brk_day
                prev_volume = brk_prev
                volume_ratio = brk_day / brk_prev
                ctx["volume_ratio"] = volume_ratio
                ctx["day_volume"] = day_volume
                ctx["prev_volume"] = prev_volume
                volume_ok = bool(volume_ratio >= vol_mult)

    ctx["volume_ok"] = volume_ok
    ctx["vol_mult"] = vol_mult

    entry_any_ok = False
    if use_hard or use_soft or use_hold:
        entry_any_ok = (
            (use_hard and hard_ok)
            or (use_soft and soft_ok)
            or (use_hold and hold_ok)
        )
    else:
        entry_any_ok = True  # TOUCH
    ctx["entry_any_ok"] = entry_any_ok

    checks = _breakout_gate_checks(
        current_price=current_price,
        change_rate=change_rate,
        level_price=level_price,
        level_kind=level_kind,
        price_ok=price_ok,
        price_floor=price_floor,
        floor_label=floor_label,
        volume_ok=volume_ok,
        volume_ratio=volume_ratio,
        vol_mult=vol_mult,
        overheat_ok=overheat_ok,
        max_change=max_change,
        use_hard=use_hard,
        hard_ok=hard_ok,
        confirm_close=confirm_close,
        confirm_high=int(ctx.get("confirm_high") or 0),
        use_soft=use_soft,
        soft_ok=soft_ok,
        soft_streak=soft_streak,
        soft_bar_streak=soft_bar_streak,
        soft_polls=soft_polls,
        use_hold=use_hold,
        hold_ok=hold_ok,
        hold_structure=hold_structure,
        hold_rsi_cross=hold_rsi_cross,
        hold_rsi_ok=hold_rsi_ok,
        hold_bullish_ok=hold_bullish_ok,
        hold_armed=bool(ctx.get("hold_armed")),
        hold_breakout_low=hold_breakout_low,
        hold_rsi=ctx.get("hold_rsi"),
        hold_rsi_prev=ctx.get("hold_rsi_prev"),
        hold_rsi_before_prev=ctx.get("hold_rsi_before_prev"),
        hold_wait_reason=str(ctx.get("hold_wait_reason") or ""),
        hold_rsi_min=float(
            ctx.get("hold_rsi_min")
            if ctx.get("hold_rsi_min") is not None
            else getattr(settings, "breakout_hold_rsi_min", 30) or 30
        ),
        entry_any_ok=entry_any_ok,
        ma20_ok=ma20_ok,
        require_ma20=require_ma20,
        ma20=ctx.get("ma20"),
        ma20_detail=str(ctx.get("ma20_grace_reason") or ""),
        body_ok=body_ok,
        body_pct=body_pct,
        body_min=body_min,
        range_ok=range_ok,
        range_ratio=range_ratio,
        range_min=range_min,
    )
    ctx["gate_checks"] = checks

    if not price_ok:
        proximity = (current_price / price_floor - 1) * 100
        extra = ""
        if use_hold and ctx.get("hold_wait_reason"):
            extra = f" · {ctx.get('hold_wait_reason')}"
        elif use_hold and ctx.get("hold_armed"):
            extra = " · HOLD armed(다음봉 대기)"
        return False, (
            f"돌파 전 ({current_price:,} ≤ {price_floor:,} {floor_label}, "
            f"{proximity:+.2f}%){extra}"
        )

    if change_rate is not None and float(change_rate) >= max_change:
        return False, f"과열 컷 ({float(change_rate):.2f}% ≥ {max_change:g}%)"

    if prev_volume <= 0:
        return False, "비교 거래량 없음(분봉)"
    if not volume_ok:
        return False, f"거래량 부족 ({volume_ratio:.2f}배 < {vol_mult:g}배)"

    if require_ma20 and not ma20_ok:
        if grace_waiting:
            return False, str(ctx.get("ma20_grace_reason") or f"MA20 유예 대기 (/{grace_bars}봉)")
        ma20_v = ctx.get("ma20")
        ma_bit = f" MA20={ma20_v:,.0f}" if ma20_v else ""
        label = "MA20 상향 돌파 아님" if ma20_mode == "cross" else "MA20 상회 아님"
        expired = str(ctx.get("ma20_grace_reason") or "")
        if ctx.get("ma20_grace_expired") and expired:
            return False, f"{label} · {expired} (종가 {confirm_close:,}{ma_bit})"
        return False, f"{label} (종가 {confirm_close:,}{ma_bit})"
    if body_min > 0 and not body_ok:
        return False, f"장대 부족 (몸통 {body_pct:.2f}% < {body_min:g}%)"
    if range_min > 0 and not range_ok:
        return False, f"범위 확장 부족 ({range_ratio:.2f}× < {range_min:g}×)"

    # 진입 확인 HARD / SOFT / HOLD
    if use_hard or use_soft or use_hold:
        passed_modes: List[str] = []
        if use_hard and hard_ok:
            passed_modes.append("HARD")
        if use_soft and soft_ok:
            if soft_bar_streak >= soft_polls:
                passed_modes.append(f"SOFT 봉{soft_bar_streak}/{soft_polls}")
            else:
                passed_modes.append(f"SOFT {soft_streak}/{soft_polls}")
        if use_hold and hold_ok:
            rsi_v = ctx.get("hold_rsi")
            rsi_bit = f" RSI{rsi_v}" if rsi_v is not None else ""
            passed_modes.append(f"HOLD{rsi_bit}+양봉")
        if not passed_modes:
            wait_parts: List[str] = []
            if use_hard:
                if confirm_close > 0 or confirm_high > 0:
                    wait_parts.append(
                        f"HARD 미충족(직전고가 {confirm_high:,}/종가 {confirm_close:,} "
                        f"≤ 레벨 {level_price:,})"
                    )
                else:
                    wait_parts.append("HARD 미충족(직전봉 없음)")
            if use_soft:
                wait_parts.append(
                    f"SOFT 스캔 {soft_streak}/{soft_polls}"
                    f"·봉 {soft_bar_streak}/{soft_polls}"
                )
            if use_hold:
                wait_parts.append(
                    str(ctx.get("hold_wait_reason") or "HOLD 미충족")
                )
            return False, f"진입 확인 대기 ({', '.join(wait_parts)})"
        mode_label = "+".join(passed_modes)
        ctx["entry_confirm_mode"] = mode_label
        quality = []
        if require_ma20:
            quality.append("MA20↑")
        if body_min > 0:
            quality.append(f"장대{body_pct:.1f}%")
        if range_min > 0:
            quality.append(f"범위{range_ratio:.1f}×")
        qbit = (" · " + "+".join(quality)) if quality else ""
        return True, (
            f"돌파 통과 ({mode_label} · {level_kind} {level_price:,}, "
            f"거래량 {volume_ratio:.2f}배{qbit})"
        )

    ctx["entry_confirm_mode"] = "TOUCH"
    return True, f"돌파 통과 (TOUCH · {level_kind} {level_price:,}, 거래량 {volume_ratio:.2f}배)"


def _gate_check(
    key: str,
    ok: bool,
    detail: str = "",
    *,
    enabled: bool = True,
) -> Dict[str, Any]:
    return {
        "key": key,
        "ok": bool(ok),
        "detail": detail or "",
        "enabled": bool(enabled),
    }


def _breakout_gate_checks(
    *,
    current_price: int,
    change_rate: Optional[float],
    level_price: int,
    level_kind: str,
    price_ok: bool,
    price_floor: int,
    floor_label: str,
    volume_ok: bool,
    volume_ratio: Optional[float],
    vol_mult: float,
    overheat_ok: bool,
    max_change: float,
    use_hard: bool,
    hard_ok: bool,
    confirm_close: int,
    confirm_high: int,
    use_soft: bool,
    soft_ok: bool,
    soft_streak: int,
    soft_bar_streak: int,
    soft_polls: int,
    use_hold: bool,
    hold_ok: bool,
    hold_structure: bool,
    hold_rsi_cross: bool,
    hold_rsi_ok: bool,
    hold_bullish_ok: bool,
    hold_armed: bool,
    hold_breakout_low: int,
    hold_rsi: Any,
    hold_rsi_prev: Any,
    hold_rsi_before_prev: Any,
    hold_wait_reason: str,
    hold_rsi_min: float,
    entry_any_ok: bool,
    ma20_ok: bool = True,
    require_ma20: bool = False,
    ma20: Any = None,
    ma20_detail: str = "",
    body_ok: bool = True,
    body_pct: float = 0.0,
    body_min: float = 0.0,
    range_ok: bool = True,
    range_ratio: float = 0.0,
    range_min: float = 0.0,
) -> List[Dict[str, Any]]:
    """돌파 후보 UI용 조건별 체크리스트."""
    checks: List[Dict[str, Any]] = []
    price_detail = (
        f"{current_price:,} > {price_floor:,} ({floor_label})"
        if price_ok
        else f"{current_price:,} ≤ {price_floor:,} ({floor_label})"
    )
    checks.append(_gate_check("가격", price_ok, price_detail))

    if volume_ratio is None:
        vol_detail = "비교 거래량 없음"
    else:
        vol_detail = f"{volume_ratio:.2f}배 / ≥{vol_mult:g}배"
    checks.append(_gate_check("거래량", volume_ok, vol_detail))

    if require_ma20:
        if ma20_detail:
            detail = ma20_detail
        elif ma20 is not None:
            detail = (
                f"종가 상향돌파 MA20={float(ma20):,.0f}"
                if ma20_ok
                else f"미돌파 MA20={float(ma20):,.0f}"
            )
        else:
            detail = "MA20 계산 불가" if not ma20_ok else "OK"
        checks.append(_gate_check("MA20상향", ma20_ok, detail, enabled=True))
    if body_min > 0:
        checks.append(_gate_check(
            "장대", body_ok, f"몸통 {body_pct:.2f}% / ≥{body_min:g}%", enabled=True,
        ))
    if range_min > 0:
        checks.append(_gate_check(
            "범위확장", range_ok, f"{range_ratio:.2f}× / ≥{range_min:g}×", enabled=True,
        ))

    if change_rate is None:
        oh_detail = f"등락 없음 · 컷 {max_change:g}%"
    else:
        oh_detail = f"{float(change_rate):+.2f}% / <{max_change:g}%"
    checks.append(_gate_check("과열", overheat_ok, oh_detail))

    if use_hard:
        if hard_ok:
            if confirm_close > level_price:
                hard_detail = f"종가 {confirm_close:,} > 레벨 {level_price:,}"
            else:
                hard_detail = f"고가 {confirm_high:,} > 레벨 {level_price:,}"
        elif confirm_close > 0 or confirm_high > 0:
            hard_detail = (
                f"고가 {confirm_high:,}/종가 {confirm_close:,} ≤ 레벨 {level_price:,}"
            )
        else:
            hard_detail = "직전봉 없음"
        checks.append(_gate_check("HARD", hard_ok, hard_detail, enabled=True))
    else:
        checks.append(_gate_check("HARD", False, "OFF", enabled=False))

    if use_soft:
        soft_detail = (
            f"스캔 {soft_streak}/{soft_polls} · 봉 {soft_bar_streak}/{soft_polls}"
        )
        checks.append(_gate_check("SOFT", soft_ok, soft_detail, enabled=True))
    else:
        checks.append(_gate_check("SOFT", False, "OFF", enabled=False))

    if use_hold:
        struct_detail = (
            f"저가유지 · 돌파저가 {hold_breakout_low:,}"
            if hold_structure and hold_breakout_low
            else ("armed(다음봉 대기)" if hold_armed else (hold_wait_reason or "구조 미충족"))
        )
        checks.append(_gate_check("HOLD구조", hold_structure, struct_detail, enabled=True))

        if hold_rsi_before_prev is not None and hold_rsi_prev is not None:
            cross_detail = f"{float(hold_rsi_before_prev):.1f}→{float(hold_rsi_prev):.1f}"
        elif hold_rsi_prev is not None:
            cross_detail = f"전봉 RSI {float(hold_rsi_prev):.1f}"
        else:
            cross_detail = hold_wait_reason or "전봉 교차 대기"
        checks.append(_gate_check("전봉RSI교차", hold_rsi_cross, cross_detail, enabled=True))

        rsi_held = bool(hold_rsi is not None and float(hold_rsi) > float(hold_rsi_min))
        rsi_now_detail = (
            f"현재 RSI {float(hold_rsi):.1f} / >{hold_rsi_min:g}"
            if hold_rsi is not None
            else f"RSI 없음 · >{hold_rsi_min:g}"
        )
        checks.append(_gate_check("RSI유지", rsi_held, rsi_now_detail, enabled=True))
        checks.append(_gate_check(
            "현재양봉",
            hold_bullish_ok,
            "양봉" if hold_bullish_ok else "양봉 아님",
            enabled=True,
        ))
        checks.append(_gate_check(
            "HOLD",
            hold_ok,
            "통과" if hold_ok else (hold_wait_reason or "미충족"),
            enabled=True,
        ))
    else:
        checks.append(_gate_check("HOLD", False, "OFF", enabled=False))

    checks.append(_gate_check(
        "진입확인",
        entry_any_ok,
        "HARD∨SOFT∨HOLD" if (use_hard or use_soft or use_hold) else "TOUCH",
    ))
    return checks


def resolve_breakout_level_from_minute_bars(
    bars: List[Dict[str, Any]],
    settings: AutoTradeSettings,
    *,
    exclude_forming: bool = True,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """5분봉 기준 돌파 레벨·거래량·MA20 컨텍스트.

    - exclude_forming=True(실매매): 최신봉은 형성 중 → 직전 완성봉이 확인봉
    - exclude_forming=False(히스토리 시뮬): 마지막 봉을 완성 확인봉으로 사용
    - 돌파 레벨은 확인봉을 제외한 이전 완성봉으로 계산
    - 거래량: 확인봉 ÷ 직전 N봉 평균
    - MA20 상향: 직전봉 종가≤직전 MA20 이고 확인봉 종가>MA20 (전일 분봉 포함 SMA)
    """
    if not bars:
        return None, "5분봉 데이터 없음(돌파)"
    rows = sorted(bars, key=lambda row: str(row.get("timestamp", "")))
    if len(rows) < 3:
        return None, "5분봉 부족(돌파)"

    if exclude_forming:
        completed = rows[:-1]
    else:
        completed = rows
    if len(completed) < 2:
        return None, "5분봉 부족(돌파)"
    confirm = completed[-1]
    prior_for_level = completed[:-1]
    if not prior_for_level:
        return None, "5분봉 부족(돌파 레벨)"

    mode = str(getattr(settings, "breakout_level_mode", "prev_high") or "prev_high")
    n_bar = max(1, int(getattr(settings, "breakout_n_day", 10) or 10))

    if mode in ("n_day_high", "n_bar_high"):
        mode = "n_day_high"
        window = prior_for_level[-n_bar:]
        if len(window) < n_bar:
            return None, f"N봉 고가 데이터 부족 ({len(window)}/{n_bar})"
        level = max(int(row.get("high") or 0) for row in window)
    else:
        mode = "prev_high"
        level = int(prior_for_level[-1].get("high") or 0)

    if level <= 0:
        return None, "돌파 레벨 계산 불가"

    confirm_open = int(confirm.get("open") or 0)
    confirm_close = int(confirm.get("close") or 0)
    confirm_high = int(confirm.get("high") or 0)
    confirm_low = int(confirm.get("low") or 0)

    soft_bar_streak = 0
    for row in reversed(completed):
        c = int(row.get("close") or 0)
        h = int(row.get("high") or 0)
        if c > level or h > level:
            soft_bar_streak += 1
        else:
            break

    vol_window = prior_for_level[-n_bar:] if prior_for_level else []
    avg_prev = 0.0
    if vol_window:
        vols = [int(row.get("volume") or 0) for row in vol_window]
        positive = [v for v in vols if v > 0]
        if not positive and len(prior_for_level) > len(vol_window):
            # N창이 전부 0이면 확인봉 이전 전체에서 양성 거래량 평균 (장초·데이터 공백 완화)
            vols_all = [int(row.get("volume") or 0) for row in prior_for_level]
            positive = [v for v in vols_all if v > 0]
        if positive:
            avg_prev = sum(positive) / len(positive)

    body_pct = 0.0
    if confirm_open > 0 and confirm_close > 0:
        body_pct = (confirm_close / confirm_open - 1.0) * 100.0

    range_lookback = min(12, len(prior_for_level))
    avg_range = 0.0
    range_ratio = 0.0
    if range_lookback > 0:
        ranges = [
            max(0, int(r.get("high") or 0) - int(r.get("low") or 0))
            for r in prior_for_level[-range_lookback:]
        ]
        positive_r = [x for x in ranges if x > 0]
        if positive_r:
            avg_range = sum(positive_r) / len(positive_r)
            bar_range = max(0, confirm_high - confirm_low)
            if avg_range > 0:
                range_ratio = bar_range / avg_range

    closes = [float(r.get("close") or 0) for r in completed]
    ma20 = None
    ma20_prev = None
    ma20_cross_ok = False
    ma20_above_ok = False
    if len(closes) >= 20:
        ma20 = sum(closes[-20:]) / 20.0
        ma20_above_ok = bool(confirm_close > ma20)
        if len(closes) >= 21:
            ma20_prev = sum(closes[-21:-1]) / 20.0
            prev_close = closes[-2]
            # クラシック 상향 + 당봉 시가 이하에서 종가 돌파 + 저가 터치 후 종가 상회
            classic = (
                prev_close > 0
                and ma20_prev is not None
                and prev_close <= ma20_prev
                and confirm_close > ma20
            )
            intrabar = bool(confirm_open > 0 and confirm_open <= ma20 < confirm_close)
            reclaim = bool(
                confirm_low > 0
                and ma20 is not None
                and confirm_low <= ma20 * 1.002
                and confirm_close > ma20
                and confirm_close > confirm_open
            )
            ma20_cross_ok = bool(classic or intrabar or reclaim)

    # above=종가>MA20 / cross=상향돌파(기본 above — 갭장·HTS이평 차이 완화)
    ma20_mode = str(getattr(settings, "breakout_ma20_mode", None) or "above").strip().lower()
    if ma20_mode not in ("above", "cross"):
        ma20_mode = "above"
    ma20_signal_ok = ma20_cross_ok if ma20_mode == "cross" else ma20_above_ok

    return {
        "level_kind": mode,
        "level_price": level,
        "confirm_open": confirm_open,
        "confirm_close": confirm_close,
        "confirm_high": confirm_high,
        "confirm_low": confirm_low,
        "soft_bar_streak": soft_bar_streak,
        "day_volume": int(confirm.get("volume") or 0),
        "prev_volume": int(round(avg_prev)) if avg_prev > 0 else 0,
        "bar_interval": BREAKOUT_BAR_INTERVAL,
        "n_bar": n_bar,
        "body_pct": body_pct,
        "range_ratio": range_ratio,
        "avg_range": avg_range,
        "ma20": ma20,
        "ma20_prev": ma20_prev,
        "ma20_cross_ok": ma20_cross_ok,
        "ma20_above_ok": ma20_above_ok,
        "ma20_mode": ma20_mode,
        "ma20_signal_ok": ma20_signal_ok,
        "breakout_bar_low": confirm_low,
    }, ""


async def _load_daily_gate_bars(kiwoom_api, stock_code: str) -> Tuple[Optional[Dict], Optional[Dict], str]:
    """(today_bar, prev_bar, fail_reason)."""
    code = getattr(kiwoom_api, "normalize_stock_code", lambda c: c)(stock_code)
    daily_bars = await kiwoom_api.get_stock_chart_data(code, "1D")
    if not daily_bars:
        if not api_rate_limiter.is_api_available():
            return None, None, "API 호출 제한(일봉)"
        return None, None, "일봉 데이터 없음(게이트)"

    today_str = kst_date_str()
    today_bar = None
    prev_bar = None
    for bar in reversed(daily_bars):
        ts = str(bar.get("timestamp", ""))[:10]
        if ts == today_str:
            today_bar = bar
            break
    if not today_bar:
        today_bar = daily_bars[-1]
    for bar in reversed(daily_bars):
        ts = str(bar.get("timestamp", ""))[:10]
        if ts != str(today_bar.get("timestamp", ""))[:10]:
            prev_bar = bar
            break
    return today_bar, prev_bar, ""


async def _eval_legacy_momentum(
    kiwoom_api,
    settings: AutoTradeSettings,
    stock_code: str,
    current_price: int,
) -> Tuple[bool, str]:
    """기존 진입 게이트 AND 묶음 (legacy_momentum)."""
    if not settings.use_entry_gate:
        return True, "게이트 비활성"

    if not current_price or current_price <= 0:
        return False, "현재가 없음"

    code = getattr(kiwoom_api, "normalize_stock_code", lambda c: c)(stock_code)
    daily_bars = await kiwoom_api.get_stock_chart_data(code, "1D")
    if not daily_bars:
        if not api_rate_limiter.is_api_available():
            return False, "API 호출 제한(일봉)"
        return False, "일봉 데이터 없음(게이트)"

    today_str = kst_date_str()
    today_bar = None
    prev_bar = None
    for bar in reversed(daily_bars):
        ts = str(bar.get("timestamp", ""))[:10]
        if ts == today_str:
            today_bar = bar
            break
    if not today_bar:
        today_bar = daily_bars[-1]
    for bar in reversed(daily_bars):
        ts = str(bar.get("timestamp", ""))[:10]
        if ts != str(today_bar.get("timestamp", ""))[:10]:
            prev_bar = bar
            break

    day_open = int(today_bar.get("open") or 0)
    day_high = int(today_bar.get("high") or current_price)
    day_low = int(today_bar.get("low") or current_price)
    day_volume = int(today_bar.get("volume") or 0)
    prev_volume = int(prev_bar.get("volume") or 0) if prev_bar else 0

    if settings.require_above_open and day_open > 0 and current_price < day_open:
        return False, f"시가 미만 ({current_price:,} < {day_open:,})"

    if settings.require_above_vwap:
        minute_bars = await kiwoom_api.get_stock_chart_data(code, VWAP_BAR_INTERVAL)
        vwap = _compute_vwap(minute_bars, today_str)
        if vwap is None:
            if not api_rate_limiter.is_api_available():
                return False, "API 호출 제한(VWAP)"
            return False, "VWAP 계산 불가"
        if current_price < vwap:
            return False, f"VWAP 미만 ({current_price:,} < {vwap:,.0f})"

    pos_min = settings.day_position_min
    if pos_min is not None and day_high > day_low:
        position = (current_price - day_low) / (day_high - day_low)
        if position < float(pos_min):
            return False, f"당일 위치 부족 ({position:.2f} < {pos_min})"

    pos_max = getattr(settings, "day_position_max", None)
    if pos_max is not None and day_high > day_low:
        position = (current_price - day_low) / (day_high - day_low)
        if position > float(pos_max):
            return False, f"당일 위치 과열 ({position:.2f} > {pos_max})"

    vol_ratio_min = settings.volume_ratio_min
    if vol_ratio_min is not None and prev_volume > 0:
        ratio = day_volume / prev_volume * 100
        if ratio < float(vol_ratio_min):
            return False, f"거래량비 부족 ({ratio:.0f}% < {vol_ratio_min}%)"

    rsi_min = getattr(settings, "legacy_rsi_min", None)
    rsi_max = getattr(settings, "legacy_rsi_max", None)
    if rsi_min is not None or rsi_max is not None:
        rsi = compute_legacy_rsi14(daily_bars, current_price=current_price)
        if rsi is None:
            return False, "RSI(14) 계산 불가"
        if rsi_min is not None and rsi < float(rsi_min):
            return False, f"RSI 하한 미달 ({rsi:.1f} < {float(rsi_min):g})"
        if rsi_max is not None and rsi > float(rsi_max):
            return False, f"RSI 과열 ({rsi:.1f} > {float(rsi_max):g})"

    return True, "게이트 통과"


async def _eval_sangtta_breakout(
    kiwoom_api,
    settings: AutoTradeSettings,
    stock_code: str,
    current_price: int,
    change_rate: Optional[float] = None,
    *,
    ctx: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    skip_time_check: bool = False,
) -> Tuple[bool, str]:
    """상따 전용 게이트. ctx가 충분하면 API 생략."""
    merged: Dict[str, Any] = dict(ctx or {})
    need_bars = not (
        merged.get("day_open") is not None
        and (merged.get("prev_close") is not None or change_rate is not None)
    )
    if need_bars and kiwoom_api is not None:
        today_bar, prev_bar, err = await _load_daily_gate_bars(kiwoom_api, stock_code)
        if err and change_rate is None and merged.get("prev_close") is None:
            return False, err
        if today_bar:
            merged.setdefault("day_open", int(today_bar.get("open") or 0))
            merged.setdefault("day_high", int(today_bar.get("high") or current_price))
            merged.setdefault("day_low", int(today_bar.get("low") or current_price))
        if prev_bar:
            merged.setdefault("prev_close", int(prev_bar.get("close") or 0))

    if merged.get("market_cap") is None:
        try:
            from utils.fundamental_mart_store import get_latest_by_code
            fund = get_latest_by_code(
                getattr(kiwoom_api, "normalize_stock_code", lambda c: c)(stock_code)
                if kiwoom_api
                else stock_code
            ) or {}
            if fund.get("market_cap") is not None:
                merged["market_cap"] = fund.get("market_cap")
        except Exception:
            pass

    if merged.get("upper_limit_price") is None and merged.get("prev_close"):
        merged["upper_limit_price"] = estimate_upper_limit_price(int(merged["prev_close"]))

    return evaluate_sangtta_breakout_from_ctx(
        settings,
        current_price,
        change_rate,
        merged,
        now=now,
        skip_time_check=skip_time_check,
    )


async def _eval_oversold_breakout(
    kiwoom_api,
    settings: AutoTradeSettings,
    stock_code: str,
    current_price: int,
    change_rate: Optional[float] = None,
    *,
    ctx: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    skip_time_check: bool = False,
    update_soft_streak: bool = True,
) -> Tuple[bool, str]:
    """과매도 이력은 조건식(5분 RSI)에 맡기고, 소수 후보의 5분봉으로 돌파를 판정."""
    merged: Dict[str, Any] = dict(ctx or {})
    _, use_soft, soft_polls, use_hold = _breakout_entry_flags(settings)
    # 시그널 meta에는 level_price만 넣고 prev_volume을 안 넣는 경우가 많음.
    # 그때 분봉 resolve를 건너뛰면 prev_volume=0 → "비교 거래량 없음(분봉)" 오탐.
    need_level = not merged.get("level_price")
    need_volume_ctx = int(merged.get("prev_volume") or 0) <= 0
    need_hold = use_hold and "hold_structure_ok" not in merged
    # MA20 유예(N>1): 시그널 meta의 스냅샷 MA20만으로 즉시 탈락하지 않도록 5분봉 재조회
    grace_n = max(1, int(getattr(settings, "breakout_ma20_grace_bars", None) or 3))
    need_ma20_grace = bool(getattr(settings, "breakout_require_ma20_cross", False)) and grace_n > 1
    need_bars = need_level or need_volume_ctx or need_hold or need_ma20_grace
    bars = None
    if need_bars:
        code = getattr(kiwoom_api, "normalize_stock_code", lambda c: c)(stock_code)
        bars = await kiwoom_api.get_stock_chart_data(code, BREAKOUT_BAR_INTERVAL)
        if not bars:
            if not api_rate_limiter.is_api_available():
                return False, "API 호출 제한(5분봉)"
            return False, "5분봉 데이터 없음(돌파)"
        if need_level or need_volume_ctx:
            resolved, err = resolve_breakout_level_from_minute_bars(bars, settings)
            if err:
                return False, err
            merged.update(resolved or {})
        if need_ma20_grace or need_level or need_volume_ctx:
            merged.update(resolve_breakout_ma20_grace_from_minute_bars(bars, settings))
        if use_hold:
            hold_ctx = resolve_breakout_hold_from_minute_bars(
                bars,
                settings,
                stock_code,
                update_armed=update_soft_streak,
            )
            merged.update(hold_ctx)
            if hold_ctx.get("hold_armed") and update_soft_streak:
                logger.info(
                    f"📈 [돌파] 진입확인 HOLD armed {stock_code}: "
                    f"{hold_ctx.get('hold_wait_reason') or ''}"
                )

    level_price = int(merged.get("level_price") or 0)
    confirm_close = int(merged.get("confirm_close") or 0)
    confirm_high = int(merged.get("confirm_high") or 0)
    # 이미 확인봉에서 돌파했으면 틱이 레벨 근처여도 SOFT 스트릭 유지
    broken_on_confirm = bool(
        level_price
        and (confirm_close > level_price or confirm_high > level_price)
    )
    above_level = bool(
        broken_on_confirm
        or (current_price and level_price and current_price > level_price)
    )
    prev_streak = get_breakout_entry_soft_streak(stock_code)
    if update_soft_streak:
        streak = update_breakout_entry_soft_streak(stock_code, above_level)
        if use_soft and (streak != prev_streak):
            if above_level:
                logger.info(
                    f"📈 [돌파] 진입확인 SOFT {stock_code}: {streak}/{soft_polls} "
                    f"(가격={current_price:,} 레벨={level_price:,}, "
                    f"확인고가={confirm_high:,}/종가={confirm_close:,}, "
                    f"봉SOFT={int(merged.get('soft_bar_streak') or 0)})"
                )
            elif prev_streak > 0:
                logger.info(
                    f"📈 [돌파] 진입확인 SOFT 리셋 {stock_code}: {prev_streak}→0 "
                    f"(가격={current_price:,} ≤ 레벨={level_price:,})"
                )
    else:
        streak = prev_streak
    merged["entry_soft_streak"] = streak

    if ctx is not None:
        ctx.update(merged)
    result = evaluate_oversold_breakout_from_ctx(
        settings,
        current_price,
        change_rate,
        merged,
        now=now,
        skip_time_check=skip_time_check,
    )
    if ctx is not None:
        ctx.update(merged)
    ok, reason = result
    if ok and update_soft_streak:
        logger.info(
            f"📈 [돌파] 진입확인 통과 {stock_code}: {reason}"
        )
    return result


async def evaluate_gate_pack(
    kiwoom_api,
    settings: AutoTradeSettings,
    pack_name: str,
    stock_code: str,
    current_price: int,
    *,
    change_rate: Optional[float] = None,
    ctx: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    skip_time_check: bool = False,
    update_soft_streak: bool = True,
) -> Tuple[bool, str]:
    """게이트 패키지 평가. pack: legacy_momentum | sangtta_breakout | oversold_breakout."""
    pack = (pack_name or "legacy_momentum").strip().lower()
    if pack in ("sangtta", "sangtta_breakout"):
        return await _eval_sangtta_breakout(
            kiwoom_api,
            settings,
            stock_code,
            current_price,
            change_rate,
            ctx=ctx,
            now=now,
            skip_time_check=skip_time_check,
        )
    if pack in ("breakout", "oversold_breakout"):
        return await _eval_oversold_breakout(
            kiwoom_api,
            settings,
            stock_code,
            current_price,
            change_rate,
            ctx=ctx,
            now=now,
            skip_time_check=skip_time_check,
            update_soft_streak=update_soft_streak,
        )
    if pack in ("ymgp", "yeokmaegongpa"):
        return await _eval_ymgp(
            kiwoom_api,
            settings,
            stock_code,
            current_price,
            change_rate,
            ctx=ctx,
            now=now,
            skip_time_check=skip_time_check,
        )
    if pack in ("jongga", "jongga_closing", "closing_bet"):
        return await _eval_jongga(
            kiwoom_api,
            settings,
            stock_code,
            current_price,
            change_rate,
            ctx=ctx,
            now=now,
            skip_time_check=skip_time_check,
        )
    return await _eval_legacy_momentum(kiwoom_api, settings, stock_code, current_price)


async def _eval_jongga(
    kiwoom_api,
    settings: AutoTradeSettings,
    stock_code: str,
    current_price: int,
    change_rate: Optional[float] = None,
    *,
    ctx: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    skip_time_check: bool = False,
) -> Tuple[bool, str]:
    """종가배팅: 시간창·후보 소속만 확인 (테마/스코어는 스캐너에서 확정)."""
    if not skip_time_check:
        ok, reason = allows_strategy_new_buy(settings, "jongga", now=now)
        if not ok:
            return False, reason or "종가배팅 시간 외"
    if not getattr(settings, "use_jongga", False):
        return False, "종가배팅 비활성"
    if current_price is None or int(current_price) <= 0:
        return False, "현재가 없음"
    ctx = ctx if isinstance(ctx, dict) else {}
    code = str(stock_code or "").replace("A", "").zfill(6)
    allowed_codes = ctx.get("jongga_candidate_codes")
    if allowed_codes is not None:
        norm = {
            str(c or "").replace("A", "").zfill(6)
            for c in (allowed_codes or [])
            if c
        }
        if code not in norm:
            return False, "종가배팅 후보 아님"
    if ctx.get("theme"):
        ctx.setdefault("jongga_theme", ctx.get("theme"))
    return True, "종가배팅 게이트 통과"


async def _eval_ymgp(
    kiwoom_api,
    settings: AutoTradeSettings,
    stock_code: str,
    current_price: int,
    change_rate: Optional[float] = None,
    *,
    ctx: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    skip_time_check: bool = False,
) -> Tuple[bool, str]:
    """역매공파: 일봉 상태 ARMED + 기준봉 고점 돌파(또는 meta entry_leg=2 눌림)."""
    from utils.ymgp_engine import (
        entry1_breakout_ok,
        entry2_pullback_ok,
        evaluate_ymgp_from_daily,
        format_ymgp_checks_summary,
        format_ymgp_fail_brief,
        get_stock_state,
        is_reentry_locked,
        log_ymgp_stage_metrics,
        update_stock_state,
    )

    merged: Dict[str, Any] = dict(ctx or {})
    if not skip_time_check:
        allowed, reason = allows_strategy_new_buy(settings, "ymgp", now)
        if not allowed:
            merged["gate_checks"] = [{"key": "time", "label": "시간대", "passed": False, "actual": reason}]
            if ctx is not None:
                ctx.update(merged)
            return False, reason or "역매공파 시간대 외"

    if is_reentry_locked(stock_code, settings):
        merged["gate_checks"] = [{"key": "lock", "label": "재진입 락", "passed": False, "actual": "락 중"}]
        if ctx is not None:
            ctx.update(merged)
        return False, "손절 후 재진입 락"

    code = getattr(kiwoom_api, "normalize_stock_code", lambda c: c)(stock_code)
    bars = merged.get("daily_bars")
    if not bars:
        bars = await kiwoom_api.get_stock_chart_data(
            code, "1D", max_bars=520, allow_off_hours=True,
        )
        merged["daily_bars"] = bars
    if not bars:
        if not api_rate_limiter.is_api_available():
            return False, "API 호출 제한(일봉)"
        return False, "일봉 데이터 없음(역매공파)"

    prior = get_stock_state(code)
    evaled = evaluate_ymgp_from_daily(
        bars,
        settings,
        current_price=current_price,
        change_rate=change_rate,
        prior_stage=prior.get("stage"),
        stopped_lock=False,
    )
    merged.update({
        "ymgp_stage": evaled.get("stage"),
        "ymgp_checks": evaled.get("checks"),
        "ymgp_mas": evaled.get("mas"),
        "ymgp_box": evaled.get("box"),
        "ymgp_ref": evaled.get("ref") or prior.get("ref"),
        "gate_checks": evaled.get("checks") or [],
        "ymgp_checks_summary": format_ymgp_checks_summary(evaled),
        "ymgp_fail_brief": format_ymgp_fail_brief(evaled),
    })
    name = str(merged.get("stock_name") or "")
    log_ymgp_stage_metrics(code, evaled, stock_name=name)
    ref = merged.get("ymgp_ref") or {}
    if evaled.get("ref"):
        update_stock_state(
            code,
            stage=evaled.get("stage"),
            ref=evaled.get("ref"),
            box=evaled.get("box"),
        )
    else:
        update_stock_state(code, stage=evaled.get("stage"))

    entry_leg = int(merged.get("entry_leg") or merged.get("ymgp_entry_leg") or 1)
    if entry_leg >= 2:
        ok2, reason2 = entry2_pullback_ok(current_price, ref, evaled.get("mas") or {}, settings)
        if ctx is not None:
            ctx.update(merged)
        return (ok2, reason2) if ok2 else (False, reason2)

    stage = evaled.get("stage")
    if stage != "ARMED" and not (ref and ref.get("high")):
        if ctx is not None:
            ctx.update(merged)
        brief = merged.get("ymgp_fail_brief") or ""
        base = f"단계 미달 ({stage})"
        return False, f"{base} · {brief}" if brief else base
    if evaled.get("overheat"):
        if ctx is not None:
            ctx.update(merged)
        return False, "과열 컷"

    # prev_high for entry mode
    if len(bars) >= 2:
        try:
            ref = dict(ref or {})
            ref["prev_high"] = int(bars[-2].get("high") or 0)
            merged["ymgp_ref"] = ref
        except (TypeError, ValueError):
            pass

    ok1, reason1 = entry1_breakout_ok(current_price, ref, settings)
    if ctx is not None:
        ctx.update(merged)
        ctx["level_kind"] = "ymgp_ref_high"
        ctx["level_price"] = int((ref or {}).get("high") or 0)
        ctx["breakout_level_price"] = int((ref or {}).get("low") or 0)  # 손절 기준가 저장용
    return (ok1, reason1) if ok1 else (False, reason1)


async def check_entry_gate(
    kiwoom_api,
    settings: AutoTradeSettings,
    stock_code: str,
    current_price: int,
) -> Tuple[bool, str]:
    """진입 타이밍 게이트 (legacy_momentum). use_entry_gate=False면 항상 통과."""
    return await evaluate_gate_pack(
        kiwoom_api, settings, "legacy_momentum", stock_code, current_price,
    )


async def fetch_entry_gate_context(
    kiwoom_api,
    stock_code: str,
    current_price: int,
    today_str: Optional[str] = None,
) -> Dict[str, Any]:
    """진입 게이트 평가용 당일 컨텍스트."""
    today_str = today_str or kst_date_str()
    code = getattr(kiwoom_api, "normalize_stock_code", lambda c: c)(stock_code)
    ctx: Dict[str, Any] = {}
    daily_bars = await kiwoom_api.get_stock_chart_data(code, "1D")
    if not daily_bars:
        return ctx
    today_bar = None
    prev_bar = None
    for bar in reversed(daily_bars):
        ts = str(bar.get("timestamp", ""))[:10]
        if ts == today_str:
            today_bar = bar
            break
    if not today_bar:
        today_bar = daily_bars[-1]
    for bar in reversed(daily_bars):
        ts = str(bar.get("timestamp", ""))[:10]
        if ts != str(today_bar.get("timestamp", ""))[:10]:
            prev_bar = bar
            break
    ctx["day_open"] = int(today_bar.get("open") or 0)
    ctx["day_high"] = int(today_bar.get("high") or current_price)
    ctx["day_low"] = int(today_bar.get("low") or current_price)
    ctx["day_volume"] = int(today_bar.get("volume") or 0)
    ctx["prev_volume"] = int(prev_bar.get("volume") or 0) if prev_bar else 0
    ctx["rsi14"] = compute_legacy_rsi14(daily_bars, current_price=current_price)
    minute_bars = await kiwoom_api.get_stock_chart_data(code, VWAP_BAR_INTERVAL)
    ctx["vwap"] = _compute_vwap(minute_bars, today_str)
    return ctx


def _compute_vwap(minute_bars: List[Dict[str, Any]], today_str: str) -> Optional[float]:
    if not minute_bars:
        return None
    total_pv = 0
    total_v = 0
    for bar in minute_bars:
        ts = str(bar.get("timestamp", ""))[:10]
        if ts != today_str:
            continue
        vol = int(bar.get("volume") or 0)
        price = int(bar.get("close") or 0)
        if vol > 0 and price > 0:
            total_pv += price * vol
            total_v += vol
    if total_v <= 0:
        return None
    return total_pv / total_v


def parse_signal_meta(signal) -> Dict[str, Any]:
    """PendingBuySignal.additional_data JSON 파싱."""
    raw = getattr(signal, "additional_data", None)
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        import json
        return json.loads(raw) if isinstance(raw, str) else {}
    except Exception:
        return {}


def classify_breakout_wait_kind(reason: str) -> Optional[str]:
    """게이트 실패 사유 → WATCHING wait_kind. None이면 관측 대상 아님(즉시 탈락)."""
    r = str(reason or "")
    if "MA20 유예" in r or "유예 대기" in r:
        return "ma20_grace"
    if "진입 확인 대기" in r:
        return "entry_confirm"
    if "HOLD 대기" in r:
        return "hold"
    return None


def is_breakout_watching_reason(reason: str) -> bool:
    return classify_breakout_wait_kind(reason) is not None


# ORDERED 신호가 이 시간을 넘기면 슬롯에서 제외·FAILED 처리
STALE_BUY_ORDERED_MINUTES = 45
# 주문 직후~포지션 생성 전 in-flight ORDERED만 슬롯에 잠깐 포함
IN_FLIGHT_BUY_ORDERED_MINUTES = 15
# WATCHING 장기 방치 정리 (차트 재평가로 만료되지 않은 경우)
STALE_WATCHING_HOURS = 6


def prune_stale_buy_slot_reservations(session: Session) -> int:
    """미체결·만료 매수 신호 정리 — 동시보유 슬롯 누수 방지. WATCHING은 슬롯 외이지만 장기 방치 정리."""
    from core.models import PendingBuySignal, Position

    now = utc_now_naive()
    stale_cutoff = now - timedelta(minutes=STALE_BUY_ORDERED_MINUTES)
    watching_cutoff = now - timedelta(hours=STALE_WATCHING_HOURS)
    holding_codes = {
        (c or "").strip()
        for (c,) in session.query(Position.stock_code).filter(Position.status == "HOLDING").all()
        if c
    }
    n = 0
    open_sigs = session.query(PendingBuySignal).filter(
        PendingBuySignal.status.in_(["PENDING", "PROCESSING", "ORDERED", "WATCHING"]),
    ).all()
    for sig in open_sigs:
        code = (sig.stock_code or "").strip()
        if sig.status == "WATCHING":
            if sig.detected_at and sig.detected_at < watching_cutoff:
                sig.status = "EXPIRED"
                sig.failure_reason = "관측 만료(장기 미해소)"
                n += 1
            continue
        if code in holding_codes:
            if sig.status == "ORDERED":
                sig.status = "FILLED"
                sig.failure_reason = None
                n += 1
            continue
        if sig.status in ("ORDERED", "PROCESSING") and sig.detected_at < stale_cutoff:
            sig.status = "FAILED"
            sig.failure_reason = "슬롯 정리(미체결·만료)"
            n += 1
        elif sig.status == "PENDING" and sig.detected_at < now - timedelta(hours=24):
            sig.status = "EXPIRED"
            sig.failure_reason = "슬롯 정리(만료)"
            n += 1
    if n:
        session.flush()
    return n


def describe_open_position_slots(session: Session) -> Dict[str, int]:
    """슬롯 구성(보유·대기) — 로그/디버그용."""
    from core.models import PendingBuySignal, Position

    holding_codes = {
        (c or "").strip()
        for (c,) in session.query(Position.stock_code).filter(Position.status == "HOLDING").all()
        if c
    }
    in_flight_cutoff = utc_now_naive() - timedelta(minutes=IN_FLIGHT_BUY_ORDERED_MINUTES)
    reserved: set = set()
    for sig in session.query(PendingBuySignal).filter(
        PendingBuySignal.status.in_(["PENDING", "PROCESSING", "ORDERED"]),
    ).all():
        if parse_signal_meta(sig).get("is_add_buy"):
            continue
        code = (sig.stock_code or "").strip()
        if not code or code in holding_codes:
            continue
        if sig.status == "ORDERED" and sig.detected_at < in_flight_cutoff:
            continue
        reserved.add(code)
    return {
        "holdings": len(holding_codes),
        "reserved": len(reserved),
        "total": len(holding_codes) + len(reserved),
    }


def count_open_position_slots(session: Session) -> int:
    """보유(HOLDING) + 신규 매수 대기·진행 슬롯 수."""
    return describe_open_position_slots(session)["total"]


def max_concurrent_positions_limit(settings: AutoTradeSettings) -> int:
    return int(getattr(settings, "max_concurrent_positions", None) or 0)


def is_max_concurrent_positions_reached(
    settings: AutoTradeSettings,
    session: Session,
    *,
    for_new_signal: bool = False,
) -> bool:
    """동시 보유 한도 도달 여부.

    for_new_signal=True  → 스캐너: 슬롯 >= limit 이면 신호 생성 불가
    for_new_signal=False → 실행기: 슬롯 > limit 이면 매수 불가 (정확히 limit 슬롯은 허용)
    """
    limit = max_concurrent_positions_limit(settings)
    if limit <= 0:
        return False
    slots = count_open_position_slots(session)
    if for_new_signal:
        return slots >= limit
    return slots > limit


def _count_strategy_slots(session: Session, strategy_key: str) -> int:
    """전략별 점유 슬롯 수 (HOLDING + 신규매수 예약).

    - 해당 strategy_key HOLDING 종목만 카운트
    - 대기 신호는 종목코드 기준 중복 제거
    - 이미 HOLDING인 종목·추가매수·만료 ORDERED는 제외
    """
    from core.models import PendingBuySignal, Position

    holding_codes = {
        (p.stock_code or "").strip()
        for p in session.query(Position).filter(Position.status == "HOLDING").all()
        if getattr(p, "strategy_key", None) == strategy_key and (p.stock_code or "").strip()
    }
    in_flight_cutoff = utc_now_naive() - timedelta(minutes=IN_FLIGHT_BUY_ORDERED_MINUTES)
    reserved: set = set()
    for sig in session.query(PendingBuySignal).filter(
        PendingBuySignal.status.in_(["PENDING", "PROCESSING", "ORDERED"]),
    ).all():
        meta = parse_signal_meta(sig)
        if meta.get("strategy") != strategy_key:
            continue
        if meta.get("is_add_buy"):
            continue
        code = (sig.stock_code or "").strip()
        if not code or code in holding_codes:
            continue
        if sig.status == "ORDERED" and sig.detected_at and sig.detected_at < in_flight_cutoff:
            continue
        reserved.add(code)
    return len(holding_codes) + len(reserved)


def is_strategy_slot_available(
    settings: AutoTradeSettings,
    session: Session,
    strategy_key: str,
    *,
    for_new_signal: bool = True,
) -> bool:
    """전략별 슬롯 여유 확인. 전략별 제한이 없으면 True 반환.

    for_new_signal=True  → 스캐너: 새 신호 생성 전, count < limit
    for_new_signal=False → 실행기: 현재 신호가 이미 예약에 포함되므로 count <= limit 허용
    """
    if not strategy_key:
        return True
    if strategy_key == "sangtta":
        limit = effective_sangtta_max_slots(settings)
        if limit <= 0:
            return True
        count = _count_strategy_slots(session, strategy_key)
        return count < limit if for_new_signal else count <= limit
    if strategy_key == "breakout":
        limit = effective_breakout_max_slots(settings)
        if limit <= 0:
            return True
        count = _count_strategy_slots(session, strategy_key)
        return count < limit if for_new_signal else count <= limit
    if strategy_key == "ymgp":
        limit = effective_ymgp_max_slots(settings)
        if limit <= 0:
            return True
        count = _count_strategy_slots(session, strategy_key)
        return count < limit if for_new_signal else count <= limit
    if strategy_key == "jongga":
        limit = effective_jongga_max_slots(settings)
        if limit <= 0:
            return True
        count = _count_strategy_slots(session, strategy_key)
        return count < limit if for_new_signal else count <= limit
    return True


def allows_strategy_new_buy(settings: Optional[AutoTradeSettings], strategy_key: Optional[str], now: Optional[datetime] = None) -> Tuple[bool, Optional[str]]:
    """전략별 매수 시간 허용 여부. (허용, 차단사유)."""
    if not settings:
        return False, "자동매매 설정 없음"
    now = as_kst(now)
    off_day = trading_day_block_reason(now)
    if off_day:
        return False, off_day
    if now.weekday() < 5 and getattr(settings, "liquidate_before_close", False):
        # 종가배팅은 동시호가(3차)까지 매수 — 전역 청산시각 게이트 제외
        if strategy_key != "jongga":
            try:
                liq = getattr(settings, "liquidate_time", None) or "15:10"
                lh, lm = map(int, str(liq).split(":"))
                if now.time() >= dt_time(lh, lm):
                    return False, f"장마감 청산 시각({liq}) 이후 — 신규 매수 중단"
            except Exception:
                pass
    if strategy_key == "sangtta":
        try:
            start = getattr(settings, "sangtta_trade_start_time", None) or "09:05"
            end = getattr(settings, "sangtta_trade_end_time", None) or "11:00"
            sh, sm = map(int, str(start).split(":"))
            eh, em = map(int, str(end).split(":"))
            in_window = dt_time(sh, sm) <= now.time() <= dt_time(eh, em)
            if not in_window:
                return False, f"상따 시간대 외 ({start}~{end})"
        except Exception:
            return False, "상따 시간 판정 오류"
        return True, None
    if strategy_key == "breakout":
        try:
            start = getattr(settings, "breakout_trade_start_time", None) or "11:00"
            end = getattr(settings, "breakout_trade_end_time", None) or "14:30"
            sh, sm = map(int, str(start).split(":"))
            eh, em = map(int, str(end).split(":"))
            if not dt_time(sh, sm) <= now.time() <= dt_time(eh, em):
                return False, f"돌파 시간대 외 ({start}~{end})"
        except Exception:
            return False, "돌파 시간 판정 오류"
        return True, None
    if strategy_key == "ymgp":
        try:
            start = getattr(settings, "ymgp_trade_start_time", None) or "09:30"
            end = getattr(settings, "ymgp_trade_end_time", None) or "14:30"
            sh, sm = map(int, str(start).split(":"))
            eh, em = map(int, str(end).split(":"))
            if not dt_time(sh, sm) <= now.time() <= dt_time(eh, em):
                return False, f"역매공파 시간대 외 ({start}~{end})"
        except Exception:
            return False, "역매공파 시간 판정 오류"
        return True, None
    if strategy_key == "jongga":
        try:
            from utils.jongga_engine import jongga_buy_window_end

            start = getattr(settings, "jongga_trade_start_time", None) or "14:30"
            end = jongga_buy_window_end(settings)
            sh, sm = map(int, str(start).split(":"))
            eh, em = map(int, str(end).split(":"))
            from datetime import timedelta
            end_dt = datetime.combine(now.date(), dt_time(eh, em), tzinfo=now.tzinfo)
            grace = end_dt + timedelta(minutes=2)
            if now.time() < dt_time(sh, sm):
                return False, f"종가배팅 시간대 외 ({start}~{end})"
            if now > grace:
                return False, f"종가배팅 시간대 외 ({start}~{end})"
        except Exception:
            return False, "종가배팅 시간 판정 오류"
        return True, None
    # 레거시(또는 미지정) — 전역 레거시 시간창만
    if getattr(Config, "ALLOW_OUT_OF_MARKET_TRADING", False):
        return True, None
    if in_trade_hours(settings, now):
        return True, None
    start = settings.trade_start_time or "10:00"
    end = settings.trade_end_time or "15:20"
    return False, f"레거시 시간대 외 ({start}~{end})"
