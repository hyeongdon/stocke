from types import SimpleNamespace

import pytest

from managers.buy_order_executor import BuyOrderExecutor


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (None, 60),
        (SimpleNamespace(scan_interval_sec=None), 60),
        (SimpleNamespace(scan_interval_sec="30"), 30),
        (SimpleNamespace(scan_interval_sec=5), 15),
        (SimpleNamespace(scan_interval_sec=900), 600),
        (SimpleNamespace(scan_interval_sec="invalid"), 60),
    ],
)
def test_poll_interval_is_parsed_and_bounded(settings, expected):
    assert BuyOrderExecutor._poll_interval(settings) == expected
