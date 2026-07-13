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


def effective_min_change_rate(settings: AutoTradeSettings) -> Optional[float]:
    if settings.min_change_rate_buy is not None:
        return float(settings.min_change_rate_buy)
    if settings.signal_min_threshold is not None:
        return float(settings.signal_min_threshold)
    return None


def has_buy_conditions(settings: AutoTradeSettings) -> bool:
    return bool(settings.buy_below_price) or effective_min_change_rate(settings) is not None


def in_trade_hours(settings: AutoTradeSettings, now: Optional[datetime] = None) -> bool:
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
    """신규 매수가 막힌 이유. None이면 매수 허용."""
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

    if not in_trade_hours(settings, now):
        if getattr(Config, "ALLOW_OUT_OF_MARKET_TRADING", False):
            return None
        start = settings.trade_start_time or "10:00"
        end = settings.trade_end_time or "15:20"
        return f"매매 시간 외 ({start}~{end})"
    return None


def allows_new_buy(settings: Optional[AutoTradeSettings], now: Optional[datetime] = None) -> bool:
    """신규 매수 허용 — 매매 시간 내이며 장마감 청산 시각 이전."""
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


async def check_entry_gate(
    kiwoom_api,
    settings: AutoTradeSettings,
    stock_code: str,
    current_price: int,
) -> Tuple[bool, str]:
    """진입 타이밍 게이트. use_entry_gate=False면 항상 통과."""
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

    return True, "게이트 통과"


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
