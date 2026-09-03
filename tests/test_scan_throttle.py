"""스캔 종목 간 적응형 스로틀."""
from managers.auto_trade_scanner import compute_scan_throttle_sec


def test_throttle_short_when_api_headroom():
    # 잔여 5+ → ~1.2초 (min_iv 3 * 0.4)
    assert compute_scan_throttle_sec(
        use_entry_gate=True,
        remaining_calls=5,
        min_call_interval=3.0,
        base_pause_sec=3.0,
    ) == 1.2


def test_throttle_base_when_api_tight():
    assert compute_scan_throttle_sec(
        use_entry_gate=True,
        remaining_calls=0,
        min_call_interval=3.0,
        base_pause_sec=3.0,
    ) == 3.0


def test_throttle_waits_for_rate_window_not_stack_base():
    # until=2초면 base 3을 겹치지 않고 2초만
    assert compute_scan_throttle_sec(
        use_entry_gate=True,
        remaining_calls=0,
        seconds_until_available=2.0,
        min_call_interval=3.0,
        base_pause_sec=3.0,
    ) == 2.0


def test_throttle_half_without_entry_gate():
    assert compute_scan_throttle_sec(
        use_entry_gate=False,
        remaining_calls=0,
        base_pause_sec=3.0,
    ) == 1.5
