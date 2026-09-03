"""1회 스캔 총한도 — 레거시 상위 잔여 배정."""
from managers.auto_trade_scanner import effective_legacy_scan_limit


def test_legacy_gets_full_cap_when_alone():
    assert effective_legacy_scan_limit(60, 60, 0) == 60


def test_legacy_shrinks_for_non_legacy_reserved():
    # 상따 20 + 돌파 5 + 프랙탈 5 = 30 → 레거시 30
    assert effective_legacy_scan_limit(60, 60, 30) == 30


def test_legacy_zero_when_reserved_fills_total():
    assert effective_legacy_scan_limit(60, 60, 60) == 0
    assert effective_legacy_scan_limit(60, 60, 75) == 0


def test_screener_cap_still_bounds_legacy():
    # 잔여 50이어도 스크리너 상한이 40이면 40
    assert effective_legacy_scan_limit(60, 40, 10) == 40


def test_screener_cap_zero_means_no_legacy():
    assert effective_legacy_scan_limit(60, 0, 0) == 0
