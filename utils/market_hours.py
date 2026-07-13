"""KRX 정규장·거래일 판별 — 현재가 조회·자동매매 허용 구간 (KST)."""
from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta
from typing import Optional, Tuple

from utils.datetime_kst import KST, as_kst
from utils.krx_holiday_store import holiday_dates_for_year, holiday_label, is_holiday


def krx_holidays_for_year(year: int):
    return holiday_dates_for_year(year)


def is_krx_holiday(day: date) -> bool:
    return is_holiday(day)


def is_krx_trading_day(now: Optional[datetime] = None) -> bool:
    """평일이면서 KRX 휴장일이 아닌 날."""
    kst = as_kst(now)
    if kst.weekday() >= 5:
        return False
    return not is_krx_holiday(kst.date())


def trading_day_block_reason(now: Optional[datetime] = None) -> Optional[str]:
    """거래 불가일이면 사유 문자열, 거래일이면 None."""
    kst = as_kst(now)
    if kst.weekday() >= 5:
        return "주말(토·일) - 거래소 휴장"
    if is_krx_holiday(kst.date()):
        label = holiday_label(kst.date())
        if label:
            return f"거래소 휴장일({kst.strftime('%Y-%m-%d')} {label})"
        return f"거래소 휴장일({kst.strftime('%Y-%m-%d')})"
    return None


def is_krx_session(now: Optional[datetime] = None) -> bool:
    """평일 거래일 09:00~15:30 KST — 현재가·일봉 차트·ATR 실시간 조회 허용 구간."""
    kst = as_kst(now)
    if not is_krx_trading_day(kst):
        return False
    return dt_time(9, 0) <= kst.time() <= dt_time(15, 30)


def _parse_hm(value: Optional[str], default: Tuple[int, int]) -> dt_time:
    try:
        h, m = map(int, (value or f"{default[0]:02d}:{default[1]:02d}").split(":"))
        return dt_time(h, m)
    except Exception:
        return dt_time(*default)


def linked_trading_session_bounds(settings, now: Optional[datetime] = None) -> Tuple[dt_time, dt_time]:
    """스캐너·매수·손절 모니터 공통 구간 (trade_start ~ trade_end, 장마감청산 시 연장)."""
    start = _parse_hm(getattr(settings, "trade_start_time", None), (10, 0))
    end = _parse_hm(getattr(settings, "trade_end_time", None), (15, 20))
    if getattr(settings, "liquidate_before_close", False):
        liq = _parse_hm(getattr(settings, "liquidate_time", None), (15, 10))
        if liq > end:
            end = liq
    return start, end


def linked_trading_session_window_str(settings, now: Optional[datetime] = None) -> str:
    start, end = linked_trading_session_bounds(settings, now)
    return f"{start.strftime('%H:%M')}~{end.strftime('%H:%M')}"


def in_linked_trading_session(settings, now: Optional[datetime] = None) -> bool:
    """종목 스캔·매수 실행기·손절/익절 모니터 — 동일 매매 시간 연동."""
    if settings is None:
        return False
    kst = as_kst(now)
    if not is_krx_trading_day(kst):
        return False
    start, end = linked_trading_session_bounds(settings, now)
    return start <= kst.time() <= end


def linked_trading_session_block_reason(
    settings,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """연동 세션 외 사유. None이면 가동 가능."""
    off = trading_day_block_reason(now)
    if off:
        return off
    if settings is None:
        return "자동매매 설정 없음"
    if in_linked_trading_session(settings, now):
        return None
    window = linked_trading_session_window_str(settings, now)
    return f"매매 시간 외 ({window})"


def in_auto_trade_engine_session(settings, now: Optional[datetime] = None) -> bool:
    """거래일 trade_start ~ trade_end — 스캐너·매수기 가동 구간."""
    return in_linked_trading_session(settings, now)


def auto_trade_engine_block_reason(settings, now: Optional[datetime] = None) -> Optional[str]:
    """스캐너·매수 실행기가 멈춰야 할 사유. None이면 가동 가능."""
    return linked_trading_session_block_reason(settings, now)


def is_stop_loss_monitoring_session(
    settings=None,
    now: Optional[datetime] = None,
) -> bool:
    """손절/익절 모니터 — 스캐너·매수와 동일 매매 시간."""
    if settings is None:
        from core.models import AutoTradeSettings, get_db

        for db in get_db():
            settings = db.query(AutoTradeSettings).first()
            break
    return in_linked_trading_session(settings, now)


def seconds_until_stop_loss_monitoring(
    settings=None,
    now: Optional[datetime] = None,
) -> int:
    """다음 연동 세션 시작까지 초 (trade_start KST, 최소 60초)."""
    kst = as_kst(now)

    if settings is None:
        from core.models import AutoTradeSettings, get_db

        for db in get_db():
            settings = db.query(AutoTradeSettings).first()
            break

    if settings and is_krx_trading_day(kst):
        start, _ = linked_trading_session_bounds(settings, kst)
        if kst.time() < start:
            target = kst.replace(
                hour=start.hour,
                minute=start.minute,
                second=0,
                microsecond=0,
            )
            return max(60, int((target - kst).total_seconds()))

    d = kst.date() + timedelta(days=1)
    default_start = dt_time(10, 0)
    if settings:
        default_start, _ = linked_trading_session_bounds(settings, kst)

    for _ in range(400):
        probe = datetime.combine(d, default_start, tzinfo=KST)
        if is_krx_trading_day(probe):
            return max(60, min(int((probe - kst).total_seconds()), 3600))
        d += timedelta(days=1)
    return 3600


def telegram_market_alert_block_reason(now: Optional[datetime] = None) -> Optional[str]:
    """조건식·정기 텔레그램 알림 — 장중(거래일 09:00~15:30 KST)만 허용."""
    kst = as_kst(now)
    off_day = trading_day_block_reason(kst)
    if off_day:
        return off_day
    if kst.time() < dt_time(9, 0):
        return f"장 시작 전 ({kst.strftime('%H:%M')}) — 알림 미전송"
    if kst.time() > dt_time(15, 30):
        return f"장 마감 후 ({kst.strftime('%H:%M')}) — 알림 미전송"
    return None


def telegram_market_alert_allowed(now: Optional[datetime] = None) -> bool:
    return telegram_market_alert_block_reason(now) is None
