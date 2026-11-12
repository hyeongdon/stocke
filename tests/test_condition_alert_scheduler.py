import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import utils.condition_alert_scheduler as condition_alert_scheduler
from core.models import Base, ConditionAlertConfig


@pytest.fixture
def condition_alert_db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        condition_alert_scheduler,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True),
    )
    yield
    engine.dispose()


def test_condition_alert_config_persists_selected_conditions_and_schedule(condition_alert_db):
    saved = condition_alert_scheduler.save_condition_alert_config(
        None,
        "오전 돌파",
        ["돌파", "거래량", "돌파"],
        300,
        True,
        False,
    )

    assert saved["name"] == "오전 돌파"
    assert saved["condition_names"] == ["돌파", "거래량"]
    assert saved["interval_seconds"] == 300
    assert saved["is_intraday"] is False
    assert saved["is_enabled"] is True
    assert saved["next_run_at"] is not None

    updated = condition_alert_scheduler.save_condition_alert_config(
        saved["id"],
        "오전 돌파",
        ["돌파"],
        900,
        False,
        False,
    )
    assert updated["condition_names"] == ["돌파"]
    assert updated["interval_seconds"] == 900
    assert updated["is_enabled"] is False


@pytest.mark.parametrize("interval", [7200, 10800, 18000])
def test_intraday_condition_alert_accepts_extended_intervals(condition_alert_db, interval):
    saved = condition_alert_scheduler.save_condition_alert_config(
        None,
        "장중 알림",
        ["돌파"],
        interval,
        True,
        True,
    )

    assert saved["interval_seconds"] == interval
    assert saved["is_intraday"] is True
    assert saved["is_enabled"] is True


def test_condition_alert_config_rejects_empty_conditions_and_unknown_interval(condition_alert_db):
    with pytest.raises(ValueError, match="조건식을 하나 이상"):
        condition_alert_scheduler.save_condition_alert_config(
            None, "빈 조건식", [], 300, False,
        )

    with pytest.raises(ValueError, match="지원하지 않는"):
        condition_alert_scheduler.save_condition_alert_config(
            None, "잘못된 주기", ["돌파"], 120, False, False,
        )

    with pytest.raises(ValueError, match="지원하지 않는"):
        condition_alert_scheduler.save_condition_alert_config(
            None, "장중 5분", ["돌파"], 300, False, True,
        )


def test_run_config_uses_saved_conditions_and_persists_result(condition_alert_db, monkeypatch):
    config = condition_alert_scheduler.save_condition_alert_config(
        None,
        "돌파 알림",
        ["돌파"],
        600,
        False,
    )
    calls = {}

    async def send_alert(api, notifier, **kwargs):
        calls.update(kwargs)
        return {"sent": True, "condition_count": 1, "stock_count": 2}

    monkeypatch.setattr(condition_alert_scheduler, "send_condition_alert", send_alert)

    result = asyncio.run(
        condition_alert_scheduler.ConditionAlertScheduler(object()).run_config(config["id"]),
    )

    assert result == {
        "success": True,
        "result": "sent",
        "message": "조건식 1개 · 종목 2개 전송 완료",
    }
    assert calls["names"] == ["돌파"]
    assert calls["alert_name"] == "돌파 알림"
    assert calls["skip_market_hours_check"] is True
    with condition_alert_scheduler.SessionLocal() as db:
        saved = db.get(ConditionAlertConfig, config["id"])
        assert saved.last_result == "sent"
        assert saved.last_run_at is not None
        assert saved.last_message == result["message"]


def test_intraday_alert_only_runs_during_configured_market_window(monkeypatch):
    monkeypatch.setattr(
        condition_alert_scheduler,
        "is_krx_trading_day",
        lambda now: now.weekday() < 5,
    )
    config = {
        "is_intraday": True,
        "interval_seconds": 600,
        "last_run_at": None,
        "next_run_at": None,
    }

    assert condition_alert_scheduler._is_due(config, condition_alert_scheduler.datetime(2026, 1, 5, 0, 0))
    config["last_run_at"] = "2026-01-05T00:00:00Z"
    assert not condition_alert_scheduler._is_due(
        config, condition_alert_scheduler.datetime(2026, 1, 5, 0, 5),
    )
    assert condition_alert_scheduler._is_due(
        config, condition_alert_scheduler.datetime(2026, 1, 5, 0, 10),
    )
    assert not condition_alert_scheduler._is_due(
        config, condition_alert_scheduler.datetime(2026, 1, 5, 11, 1),
    )
    assert not condition_alert_scheduler._is_due(
        config, condition_alert_scheduler.datetime(2026, 1, 10, 0, 10),
    )


def test_intraday_alert_schedule_is_aligned_to_market_open(monkeypatch):
    from utils.datetime_kst import KST

    monkeypatch.setattr(condition_alert_scheduler, "is_krx_trading_day", lambda _now: True)
    now = condition_alert_scheduler.datetime(2026, 1, 5, 9, 5, tzinfo=KST)
    first_slot = condition_alert_scheduler._next_intraday_run(600, None, now)
    assert first_slot == condition_alert_scheduler.datetime(2026, 1, 5, 0, 0)

    next_slot = condition_alert_scheduler._next_intraday_run(600, first_slot, now)
    assert next_slot == condition_alert_scheduler.datetime(2026, 1, 5, 0, 10)
