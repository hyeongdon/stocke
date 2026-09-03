"""매도가능수량 부족(800033)·미체결 잠금·부분체결 보호."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from managers.stop_loss_manager import (
    StopLossManager,
    effective_sellable_qty,
    is_sell_qty_shortage_error,
    is_unfilled_sell_side,
)


def test_detects_kiwoom_800033_message():
    msg = "[2000](800033:모의투자 매도가능수량이 부족합니다.)"
    assert is_sell_qty_shortage_error(msg) is True


def test_detects_plain_shortage_text():
    assert is_sell_qty_shortage_error("매도가능수량이 부족합니다") is True


def test_ignores_unrelated_errors():
    assert is_sell_qty_shortage_error("토큰 없음") is False
    assert is_sell_qty_shortage_error("") is False
    assert is_sell_qty_shortage_error(None) is False


def test_effective_sellable_uses_field_when_present():
    assert effective_sellable_qty(500, sellable_field=120, locked_qty=400) == 120


def test_effective_sellable_falls_back_to_qty_minus_locked():
    assert effective_sellable_qty(500, sellable_field=None, locked_qty=400) == 100
    assert effective_sellable_qty(100, sellable_field=None, locked_qty=200) == 0


def test_is_unfilled_sell_side():
    assert is_unfilled_sell_side({"io_tp_nm": "매도", "trde_tp": "1"}) is True
    assert is_unfilled_sell_side({"io_tp_nm": "매수", "trde_tp": "2"}) is False


def test_open_sell_locked_qty_sums_oso():
    items = [
        {"stk_cd": "036090", "oso_qty": 200, "io_tp_nm": "매도", "trde_tp": "1"},
        {"stk_cd": "036090", "oso_qty": 100, "io_tp_nm": "매도", "trde_tp": "1"},
        {"stk_cd": "070300", "oso_qty": 50, "io_tp_nm": "매도", "trde_tp": "1"},
    ]
    assert StopLossManager._open_sell_locked_qty("036090", items) == 300


@pytest.mark.asyncio
async def test_fetch_account_qty_forces_fresh_balance():
    manager = StopLossManager()
    manager.kiwoom_api.get_account_balance = AsyncMock(
        return_value={"holdings": [{"stock_code": "A015230", "quantity": "210"}]},
    )
    manager._holdings_qty_map = Mock(return_value={"015230": 210})

    qty = await manager._fetch_account_qty("015230", force_refresh=True)

    assert qty == 210
    assert manager.kiwoom_api.get_account_balance.await_args.kwargs["force_refresh"] is True


@pytest.mark.asyncio
async def test_fetch_account_qty_rejects_stale_refresh_result():
    manager = StopLossManager()
    manager.kiwoom_api.get_account_balance = AsyncMock(
        return_value={"_cached": True, "_stale": True},
    )

    qty = await manager._fetch_account_qty("015230", force_refresh=True)

    assert qty is None


@pytest.mark.asyncio
async def test_execute_sell_skips_when_unfilled_lock():
    manager = StopLossManager()
    position = SimpleNamespace(
        id=128,
        stock_code="036090",
        stock_name="위지트",
        buy_quantity=509,
        current_price=1400,
    )
    manager._has_any_pending_sell_order = AsyncMock(return_value=False)
    manager.kiwoom_api.get_account_balance = AsyncMock(
        return_value={
            "stk_acnt_evlt_prst": [
                {"stk_cd": "036090", "qty": "509", "sellable_qty": "0"},
            ],
        },
    )
    manager._fetch_unfilled_sells = AsyncMock(
        return_value=(
            [{"stk_cd": "036090", "oso_qty": 509, "io_tp_nm": "매도", "trde_tp": "1"}],
            True,
        ),
    )
    manager.kiwoom_api.place_sell_order = AsyncMock()
    manager._create_sell_order = AsyncMock(return_value=1)

    await manager._execute_sell_order(position, 1400, "MARKET_CLOSE", "test")

    manager.kiwoom_api.place_sell_order.assert_not_called()
    manager._create_sell_order.assert_not_called()


@pytest.mark.asyncio
async def test_execute_sell_skips_when_db_pending_exists():
    manager = StopLossManager()
    position = SimpleNamespace(
        id=128,
        stock_code="036090",
        stock_name="위지트",
        buy_quantity=500,
        current_price=1400,
    )
    manager._has_any_pending_sell_order = AsyncMock(return_value=True)
    manager.kiwoom_api.place_sell_order = AsyncMock()

    await manager._execute_sell_order(position, 1400, "MARKET_CLOSE", "test")

    manager.kiwoom_api.place_sell_order.assert_not_called()


@pytest.mark.asyncio
async def test_recover_800033_skips_retry_when_sellable_zero():
    manager = StopLossManager()
    position = SimpleNamespace(
        id=128,
        stock_code="036090",
        stock_name="위지트",
        buy_quantity=509,
        current_price=1400,
    )
    manager.kiwoom_api.get_account_balance = AsyncMock(
        return_value={
            "stk_acnt_evlt_prst": [
                {"stk_cd": "036090", "qty": "509", "sellable_qty": "0"},
            ],
        },
    )
    manager._fetch_unfilled_sells = AsyncMock(
        return_value=(
            [{"stk_cd": "036090", "oso_qty": 509, "io_tp_nm": "매도", "trde_tp": "1"}],
            True,
        ),
    )
    manager.kiwoom_api.place_sell_order = AsyncMock()
    manager._update_sell_order_status = AsyncMock()

    # get_db path for qty sync — avoid real DB
    from managers import stop_loss_manager as slm

    original_get_db = slm.get_db

    def _fake_get_db():
        session = Mock()
        session.query.return_value.filter.return_value.first.return_value = None
        yield session

    slm.get_db = _fake_get_db
    try:
        ok = await manager._recover_sell_after_qty_shortage(
            position,
            sell_order_id=99,
            sell_reason="MARKET_CLOSE",
            error_msg="[2000](800033:모의투자 매도가능수량이 부족합니다.)",
            requested_qty=509,
            sell_price=1400,
        )
    finally:
        slm.get_db = original_get_db

    assert ok is True
    manager.kiwoom_api.place_sell_order.assert_not_called()
    manager._update_sell_order_status.assert_awaited()
    assert manager._update_sell_order_status.await_args.args[1] == "FAILED"


@pytest.mark.asyncio
async def test_cancel_inferior_keeps_ordered_when_broker_cancel_fails():
    manager = StopLossManager()
    sell = SimpleNamespace(
        id=10,
        status="ORDERED",
        sell_reason="STOP_LOSS",
        sell_order_id="0142101",
        stock_name="위지트",
        stock_code="036090",
        sell_quantity=500,
    )
    session = Mock()
    session.query.return_value.filter.return_value.all.return_value = [sell]
    manager._broker_cancel_sell = AsyncMock(return_value=False)

    n = await manager._cancel_inferior_sell_orders(session, 128, "MARKET_CLOSE")

    assert n == 0
    assert sell.status == "ORDERED"


@pytest.mark.asyncio
async def test_cancel_inferior_marks_cancelled_after_broker_ok():
    manager = StopLossManager()
    sell = SimpleNamespace(
        id=10,
        status="ORDERED",
        sell_reason="STOP_LOSS",
        sell_order_id="0142101",
        stock_name="위지트",
        stock_code="036090",
        sell_quantity=500,
    )
    session = Mock()
    session.query.return_value.filter.return_value.all.return_value = [sell]
    manager._broker_cancel_sell = AsyncMock(return_value=True)

    n = await manager._cancel_inferior_sell_orders(session, 128, "MARKET_CLOSE")

    assert n == 1
    assert sell.status == "CANCELLED"


@pytest.mark.asyncio
async def test_map_holding_preserves_sellable_qty():
    from api.kiwoom_api import KiwoomAPI

    api = KiwoomAPI.__new__(KiwoomAPI)
    parsed = api._parse_account_balance_safe(
        {
            "stk_acnt_evlt_prst": [
                {
                    "stk_cd": "036090",
                    "stk_nm": "위지트",
                    "rmnd_qty": "509",
                    "clrn_alow_qty": "0",
                    "pur_amt": "0",
                    "evlt_amt": "0",
                    "pl_amt": "0",
                    "pl_rt": "0",
                    "cur_prc": "1400",
                    "avg_prc": "1433",
                }
            ],
        }
    )
    row = parsed["stk_acnt_evlt_prst"][0]
    assert row["qty"] == "509"
    assert row["sellable_qty"] == "0"
