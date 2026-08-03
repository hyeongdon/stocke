"""단일 종목 전략 진입·청산 역사 시뮬레이션 — 일봉 기반 MVP.

전략 프로필(legacy / sangtta / breakout)별 게이트·청산 규칙을 적용한다.
조건식 과거 편입 이력은 없으므로 종목은 사용자가 직접 지정한다.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from api.kiwoom_api import KiwoomAPI
from core.models import AutoTradeSettings, Position
from utils.auto_trade_engine import (
    estimate_upper_limit_price,
    evaluate_oversold_breakout_from_ctx,
    evaluate_sangtta_breakout_from_ctx,
    get_auto_trade_settings_sync,
)
from utils.buy_condition_checks import build_buy_condition_checklist, checklist_summary
from utils.datetime_kst import kst_today
from utils.sell_condition_checks import build_sell_condition_checklist, sell_checklist_summary
from utils.technical_mart_store import get_daily_bars_for_code, latest_as_of_date

STRATEGY_LABELS = {
    "legacy": "거래대금 눌림목",
    "sangtta": "상따",
    "breakout": "수급 돌파",
    "ymgp": "역매공파",
    "jongga": "종가배팅",
}

STRATEGY_ALIASES = {
    "legacy": "legacy",
    "legacy_momentum": "legacy",
    "sangtta": "sangtta",
    "sangtta_breakout": "sangtta",
    "breakout": "breakout",
    "oversold_breakout": "breakout",
    "ymgp": "ymgp",
    "yeokmaegongpa": "ymgp",
    "jongga": "jongga",
    "jongga_closing": "jongga",
    "closing_bet": "jongga",
}


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _normalize_strategy(raw: Optional[str]) -> str:
    key = (raw or "legacy").strip().lower()
    return STRATEGY_ALIASES.get(key, "legacy")


def _peak_rate_pct(buy_price: int, peak: int) -> float:
    if not buy_price:
        return 0.0
    return (peak - buy_price) / buy_price * 100.0


def _trailing_floor_price(buy_price: int, trail_start_rate: float) -> int:
    return int(buy_price * (1 + trail_start_rate / 100.0))


def _trailing_floor_for_buy(
    buy_price: int,
    trail_start_rate: float,
    stored_floor: Optional[int],
    peak: int,
) -> int:
    target = _trailing_floor_price(buy_price, trail_start_rate)
    old = int(stored_floor or 0)
    if peak < target:
        return old if old > 0 else target
    return max(old, target) if old > 0 else target


def _resolve_trailing_state(
    *,
    trailing_armed: bool,
    trailing_floor: Optional[int],
    buy_price: int,
    peak: int,
    trail_start_rate: Optional[float],
) -> Tuple[bool, Optional[int]]:
    """시작% 도달 시 armed+floor. 한 번 잠긴 바닥은 고점 하락으로 해제하지 않음."""
    if trail_start_rate is None or trail_start_rate <= 0:
        return True, None

    peak_rate = _peak_rate_pct(buy_price, peak)
    if trailing_armed:
        floor = _trailing_floor_for_buy(
            buy_price, trail_start_rate, trailing_floor, peak,
        )
        return True, floor

    if peak_rate >= trail_start_rate:
        return True, _trailing_floor_price(buy_price, trail_start_rate)

    return False, None


def _build_stop_candidates(
    settings: Dict[str, Any],
    buy_price: int,
    peak: int,
    atr: Optional[float],
    *,
    trailing_armed: bool = False,
    trailing_floor_price: Optional[int] = None,
    strategy_key: str = "legacy",
) -> List[Tuple[str, float, str]]:
    candidates: List[Tuple[str, float, str]] = []
    floor = int(trailing_floor_price) if trailing_floor_price else None
    is_breakout = strategy_key == "breakout"
    is_ymgp = strategy_key == "ymgp"

    def _apply_trail_floor(raw: float) -> float:
        if floor is not None:
            return max(raw, float(floor))
        return raw

    if is_ymgp:
        sl = _num(settings.get("ymgp_stop_loss_pct"))
    elif is_breakout:
        sl = _num(settings.get("breakout_stop_loss_pct"))
    else:
        sl = _num(settings.get("stop_loss_rate"))
    if sl:
        candidates.append(("STOP_LOSS", buy_price * (1 - abs(sl) / 100.0), "PCT"))

    atr_stop_mult = _num(settings.get("atr_mult_stop"))
    if not is_breakout and not is_ymgp and atr and atr_stop_mult:
        candidates.append(("STOP_LOSS", buy_price - atr * atr_stop_mult, "ATR"))

    lock_trigger = _num(settings.get("profit_lock_trigger"))
    if lock_trigger:
        peak_rate = _peak_rate_pct(buy_price, peak)
        if peak_rate >= lock_trigger:
            lock_floor = _num(settings.get("profit_lock_floor"))
            lock_floor = 0.0 if lock_floor is None else lock_floor
            candidates.append(("PROFIT_LOCK", buy_price * (1 + lock_floor / 100.0), "PCT"))

    if trailing_armed:
        if is_ymgp:
            tr = _num(settings.get("ymgp_trailing_pct"))
        elif is_breakout:
            tr = _num(settings.get("breakout_trailing_pct"))
        else:
            tr = _num(settings.get("trailing_stop_pct"))
        if tr:
            raw = peak * (1 - tr / 100.0)
            candidates.append(("TRAILING", _apply_trail_floor(raw), "PCT"))

        atr_trail_mult = _num(settings.get("atr_mult_trail"))
        if not is_breakout and not is_ymgp and atr and atr_trail_mult:
            raw = peak - atr * atr_trail_mult
            candidates.append(("TRAILING", _apply_trail_floor(raw), "ATR"))

    return candidates


def _settings_to_dict(settings: AutoTradeSettings) -> Dict[str, Any]:
    keys = [
        "stop_loss_rate", "take_profit_rate", "trailing_stop_pct",
        "atr_mult_stop", "atr_mult_trail", "atr_period",
        "profit_lock_trigger", "profit_lock_floor",
        "liquidate_before_close", "liquidate_time",
        "use_entry_gate", "require_above_open", "require_above_vwap",
        "day_position_min", "day_position_max", "volume_ratio_min",
        "legacy_rsi_min", "legacy_rsi_max",
        "trade_start_time", "trade_end_time",
        "sangtta_trade_start_time", "sangtta_trade_end_time",
        "sangtta_max_market_cap", "sangtta_change_min", "sangtta_change_max",
        "sangtta_open_rise_min_pct",
        "breakout_trade_start_time", "breakout_trade_end_time",
        "breakout_level_mode", "breakout_n_day", "breakout_vol_mult",
        "breakout_body_pct", "breakout_range_mult", "breakout_require_ma20_cross",
        # MA20 유예: 돌파봉 포함 N봉(기본 3). 시뮬도 라이브와 동일 키 사용
        "breakout_ma20_mode", "breakout_ma20_grace_bars",
        "breakout_entry_hard", "breakout_entry_soft", "breakout_entry_soft_polls",
        "breakout_entry_hold", "breakout_hold_expire_bars",
        "breakout_hold_rsi_min", "breakout_rsi_period",
        "breakout_max_change_pct", "breakout_stop_loss_pct",
        "breakout_trailing_start_pct", "breakout_trailing_pct",
        "struct_break_soft_pct", "struct_break_hard_pct",
        "limit_break_soft_pct", "limit_break_hard_pct",
        "sharp_drop_soft_pct", "sharp_drop_hard_pct",
        "soft_confirm_polls",
        # 역매공파
        "ymgp_trade_start_time", "ymgp_trade_end_time",
        "ymgp_ma_fast", "ymgp_ma_mid", "ymgp_ma_slow",
        "ymgp_box_days", "ymgp_box_width_pct",
        "ymgp_accum_vol_mult", "ymgp_accum_body_pct",
        "ymgp_accum_wick_vol_mult", "ymgp_accum_wick_body_mult",
        "ymgp_ma_near_pct", "ymgp_pivot_tol_pct",
        "ymgp_drop_lookback", "ymgp_drop_pct",
        "ymgp_stop_ma_mode", "ymgp_stop_loss_pct", "ymgp_entry_mode",
        "ymgp_max_change_pct", "ymgp_pullback_tol_pct",
        "ymgp_enable_partial_tp", "ymgp_tp1_pct_of_pos", "ymgp_tp2_pct_of_pos",
        "ymgp_trailing_start_pct", "ymgp_trailing_pct",
    ]
    out: Dict[str, Any] = {}
    for k in keys:
        if hasattr(settings, k):
            out[k] = getattr(settings, k)
    return out


def _parse_date(s: str) -> date:
    return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()


def _atr14_series(bars: List[Dict[str, Any]]) -> List[Optional[float]]:
    """일봉 리스트에 대응하는 ATR14 (인덱스 i = bars[i])."""
    period = 14
    if len(bars) < period + 1:
        return [None] * len(bars)
    highs = [float(b.get("high") or 0) for b in bars]
    lows = [float(b.get("low") or 0) for b in bars]
    closes = [float(b.get("close") or 0) for b in bars]
    trs: List[float] = []
    out: List[Optional[float]] = [None] * len(bars)
    prev_close: Optional[float] = None
    for i, bar in enumerate(bars):
        h, l, c = highs[i], lows[i], closes[i]
        if h <= 0 or l <= 0:
            if c > 0:
                prev_close = c
            continue
        tr = (h - l) if prev_close is None else max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
        if len(trs) >= period:
            out[i] = sum(trs[-period:]) / period
    return out


def _normalize_chart_bars(chart: List[Dict[str, Any]], code: str) -> List[Dict[str, Any]]:
    ordered = sorted(chart, key=lambda x: str(x.get("timestamp") or ""))
    out: List[Dict[str, Any]] = []
    for row in ordered:
        d = str(row.get("timestamp") or "")[:10]
        if len(d) != 10:
            continue
        out.append({
            "stock_code": code,
            "date": d,
            "open": int(row.get("open") or 0),
            "high": int(row.get("high") or 0),
            "low": int(row.get("low") or 0),
            "close": int(row.get("close") or 0),
            "volume": int(row.get("volume") or 0),
        })
    atrs = _atr14_series(out)
    for i, bar in enumerate(out):
        if atrs[i] is not None:
            bar["atr14"] = atrs[i]
    return out


def _bars_needed_to_cover(fetch_start: date, fetch_end: date) -> int:
    """키움 일봉은 최신부터 슬라이스되므로, 진입일까지 포함하려면
    (오늘←fetch_start) 구간을 커버할 만큼 max_bars를 크게 잡는다.
    """
    today = kst_today()
    span_from_start = max(0, (today - fetch_start).days) + 5
    span_window = max(0, (fetch_end - fetch_start).days) + 40
    need = max(int(span_from_start * 0.78) + 40, span_window, 80)
    return min(need, 900)


def _snap_entry_index(dates: List[date], entry_d: date) -> Tuple[Optional[int], date, Optional[str]]:
    """휴장·주말이면 직전(없으면 직후) 거래일로 스냅."""
    exact = next((i for i, d in enumerate(dates) if d == entry_d), None)
    if exact is not None:
        return exact, entry_d, None
    before = [i for i, d in enumerate(dates) if d <= entry_d]
    if before:
        i = before[-1]
        return i, dates[i], f"진입일 {entry_d.isoformat()} 휴장 → {dates[i].isoformat()}로 조정"
    after = [i for i, d in enumerate(dates) if d >= entry_d]
    if after:
        i = after[0]
        return i, dates[i], f"진입일 {entry_d.isoformat()} 데이터 없음 → {dates[i].isoformat()}로 조정"
    return None, entry_d, None


async def _load_daily_bars(
    code: str,
    fetch_start: date,
    fetch_end: date,
    *,
    min_bars: int,
) -> Tuple[List[Dict[str, Any]], str]:
    """DB technical_snapshots 우선, 부족 시 Kiwoom 일봉 API.

    연속 호출 시 슬롯/제한으로 빈 응답이 오면 대기 후 재시도한다.
    """
    import asyncio
    from api.api_rate_limiter import api_rate_limiter

    db_bars = get_daily_bars_for_code(code, start_date=fetch_start, end_date=fetch_end)
    if len(db_bars) >= min_bars:
        return db_bars, "technical_snapshots"

    need = max(min_bars + 30, _bars_needed_to_cover(fetch_start, fetch_end))
    api = KiwoomAPI()
    if not api.token_manager.get_valid_token():
        api.authenticate()

    last_chart = None
    for attempt in range(4):
        chart = await api.get_stock_chart_data(
            code, "1D", max_bars=need, allow_off_hours=True,
        )
        last_chart = chart
        if chart:
            break
        wait_more = max(
            3.5,
            min(api_rate_limiter.seconds_until_available() or 4.0, 25.0),
        )
        logger = __import__("logging").getLogger(__name__)
        logger.warning(
            f"일봉 재시도 {attempt + 1}/4 {code} — {wait_more:.1f}s 대기 "
            f"(limiter={api_rate_limiter.status.value})"
        )
        await asyncio.sleep(wait_more)

    if last_chart:
        api_bars = _normalize_chart_bars(last_chart, code)
        filtered = [
            b for b in api_bars
            if fetch_start <= _parse_date(b["date"]) <= fetch_end
        ]
        if filtered and any(_parse_date(b["date"]) >= fetch_start for b in filtered):
            has_near_entry = any(
                abs((_parse_date(b["date"]) - fetch_start).days) <= 45 for b in filtered
            )
            if has_near_entry and len(filtered) >= min(5, min_bars):
                return filtered, "kiwoom_api"
        if api_bars:
            in_range = [
                b for b in api_bars
                if fetch_start <= _parse_date(b["date"]) <= fetch_end
            ]
            return (in_range or api_bars), "kiwoom_api"

    if db_bars:
        return db_bars, "technical_snapshots"
    return [], "none"


def _exit_fill_price(stop_line: float, bar_open: int, bar_low: int) -> int:
    """갭 하락 시 시가, 아니면 손절선."""
    line = int(round(stop_line))
    if bar_open and bar_open <= line:
        return int(bar_open)
    if bar_low and bar_low <= line:
        return line
    return line


def _reason_label(reason: str) -> str:
    labels = {
        "STOP_LOSS": "손절",
        "TAKE_PROFIT": "익절",
        "TRAILING": "트레일링 스탑",
        "PROFIT_LOCK": "수익 잠금",
        "HOLDING": "미청산",
        "END_OF_PERIOD": "기간 종료 청산",
        "MARKET_CLOSE": "장마감 청산",
        "SANGTTA_LIMIT": "상한가 이탈",
        "SANGTTA_DROP": "급락",
        "BREAKOUT_STRUCTURE": "돌파 구조 이탈",
        "YMGP_STRUCTURE": "역매공파 구조 이탈",
    }
    return labels.get(reason, reason)


def _strategy_time_window(settings: Dict[str, Any], strategy: str) -> Tuple[str, str]:
    if strategy == "sangtta":
        return (
            str(settings.get("sangtta_trade_start_time") or "09:05"),
            str(settings.get("sangtta_trade_end_time") or "11:00"),
        )
    if strategy == "breakout":
        return (
            str(settings.get("breakout_trade_start_time") or "11:00"),
            str(settings.get("breakout_trade_end_time") or "14:30"),
        )
    if strategy == "ymgp":
        return (
            str(settings.get("ymgp_trade_start_time") or "09:30"),
            str(settings.get("ymgp_trade_end_time") or "14:30"),
        )
    return (
        str(settings.get("trade_start_time") or "10:00"),
        str(settings.get("trade_end_time") or "15:20"),
    )


def _breakout_level_from_bars(
    bars: List[Dict[str, Any]],
    entry_idx: int,
    settings: Dict[str, Any],
) -> Tuple[int, str]:
    mode = str(settings.get("breakout_level_mode") or "prev_high")
    prior = bars[:entry_idx]
    if not prior:
        return 0, mode
    if mode == "n_day_high":
        n_day = max(1, int(settings.get("breakout_n_day") or 10))
        window = prior[-n_day:]
        if len(window) < n_day:
            return 0, mode
        return max(int(b.get("high") or 0) for b in window), mode
    return int(prior[-1].get("high") or 0), "prev_high"


def _build_entry_ctx(
    bars: List[Dict[str, Any]],
    entry_idx: int,
    settings: Dict[str, Any],
    strategy: str,
    code: str,
) -> Dict[str, Any]:
    bar = bars[entry_idx]
    prev = bars[entry_idx - 1] if entry_idx > 0 else None
    ctx: Dict[str, Any] = {
        "day_open": int(bar.get("open") or 0),
        "day_high": int(bar.get("high") or 0),
        "day_low": int(bar.get("low") or 0),
        "day_volume": int(bar.get("volume") or 0),
        "prev_close": int(prev.get("close") or 0) if prev else 0,
        "prev_volume": int(prev.get("volume") or 0) if prev else 0,
    }
    if ctx["prev_close"]:
        ctx["upper_limit_price"] = estimate_upper_limit_price(int(ctx["prev_close"]))

    if strategy == "breakout":
        level, kind = _breakout_level_from_bars(bars, entry_idx, settings)
        ctx["level_price"] = level
        ctx["level_kind"] = kind
        ctx["breakout_level_price"] = level

    if strategy == "sangtta":
        try:
            from utils.fundamental_mart_store import get_latest_by_code
            fund = get_latest_by_code(code) or {}
            if fund.get("market_cap") is not None:
                ctx["market_cap"] = fund.get("market_cap")
        except Exception:
            pass

    if strategy == "legacy":
        try:
            from utils.auto_trade_engine import compute_legacy_rsi14
            ctx["daily_bars"] = bars
            ctx["rsi_asof_idx"] = entry_idx
            ctx["rsi14"] = compute_legacy_rsi14(
                bars,
                current_price=int(bar.get("close") or 0) or None,
                asof_idx=entry_idx,
            )
        except Exception:
            pass

    return ctx


def _evaluate_legacy_entry(
    settings: Dict[str, Any],
    price: int,
    ctx: Dict[str, Any],
) -> Tuple[bool, str]:
    if not settings.get("use_entry_gate"):
        return True, "게이트 비활성"
    if not price or price <= 0:
        return False, "현재가 없음"

    day_open = int(ctx.get("day_open") or 0)
    if settings.get("require_above_open") and day_open > 0 and price < day_open:
        return False, f"시가 미만 ({price:,} < {day_open:,})"

    # VWAP: ctx에 있으면 검사(15분봉), 없으면 스킵(일봉)
    if settings.get("require_above_vwap"):
        vwap = ctx.get("vwap")
        if vwap is not None and price < float(vwap):
            return False, f"VWAP 미만 ({price:,} < {float(vwap):,.0f})"

    day_high = int(ctx.get("day_high") or 0)
    day_low = int(ctx.get("day_low") or 0)
    pos_min = _num(settings.get("day_position_min"))
    if pos_min is not None and day_high > day_low:
        position = (price - day_low) / (day_high - day_low)
        if position < float(pos_min):
            return False, f"당일 위치 부족 ({position:.2f} < {pos_min})"

    pos_max = _num(settings.get("day_position_max"))
    if pos_max is not None and day_high > day_low:
        position = (price - day_low) / (day_high - day_low)
        if position > float(pos_max):
            return False, f"당일 위치 과열 ({position:.2f} > {pos_max})"

    vol_ratio_min = _num(settings.get("volume_ratio_min"))
    prev_volume = int(ctx.get("prev_volume") or 0)
    day_volume = int(ctx.get("day_volume") or 0)
    if vol_ratio_min is not None and prev_volume > 0:
        ratio = day_volume / prev_volume * 100
        if ratio < float(vol_ratio_min):
            return False, f"거래량비 부족 ({ratio:.0f}% < {vol_ratio_min}%)"

    rsi_min = _num(settings.get("legacy_rsi_min"))
    rsi_max = _num(settings.get("legacy_rsi_max"))
    if rsi_min is not None or rsi_max is not None:
        rsi = ctx.get("rsi14")
        if rsi is None:
            bars = ctx.get("daily_bars") or []
            asof = ctx.get("rsi_asof_idx")
            if bars:
                from utils.auto_trade_engine import compute_legacy_rsi14
                rsi = compute_legacy_rsi14(
                    bars,
                    current_price=price,
                    asof_idx=asof if asof is not None else len(bars) - 1,
                )
        if rsi is None:
            return False, "RSI(14) 계산 불가"
        rv = float(rsi)
        if rsi_min is not None and rv < float(rsi_min):
            return False, f"RSI 하한 미달 ({rv:.1f} < {float(rsi_min):g})"
        if rsi_max is not None and rv > float(rsi_max):
            return False, f"RSI 과열 ({rv:.1f} > {float(rsi_max):g})"

    return True, "게이트 통과(일봉 근사, VWAP 제외)"


def _evaluate_entry(
    strategy: str,
    settings: Dict[str, Any],
    settings_obj: AutoTradeSettings,
    price: int,
    change_rate: Optional[float],
    ctx: Dict[str, Any],
) -> Tuple[bool, str]:
    if strategy == "sangtta":
        return evaluate_sangtta_breakout_from_ctx(
            settings_obj, price, change_rate, ctx, skip_time_check=True,
        )
    if strategy == "breakout":
        return evaluate_oversold_breakout_from_ctx(
            settings_obj, price, change_rate, ctx, skip_time_check=True,
        )
    if strategy == "ymgp":
        from utils.ymgp_engine import evaluate_ymgp_entry_from_daily
        bars = ctx.get("daily_bars") or []
        asof = ctx.get("ymgp_asof_idx")
        ok, reason, meta = evaluate_ymgp_entry_from_daily(
            bars,
            settings_obj,
            current_price=price,
            change_rate=change_rate,
            asof_idx=asof,
        )
        ctx.update(meta)
        return ok, reason
    return _evaluate_legacy_entry(settings, price, ctx)


def _check_ymgp_structure_exit(
    settings: Dict[str, Any],
    *,
    price: int,
    bar_low: int,
    ref: Optional[Dict[str, Any]],
    mas: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, float, str]]:
    """기준봉 저점·손절 MA 이탈 (일봉/분봉 공통 HARD)."""
    from utils.ymgp_engine import stop_invalidated

    # 저가 기준으로 먼저 HARD 저점 이탈 확인
    probe = min(int(price or 0), int(bar_low or 0)) if bar_low else int(price or 0)
    if probe <= 0:
        return None
    ok, detail = stop_invalidated(
        probe, ref or {}, mas or {}, settings, use_close_vs_ma=True,
    )
    if not ok:
        return None
    return ("STOP_LOSS", float(probe), detail)


def _check_ymgp_take_profit(
    settings: Dict[str, Any],
    *,
    bar_high: int,
    box: Optional[Dict[str, Any]],
    mas: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, float, str]]:
    """시뮬 MVP: T1(박스고점) 도달 시 전량 익절로 단순화."""
    if not settings.get("ymgp_enable_partial_tp", True):
        return None
    from utils.ymgp_engine import take_profit_target

    target, label = take_profit_target(0, box, mas or {})
    if target is None or bar_high <= 0:
        return None
    if bar_high >= float(target):
        return ("TAKE_PROFIT", float(target), f"{label} 도달 (시뮬 전량)")
    return None


def _change_rate(price: int, prev_close: int) -> Optional[float]:
    if not prev_close or prev_close <= 0 or not price:
        return None
    return (price - prev_close) / prev_close * 100.0


def _check_sangtta_hard_exit(
    settings: Dict[str, Any],
    *,
    bar_low: int,
    peak: int,
    prev_close: int,
) -> Optional[Tuple[str, float, str]]:
    """일봉 HARD만 — SOFT 연속 폴링은 미재현."""
    ul = estimate_upper_limit_price(prev_close) if prev_close > 0 else None
    lim_hard = _num(settings.get("limit_break_hard_pct")) or 3.0
    drop_hard = _num(settings.get("sharp_drop_hard_pct")) or 5.0

    if ul and peak >= int(ul * 0.999):
        hard_px = int(ul * (1 - lim_hard / 100.0))
        if bar_low <= hard_px:
            return (
                "STOP_LOSS",
                float(hard_px),
                f"상한가 이탈(HARD): low {bar_low:,} ≤ {hard_px:,} (상한가 {ul:,})",
            )

    if peak > 0:
        hard_px2 = int(peak * (1 - drop_hard / 100.0))
        if bar_low <= hard_px2:
            return (
                "STOP_LOSS",
                float(hard_px2),
                f"급락(HARD): low {bar_low:,} ≤ {hard_px2:,} (고점 {peak:,})",
            )
    return None


def _check_breakout_structure_exit(
    settings: Dict[str, Any],
    *,
    bar_low: int,
    level_price: int,
) -> Optional[Tuple[str, float, str]]:
    if level_price <= 0:
        return None
    hard_pct = _num(settings.get("struct_break_hard_pct")) or 2.0
    hard_line = level_price * (1 - abs(hard_pct) / 100.0)
    if bar_low <= hard_line:
        return (
            "STOP_LOSS",
            float(hard_line),
            f"구조 이탈(HARD): low {bar_low:,} ≤ {int(hard_line):,} (돌파레벨 {level_price:,})",
        )
    return None


@dataclass
class _ReplayState:
    peak: int
    trailing_armed: bool = False
    trailing_floor: Optional[int] = None
    stop_loss_price: Optional[int] = None


def run_stock_exit_replay(
    stock_code: str,
    entry_date: str,
    *,
    strategy: str = "legacy",
    entry_price_mode: str = "close",
    days: int = 120,
    force_exit: bool = True,
    settings_override: Optional[Dict[str, Any]] = None,
    resolution: str = "15m",
) -> Dict[str, Any]:
    """동기 래퍼 — CLI·스크립트용."""
    return asyncio.run(
        run_stock_exit_replay_async(
            stock_code,
            entry_date,
            strategy=strategy,
            entry_price_mode=entry_price_mode,
            days=days,
            force_exit=force_exit,
            settings_override=settings_override,
            resolution=resolution,
        ),
    )


async def run_stock_exit_replay_async(
    stock_code: str,
    entry_date: str,
    *,
    strategy: str = "legacy",
    entry_price_mode: str = "close",
    days: int = 120,
    force_exit: bool = True,
    settings_override: Optional[Dict[str, Any]] = None,
    resolution: str = "15m",
) -> Dict[str, Any]:
    """전략 프로필 기준 단일 종목·진입일 게이트·청산 시뮬레이션.

    resolution: 15m (기본) | 5m (단일 종목 정밀) | 1d (일봉 장기)
    당일 조건식 검증(day-verify)은 15m를 직접 호출하므로 여기 5m와 무관.
    """
    res = (resolution or "15m").strip().lower()
    if res in ("15m", "15", "5m", "5", "intraday", "minute"):
        from utils.stock_exit_replay_15m import run_stock_exit_replay_15m_async
        # 분봉은 보유 일수 상한 7
        hold = min(max(int(days or 5), 1), 7)
        bar_minutes = 5 if res in ("5m", "5") else 15
        return await run_stock_exit_replay_15m_async(
            stock_code,
            entry_date,
            strategy=strategy,
            days=hold,
            force_exit=force_exit,
            settings_override=settings_override,
            bar_minutes=bar_minutes,
        )

    code = KiwoomAPI.normalize_stock_code(stock_code) or str(stock_code).strip().zfill(6)
    if not code or len(code) != 6:
        return {"success": False, "error": "유효하지 않은 종목코드"}

    try:
        entry_d = _parse_date(entry_date)
    except ValueError:
        return {"success": False, "error": "entry_date는 YYYY-MM-DD 형식이어야 합니다."}

    strategy_key = _normalize_strategy(strategy)
    mode = (entry_price_mode or "close").strip().lower()
    if mode not in ("close", "next_open"):
        return {"success": False, "error": "entry_price_mode는 close 또는 next_open"}

    days = max(10, min(int(days or 120), 365))

    db_settings = get_auto_trade_settings_sync()
    if not db_settings and not settings_override:
        return {"success": False, "error": "AutoTradeSettings 없음"}

    settings = _settings_to_dict(db_settings) if db_settings else {}
    if settings_override:
        settings.update(settings_override)

    # settings 객체를 게이트 평가에 재사용 (override 반영)
    class _S:
        pass

    settings_obj = db_settings or _S()
    if settings_override or not db_settings:
        for k, v in settings.items():
            setattr(settings_obj, k, v)

    latest = latest_as_of_date("1D")
    end_d = kst_today()
    # 돌파 N일 고가용 워밍업 · 역매공파는 MA480용 장기 일봉 필요
    warmup = max(40, int(settings.get("breakout_n_day") or 10) + 5)
    if strategy_key == "ymgp":
        warmup = max(warmup, 800)
    fetch_start = entry_d - timedelta(days=warmup)
    fetch_end = min(end_d, entry_d + timedelta(days=days + 10))
    min_bars = max(20, days // 2)
    if strategy_key == "ymgp":
        min_bars = max(min_bars, 120)

    bars, data_source = await _load_daily_bars(
        code, fetch_start, fetch_end, min_bars=min_bars,
    )
    if not bars:
        return {
            "success": False,
            "error": f"일봉 데이터 없음 ({code}) — technical_mart 배치 또는 Kiwoom API 확인",
        }

    from utils.technical_mart_store import get_latest_map_by_codes
    snap = get_latest_map_by_codes([code]).get(code) or {}
    stock_name = str(bars[-1].get("stock_name") or snap.get("stock_name") or code)
    dates = [_parse_date(b["date"]) for b in bars]
    requested_entry = entry_d
    entry_idx, entry_d, snap_note = _snap_entry_index(dates, entry_d)
    if entry_idx is None:
        return {
            "success": False,
            "error": (
                f"진입일 {requested_entry.isoformat()} 일봉 없음 "
                f"(데이터: {dates[0]} ~ {dates[-1]}, 출처: {data_source})"
            ),
        }

    if mode == "close":
        buy_price = int(bars[entry_idx].get("close") or 0)
        if buy_price <= 0:
            return {"success": False, "error": "진입일 종가 없음"}
        gate_idx = entry_idx
        sim_start_idx = entry_idx + 1
        entry_price_label = f"{entry_d.isoformat()} 종가"
        buy_date_str = entry_d.isoformat()
    else:
        if entry_idx + 1 >= len(bars):
            return {"success": False, "error": "다음 거래일 일봉 없음 (next_open)"}
        buy_bar = bars[entry_idx + 1]
        buy_price = int(buy_bar.get("open") or 0)
        if buy_price <= 0:
            return {"success": False, "error": "다음 거래일 시가 없음"}
        gate_idx = entry_idx + 1
        sim_start_idx = entry_idx + 1
        entry_price_label = f"{buy_bar['date']} 시가"
        buy_date_str = str(buy_bar["date"])

    gate_ctx = _build_entry_ctx(bars, gate_idx, settings, strategy_key, code)
    if strategy_key == "ymgp":
        # 진입일 직전 확정 일봉까지로 단계·기준봉 판정
        gate_ctx["daily_bars"] = bars
        gate_ctx["ymgp_asof_idx"] = max(0, gate_idx - 1)
    change_rate = _change_rate(buy_price, int(gate_ctx.get("prev_close") or 0))
    entry_ok, entry_reason = _evaluate_entry(
        strategy_key, settings, settings_obj, buy_price, change_rate, gate_ctx,
    )

    win_start, win_end = _strategy_time_window(settings, strategy_key)
    source_map = {
        "legacy": "screener",
        "sangtta": "sangtta",
        "breakout": "breakout",
        "ymgp": "ymgp",
    }
    buy_meta = {
        "strategy": strategy_key,
        "source": source_map[strategy_key],
        "current_price": buy_price,
        "change_rate": change_rate,
        "level_kind": gate_ctx.get("level_kind"),
        "level_price": gate_ctx.get("level_price"),
        "breakout_level_price": gate_ctx.get("level_price"),
        "volume_ratio": gate_ctx.get("volume_ratio"),
        "gate_pack": {
            "legacy": "legacy_momentum",
            "sangtta": "sangtta_breakout",
            "breakout": "oversold_breakout",
            "ymgp": "yeokmaegongpa",
        }[strategy_key],
        "ymgp_stage": gate_ctx.get("ymgp_stage"),
        "ymgp_ref": gate_ctx.get("ymgp_ref"),
    }
    # breakout 게이트가 volume_ratio를 ctx에 채워 줌
    if strategy_key == "breakout" and gate_ctx.get("volume_ratio") is None:
        pv = int(gate_ctx.get("prev_volume") or 0)
        dv = int(gate_ctx.get("day_volume") or 0)
        if pv > 0:
            buy_meta["volume_ratio"] = dv / pv

    buy_checks = build_buy_condition_checklist(
        settings,
        meta=buy_meta,
        price=buy_price,
        change_rate=change_rate,
        fill_amount=buy_price if entry_ok else None,
        gate_ctx=gate_ctx,
    )
    # 유니버스(조건식)는 미재현 — 명시
    for item in buy_checks:
        if item.get("key") in ("candidate_source", "breakout_universe"):
            item["passed"] = None
            item["note"] = "조건식 이력 없음 — 종목 직접 지정(MVP)"
            item["actual"] = "직접 지정"

    buy_checks.insert(0, {
        "group": "진입 판정",
        "key": "entry_gate_pack",
        "label": f"{STRATEGY_LABELS[strategy_key]} 게이트",
        "enabled": True,
        "passed": entry_ok,
        "actual": entry_reason,
        "required": "일봉 근사 AND 통과",
        "note": "시간대·조건식 편입은 스킵",
    })

    sim_end_idx = min(len(bars) - 1, sim_start_idx + days - 1)
    if sim_start_idx >= len(bars):
        return {
            "success": False,
            "error": (
                f"진입일({entry_d.isoformat()}) 이후 일봉이 없습니다. "
                "15분봉 모드를 사용하거나, 더 이른 진입일을 선택하세요."
            ),
        }

    entry_atr = _num(bars[gate_idx].get("atr14"))
    is_breakout = strategy_key == "breakout"
    is_ymgp = strategy_key == "ymgp"
    if is_ymgp:
        trail_start = _num(settings.get("ymgp_trailing_start_pct"))
    elif is_breakout:
        trail_start = _num(settings.get("breakout_trailing_start_pct"))
    else:
        trail_start = _num(settings.get("take_profit_rate"))
    trail_start_val = trail_start if trail_start and trail_start > 0 else None
    level_price = int(gate_ctx.get("level_price") or 0)
    level_kind = gate_ctx.get("level_kind")
    ymgp_ref = gate_ctx.get("ymgp_ref") or {}
    ymgp_box = gate_ctx.get("ymgp_box")

    # 게이트용 prev_close (상따 상한가) — 진입일 전일
    entry_prev_close = int(gate_ctx.get("prev_close") or 0)

    state = _ReplayState(peak=buy_price)
    timeline: List[Dict[str, Any]] = []
    exit_event: Optional[Dict[str, Any]] = None
    exit_steps: List[Dict[str, Any]] = []
    reason_detail: Optional[str] = None

    assumptions = [
        f"전략: {STRATEGY_LABELS[strategy_key]} ({strategy_key})",
        f"일봉 출처: {data_source}",
        "종목 직접 지정 — 키움 조건식 과거 편입 이력 미재현",
        f"매수 시간대 가정: {win_start}~{win_end} (일봉이라 시각 미확정)",
        "일봉 OHLC — 장중 터치는 당일 low/high로 판정",
        "동일 봉에서 여러 규칙 후보 충돌 시 통합 손절선(최고가) 1개만 적용",
        "갭 하락 시 청산가 = min(손절선, 시가)",
        "SOFT 연속 확인(폴링)은 일봉 MVP에서 HARD만 적용",
        "장마감 전량청산(MARKET_CLOSE)은 일봉 MVP에서 제외",
    ]
    if strategy_key == "legacy" and settings.get("require_above_vwap"):
        assumptions.append("레거시 VWAP 조건은 분봉 필요 → 일봉 MVP에서 스킵")
    if is_breakout:
        assumptions.append("수급 돌파: 오버나잇 허용 · ATR 손절/트레일 미적용")
    if is_ymgp:
        assumptions.append(
            "역매공파: 진입일 직전 일봉으로 ARMED·기준봉 판정 후 당일 고점 돌파 근사(종가/시가)"
        )
        assumptions.append("역매공파: 오버나잇 허용 · T1 박스고점은 시뮬에서 전량 익절로 단순화")
    if mode == "close":
        assumptions.append("진입 당일(close 모드)은 다음 거래일부터 청산 판정")
    if snap_note:
        assumptions.insert(1, snap_note)

    for i in range(sim_start_idx, sim_end_idx + 1):
        bar = bars[i]
        bar_date = bar["date"]
        o = int(bar.get("open") or 0)
        h = int(bar.get("high") or 0)
        l = int(bar.get("low") or 0)
        c = int(bar.get("close") or 0)
        if h <= 0 or l <= 0:
            continue

        state.peak = max(state.peak, h)
        armed, floor = _resolve_trailing_state(
            trailing_armed=state.trailing_armed,
            trailing_floor=state.trailing_floor,
            buy_price=buy_price,
            peak=state.peak,
            trail_start_rate=trail_start_val,
        )

        if armed and floor:
            if not state.trailing_armed or (floor and int(floor) > int(state.trailing_floor or 0)):
                state.trailing_armed = True
                state.trailing_floor = int(floor)

        # 전략 전용 HARD 청산 (고정손절보다 우선)
        special: Optional[Tuple[str, float, str]] = None
        if strategy_key == "sangtta":
            # 진입일 전일 종가 기준 상한가 유지 (실전과 동일 근사)
            special = _check_sangtta_hard_exit(
                settings, bar_low=l, peak=state.peak, prev_close=entry_prev_close,
            )
        elif strategy_key == "breakout":
            special = _check_breakout_structure_exit(
                settings, bar_low=l, level_price=level_price,
            )
        elif strategy_key == "ymgp":
            from utils.ymgp_engine import compute_mas, bars_for_ymgp_eval
            mas_i = compute_mas(bars_for_ymgp_eval(bars[: i + 1]), settings_obj)
            special = _check_ymgp_structure_exit(
                settings, price=c, bar_low=l, ref=ymgp_ref, mas=mas_i,
            )
            if special is None:
                special = _check_ymgp_take_profit(
                    settings, bar_high=h, box=ymgp_box, mas=mas_i,
                )

        atr = _num(bar.get("atr14")) or entry_atr
        candidates = _build_stop_candidates(
            settings,
            buy_price,
            state.peak,
            atr,
            trailing_armed=state.trailing_armed,
            trailing_floor_price=state.trailing_floor,
            strategy_key=strategy_key,
        )

        eff_stop: Optional[float] = None
        eff_reason: Optional[str] = None
        eff_kind = ""
        if candidates:
            eff_reason, eff_stop, eff_kind = max(candidates, key=lambda x: x[1])
            state.stop_loss_price = int(eff_stop)

        peak_rate = _peak_rate_pct(buy_price, state.peak)
        pl_rate = _peak_rate_pct(buy_price, c) if c else 0.0

        day_row: Dict[str, Any] = {
            "date": bar_date,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "peak": state.peak,
            "peak_rate_pct": round(peak_rate, 2),
            "trailing_armed": state.trailing_armed,
            "trailing_floor": state.trailing_floor,
            "effective_stop": int(eff_stop) if eff_stop is not None else None,
            "effective_stop_reason": eff_reason,
            "unrealized_pct": round(pl_rate, 2),
        }
        timeline.append(day_row)

        if special is not None:
            sp_reason, sp_line, sp_detail = special
            sell_px = _exit_fill_price(sp_line, o, l)
            pl_pct = (sell_px - buy_price) / buy_price * 100.0
            reason_detail = sp_detail
            exit_label_key = "SANGTTA_DROP" if "급락" in sp_detail else (
                "SANGTTA_LIMIT" if "상한가" in sp_detail else (
                    "TAKE_PROFIT" if sp_reason == "TAKE_PROFIT" else (
                        "YMGP_STRUCTURE" if strategy_key == "ymgp" else "BREAKOUT_STRUCTURE"
                    )
                )
            )
            exit_steps.append({
                "rule": sp_detail,
                "price": int(sp_line),
                "note": f"{bar_date} {sp_detail}",
            })
            exit_event = {
                "date": bar_date,
                "reason": sp_reason,
                "reason_label": _reason_label(exit_label_key),
                "price": sell_px,
                "profit_loss_rate_pct": round(pl_pct, 2),
                "bar_low": l,
                "stop_line": int(sp_line),
                "detail": sp_detail,
            }
            break

        if eff_stop is not None and l <= eff_stop:
            sell_px = _exit_fill_price(eff_stop, o, l)
            pl_pct = (sell_px - buy_price) / buy_price * 100.0
            rule_note = f"{eff_reason} ({eff_kind})"
            exit_steps.append({
                "rule": rule_note,
                "price": int(eff_stop),
                "note": f"{bar_date} low {l:,} ≤ 선 {int(eff_stop):,}",
            })
            exit_event = {
                "date": bar_date,
                "reason": eff_reason,
                "reason_label": _reason_label(eff_reason or ""),
                "price": sell_px,
                "profit_loss_rate_pct": round(pl_pct, 2),
                "bar_low": l,
                "stop_line": int(eff_stop),
            }
            break

    if exit_event is None and force_exit and timeline:
        last = timeline[-1]
        sell_px = int(last["close"] or buy_price)
        pl_pct = (sell_px - buy_price) / buy_price * 100.0
        exit_event = {
            "date": last["date"],
            "reason": "END_OF_PERIOD",
            "reason_label": _reason_label("END_OF_PERIOD"),
            "price": sell_px,
            "profit_loss_rate_pct": round(pl_pct, 2),
            "bar_low": last.get("low"),
            "stop_line": last.get("effective_stop"),
        }

    closed = exit_event is not None
    sell_reason = (exit_event or {}).get("reason") or "HOLDING"
    sell_price = (exit_event or {}).get("price")
    sell_pl_rate = (exit_event or {}).get("profit_loss_rate_pct")

    # 포지션에 전략별 손절·익절 스냅샷
    pos_sl = float(
        (
            settings.get("ymgp_stop_loss_pct") if is_ymgp
            else settings.get("breakout_stop_loss_pct") if is_breakout
            else settings.get("stop_loss_rate")
        )
        or 0
    )
    pos_tp = float(
        (
            settings.get("ymgp_trailing_start_pct") if is_ymgp
            else settings.get("breakout_trailing_start_pct") if is_breakout
            else settings.get("take_profit_rate")
        )
        or 0
    )

    pos = Position(
        stock_code=code,
        stock_name=stock_name,
        buy_price=buy_price,
        buy_quantity=1,
        buy_amount=buy_price,
        stop_loss_rate=pos_sl,
        take_profit_rate=pos_tp,
        status=sell_reason if closed else "HOLDING",
        peak_price=state.peak,
        trailing_armed=state.trailing_armed,
        trailing_floor_price=state.trailing_floor,
        stop_loss_price=state.stop_loss_price,
        buy_atr=entry_atr,
        buy_atr_period=int(settings.get("atr_period") or 14),
        current_profit_loss=int(sell_price - buy_price) if sell_price else None,
        current_profit_loss_rate=sell_pl_rate,
        sell_time=datetime.utcnow() if closed else None,
        strategy_key=strategy_key,
        breakout_level_kind=level_kind if (is_breakout or is_ymgp) else None,
        breakout_level_price=level_price if (is_breakout or is_ymgp) else None,
    )

    checks = build_sell_condition_checklist(
        settings,
        pos,
        buy_price=buy_price,
        qty=1,
        sell_price=int(sell_price) if sell_price else None,
        trigger_reason=sell_reason if closed else None,
        exit_steps=exit_steps,
        has_sell_order=False,
        reason_detail=reason_detail or (exit_event or {}).get("detail"),
        strategy_key=strategy_key,
    )
    for item in checks:
        if item.get("key") == "sell_order_db":
            item["note"] = "시뮬레이션 — 실제 주문 없음"

    chart_points = [
        {
            "date": buy_date_str,
            "close": buy_price,
            "kind": "buy",
        },
    ]
    for row in timeline:
        chart_points.append({
            "date": row["date"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "effective_stop": row.get("effective_stop"),
            "kind": "bar",
        })
    if exit_event and exit_event.get("date"):
        chart_points.append({
            "date": exit_event["date"],
            "close": exit_event.get("price"),
            "kind": "sell",
            "reason": exit_event.get("reason"),
        })

    return {
        "success": True,
        "resolution": "1d",
        "stock_code": code,
        "stock_name": stock_name,
        "strategy": {
            "key": strategy_key,
            "label": STRATEGY_LABELS[strategy_key],
            "gate_pack": buy_meta["gate_pack"],
            "time_window": f"{win_start}~{win_end}",
        },
        "entry": {
            "date": buy_date_str,
            "requested_date": requested_entry.isoformat(),
            "snapped": bool(snap_note),
            "snap_note": snap_note,
            "price_mode": mode,
            "price_label": entry_price_label,
            "price": buy_price,
            "passed": entry_ok,
            "reason": entry_reason,
            "time_approx": f"{buy_date_str} {win_start}~{win_end}",
            "time_note": "일봉 MVP — 장중 체결 시각은 확정 불가, 전략 시간대로 표기",
            "change_rate": round(change_rate, 2) if change_rate is not None else None,
            "level_price": level_price if (is_breakout or is_ymgp) else None,
            "level_kind": level_kind if (is_breakout or is_ymgp) else None,
            "ymgp_stage": gate_ctx.get("ymgp_stage"),
            "ymgp_ref": gate_ctx.get("ymgp_ref"),
        },
        "simulation": {
            "days_requested": days,
            "bars_simulated": len(timeline),
            "start_date": timeline[0]["date"] if timeline else None,
            "end_date": timeline[-1]["date"] if timeline else None,
            "data_through": bars[-1]["date"],
            "data_source": data_source,
            "mart_latest_date": latest.isoformat() if latest else None,
            "assumptions": assumptions,
            "resolution": "1d",
        },
        "exit": exit_event,
        "holding": exit_event is None,
        "summary": {
            "buy_price": buy_price,
            "sell_price": sell_price,
            "profit_loss_rate_pct": sell_pl_rate,
            "reason": sell_reason,
            "reason_label": (exit_event or {}).get("reason_label") or _reason_label(sell_reason),
            "peak_price": state.peak,
            "peak_rate_pct": round(_peak_rate_pct(buy_price, state.peak), 2),
            "closed": closed and sell_reason not in ("HOLDING",),
            "entry_passed": entry_ok,
        },
        "settings_used": settings,
        "timeline": timeline,
        "chart_points": chart_points,
        "intraday_chart": None,
        "buy_condition_checks": buy_checks,
        "buy_condition_summary": checklist_summary(buy_checks),
        "sell_condition_checks": checks,
        "sell_condition_summary": sell_checklist_summary(checks),
    }
