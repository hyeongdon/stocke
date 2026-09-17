"""NXT 연장 세션 매수는 SOR+지정가, 정규장은 기존 시장가."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from utils.auto_trade_engine import buy_order_route

KST = ZoneInfo("Asia/Seoul")


def _settings(method="MARKET"):
    return SimpleNamespace(order_method=method)


def test_regular_session_keeps_market_on_krx():
    now = datetime(2026, 9, 17, 11, 0, tzinfo=KST)
    assert buy_order_route(_settings(), 10_000, now) == (0, "3", "KRX")


def test_after_hours_forces_sor_limit():
    now = datetime(2026, 9, 17, 16, 10, tzinfo=KST)
    assert buy_order_route(_settings("MARKET"), 10_000, now) == (10_000, "0", "SOR")


def test_after_hours_limit_still_sor():
    now = datetime(2026, 9, 17, 18, 0, tzinfo=KST)
    assert buy_order_route(_settings("LIMIT"), 12_500, now) == (12_500, "0", "SOR")
