"""api_traffic_guard 회귀 테스트."""
import time

from utils.api_traffic_guard import (
    APIPriority,
    effective_max_wait,
    mark_scan_end,
    mark_scan_start,
    should_defer_dashboard_live,
    should_yield_low_priority,
)


def test_scan_burst_defers_dashboard_live():
    mark_scan_start()
    assert should_defer_dashboard_live() is True
    mark_scan_end()
    assert should_defer_dashboard_live() is True
    time.sleep(0.01)


def test_priority_max_wait_order():
    assert effective_max_wait(APIPriority.CRITICAL) > effective_max_wait(APIPriority.LOW)


def test_yield_low_priority_during_scan():
    mark_scan_start()
    try:
        assert should_yield_low_priority() is True
    finally:
        mark_scan_end()
