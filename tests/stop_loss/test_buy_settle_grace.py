"""매수 직후 잔고 유예(grace) — 가짜 MANUAL_SELL 방지."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from managers.stop_loss_manager import (
    _buy_age_seconds,
    _required_missing_confirms,
    _within_buy_settle_grace,
)


def test_buy_age_seconds_utc_naive():
    now = datetime(2026, 7, 15, 3, 33, 29)
    pos = SimpleNamespace(buy_time=datetime(2026, 7, 15, 3, 33, 27))
    assert abs(_buy_age_seconds(pos, now=now) - 2.0) < 1e-6


def test_buy_age_seconds_aware_buy_time():
    now = datetime(2026, 7, 15, 3, 33, 29)
    pos = SimpleNamespace(
        buy_time=datetime(2026, 7, 15, 3, 33, 27, tzinfo=timezone.utc),
    )
    assert abs(_buy_age_seconds(pos, now=now) - 2.0) < 1e-6


def test_within_grace_like_unisem_race():
    """유니셈 유형: 매수 1.4초 후 잔고 미반영 → 유예."""
    buy = datetime(2026, 7, 15, 3, 33, 27, 737076)
    now = datetime(2026, 7, 15, 3, 33, 29, 160150)
    pos = SimpleNamespace(buy_time=buy)
    assert _within_buy_settle_grace(pos, grace_seconds=90, now=now) is True


def test_hd_energy_race_at_90s_needs_longer_grace():
    """HD에너지솔루션: 매수 90.2초 후 잔고 미반영 — 90초 유예로는 부족."""
    buy = datetime(2026, 7, 21, 2, 45, 25, 407144)
    now = datetime(2026, 7, 21, 2, 46, 55, 641390)
    pos = SimpleNamespace(buy_time=buy)
    assert abs(_buy_age_seconds(pos, now=now) - 90.234246) < 1e-3
    assert _within_buy_settle_grace(pos, grace_seconds=90, now=now) is False
    assert _within_buy_settle_grace(pos, grace_seconds=300, now=now) is True


def test_outside_grace_after_settle():
    buy = datetime(2026, 7, 15, 3, 33, 27)
    now = buy + timedelta(seconds=91)
    pos = SimpleNamespace(buy_time=buy)
    assert _within_buy_settle_grace(pos, grace_seconds=90, now=now) is False


def test_grace_disabled_when_zero():
    pos = SimpleNamespace(buy_time=datetime(2026, 7, 15, 3, 33, 27))
    now = datetime(2026, 7, 15, 3, 33, 28)
    assert _within_buy_settle_grace(pos, grace_seconds=0, now=now) is False


def test_missing_buy_time_not_in_grace():
    pos = SimpleNamespace(buy_time=None)
    assert _within_buy_settle_grace(pos, grace_seconds=90) is False


def test_required_missing_confirms_at_least_one():
    assert _required_missing_confirms() >= 1


if __name__ == "__main__":
    test_buy_age_seconds_utc_naive()
    test_buy_age_seconds_aware_buy_time()
    test_within_grace_like_unisem_race()
    test_hd_energy_race_at_90s_needs_longer_grace()
    test_outside_grace_after_settle()
    test_grace_disabled_when_zero()
    test_missing_buy_time_not_in_grace()
    test_required_missing_confirms_at_least_one()
    print("ok")
