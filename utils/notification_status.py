"""Persistent registration and runtime status for Telegram alert processes."""
import os
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.config import Config
from core.models import NotificationJobStatus, SessionLocal
from utils.datetime_kst import utc_now_naive, utc_naive_to_api_iso

HEARTBEAT_STALE_SECONDS = 75


def _ensure_default_jobs() -> None:
    jobs = [
        {
            "job_key": "condition_telegram_polling",
            "display_name": "조건식 정기 조회 알림",
            "mode": "polling",
            "is_continuous": False,
            "interval_seconds": None,
            "condition_names": list(Config.TELEGRAM_ALERT_CONDITION_NAMES),
            "status": "stopped",
            "last_message": "아직 실행 기록이 없습니다. 알림 스크립트를 실행하면 상태가 기록됩니다.",
        },
        {
            "job_key": "condition_telegram_realtime",
            "display_name": "조건식 실시간 편입 알림",
            "mode": "realtime",
            "is_continuous": True,
            "interval_seconds": None,
            "condition_names": list(Config.TELEGRAM_ALERT_CONDITION_NAMES),
            "status": "stopped",
            "last_message": "아직 실행 기록이 없습니다. 알림 스크립트를 실행하면 상태가 기록됩니다.",
        },
    ]
    with SessionLocal() as db:
        dialect = db.get_bind().dialect.name
        if dialect == "postgresql":
            statement = postgresql_insert(NotificationJobStatus).values(jobs)
        elif dialect == "sqlite":
            statement = sqlite_insert(NotificationJobStatus).values(jobs)
        else:
            raise RuntimeError(f"지원하지 않는 알림 상태 DB: {dialect}")
        db.execute(statement.on_conflict_do_nothing(index_elements=["job_key"]))
        db.commit()


def register_notification_job(
    job_key: str,
    display_name: str,
    mode: str,
    is_continuous: bool,
    interval_seconds: Optional[int],
    condition_names: List[str],
) -> None:
    now = utc_now_naive()
    with SessionLocal() as db:
        job = db.get(NotificationJobStatus, job_key)
        if job is None:
            job = NotificationJobStatus(job_key=job_key)
            db.add(job)
        job.display_name = display_name
        job.mode = mode
        job.is_continuous = is_continuous
        job.interval_seconds = interval_seconds
        job.condition_names = condition_names
        job.status = "running"
        job.process_id = os.getpid()
        job.last_started_at = now
        job.last_heartbeat_at = now
        job.last_error = None
        db.commit()


def heartbeat_notification_job(job_key: str) -> None:
    with SessionLocal() as db:
        job = db.get(NotificationJobStatus, job_key)
        if job is None:
            raise RuntimeError(f"알림 작업이 등록되지 않았습니다: {job_key}")
        job.last_heartbeat_at = utc_now_naive()
        db.commit()


def record_notification_result(
    job_key: str,
    result: str,
    message: str,
    error: Optional[str] = None,
) -> None:
    with SessionLocal() as db:
        job = db.get(NotificationJobStatus, job_key)
        if job is None:
            raise RuntimeError(f"알림 작업이 등록되지 않았습니다: {job_key}")
        now = utc_now_naive()
        job.last_result = result
        job.last_message = message
        job.last_error = error
        if result in {"sent", "no_alert"}:
            job.last_success_at = now
        db.commit()


def finish_notification_job(job_key: str, error: Optional[str] = None) -> None:
    with SessionLocal() as db:
        job = db.get(NotificationJobStatus, job_key)
        if job is None:
            raise RuntimeError(f"알림 작업이 등록되지 않았습니다: {job_key}")
        now = utc_now_naive()
        job.status = "error" if error else "stopped"
        job.last_heartbeat_at = now
        job.last_finished_at = now
        if error:
            job.last_result = "failed"
            job.last_message = "알림 프로세스 종료 오류"
            job.last_error = error
        db.commit()


def list_notification_jobs(now: Optional[datetime] = None) -> List[Dict]:
    _ensure_default_jobs()
    checked_at = now or utc_now_naive()
    with SessionLocal() as db:
        jobs = (
            db.query(NotificationJobStatus)
            .order_by(NotificationJobStatus.display_name, NotificationJobStatus.job_key)
            .all()
        )
        results = []
        for job in jobs:
            age = (
                (checked_at - job.last_heartbeat_at).total_seconds()
                if job.last_heartbeat_at
                else None
            )
            is_running = (
                job.status == "running"
                and age is not None
                and -30 <= age <= HEARTBEAT_STALE_SECONDS
            )
            status = "running" if is_running else (
                "error" if job.status == "error" else "stopped"
            )
            results.append({
                "job_key": job.job_key,
                "name": job.display_name,
                "mode": job.mode,
                "is_continuous": job.is_continuous,
                "interval_seconds": job.interval_seconds,
                "condition_names": job.condition_names or [],
                "status": status,
                "is_running": is_running,
                "process_id": job.process_id,
                "last_started_at": utc_naive_to_api_iso(job.last_started_at),
                "last_heartbeat_at": utc_naive_to_api_iso(job.last_heartbeat_at),
                "last_finished_at": utc_naive_to_api_iso(job.last_finished_at),
                "last_success_at": utc_naive_to_api_iso(job.last_success_at),
                "last_result": job.last_result,
                "last_message": job.last_message,
                "last_error": job.last_error,
            })
        return results
