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


def _breakout_entry_flags(settings: AutoTradeSettings) -> Tuple[bool, bool, int]:
    hard_raw = getattr(settings, "breakout_entry_hard", None)
    soft_raw = getattr(settings, "breakout_entry_soft", None)
    use_hard = True if hard_raw is None else bool(hard_raw)
    use_soft = True if soft_raw is None else bool(soft_raw)
    polls = int(
        getattr(settings, "breakout_entry_soft_polls", None)
        or getattr(settings, "soft_confirm_polls", None)
        or 2
    )
    return use_hard, use_soft, max(1, polls)

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


def effective_sangtta_buy_amount(settings: Optional[AutoTradeSettings]) -> int:
    """상따 1회 매수 금액. 미설정/0이면 기본 소액."""
    if not settings:
        return DEFAULT_SANGTTA_BUY_AMOUNT
    try:
        amt = int(getattr(settings, "sangtta_buy_amount", 0) or 0)
    except (TypeError, ValueError):
        amt = 0
    return amt if amt > 0 else DEFAULT_SANGTTA_BUY_AMOUNT


def effective_sangtta_max_slots(settings: Optional[AutoTradeSettings]) -> int:
    if not settings:
        return DEFAULT_SANGTTA_MAX_SLOTS
    try:
        n = int(getattr(settings, "sangtta_max_slots", 0) or 0)
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else DEFAULT_SANGTTA_MAX_SLOTS


def effective_breakout_buy_amount(settings: Optional[AutoTradeSettings]) -> int:
    try:
        amount = int(getattr(settings, "breakout_buy_amount", 0) or 0)
    except (TypeError, ValueError):
        amount = 0
    return amount if amount > 0 else DEFAULT_BREAKOUT_BUY_AMOUNT


def effective_breakout_max_slots(settings: Optional[AutoTradeSettings]) -> int:
    try:
        slots = int(getattr(settings, "breakout_max_slots", 0) or 0)
    except (TypeError, ValueError):
        slots = 0
    return slots if slots > 0 else DEFAULT_BREAKOUT_MAX_SLOTS


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
) -> int:
    if is_add_buy:
        return int(settings.add_buy_amount or settings.initial_min_amount or settings.max_invest_amount or 0)

    method = (settings.sizing_method or "FIXED").upper()
    imin = int(settings.initial_min_amount or settings.max_invest_amount or 0)
    imax = int(settings.initial_max_amount or settings.max_invest_amount or imin)

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


