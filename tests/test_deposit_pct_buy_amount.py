"""예수금 비중 매수금액 환산."""
from types import SimpleNamespace

from utils.auto_trade_engine import (
    compute_buy_amount,
    effective_breakout_buy_amount,
    effective_sangtta_buy_amount,
    resolve_buy_amount_won,
)


def test_deposit_pct_resolution():
    s = SimpleNamespace(
        buy_amount_unit="DEPOSIT_PCT",
        sangtta_buy_amount=500_000,
        sangtta_buy_deposit_pct=10,
        breakout_buy_amount=1_000_000,
        breakout_buy_deposit_pct=5,
    )
    assert resolve_buy_amount_won(
        s, amount_won=500_000, deposit_pct=10, deposit=30_000_000
    ) == 3_000_000
    assert effective_sangtta_buy_amount(s, deposit=30_000_000) == 3_000_000
    assert effective_breakout_buy_amount(s, deposit=30_000_000) == 1_500_000


def test_won_unit_ignores_pct():
    s = SimpleNamespace(
        buy_amount_unit="WON",
        sangtta_buy_amount=500_000,
        sangtta_buy_deposit_pct=10,
    )
    assert effective_sangtta_buy_amount(s, deposit=30_000_000) == 500_000


def test_fixed_sizing_with_deposit_pct():
    s = SimpleNamespace(
        buy_amount_unit="DEPOSIT_PCT",
        sizing_method="FIXED",
        initial_min_amount=2_000_000,
        initial_max_amount=5_000_000,
        initial_min_deposit_pct=10,
        initial_max_deposit_pct=10,
        max_invest_amount=5_000_000,
        signal_min_threshold=2,
        signal_max_threshold=10,
        add_buy_amount=1_000_000,
        add_buy_deposit_pct=3,
    )
    assert compute_buy_amount(s, deposit=30_000_000) == 3_000_000
    assert compute_buy_amount(s, is_add_buy=True, deposit=30_000_000) == 900_000
