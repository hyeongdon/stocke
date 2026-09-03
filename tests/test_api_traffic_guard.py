"""api_traffic_guard 회귀 테스트."""
import time

from utils.api_traffic_guard import (
    APIPriority,
    SCAN_BURST_DEFER_SEC,
    YIELD_ON_USAGE_PCT,
    effective_max_wait,
    get_traffic_status,
    mark_scan_end,
    mark_scan_start,
    should_defer_dashboard_live,
    should_yield_low_priority,
)


def test_scan_burst_defers_dashboard_live():
    mark_scan_start()
    st = get_traffic_status()
    assert st["defer_dashboard_live"] is True
    assert st["scanner_active"] is True
    assert st["defer_reason"] == "scan"
    assert st["defer_stock_threshold"] is None
    assert should_defer_dashboard_live() is True
    mark_scan_end()
    st2 = get_traffic_status()
    assert st2["defer_dashboard_live"] is True
    assert st2["defer_reason"] == "post_scan_burst"
    assert st2["post_scan_burst_sec"] == SCAN_BURST_DEFER_SEC
    assert should_defer_dashboard_live() is True
    time.sleep(0.01)


def test_priority_max_wait_order():
    assert effective_max_wait(APIPriority.CRITICAL) > effective_max_wait(APIPriority.LOW)


def test_yield_low_priority_during_scan():
    mark_scan_start()
    try:
        assert should_yield_low_priority() is True
        assert get_traffic_status()["yield_on_usage_pct"] == YIELD_ON_USAGE_PCT
    finally:
        mark_scan_end()


def test_traffic_status_idle_after_burst(monkeypatch):
    mark_scan_start()
    mark_scan_end()
    # 버스트 구간을 이미 지난 것처럼 강제
    import utils.api_traffic_guard as g
    monkeypatch.setattr(g, "_defer_low_until", time.monotonic() - 1)
    st = get_traffic_status()
    assert st["defer_dashboard_live"] is False
    assert st["defer_reason"] is None
    assert st["defer_remaining_sec"] == 0.0
