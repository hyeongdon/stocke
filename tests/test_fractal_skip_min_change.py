"""프랙탈 매수는 레거시 최소등락률(예: 3.5%)을 적용하지 않는다."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from managers.buy_order_executor import BuyOrderExecutor


def _signal(*, strategy: str, change_rate: float) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        stock_code="161890",
        stock_name="한국콜마",
        additional_data={
            "strategy": strategy,
            "source": strategy,
            "change_rate": change_rate,
            "current_price": 138500,
            "stop_price": 137000,
            "gate_pack": "ema_fractal_pullback",
        },
    )


def _settings(**kwargs) -> SimpleNamespace:
    base = dict(
        signal_min_threshold=3.5,
        buy_below_price=None,
        use_entry_gate=False,
        fractal_trade_start_time="09:30",
        fractal_trade_end_time="14:50",
        fractal_risk_pct=0.5,
        fractal_qty_cap=0,
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
async def test_fractal_skips_legacy_min_change_rate(monkeypatch):
    """게이트 통과 후 등락 0.58%여도 레거시 3.5%에 막히지 않는다."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ex = BuyOrderExecutor()
    ex.auto_trade_settings = _settings()
    ex._get_account_info = AsyncMock(return_value=_account())
    ex._check_stock_status = AsyncMock(return_value={"tradeable": True})
    ex._has_pending_order = AsyncMock(return_value=False)
    ex._get_current_price = AsyncMock(return_value=138500)

    # 장중(프랙탈 윈도우)
    fixed_now = datetime(2026, 8, 21, 11, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    monkeypatch.setattr("managers.buy_order_executor.as_kst", lambda: fixed_now)

    # get_db 슬롯 검사 스킵용 빈 세션
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.count.return_value = 0
    fake_db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(
        "managers.buy_order_executor.get_db",
        lambda: iter([fake_db]),
    )

    signal = _signal(strategy="fractal", change_rate=0.58)
    result = await ex._validate_buy_conditions(signal)
    assert result["valid"] is True, result


@pytest.mark.asyncio
async def test_legacy_still_blocks_low_change_rate(monkeypatch):
    """레거시는 최소등락률을 계속 적용한다."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ex = BuyOrderExecutor()
    ex.auto_trade_settings = _settings()
    ex._get_account_info = AsyncMock(return_value=_account())
    ex._check_stock_status = AsyncMock(return_value={"tradeable": True})
    ex._has_pending_order = AsyncMock(return_value=False)
    ex._get_current_price = AsyncMock(return_value=138500)
    ex.kiwoom_api.get_stock_snapshot = AsyncMock(
        return_value={
            "success": True,
            "snapshot": {"current_price": 138500, "change_rate": "0.58"},
        }
    )

    fixed_now = datetime(2026, 8, 21, 11, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    monkeypatch.setattr("managers.buy_order_executor.as_kst", lambda: fixed_now)

    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.count.return_value = 0
    fake_db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(
        "managers.buy_order_executor.get_db",
        lambda: iter([fake_db]),
    )

    signal = _signal(strategy="legacy", change_rate=0.58)
    # legacy source in meta
    signal.additional_data["strategy"] = None
    signal.additional_data["source"] = "screener"
    result = await ex._validate_buy_conditions(signal)
    assert result["valid"] is False
    assert "등락률 미달" in (result.get("reason") or "")
