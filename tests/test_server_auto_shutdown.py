"""서버 야간 자동 종료 — 시각 판정."""
from datetime import datetime
from zoneinfo import ZoneInfo

from utils.market_hours import should_auto_shutdown_server

KST = ZoneInfo("Asia/Seoul")


def _kst(h: int, m: int) -> datetime:
    return datetime(2026, 7, 30, h, m, tzinfo=KST)


def test_shutdown_before_cutoff():
    assert not should_auto_shutdown_server(
        _kst(18, 59), enabled=True, shutdown_hm="19:00"
    )


def test_shutdown_at_cutoff():
    assert should_auto_shutdown_server(
        _kst(19, 0), enabled=True, shutdown_hm="19:00"
    )


def test_shutdown_after_cutoff():
    assert should_auto_shutdown_server(
        _kst(21, 30), enabled=True, shutdown_hm="19:00"
    )


def test_shutdown_disabled():
    assert not should_auto_shutdown_server(
        _kst(22, 0), enabled=False, shutdown_hm="19:00"
    )
