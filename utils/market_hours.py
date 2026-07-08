"""KRX 정규장·거래일 판별 — 현재가 조회·자동매매 허용 구간."""
from __future__ import annotations

from datetime import date, datetime, time as dt_time
from typing import Optional

from utils.krx_holiday_store import holiday_dates_for_year, holiday_label, is_holiday


def krx_holidays_for_year(year: int):
    return holiday_dates_for_year(year)


def is_krx_holiday(day: date) -> bool:
    return is_holiday(day)


def is_krx_trading_day(now: Optional[datetime] = None) -> bool:
    """평일이면서 KRX 휴장일이 아닌 날."""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return not is_krx_holiday(now.date())


def trading_day_block_reason(now: Optional[datetime] = None) -> Optional[str]:
    """거래 불가일이면 사유 문자열, 거래일이면 None."""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return "주말(토·일) - 거래소 휴장"
    if is_krx_holiday(now.date()):
        label = holiday_label(now.date())
        if label:
            return f"거래소 휴장일({now.strftime('%Y-%m-%d')} {label})"
        return f"거래소 휴장일({now.strftime('%Y-%m-%d')})"
    return None


def is_krx_session(now: Optional[datetime] = None) -> bool:
    """평일 거래일 09:00~15:30 — 현재가·일봉 차트·ATR 실시간 조회 허용 구간."""
    now = now or datetime.now()
    if not is_krx_trading_day(now):
        return False
    return dt_time(9, 0) <= now.time() <= dt_time(15, 30)


def telegram_market_alert_block_reason(now: Optional[datetime] = None) -> Optional[str]:
    """조건식·정기 텔레그램 알림 — 장중(거래일 09:00~15:30)만 허용."""
    now = now or datetime.now()
    off_day = trading_day_block_reason(now)
    if off_day:
        return off_day
    if now.time() < dt_time(9, 0):
        return f"장 시작 전 ({now.strftime('%H:%M')}) — 알림 미전송"
    if now.time() > dt_time(15, 30):
        return f"장 마감 후 ({now.strftime('%H:%M')}) — 알림 미전송"
    return None


def telegram_market_alert_allowed(now: Optional[datetime] = None) -> bool:
    return telegram_market_alert_block_reason(now) is None
