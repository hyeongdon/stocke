"""레거시 매수 시간창은 trade_start_time을 단독으로 본다 (다른 전략 창에 편승하지 않음)."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from utils.auto_trade_engine import allows_strategy_new_buy

KST = ZoneInfo("Asia/Seoul")


def _settings(**kwargs):
    base = dict(
        trade_start_time="09:50",
        trade_end_time="15:20",
        sangtta_trade_start_time="09:30",
        sangtta_trade_end_time="15:00",
        breakout_trade_start_time="09:00",
        breakout_trade_end_time="15:20",
        liquidate_before_close=False,
        use_breakout=True,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_legacy_blocked_before_start_even_if_breakout_open():
    settings = _settings()
    now = datetime(2026, 8, 5, 9, 30, tzinfo=KST)
    ok, reason = allows_strategy_new_buy(settings, "legacy", now)
    assert ok is False
    assert reason and "레거시 시간대 외" in reason


def test_legacy_allowed_after_start():
    settings = _settings()
    now = datetime(2026, 8, 5, 9, 50, tzinfo=KST)
    ok, reason = allows_strategy_new_buy(settings, "legacy", now)
    assert ok is True
    assert reason is None


def test_breakout_still_allowed_before_legacy_start():
    settings = _settings()
    now = datetime(2026, 8, 5, 9, 30, tzinfo=KST)
    ok, reason = allows_strategy_new_buy(settings, "breakout", now)
    assert ok is True
    assert reason is None


def test_jongga_add_buy_allowed_at_open_avg_window():
    settings = _settings(
        jongga_trade_start_time="14:30",
        jongga_pig_split=True,
        jongga_leg3_end_time="15:28",
        use_jongga=True,
    )
    now = datetime(2026, 8, 5, 9, 3, tzinfo=KST)
    ok, _ = allows_strategy_new_buy(settings, "jongga", now, is_add_buy=True)
    assert ok is True
    ok2, reason = allows_strategy_new_buy(settings, "jongga", now, is_add_buy=False)
    assert ok2 is False
    assert reason and "종가배팅 시간대 외" in reason


def test_legacy_blocked_when_strategy_off():
    settings = _settings(use_legacy=False)
    now = datetime(2026, 8, 5, 11, 0, tzinfo=KST)
    ok, reason = allows_strategy_new_buy(settings, "legacy", now)
    assert ok is False
    assert reason and "레거시 전략 OFF" in reason


def test_sangtta_blocked_when_strategy_off():
    settings = _settings(use_sangtta=False)
    now = datetime(2026, 8, 5, 10, 0, tzinfo=KST)
    ok, reason = allows_strategy_new_buy(settings, "sangtta", now)
    assert ok is False
    assert reason and "상따 전략 OFF" in reason


def test_linked_session_excludes_disabled_legacy_sangtta():
    from utils.market_hours import any_strategy_buy_window_open, _strategy_windows

    settings = SimpleNamespace(
        use_legacy=False,
        use_sangtta=False,
        use_breakout=False,
        breakout_condition_names="",
        use_jongga=False,
        use_fractal=False,
        fractal_condition_names="",
        use_ma1592=True,
        ma1592_trade_start_time="09:10",
        ma1592_trade_end_time="15:15",
        trade_start_time="10:00",
        trade_end_time="15:20",
        sangtta_trade_start_time="09:05",
        sangtta_trade_end_time="11:00",
        liquidate_before_close=False,
    )
    labels = [w[0] for w in _strategy_windows(settings)]
    assert labels == ["15/92"]
    assert any_strategy_buy_window_open(settings, datetime(2026, 8, 5, 10, 0, tzinfo=KST)) is True
    assert any_strategy_buy_window_open(settings, datetime(2026, 8, 5, 9, 0, tzinfo=KST)) is False


def test_linked_session_includes_jongga_open_avg_window():
    from utils.market_hours import in_linked_trading_session

    settings = SimpleNamespace(
        use_jongga=True,
        jongga_pig_split=True,
        jongga_trade_start_time="14:30",
        jongga_leg3_end_time="15:28",
        jongga_pick_end_time="14:40",
        trade_start_time="10:00",
        trade_end_time="15:20",
        sangtta_trade_start_time="09:05",
        sangtta_trade_end_time="11:00",
        use_breakout=False,
        breakout_condition_names="",
        use_fractal=False,
        fractal_condition_names="",
        liquidate_before_close=False,
    )
    assert in_linked_trading_session(settings, datetime(2026, 8, 5, 8, 59, tzinfo=KST)) is False
    assert in_linked_trading_session(settings, datetime(2026, 8, 5, 9, 0, tzinfo=KST)) is True
    assert in_linked_trading_session(settings, datetime(2026, 8, 5, 9, 10, tzinfo=KST)) is True
