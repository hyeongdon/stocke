"""레거시 진입 게이트 — 일봉 RSI(14) 상·하한."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from utils.auto_trade_engine import compute_legacy_rsi14, compute_rsi_series


def _rising_closes(n: int = 40, start: float = 100.0) -> list:
    """완만 상승 → RSI 고점 쪽."""
    return [start + i * 1.5 for i in range(n)]


def _falling_closes(n: int = 40, start: float = 200.0) -> list:
    return [start - i * 1.5 for i in range(n)]


def _bars_from_closes(closes: list) -> list:
    return [{"close": c, "open": c, "high": c, "low": c, "volume": 1000} for c in closes]


def test_compute_legacy_rsi14_basic():
    closes = _rising_closes(30)
    rsi = compute_legacy_rsi14(_bars_from_closes(closes))
    assert rsi is not None
    assert rsi > 70


def test_compute_legacy_rsi14_needs_enough_bars():
    assert compute_legacy_rsi14(_bars_from_closes([100.0] * 10)) is None


def test_compute_legacy_rsi14_uses_current_price():
    closes = _rising_closes(30)
    bars = _bars_from_closes(closes)
    # 급락 현재가 → RSI 하락
    rsi_close = compute_legacy_rsi14(bars)
    rsi_px = compute_legacy_rsi14(bars, current_price=int(closes[0]))
    assert rsi_close is not None and rsi_px is not None
    assert rsi_px < rsi_close


@pytest.mark.asyncio
async def test_eval_legacy_rsi_max_blocks():
    from utils.auto_trade_engine import _eval_legacy_momentum
    from unittest.mock import patch

    closes = _rising_closes(40)
    bars = _bars_from_closes(closes)
    for i, b in enumerate(bars):
        b["timestamp"] = f"2026-06-{(i % 28) + 1:02d}"
    bars[-1]["timestamp"] = "2026-07-28"
    bars[-1]["open"] = closes[-1]
    bars[-1]["high"] = closes[-1]
    bars[-1]["low"] = closes[-1]

    class Api:
        def normalize_stock_code(self, c):
            return str(c).zfill(6)

        async def get_stock_chart_data(self, code, interval):
            return bars

    settings = SimpleNamespace(
        use_entry_gate=True,
        require_above_open=False,
        require_above_vwap=False,
        day_position_min=None,
        day_position_max=None,
        volume_ratio_min=None,
        legacy_rsi_min=None,
        legacy_rsi_max=75.0,
    )
    with patch("utils.auto_trade_engine.kst_date_str", return_value="2026-07-28"):
        ok, reason = await _eval_legacy_momentum(
            Api(), settings, "005930", int(closes[-1]),
        )
    assert ok is False
    assert "RSI 과열" in reason


@pytest.mark.asyncio
async def test_eval_legacy_rsi_band_pass():
    from utils.auto_trade_engine import _eval_legacy_momentum
    from unittest.mock import patch

    # 중립에 가까운 시계열
    closes = [100.0 + ((i % 5) - 2) * 0.5 for i in range(40)]
    bars = _bars_from_closes(closes)
    bars[-1]["timestamp"] = "2026-07-28"
    bars[-1]["open"] = closes[-1]
    bars[-1]["high"] = closes[-1] + 1
    bars[-1]["low"] = closes[-1] - 1

    class Api:
        def normalize_stock_code(self, c):
            return str(c).zfill(6)

        async def get_stock_chart_data(self, code, interval):
            return bars

    settings = SimpleNamespace(
        use_entry_gate=True,
        require_above_open=False,
        require_above_vwap=False,
        day_position_min=None,
        day_position_max=None,
        volume_ratio_min=None,
        legacy_rsi_min=30.0,
        legacy_rsi_max=75.0,
    )
    with patch("utils.auto_trade_engine.kst_date_str", return_value="2026-07-28"):
        ok, reason = await _eval_legacy_momentum(
            Api(), settings, "005930", int(closes[-1]),
        )
    assert ok is True, reason
    assert reason == "게이트 통과"


def test_rsi_series_matches_helper():
    closes = _falling_closes(25)
    series = compute_rsi_series(closes, 14)
    helper = compute_legacy_rsi14(_bars_from_closes(closes))
    assert helper == round(float(series[-1]), 2)
