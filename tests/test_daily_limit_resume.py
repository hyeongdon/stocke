"""일일 손익 한도 완화 시 자동매매 재개 판정."""
from utils.auto_trade_engine import should_resume_after_daily_limit_change


def test_resume_when_loss_limit_raised_past_pnl():
    assert should_resume_after_daily_limit_change(
        old_loss_limit=-200_000,
        old_profit_target=150_000,
        new_loss_limit=-300_000,
        new_profit_target=150_000,
        pnl=-200_000,
        currently_enabled=False,
    )


def test_no_resume_when_still_over_new_limit():
    assert not should_resume_after_daily_limit_change(
        old_loss_limit=-200_000,
        old_profit_target=150_000,
        new_loss_limit=-250_000,
        new_profit_target=150_000,
        pnl=-280_000,
        currently_enabled=False,
    )


def test_no_resume_when_manual_off_and_pnl_within_old_limit():
    """수동 OFF 후 한도만 바꾼 경우 — 이전 한도로도 미도달이면 재개하지 않음."""
    assert not should_resume_after_daily_limit_change(
        old_loss_limit=-200_000,
        old_profit_target=150_000,
        new_loss_limit=-300_000,
        new_profit_target=150_000,
        pnl=-50_000,
        currently_enabled=False,
    )


def test_no_resume_when_already_enabled():
    assert not should_resume_after_daily_limit_change(
        old_loss_limit=-200_000,
        old_profit_target=150_000,
        new_loss_limit=-300_000,
        new_profit_target=150_000,
        pnl=-200_000,
        currently_enabled=True,
    )


def test_resume_stale_off_same_limit_with_significant_loss():
    """한도는 이미 -34만인데 OFF만 남은 잔존 상태."""
    assert should_resume_after_daily_limit_change(
        old_loss_limit=-340_000,
        old_profit_target=1_000_000,
        new_loss_limit=-340_000,
        new_profit_target=1_000_000,
        pnl=-230_655,
        currently_enabled=False,
    )


def test_no_resume_same_limit_small_loss_manual_off():
    """소액 손실 + 동일 한도 재저장 = 수동 OFF로 보고 재개하지 않음."""
    assert not should_resume_after_daily_limit_change(
        old_loss_limit=-340_000,
        old_profit_target=1_000_000,
        new_loss_limit=-340_000,
        new_profit_target=1_000_000,
        pnl=-10_000,
        currently_enabled=False,
    )


def test_resume_when_profit_target_raised():
    assert should_resume_after_daily_limit_change(
        old_loss_limit=-200_000,
        old_profit_target=150_000,
        new_loss_limit=-200_000,
        new_profit_target=300_000,
        pnl=150_000,
        currently_enabled=False,
    )


def test_resume_with_pending_halt_flag():
    from utils.auto_trade_engine import (
        clear_daily_limit_halt,
        mark_daily_limit_halt,
    )

    clear_daily_limit_halt()
    mark_daily_limit_halt("일일 손실 한도 도달: -200,000원")
    try:
        assert should_resume_after_daily_limit_change(
            old_loss_limit=-340_000,
            old_profit_target=1_000_000,
            new_loss_limit=-340_000,
            new_profit_target=1_000_000,
            pnl=-50_000,  # 플래그가 있으면 소액이어도 재개
            currently_enabled=False,
        )
    finally:
        clear_daily_limit_halt()