def disable_auto_trade(reason: str) -> None:
    for db in get_db():
        session: Session = db
        settings = session.query(AutoTradeSettings).first()
        if settings and settings.is_enabled:
            settings.is_enabled = False
            settings.updated_at = utc_now_naive()
            session.commit()
            logger.warning(f"🛑 [AUTO_TRADE] 자동매매 OFF — {reason}")
        break


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

    진입 확인 (둘 다 켜면 OR):
    - HARD: 직전 완성 5분봉 종가 > 돌파 레벨 → 즉시
    - SOFT: 현재가 > 레벨이 연속 N스캔 → 통과
    - 둘 다 끄면: 레벨 위 터치(기존) 동작
    """
    if ctx is None:
        ctx = {}
    if not current_price or current_price <= 0:
        return False, "현재가 없음"
    if not skip_time_check:
        allowed, reason = allows_strategy_new_buy(settings, "breakout", now)
        if not allowed:
            return False, reason or "돌파 시간대 외"

    level_price = int(ctx.get("level_price") or 0)
    level_kind = str(
        ctx.get("level_kind")
        or getattr(settings, "breakout_level_mode", "prev_high")
        or "prev_high"
    )
    if level_kind not in ("prev_high", "n_day_high", "prev_bar_high", "n_bar_high"):
        return False, f"지원하지 않는 돌파 레벨 ({level_kind})"
    if level_price <= 0:
        return False, "돌파 레벨 계산 불가"
    if current_price <= level_price:
        proximity = (current_price / level_price - 1) * 100
        return False, f"돌파 전 ({current_price:,} ≤ {level_price:,}, {proximity:+.2f}%)"

    use_hard, use_soft, soft_polls = _breakout_entry_flags(settings)
    confirm_close = int(ctx.get("confirm_close") or 0)
    soft_streak = int(ctx.get("entry_soft_streak") or 0)
    hard_ok = confirm_close > level_price
    soft_ok = soft_streak >= soft_polls
    ctx["confirm_close"] = confirm_close
    ctx["entry_soft_streak"] = soft_streak
    ctx["entry_soft_polls"] = soft_polls
    ctx["entry_hard_ok"] = hard_ok
    ctx["entry_soft_ok"] = soft_ok
    ctx["entry_hard_enabled"] = use_hard
    ctx["entry_soft_enabled"] = use_soft

    max_change = float(getattr(settings, "breakout_max_change_pct", 12.0) or 12.0)
    if change_rate is not None and float(change_rate) >= max_change:
        return False, f"과열 컷 ({float(change_rate):.2f}% ≥ {max_change:g}%)"

    day_volume = int(ctx.get("day_volume") or 0)
    prev_volume = int(ctx.get("prev_volume") or 0)
    vol_mult = float(getattr(settings, "breakout_vol_mult", 1.5) or 1.5)
    if prev_volume <= 0:
        return False, "비교 거래량 없음(분봉)"
    volume_ratio = day_volume / prev_volume
    ctx["volume_ratio"] = volume_ratio
    if volume_ratio < vol_mult:
        return False, f"거래량 부족 ({volume_ratio:.2f}배 < {vol_mult:g}배)"

    # 진입 확인 HARD / SOFT
    if use_hard or use_soft:
        passed_modes: List[str] = []
        if use_hard and hard_ok:
            passed_modes.append("HARD")
        if use_soft and soft_ok:
            passed_modes.append(f"SOFT {soft_streak}/{soft_polls}")
        if not passed_modes:
            wait_parts: List[str] = []
            if use_hard:
                if confirm_close > 0:
                    wait_parts.append(
                        f"HARD 미충족(직전종가 {confirm_close:,} ≤ {level_price:,})"
                    )
                else:
                    wait_parts.append("HARD 미충족(직전종가 없음)")
            if use_soft:
                wait_parts.append(f"SOFT {soft_streak}/{soft_polls}")
            return False, f"진입 확인 대기 ({', '.join(wait_parts)})"
        mode_label = "+".join(passed_modes)
        ctx["entry_confirm_mode"] = mode_label
        return True, (
            f"과매도 돌파 통과 ({mode_label} · {level_kind} {level_price:,}, "
            f"거래량 {volume_ratio:.2f}배)"
        )

    ctx["entry_confirm_mode"] = "TOUCH"
    return True, f"과매도 돌파 통과 (TOUCH · {level_kind} {level_price:,}, 거래량 {volume_ratio:.2f}배)"


def resolve_breakout_level_from_minute_bars(
    bars: List[Dict[str, Any]],
    settings: AutoTradeSettings,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """5분봉 기준 돌파 레벨·거래량 컨텍스트.

    - prev_high / prev_bar_high: 직전 완성봉 고가
    - n_day_high / n_bar_high: 직전 N봉(기본 breakout_n_day) 고가 최대
    - 거래량: 현재(최신)봉 거래량 ÷ 직전 N봉 평균 거래량
    """
    if not bars:
        return None, "5분봉 데이터 없음(돌파)"
    rows = sorted(bars, key=lambda row: str(row.get("timestamp", "")))
    if len(rows) < 2:
        return None, "5분봉 부족(돌파)"

    current = rows[-1]
    prior = rows[:-1]
    mode = str(getattr(settings, "breakout_level_mode", "prev_high") or "prev_high")
    n_bar = max(1, int(getattr(settings, "breakout_n_day", 10) or 10))

    if mode in ("n_day_high", "n_bar_high"):
        mode = "n_day_high"
        window = prior[-n_bar:]
        if len(window) < n_bar:
            return None, f"N봉 고가 데이터 부족 ({len(window)}/{n_bar})"
        level = max(int(row.get("high") or 0) for row in window)
    else:
        mode = "prev_high"
        level = int(prior[-1].get("high") or 0)

    if level <= 0:
        return None, "돌파 레벨 계산 불가"

    confirm_close = int(prior[-1].get("close") or 0)

    vol_window = prior[-n_bar:] if prior else []
    avg_prev = 0.0
    if vol_window:
        vols = [int(row.get("volume") or 0) for row in vol_window]
        positive = [v for v in vols if v > 0]
        if positive:
            avg_prev = sum(positive) / len(positive)

    return {
        "level_kind": mode,
        "level_price": level,
        "confirm_close": confirm_close,
        "day_volume": int(current.get("volume") or 0),
        "prev_volume": int(round(avg_prev)) if avg_prev > 0 else 0,
        "bar_interval": BREAKOUT_BAR_INTERVAL,
        "n_bar": n_bar,
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

    today_bar, prev_bar, err = await _load_daily_gate_bars(kiwoom_api, stock_code)
    if err:
        return False, err

    code = getattr(kiwoom_api, "normalize_stock_code", lambda c: c)(stock_code)
    today_str = kst_date_str()
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
    if not merged.get("level_price"):
        code = getattr(kiwoom_api, "normalize_stock_code", lambda c: c)(stock_code)
        bars = await kiwoom_api.get_stock_chart_data(code, BREAKOUT_BAR_INTERVAL)
        if not bars:
            if not api_rate_limiter.is_api_available():
                return False, "API 호출 제한(5분봉)"
            return False, "5분봉 데이터 없음(돌파)"
        resolved, err = resolve_breakout_level_from_minute_bars(bars, settings)
        if err:
            return False, err
        merged.update(resolved or {})

    level_price = int(merged.get("level_price") or 0)
    above_level = bool(current_price and level_price and current_price > level_price)
    _, use_soft, soft_polls = _breakout_entry_flags(settings)
    prev_streak = get_breakout_entry_soft_streak(stock_code)
    if update_soft_streak:
        streak = update_breakout_entry_soft_streak(stock_code, above_level)
        if use_soft and (streak != prev_streak):
            if above_level:
                logger.info(
                    f"📈 [돌파] 진입확인 SOFT {stock_code}: {streak}/{soft_polls} "
                    f"(가격={current_price:,} > 레벨={level_price:,}, "
                    f"직전종가={int(merged.get('confirm_close') or 0):,})"
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
    return await _eval_legacy_momentum(kiwoom_api, settings, stock_code, current_price)


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


# ORDERED 신호가 이 시간을 넘기면 슬롯에서 제외·FAILED 처리
STALE_BUY_ORDERED_MINUTES = 45
# 주문 직후~포지션 생성 전 in-flight ORDERED만 슬롯에 잠깐 포함
IN_FLIGHT_BUY_ORDERED_MINUTES = 15


def prune_stale_buy_slot_reservations(session: Session) -> int:
    """미체결·만료 매수 신호 정리 — 동시보유 슬롯 누수 방지."""
    from core.models import PendingBuySignal, Position

    now = utc_now_naive()
    stale_cutoff = now - timedelta(minutes=STALE_BUY_ORDERED_MINUTES)
    holding_codes = {
        (c or "").strip()
        for (c,) in session.query(Position.stock_code).filter(Position.status == "HOLDING").all()
        if c
    }
    n = 0
    open_sigs = session.query(PendingBuySignal).filter(
        PendingBuySignal.status.in_(["PENDING", "PROCESSING", "ORDERED"]),
    ).all()
    for sig in open_sigs:
        code = (sig.stock_code or "").strip()
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
    # 레거시(또는 미지정) — 전역 레거시 시간창만
    if getattr(Config, "ALLOW_OUT_OF_MARKET_TRADING", False):
        return True, None
    if in_trade_hours(settings, now):
        return True, None
    start = settings.trade_start_time or "10:00"
    end = settings.trade_end_time or "15:20"
    return False, f"레거시 시간대 외 ({start}~{end})"
