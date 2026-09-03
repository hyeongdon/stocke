"""매수 실행기 — 잔고 일시 장애 재시도/보류 방어 로직."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from managers.buy_order_executor import BuyOrderExecutor, _MAX_TRANSIENT_DEFER


@pytest.mark.asyncio
async def test_get_account_info_rejects_error_payload():
    ex = BuyOrderExecutor()
    ex.kiwoom_api.get_account_balance = AsyncMock(
        return_value={"_error": "rate_limited", "_error_msg": "API 호출 제한 또는 슬롯 대기 초과"}
    )
    with patch("managers.buy_order_executor.Config") as cfg:
        cfg.KIWOOM_USE_MOCK_ACCOUNT = True
        cfg.KIWOOM_MOCK_ACCOUNT_NUMBER = "81312582"
        cfg.KIWOOM_ACCOUNT_NUMBER = ""
        info = await ex._get_account_info()
    assert info is None


@pytest.mark.asyncio
async def test_get_account_info_parses_deposit():
    ex = BuyOrderExecutor()
    ex.auto_trade_settings = SimpleNamespace(cash_reserve_pct=10.0)
    ex.kiwoom_api.get_account_balance = AsyncMock(
        return_value={"entr": "30000000", "d2_entra": "30000000"}
    )
    with patch("managers.buy_order_executor.Config") as cfg:
        cfg.KIWOOM_USE_MOCK_ACCOUNT = True
        cfg.KIWOOM_MOCK_ACCOUNT_NUMBER = "81312582"
        cfg.KIWOOM_ACCOUNT_NUMBER = ""
        info = await ex._get_account_info()
    assert info is not None
    assert info["deposit"] == 30_000_000
    assert info["investable_cash"] == 27_000_000
    # 매수 경로는 HIGH 우선순위로 잔고 조회 (스캐너에 밀리지 않음)
    call_kwargs = ex.kiwoom_api.get_account_balance.await_args.kwargs
    assert call_kwargs.get("max_wait") == 25.0
    from utils.api_traffic_guard import APIPriority
    assert call_kwargs.get("priority") == APIPriority.HIGH


@pytest.mark.asyncio
async def test_process_retries_then_defers_on_account_error():
    ex = BuyOrderExecutor()
    ex.retry_delay_seconds = 0
    ex.max_retry_attempts = 3
    ex.auto_trade_settings = SimpleNamespace(
        cash_reserve_pct=10.0,
        is_enabled=True,
    )
    signal = SimpleNamespace(
        id=42,
        stock_code="119850",
        stock_name="지엔씨에너지",
        additional_data={"strategy": "legacy", "change_rate": 8.0},
    )

    ex._update_signal_status = AsyncMock()
    ex._defer_transient_failure = AsyncMock()
    ex._validate_buy_conditions = AsyncMock(
        return_value={"valid": False, "reason": "계좌 정보 조회 실패", "retryable": True}
    )
    ex._get_current_price = AsyncMock(return_value=49300)

    with patch("managers.buy_order_executor.log_activity"):
        await ex._process_single_signal(signal)

    assert ex._validate_buy_conditions.await_count == 3
    ex._defer_transient_failure.assert_awaited_once()
    assert ex._defer_transient_failure.await_args.args[0] == 42
    assert "계좌 정보" in ex._defer_transient_failure.await_args.args[1]
    failed_calls = [
        c for c in ex._update_signal_status.await_args_list
        if len(c.args) >= 2 and c.args[1] == "FAILED"
    ]
    assert failed_calls == []


@pytest.mark.asyncio
async def test_defer_keeps_pending_until_limit():
    ex = BuyOrderExecutor()
    signal = MagicMock()
    signal.id = 7
    signal.stock_code = "119850"
    signal.additional_data = {"transient_defer_count": _MAX_TRANSIENT_DEFER - 1}
    signal.status = "PROCESSING"
    signal.failure_reason = None

    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = signal

    with patch("managers.buy_order_executor.get_db", return_value=iter([session])):
        with patch("managers.buy_order_executor.log_activity"):
            await ex._defer_transient_failure(7, "계좌 정보 조회 실패")

    assert signal.status == "PENDING"
    assert signal.additional_data["transient_defer_count"] == _MAX_TRANSIENT_DEFER
    session.commit.assert_called()


@pytest.mark.asyncio
async def test_defer_fails_after_limit():
    ex = BuyOrderExecutor()
    signal = MagicMock()
    signal.id = 8
    signal.stock_code = "119850"
    signal.additional_data = {"transient_defer_count": _MAX_TRANSIENT_DEFER}
    signal.status = "PROCESSING"
    signal.failure_reason = None

    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = signal

    with patch("managers.buy_order_executor.get_db", return_value=iter([session])):
        with patch("managers.buy_order_executor.log_activity"):
            await ex._defer_transient_failure(8, "계좌 정보 조회 실패")

    assert signal.status == "FAILED"
    assert "계좌 정보" in signal.failure_reason


@pytest.mark.asyncio
async def test_add_buy_does_not_unbound_strategy_key():
    """피라미딩 추가매수 시 strategy_key UnboundLocalError 회귀 방지 (키다리스튜디오 020120)."""
    ex = BuyOrderExecutor()
    ex.retry_delay_seconds = 0
    ex.max_retry_attempts = 1
    ex.auto_trade_settings = SimpleNamespace(is_enabled=True)
    signal = SimpleNamespace(
        id=1108,
        stock_code="020120",
        stock_name="키다리스튜디오",
        additional_data={
            "current_price": 6280,
            "change_rate": 13.15,
            "source": "pyramiding_add",
            "is_add_buy": True,
            "strategy": "breakout",
        },
    )

    ex._update_signal_status = AsyncMock()
    ex._defer_transient_failure = AsyncMock()
    ex._clear_transient_defer_meta = AsyncMock()
    ex._validate_buy_conditions = AsyncMock(return_value={"valid": True})
    ex._get_current_price = AsyncMock(return_value=6280)
    ex._calculate_buy_quantity = AsyncMock(return_value=(10, 62800))
    ex._execute_buy_order_with_retry = AsyncMock()

    with patch("managers.buy_order_executor.log_activity"):
        await ex._process_single_signal(signal)

    failed = [
        c for c in ex._update_signal_status.await_args_list
        if len(c.args) >= 2 and c.args[1] == "FAILED"
    ]
    assert failed == [], f"unexpected FAILED: {failed}"
    ex._execute_buy_order_with_retry.assert_awaited_once()
