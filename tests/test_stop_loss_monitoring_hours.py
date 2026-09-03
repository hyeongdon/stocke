"""손절 모니터 세션 — 매수 창과 분리 (거래일 08:00~19:30)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from utils.market_hours import (
    is_stop_loss_monitoring_session,
    seconds_until_stop_loss_monitoring,
    stop_loss_monitoring_window_str,
)

KST = ZoneInfo("Asia/Seoul")


def _kst(y, m, d, h, mi) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=KST)


def test_window_str():
    assert stop_loss_monitoring_window_str() == "08:00~19:30"


def test_active_on_trading_day_bounds():
    # 2026-08-05 수요일 (평일, 휴장일 아님 가정)
    assert is_stop_loss_monitoring_session(None, _kst(2026, 8, 5, 7, 59)) is False
    assert is_stop_loss_monitoring_session(None, _kst(2026, 8, 5, 8, 0)) is True
    assert is_stop_loss_monitoring_session(None, _kst(2026, 8, 5, 12, 0)) is True
    assert is_stop_loss_monitoring_session(None, _kst(2026, 8, 5, 16, 0)) is True
    assert is_stop_loss_monitoring_session(None, _kst(2026, 8, 5, 19, 30)) is True
    assert is_stop_loss_monitoring_session(None, _kst(2026, 8, 5, 19, 31)) is False


def test_inactive_weekend():
    assert is_stop_loss_monitoring_session(None, _kst(2026, 8, 8, 10, 0)) is False  # 토
    assert is_stop_loss_monitoring_session(None, _kst(2026, 8, 9, 10, 0)) is False  # 일


def test_seconds_until_before_open():
    now = _kst(2026, 8, 5, 7, 0)
    sec = seconds_until_stop_loss_monitoring(None, now)
    assert 3500 <= sec <= 3700  # ~08:00까지 약 1시간
