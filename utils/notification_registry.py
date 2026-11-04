"""Shared Telegram notification catalog and delivery history."""
import logging
import os
from typing import Dict, List, Optional

from sqlalchemy import desc, select

from core.models import NotificationDefinition, NotificationDeliveryLog, SessionLocal
from utils.datetime_kst import utc_now_naive, utc_naive_to_api_iso

logger = logging.getLogger(__name__)

DEFAULT_NOTIFICATIONS = (
    ("trade.buy", "매수 체결", "trade", "event", "매수 체결 시", "매매 엔진"),
    ("trade.sell", "매도 체결", "trade", "event", "매도 체결 시", "매매 엔진"),
    ("trade.slot_blocked", "매수 차단", "trade", "event", "매수 슬롯 부족 시", "매매 엔진"),
    ("system.mock_reset", "모의 계좌 초기화", "system", "event", "모의 계좌 초기화 시", "수동/배치"),
    ("condition.polling", "조건식 정기 조회", "condition", "schedule", "실행 스크립트/수동 실행 시", "Windows 작업 또는 수동"),
    ("condition.realtime", "조건식 실시간 편입", "condition", "continuous", "실시간 감시 프로세스 실행 중", "Windows 작업 또는 수동"),
    ("server.start", "서버 기동", "system", "schedule", "서버 기동 확인 시 (평일 08:30~08:50 감시)", "Windows 작업 스케줄러"),
    ("deployment.sync", "배포 동기화", "system", "event", "배포 동기화 완료/실패 시", "배포 스크립트"),
    ("system.cpu", "CPU 과부하", "system", "event", "CPU 임계치 초과 시", "시스템 감시"),
    ("stock_news.start", "종목 뉴스 배치 시작", "batch", "schedule", "작업 스케줄러 실행 시 (현재 비활성)", "Windows 작업 스케줄러"),
    ("stock_news.done", "종목 뉴스 배치 완료", "batch", "schedule", "뉴스 배치 완료 시 (현재 비활성)", "Windows 작업 스케줄러"),
    ("stock_news.error", "종목 뉴스 배치 오류", "batch", "schedule", "뉴스 배치 오류 시 (현재 비활성)", "Windows 작업 스케줄러"),
    ("daily_trade_journal", "일일 매매 일지", "batch", "schedule", "평일 19:52", "Windows 작업 스케줄러: stocke-daily-trade-journal"),
    ("failed_buy_signals", "매수 실패 신호", "batch", "schedule", "배치 작업 실행 시", "Windows 작업 스케줄러/수동"),
    ("jongga.candidates", "종가배팅 후보", "batch", "event", "후보 목록 생성 시", "종가배팅 배치"),
    ("kiwoom.pnl_sync", "키움 손익 동기화", "batch", "schedule", "평일 19:50", "Windows 작업 스케줄러: stocke-kiwoom-db-pnl-sync"),
    ("trade_industry.start", "수출입 지표 배치 시작", "batch", "schedule", "10:00 (반복 설정 확인 필요)", "Windows 작업 스케줄러: stocke-trade-industry-batch"),
    ("trade_industry.done", "수출입 지표 배치 완료", "batch", "schedule", "배치 완료 시", "Windows 작업 스케줄러: stocke-trade-industry-batch"),
    ("trade_industry.error", "수출입 지표 배치 오류", "batch", "schedule", "배치 오류 시", "Windows 작업 스케줄러: stocke-trade-industry-batch"),
    ("theme.report", "테마 배치 리포트", "batch", "schedule", "매일 22:00", "Windows 작업 스케줄러: stocke-theme-mart-batch"),
    ("theme_mart.start", "테마/키워드 배치 시작", "batch", "schedule", "매일 22:00", "Windows 작업 스케줄러: stocke-theme-mart-batch"),
    ("theme_mart.error", "테마/키워드 배치 오류", "batch", "schedule", "배치 오류 시", "Windows 작업 스케줄러"),
    ("rwm_bowl", "RWM 전략 알림", "strategy", "event", "전략 이벤트 발생 시", "RWM 스케줄러"),
    ("rsi_ladder", "분할매수 전략 알림", "strategy", "event", "전략/사용자 확인 이벤트 시", "분할매수 스케줄러"),
    ("condition.manual", "조건식 즉시 전송", "condition", "manual", "대시보드에서 수동 요청 시", "Stocke 대시보드"),
    ("telegram.unclassified", "분류되지 않은 알림", "other", "event", "발송 코드에서 호출 시", "Stocke"),
)
_DEFAULT_NOTIFICATION_MAP = {
    key: (name, category, trigger, schedule, source)
    for key, name, category, trigger, schedule, source in DEFAULT_NOTIFICATIONS
}


