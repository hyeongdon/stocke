from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import utils.notification_status as notification_status
import utils.notification_registry as notification_registry
from core.models import Base, NotificationDefinition, NotificationDeliveryLog
from notifications.telegram_notifier import TelegramNotifier
from utils.datetime_kst import utc_now_naive


@pytest.fixture
def notification_db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        notification_status,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True),
    )
    yield
    engine.dispose()


def test_notification_job_registration_and_heartbeat_status(notification_db):
    notification_status.register_notification_job(
        "condition_polling",
        "조건식 정기 조회 알림",
        "loop",
        True,
        600,
        ["돌파"],
    )

    job = next(
        job for job in notification_status.list_notification_jobs()
        if job["job_key"] == "condition_polling"
    )
    assert job["status"] == "running"
    assert job["is_running"] is True
    assert job["condition_names"] == ["돌파"]
    assert job["interval_seconds"] == 600

    notification_status.record_notification_result(
        "condition_polling", "no_alert", "조회 완료 · 전송할 종목 없음",
    )
    job = next(
        job for job in notification_status.list_notification_jobs()
        if job["job_key"] == "condition_polling"
    )
    assert job["last_result"] == "no_alert"
    assert job["last_success_at"] is not None

    old_time = utc_now_naive() + timedelta(seconds=notification_status.HEARTBEAT_STALE_SECONDS + 1)
    job = next(
        job for job in notification_status.list_notification_jobs(now=old_time)
        if job["job_key"] == "condition_polling"
    )
    assert job["status"] == "stopped"
    assert job["is_running"] is False


def test_notification_job_error_is_persistent(notification_db):
    notification_status.register_notification_job(
        "condition_realtime", "조건식 실시간 편입 알림", "realtime", True, None, [],
    )
    notification_status.finish_notification_job("condition_realtime", "알림 연결 오류 (로그 확인)")

    job = next(
        job for job in notification_status.list_notification_jobs()
        if job["job_key"] == "condition_realtime"
    )
    assert job["status"] == "error"
    assert job["last_error"] == "알림 연결 오류 (로그 확인)"
    assert job["last_finished_at"] is not None


def test_default_alert_jobs_are_saved_before_first_run(notification_db):
    jobs = notification_status.list_notification_jobs()

    assert {job["job_key"] for job in jobs} == {
        "condition_telegram_polling",
        "condition_telegram_realtime",
    }
    assert all(job["status"] == "stopped" for job in jobs)
    assert all(job["last_started_at"] is None for job in jobs)

    jobs = notification_status.list_notification_jobs()
    assert len(jobs) == 2


def test_common_registry_saves_catalog_and_delivery_history(notification_db, monkeypatch):
    monkeypatch.setattr(
        notification_registry,
        "SessionLocal",
        notification_status.SessionLocal,
    )
    notification_registry.ensure_notification_definitions()
    notification_registry.record_notification_delivery("trade.buy", True)
    notification_registry.record_notification_delivery(
        "trade.buy", False, "Telegram HTTP 500",
    )

    notifications = notification_registry.list_registered_notifications()
    trade_buy = next(item for item in notifications if item["key"] == "trade.buy")
    assert trade_buy["last_status"] == "failed"
    assert trade_buy["attempt_count"] == 2
    assert trade_buy["success_count"] == 1
    assert trade_buy["last_sent_at"] is not None

    history = notification_registry.list_recent_notification_deliveries()
    assert len(history) == 2
    assert {item["status"] for item in history} == {"sent", "failed"}
    with notification_registry.SessionLocal() as db:
        assert db.query(NotificationDefinition).count() >= len(
            notification_registry.DEFAULT_NOTIFICATIONS,
        )
        assert db.query(NotificationDeliveryLog).count() == 2


def test_telegram_notifier_records_delivery_using_common_registry(notification_db, monkeypatch):
    monkeypatch.setattr(
        notification_registry,
        "SessionLocal",
        notification_status.SessionLocal,
    )
    response = SimpleNamespace(
        status_code=200,
        content=b'{"ok":true,"result":{"message_id":123}}',
        json=lambda: {"ok": True, "result": {"message_id": 123}},
    )
    monkeypatch.setattr("notifications.telegram_notifier.requests.post", lambda *args, **kwargs: response)

    sent = TelegramNotifier("test-token", "test-chat").send_message(
        "test",
        notification_key="trade.buy",
    )

    assert sent is True
    with notification_registry.SessionLocal() as db:
        definition = db.get(NotificationDefinition, "trade.buy")
        delivery = db.query(NotificationDeliveryLog).one()
        assert definition.last_status == "sent"
        assert delivery.status == "sent"
