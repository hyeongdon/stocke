"""MA1592 매수는 레거시 최소등락률을 적용하지 않는다."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from managers.buy_order_executor import BuyOrderExecutor


def _signal(*, strategy: str, change_rate: float) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        stock_code="005930",
        stock_name="삼성전자",
        additional_data={
            "strategy": strategy,
            "source": strategy,
            "change_rate": change_rate,
            "current_price": 70000,
            "stop_price": 69600,
            "suggested_qty": 10,
            "gate_pack": "ma1592_hold",
            "ma15": 69800,
        },
    )


def _settings(**kwargs) -> SimpleNamespace:
    base = dict(
        signal_min_threshold=3.5,
        buy_below_price=None,
        use_entry_gate=False,
        use_ma1592=True,
        ma1592_trade_start_time="09:10",
        ma1592_trade_end_time="15:15",
        ma1592_max_slots=2,
        ma1592_risk_per_trade_pct=2.0,
        ma1592_stop_pct=4.0,
        ma1592_hard_break_pct=0.4,
        ma1592_tp1_frac=0.5,
        ma1592_max_invest_amount=0,
        trade_start_time="09:50",
        trade_end_time="15:20",
        liquidate_before_close=False,
        daily_loss_limit=None,
        daily_profit_target=None,
        max_concurrent_positions=0,
        cash_reserve_pct=0,
        sizing_method="FIXED",
        fixed_buy_amount=1_000_000,
        initial_min_amount=500_000,
        initial_max_amount=2_000_000,
        add_buy_amount=500_000,
        add_buy_trigger=3.0,
        min_change_rate_buy=None,
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
async def test_ma1592_skips_legacy_min_change_rate(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ex = BuyOrderExecutor()
    ex.auto_trade_settings = _settings()
    ex._get_account_info = AsyncMock(return_value=_account())
    ex._check_stock_status = AsyncMock(return_value={"tradeable": True})
    ex._has_pending_order = AsyncMock(return_value=False)
    ex._get_current_price = AsyncMock(return_value=70000)

    fixed_now = datetime(2026, 8, 26, 11, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    monkeypatch.setattr("managers.buy_order_executor.as_kst", lambda: fixed_now)

    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.count.return_value = 0
    fake_db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(
        "managers.buy_order_executor.get_db",
        lambda: iter([fake_db]),
    )

    signal = _signal(strategy="ma1592", change_rate=0.58)
    result = await ex._validate_buy_conditions(signal)
    assert result["valid"] is True, result