def ensure_notification_definitions() -> None:
    with SessionLocal() as db:
        existing = {
            row.notification_key: row
            for row in db.query(NotificationDefinition).all()
        }
        changed = False
        for key, name, category, trigger, schedule, source in DEFAULT_NOTIFICATIONS:
            row = existing.get(key)
            if row is None:
                db.add(NotificationDefinition(
                    notification_key=key,
                    display_name=name,
                    category=category,
                    trigger_type=trigger,
                    schedule_description=schedule,
                    schedule_source=source,
                    is_enabled=True,
                    attempt_count=0,
                    success_count=0,
                ))
                changed = True
                continue
            for field, value in (
                ("display_name", name),
                ("category", category),
                ("trigger_type", trigger),
                ("schedule_description", schedule),
                ("schedule_source", source),
            ):
                if getattr(row, field) != value:
                    setattr(row, field, value)
                    changed = True
        if changed:
            db.commit()


def _ensure_unknown_definition(db, notification_key: str) -> NotificationDefinition:
    row = db.get(NotificationDefinition, notification_key)
    if row is None:
        name, category, trigger, schedule, source = _DEFAULT_NOTIFICATION_MAP.get(
            notification_key,
            ("기타 알림", "other", "event", "발송 코드에서 호출 시", "Stocke"),
        )
        row = NotificationDefinition(
            notification_key=notification_key,
            display_name=name,
            category=category,
            trigger_type=trigger,
            schedule_description=schedule,
            schedule_source=source,
        )
        db.add(row)
        db.flush()
    return row


def record_notification_delivery(
    notification_key: Optional[str],
    success: bool,
    error: Optional[str] = None,
) -> None:
    key = notification_key or "telegram.unclassified"
    now = utc_now_naive()
    status = "sent" if success else "failed"
    with SessionLocal() as db:
        row = _ensure_unknown_definition(db, key)
        row.last_attempt_at = now
        row.last_status = status
        row.last_error = error[:255] if error else None
        row.attempt_count = (row.attempt_count or 0) + 1
        if success:
            row.last_sent_at = now
            row.success_count = (row.success_count or 0) + 1
        db.add(NotificationDeliveryLog(
            notification_key=key,
            attempted_at=now,
            status=status,
            process_id=os.getpid(),
            error=error[:255] if error else None,
        ))
        cutoff_ids = (
            select(NotificationDeliveryLog.id)
            .order_by(desc(NotificationDeliveryLog.attempted_at), desc(NotificationDeliveryLog.id))
            .offset(1000)
        )
        db.query(NotificationDeliveryLog).filter(
            NotificationDeliveryLog.id.in_(cutoff_ids)
        ).delete(synchronize_session=False)
        db.commit()


def _definition_to_dict(row: NotificationDefinition) -> Dict:
    return {
        "key": row.notification_key,
        "name": row.display_name,
        "category": row.category,
        "trigger_type": row.trigger_type,
        "schedule": row.schedule_description,
        "schedule_source": row.schedule_source,
        "enabled": row.is_enabled,
        "last_attempt_at": utc_naive_to_api_iso(row.last_attempt_at),
        "last_sent_at": utc_naive_to_api_iso(row.last_sent_at),
        "last_status": row.last_status,
        "last_error": row.last_error,
        "attempt_count": row.attempt_count,
        "success_count": row.success_count,
    }


def list_registered_notifications() -> List[Dict]:
    ensure_notification_definitions()
    with SessionLocal() as db:
        rows = (
            db.query(NotificationDefinition)
            .order_by(NotificationDefinition.category, NotificationDefinition.display_name)
            .all()
        )
        return [_definition_to_dict(row) for row in rows]


def list_recent_notification_deliveries(limit: int = 50) -> List[Dict]:
    ensure_notification_definitions()
    with SessionLocal() as db:
        rows = (
            db.query(NotificationDeliveryLog, NotificationDefinition.display_name)
            .outerjoin(
                NotificationDefinition,
                NotificationDefinition.notification_key == NotificationDeliveryLog.notification_key,
            )
            .order_by(desc(NotificationDeliveryLog.attempted_at), desc(NotificationDeliveryLog.id))
            .limit(max(1, min(int(limit), 200)))
            .all()
        )
        return [
            {
                "key": row.NotificationDeliveryLog.notification_key,
                "name": row.display_name or row.NotificationDeliveryLog.notification_key,
                "attempted_at": utc_naive_to_api_iso(row.NotificationDeliveryLog.attempted_at),
                "status": row.NotificationDeliveryLog.status,
                "process_id": row.NotificationDeliveryLog.process_id,
                "error": row.NotificationDeliveryLog.error,
            }
            for row in rows
        ]
