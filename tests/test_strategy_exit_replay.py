"""전략별 청산 리플레이 MVP 단위 테스트 (합성 일봉)."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from unittest.mock import patch

from utils.stock_exit_replay import run_stock_exit_replay_async


def _bar(d: str, o: int, h: int, l: int, c: int, v: int = 1000) -> Dict[str, Any]:
    return {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "date": d,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "atr14": 500.0,
    }


def _settings(**overrides) -> Dict[str, Any]:
    base = {
        "stop_loss_rate": 5.0,
        "take_profit_rate": 10.0,
        "trailing_stop_pct": 3.0,
        "atr_mult_stop": None,
        "atr_mult_trail": None,
        "atr_period": 14,
        "profit_lock_trigger": None,
        "profit_lock_floor": None,
        "liquidate_before_close": False,
        "liquidate_time": "15:10",
        "use_entry_gate": False,
        "require_above_open": False,
        "require_above_vwap": False,
        "trade_start_time": "10:00",
        "trade_end_time": "15:20",
        "sangtta_trade_start_time": "09:05",
        "sangtta_trade_end_time": "11:00",
        "sangtta_change_min": 15.0,
        "sangtta_change_max": 19.0,
        "sangtta_max_market_cap": 3000.0,
        "sangtta_open_rise_min_pct": 3.0,
        "breakout_trade_start_time": "11:00",
        "breakout_trade_end_time": "14:30",
        "breakout_level_mode": "prev_high",
        "breakout_n_day": 10,
        "breakout_vol_mult": 1.5,
        "breakout_max_change_pct": 12.0,
        "breakout_stop_loss_pct": 3.0,
        "breakout_trailing_start_pct": 10.0,
        "breakout_trailing_pct": 4.0,
        "struct_break_hard_pct": 2.0,
        "struct_break_soft_pct": 1.0,
        "limit_break_hard_pct": 3.0,
        "sharp_drop_hard_pct": 5.0,
    }
    base.update(overrides)
    return base


async def _run(bars: List[Dict], strategy: str, entry_date: str, **kw):
    settings = kw.pop("settings", _settings())

    async def fake_load(*_a, **_k):
        return bars, "test"

    with patch("utils.stock_exit_replay._load_daily_bars", fake_load), \
         patch("utils.stock_exit_replay.get_auto_trade_settings_sync", return_value=None), \
         patch("utils.stock_exit_replay.latest_as_of_date", return_value=None), \
         patch("utils.technical_mart_store.get_latest_map_by_codes", return_value={}):
        return await run_stock_exit_replay_async(
            "005930",
            entry_date,
            strategy=strategy,
            settings_override=settings,
            force_exit=True,
            days=30,
            resolution="1d",
            **kw,
        )


def test_legacy_stop_loss_on_daily_low():
    # 진입일 종가 10000 → 다음날 low가 손절(-5%) 이하
    bars = [
        _bar("2026-01-02", 9900, 10100, 9800, 10000),
        _bar("2026-01-05", 9900, 9950, 9400, 9500),  # -6% low
        _bar("2026-01-06", 9500, 9600, 9400, 9550),
    ]
    result = asyncio.run(_run(bars, "legacy", "2026-01-02"))
    assert result["success"]
    assert result["strategy"]["key"] == "legacy"
    assert result["entry"]["passed"] is True  # gate off
    assert result["exit"]["reason"] == "STOP_LOSS"
    assert result["exit"]["date"] == "2026-01-05"
    assert result["buy_condition_checks"]
    assert result["sell_condition_checks"]


def test_breakout_structure_hard_exit():
    # prev high 10000, 진입일 종가 10100 (돌파), 다음날 구조 이탈
    bars = [
        _bar("2026-01-02", 9800, 10000, 9700, 9900, 1000),
        _bar("2026-01-05", 10050, 10200, 10020, 10100, 2000),  # 돌파 + 거래량 2배
        _bar("2026-01-06", 10000, 10050, 9700, 9800, 1500),  # low < level*0.98
    ]
    result = asyncio.run(_run(bars, "breakout", "2026-01-05", settings=_settings(
        breakout_vol_mult=1.5,
        breakout_max_change_pct=20.0,
    )))
    assert result["success"]
    assert result["strategy"]["key"] == "breakout"
    assert result["entry"]["passed"] is True
    assert result["entry"]["level_price"] == 10000
    assert "구조" in (result["exit"].get("reason_label") or "") or "구조" in (result["exit"].get("detail") or "")
    assert result["exit"]["date"] == "2026-01-06"


def test_sangtta_band_fail_still_simulates_exit():
    # 등락 밴드 밖 → 진입 미통과, 그래도 청산 시뮬은 진행
    bars = [
        _bar("2026-01-02", 10000, 10100, 9900, 10000),
        _bar("2026-01-05", 10000, 10100, 9900, 10050),  # +0.5% only
        _bar("2026-01-06", 10000, 10050, 9400, 9500),
    ]
    result = asyncio.run(_run(bars, "sangtta", "2026-01-05", settings=_settings(
        stop_loss_rate=5.0,
        sangtta_change_min=15.0,
        sangtta_change_max=19.0,
    )))
    assert result["success"]
    assert result["entry"]["passed"] is False
    assert result["exit"] is not None


def test_strategy_alias_oversold_breakout():
    bars = [
        _bar("2026-01-02", 9800, 10000, 9700, 9900, 1000),
        _bar("2026-01-05", 10050, 10200, 10020, 10100, 2000),
        _bar("2026-01-06", 10100, 10300, 10050, 10200, 1500),
    ]
    result = asyncio.run(_run(
        bars, "oversold_breakout", "2026-01-05",
        settings=_settings(breakout_max_change_pct=20.0),
    ))
    assert result["strategy"]["key"] == "breakout"
