"""종가배팅은 전역 최대 동시 보유 한도와 별도로 매수한다."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from managers.buy_order_executor import BuyOrderExecutor


def _signal(*, strategy: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=99,
        stock_code="096770",
        stock_name="SK이노베이션",
        additional_data={
            "strategy": strategy,
            "source": strategy,
            "change_rate": 7.5,
            "current_price": 135000,
            "entry_leg": 1,
            "jongga_entry_leg": 1,
            "gate_pack": "jongga_closing",
        },
    )


def _settings(**kwargs) -> SimpleNamespace:
    base = dict(
        use_jongga=True,
        jongga_trade_start_time="14:30",
        jongga_pick_end_time="14:40",
        jongga_trade_end_time="15:28",
        jongga_leg3_end_time="15:28",
        jongga_max_slots=1,
        jongga_buy_amount=1_000_000,
        jongga_buy_deposit_pct=None,
        liquidate_before_close=False,
        daily_loss_limit=None,
        daily_profit_target=None,
        max_concurrent_positions=6,
        cash_reserve_pct=0,
        sizing_method="FIXED",
        fixed_buy_amount=1_000_000,
        initial_min_amount=500_000,
        initial_max_amount=2_000_000,
        add_buy_amount=500_000,
        add_buy_trigger=3.0,
        min_change_rate_buy=None,
        buy_below_price=None,
        use_entry_gate=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _account() -> dict:
    return {
        "deposit": 10_000_000,
        "investable_cash": 9_000_000,
        "cash_reserve": 1_000_000,
    }


@pytest.mark.asyncio
async def test_jongga_bypasses_global_max_concurrent_positions(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ex = BuyOrderExecutor()
    ex.auto_trade_settings = _settings()
    ex._get_account_info = AsyncMock(return_value=_account())
    ex._check_stock_status = AsyncMock(return_value={"tradeable": True})
    ex._has_pending_order = AsyncMock(return_value=False)
    ex._get_current_price = AsyncMock(return_value=135000)

    fixed_now = datetime(2026, 9, 1, 14, 35, tzinfo=ZoneInfo("Asia/Seoul"))
    monkeypatch.setattr("managers.buy_order_executor.as_kst", lambda: fixed_now)

    fake_db = MagicMock()
    monkeypatch.setattr(
        "managers.buy_order_executor.get_db",
        lambda: iter([fake_db]),
    )
    monkeypatch.setattr(
        "managers.buy_order_executor.is_max_concurrent_positions_reached",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "managers.buy_order_executor.count_open_position_slots",
        lambda *a, **k: 7,
    )

    result = await ex._validate_buy_conditions(_signal(strategy="jongga"))
    assert result["valid"] is True, result


@pytest.mark.asyncio
async def test_legacy_still_blocked_by_global_max_concurrent_positions(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ex = BuyOrderExecutor()
    ex.auto_trade_settings = _settings(use_jongga=False)
    ex._get_account_info = AsyncMock(return_value=_account())

    fixed_now = datetime(2026, 9, 1, 11, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    monkeypatch.setattr("managers.buy_order_executor.as_kst", lambda: fixed_now)

    fake_db = MagicMock()
    monkeypatch.setattr(
        "managers.buy_order_executor.get_db",
        lambda: iter([fake_db]),
    )
    monkeypatch.setattr(
        "managers.buy_order_executor.is_max_concurrent_positions_reached",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "managers.buy_order_executor.count_open_position_slots",
        lambda *a, **k: 7,
    )

    result = await ex._validate_buy_conditions(_signal(strategy="legacy"))
    assert result["valid"] is False
    assert "최대 동시 보유" in result.get("reason", "")
