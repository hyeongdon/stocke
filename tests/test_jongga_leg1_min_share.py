"""종가배팅 분할 1차 금액이 1주 미만이어도 전체 예산 안이면 1주 매수."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from managers.buy_order_executor import BuyOrderExecutor


def _settings(**kwargs) -> SimpleNamespace:
    base = dict(
        jongga_buy_amount=1_000_000,
        jongga_buy_deposit_pct=None,
        jongga_pig_split=True,
        jongga_leg1_pct=25.0,
        jongga_leg2_pct=35.0,
        jongga_leg3_pct=45.0,
        cash_reserve_pct=10.0,
        sizing_method="FIXED",
        initial_min_amount=500_000,
        initial_max_amount=2_000_000,
        max_invest_amount=None,
        add_buy_amount=500_000,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _executor(settings: SimpleNamespace, investable: int = 9_000_000) -> BuyOrderExecutor:
    ex = BuyOrderExecutor()
    ex.auto_trade_settings = settings
    ex._get_account_info = AsyncMock(return_value={
        "deposit": 10_000_000,
        "investable_cash": investable,
    })
    ex._buy_sizing_price = lambda price, _settings: price
    return ex


@pytest.mark.asyncio
async def test_leg1_buys_one_share_when_total_budget_covers():
    ex = _executor(_settings())
    detail: dict = {}
    qty, amount = await ex._calculate_buy_quantity(
        "036930", 261_250, strategy_key="jongga", entry_leg=1, detail=detail,
    )
    assert qty == 1
    assert amount == 261_250
    assert detail["jongga_total"] == 1_000_000


@pytest.mark.asyncio
async def test_leg1_fails_when_total_budget_below_one_share():
    ex = _executor(_settings())
    detail: dict = {}
    qty, amount = await ex._calculate_buy_quantity(
        "036930", 1_200_000, strategy_key="jongga", entry_leg=1, detail=detail,
    )
    assert qty == 0
    assert amount == 238_095
    assert detail["jongga_split"] is True
    assert detail["jongga_leg"] == 1


@pytest.mark.asyncio
async def test_leg1_bump_respects_cash_cap():
    ex = _executor(_settings(), investable=200_000)
    qty, _ = await ex._calculate_buy_quantity(
        "036930", 261_250, strategy_key="jongga", entry_leg=1,
    )
    assert qty == 0


@pytest.mark.asyncio
async def test_leg2_add_buy_not_bumped():
    ex = _executor(_settings())
    qty, amount = await ex._calculate_buy_quantity(
        "036930", 400_000, strategy_key="jongga", entry_leg=2, is_add_buy=True,
    )
    assert qty == 0
    assert amount == 333_333


@pytest.mark.asyncio
async def test_split_off_uses_full_budget():
    ex = _executor(_settings(jongga_pig_split=False))
    qty, _ = await ex._calculate_buy_quantity(
        "036930", 261_250, strategy_key="jongga", entry_leg=1,
    )
    assert qty == 3


@pytest.mark.asyncio
async def test_failure_reason_mentions_split_leg():
    ex = BuyOrderExecutor()
    ex.retry_delay_seconds = 0
    ex.max_retry_attempts = 1
    ex.auto_trade_settings = SimpleNamespace(is_enabled=True)
    signal = SimpleNamespace(
        id=1,
        stock_code="036930",
        stock_name="주성엔지니어링",
        additional_data={
            "current_price": 400_000,
            "source": "jongga_leg2",
            "strategy": "jongga",
            "is_add_buy": True,
            "entry_leg": 2,
            "jongga_entry_leg": 2,
        },
    )

    async def _qty(*_a, detail=None, **_k):
        detail.update(sizing_price=412_000, jongga_split=True, jongga_leg=2, jongga_total=1_000_000)
        return 0, 333_333

    ex._update_signal_status = AsyncMock()
    ex._defer_transient_failure = AsyncMock()
    ex._validate_buy_conditions = AsyncMock(return_value={"valid": True})
    ex._get_current_price = AsyncMock(return_value=400_000)
    ex._calculate_buy_quantity = _qty
    ex._execute_buy_order_with_retry = AsyncMock()

    with patch("managers.buy_order_executor.log_activity"):
        await ex._process_single_signal(signal)

    ex._execute_buy_order_with_retry.assert_not_awaited()
    args = ex._update_signal_status.await_args.args
    assert args[1] == "FAILED"
    assert args[2].startswith("2차 분할 예산 부족")
    assert "고가주" not in args[2]
    assert "현재가 400,000원+시장가 여유" in args[2]
