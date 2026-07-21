"""15분봉 전략 시뮬 단위 테스트 (합성 분봉)."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from utils.stock_exit_replay_15m import run_stock_exit_replay_15m_async


def _dbar(d: str, o: int, h: int, l: int, c: int, v: int = 1000) -> Dict[str, Any]:
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


def _mbar(ts: str, o: int, h: int, l: int, c: int, v: int = 100) -> Dict[str, Any]:
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _settings(**overrides) -> Dict[str, Any]:
    base = {
        "stop_loss_rate": 5.0,
        "take_profit_rate": 10.0,
        "trailing_stop_pct": 3.0,
        "use_entry_gate": False,
        "require_above_open": False,
        "require_above_vwap": False,
        "trade_start_time": "09:00",
        "trade_end_time": "15:20",
        "liquidate_before_close": False,
        "breakout_stop_loss_pct": 3.0,
        "breakout_trailing_start_pct": 10.0,
        "breakout_trailing_pct": 4.0,
        "breakout_level_mode": "prev_high",
        "breakout_vol_mult": 1.5,
        "breakout_max_change_pct": 20.0,
        "breakout_trade_start_time": "09:00",
        "breakout_trade_end_time": "15:00",
        "struct_break_hard_pct": 2.0,
        "struct_break_soft_pct": 1.0,
        "soft_confirm_polls": 2,
        "sangtta_trade_start_time": "09:05",
        "sangtta_trade_end_time": "11:00",
        "sangtta_change_min": 15.0,
        "sangtta_change_max": 19.0,
        "sangtta_max_market_cap": 3000.0,
    }
    base.update(overrides)
    return base


def test_15m_legacy_entry_and_stop():
    daily = [
        _dbar("2026-01-02", 9900, 10100, 9800, 10000),
        _dbar("2026-01-05", 10000, 10200, 9900, 10100),
    ]
    # 게이트 비활성 → 09:00 첫 봉 진입, 이후 손절
    mbars = [
        _mbar("2026-01-05 09:00:00", 10000, 10050, 9990, 10020),
        _mbar("2026-01-05 09:15:00", 10020, 10040, 10000, 10010),
        _mbar("2026-01-05 09:30:00", 10000, 10010, 9400, 9450),  # -5.7% low
    ]
    result = asyncio.run(run_stock_exit_replay_15m_async(
        "005930",
        "2026-01-05",
        strategy="legacy",
        days=1,
        settings_override=_settings(),
        daily_bars_override=daily,
        intraday_bars_override=mbars,
    ))
    assert result["success"]
    assert result["resolution"] == "15m"
    assert result["entry"]["passed"] is True
    assert result["entry"]["time"] == "2026-01-05 09:00:00"
    assert result["exit"]["reason"] == "STOP_LOSS"
    assert result["exit"]["time"] == "2026-01-05 09:30:00"
    chart = result["intraday_chart"]
    assert chart["markers"]["buy"]["time"]
    assert chart["markers"]["sell"]["time"]
    assert len(chart["bars"]) >= 2


def test_15m_breakout_structure_exit():
    daily = [
        _dbar("2026-01-02", 9800, 10000, 9700, 9900, 1000),
        _dbar("2026-01-05", 10050, 10300, 10000, 10200, 2500),
    ]
    mbars = [
        _mbar("2026-01-05 11:00:00", 10050, 10150, 10040, 10120, 2000),  # 돌파 + 거래량
        _mbar("2026-01-05 11:15:00", 10120, 10200, 10100, 10180, 400),
        _mbar("2026-01-05 11:30:00", 10100, 10120, 9700, 9750, 500),  # 구조 HARD
    ]
    result = asyncio.run(run_stock_exit_replay_15m_async(
        "005930",
        "2026-01-05",
        strategy="breakout",
        days=1,
        settings_override=_settings(),
        daily_bars_override=daily,
        intraday_bars_override=mbars,
    ))
    assert result["success"]
    assert result["entry"]["passed"] is True
    assert result["entry"]["level_price"] == 10000
    assert "구조" in (result["exit"].get("reason_label") or result["exit"].get("detail") or "")


def test_15m_sangtta_band_cross_entry():
    """종가는 밴드 밖이지만 고저가가 15~19% 밴드를 가로지르면 그 봉에서 진입."""
    # 전일종가 1000 → 밴드 1150~1190
    daily = [
        _dbar("2026-01-02", 980, 1010, 970, 1000),
        _dbar("2026-01-05", 1050, 1300, 1040, 1280),
    ]
    mbars = [
        # 시가 +5%, 고가 +30%(상한가권), 종가 +30% — 종가만 보면 미진입
        _mbar("2026-01-05 09:05:00", 1050, 1300, 1040, 1300),
        _mbar("2026-01-05 09:20:00", 1300, 1300, 1280, 1290),
    ]
    result = asyncio.run(run_stock_exit_replay_15m_async(
        "023790",
        "2026-01-05",
        strategy="sangtta",
        days=1,
        settings_override=_settings(
            use_entry_gate=True,
            sangtta_trade_start_time="09:00",
            sangtta_trade_end_time="15:00",
            sangtta_change_min=15.0,
            sangtta_change_max=19.0,
        ),
        daily_bars_override=daily,
        intraday_bars_override=mbars,
    ))
    assert result["success"]
    assert result["entry"]["passed"] is True
    assert result["entry"]["time"] == "2026-01-05 09:05:00"
    assert result["entry"]["fill_mode"] == "band_cross"
    # 밴드 하단(15%) ≈ 1150 추정 체결
    assert result["entry"]["price"] == 1150
    assert 14.5 <= float(result["entry"]["change_rate"]) <= 15.5


def test_5m_resolution_label_and_load_path():
    """단일 종목 5분봉 시뮬 — resolution 라벨·종가 체결 모드."""
    daily = [
        _dbar("2026-01-02", 980, 1010, 970, 1000),
        _dbar("2026-01-05", 1050, 1180, 1040, 1160),
    ]
    mbars = [
        _mbar("2026-01-05 09:05:00", 1050, 1060, 1040, 1055),
        _mbar("2026-01-05 10:30:00", 1160, 1180, 1155, 1170),
        _mbar("2026-01-05 10:35:00", 1170, 1175, 1165, 1172),
    ]
    result = asyncio.run(run_stock_exit_replay_15m_async(
        "023790",
        "2026-01-05",
        strategy="sangtta",
        days=1,
        bar_minutes=5,
        settings_override=_settings(
            use_entry_gate=True,
            sangtta_trade_start_time="09:00",
            sangtta_trade_end_time="15:00",
            sangtta_change_min=15.0,
            sangtta_change_max=19.0,
        ),
        daily_bars_override=daily,
        intraday_bars_override=mbars,
    ))
    assert result["success"]
    assert result["resolution"] == "5m"
    assert result["entry"]["passed"] is True
    assert result["entry"]["time"] == "2026-01-05 10:30:00"
    assert result["entry"]["fill_mode"] in ("5m_close", "band_cross")
