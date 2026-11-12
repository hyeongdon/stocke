"""Persisted, configurable periodic condition alerts."""
import asyncio
import logging
from datetime import datetime, time as dt_time, timedelta
from typing import Dict, List, Optional

from core.models import ConditionAlertConfig, SessionLocal
from notifications.condition_alert import send_condition_alert
from notifications.telegram_notifier import TelegramNotifier
from utils.datetime_kst import KST, UTC, as_kst, utc_naive_to_api_iso, utc_now_naive
from utils.market_hours import is_krx_trading_day

logger = logging.getLogger(__name__)

ALLOWED_INTERVAL_SECONDS = (60, 300, 600, 900, 1800, 3600, 7200, 10800, 18000)
INTRADAY_INTERVAL_SECONDS = (600, 3600, 7200, 10800, 18000)
INTRADAY_START = dt_time(9, 0)
INTRADAY_END = dt_time(20, 0)
POLL_SECONDS = 5


def _next_intraday_run(
    interval_seconds: int,
    last_run_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> datetime:
    """다음 거래일 09:00~20:00 KST의 고정 간격 실행 시각을 UTC naive로 반환."""
    kst_now = as_kst(now)
    day = kst_now.date()

    if is_krx_trading_day(kst_now) and kst_now.time() <= INTRADAY_END:
        opening = datetime.combine(day, INTRADAY_START, tzinfo=KST)
        if kst_now <= opening:
            return opening.astimezone(UTC).replace(tzinfo=None)

        elapsed = (kst_now - opening).total_seconds()
        slot = opening + timedelta(
            seconds=int(elapsed // interval_seconds) * interval_seconds,
        )
        slot_utc = slot.astimezone(UTC).replace(tzinfo=None)
        if last_run_at is None or last_run_at < slot_utc:
            return slot_utc

        next_slot = slot + timedelta(seconds=interval_seconds)
        if next_slot.time() <= INTRADAY_END:
            return next_slot.astimezone(UTC).replace(tzinfo=None)

    day += timedelta(days=1)
    for _ in range(400):
        candidate = datetime.combine(day, INTRADAY_START, tzinfo=KST)
        if is_krx_trading_day(candidate):
            return candidate.astimezone(UTC).replace(tzinfo=None)
        day += timedelta(days=1)
    raise RuntimeError("다음 조건식 알림 거래일을 찾지 못했습니다.")


def _is_due(config: Dict, now_utc: datetime) -> bool:
    if config["is_intraday"]:
        now_kst = now_utc.replace(tzinfo=UTC).astimezone(KST)
        if not is_krx_trading_day(now_kst) or not (
            INTRADAY_START <= now_kst.time() <= INTRADAY_END
        ):
            return False
        opening = datetime.combine(now_kst.date(), INTRADAY_START, tzinfo=KST)
        elapsed = (now_kst - opening).total_seconds()
        slot = opening + timedelta(
            seconds=int(elapsed // config["interval_seconds"])
            * config["interval_seconds"],
        )
        slot_utc = slot.astimezone(UTC).replace(tzinfo=None)
        last_run = config["last_run_at"]
        if last_run:
            last_run = datetime.fromisoformat(
                last_run.replace("Z", "+00:00"),
            ).replace(tzinfo=None)
        return last_run is None or last_run < slot_utc

    next_run = config["next_run_at"]
    if not next_run:
        return True
    scheduled_at = datetime.fromisoformat(next_run.replace("Z", "+00:00")).replace(tzinfo=None)
    return scheduled_at <= now_utc


def _config_to_dict(config: ConditionAlertConfig) -> Dict:
    if config.is_intraday:
        next_run = _next_intraday_run(config.interval_seconds, config.last_run_at)
    else:
        run_anchor = config.last_run_at or config.updated_at
        next_run = run_anchor + timedelta(seconds=config.interval_seconds) if run_anchor else None
    return {
        "id": config.id,
        "name": config.name,
        "condition_names": config.condition_names or [],
        "interval_seconds": config.interval_seconds,
        "is_intraday": config.is_intraday,
        "is_enabled": config.is_enabled,
        "last_run_at": utc_naive_to_api_iso(config.last_run_at),
        "next_run_at": utc_naive_to_api_iso(next_run),
        "last_result": config.last_result,
        "last_message": config.last_message,
        "last_error": config.last_error,
    }


def list_condition_alert_configs() -> List[Dict]:
    with SessionLocal() as db:
        configs = (
            db.query(ConditionAlertConfig)
            .order_by(ConditionAlertConfig.id)
            .all()
        )
        return [_config_to_dict(config) for config in configs]


def save_condition_alert_config(
    config_id: Optional[int],
    name: str,
    condition_names: List[str],
    interval_seconds: int,
    is_enabled: bool,
    is_intraday: bool = True,
) -> Dict:
    normalized_name = str(name or "").strip()
    normalized_conditions = list(dict.fromkeys(
        str(condition).strip() for condition in condition_names if str(condition).strip()
    ))
    if not normalized_name:
        raise ValueError("알림 이름을 입력하세요.")
    if len(normalized_name) > 120:
        raise ValueError("알림 이름은 120자 이내로 입력하세요.")
    if not normalized_conditions:
        raise ValueError("조건식을 하나 이상 선택하세요.")
    allowed_intervals = INTRADAY_INTERVAL_SECONDS if is_intraday else ALLOWED_INTERVAL_SECONDS
    if interval_seconds not in allowed_intervals:
        raise ValueError("지원하지 않는 알림 주기입니다.")

    with SessionLocal() as db:
        config = db.get(ConditionAlertConfig, config_id) if config_id else None
        if config_id and config is None:
            raise LookupError("조건식 알림 설정을 찾을 수 없습니다.")
        if config is None:
            config = ConditionAlertConfig()
            db.add(config)
        config.name = normalized_name
        config.condition_names = normalized_conditions
        config.interval_seconds = interval_seconds
        config.is_intraday = bool(is_intraday)
        config.is_enabled = bool(is_enabled)
        config.last_run_at = None
        config.last_result = None
        config.last_message = "설정 저장됨"
        config.last_error = None
        db.commit()
        db.refresh(config)
        return _config_to_dict(config)


def delete_condition_alert_config(config_id: int) -> bool:
    with SessionLocal() as db:
        config = db.get(ConditionAlertConfig, config_id)
        if config is None:
            return False
        db.delete(config)
        db.commit()
        return True


def _record_run_start(config_id: int) -> Optional[Dict]:
    with SessionLocal() as db:
        config = db.get(ConditionAlertConfig, config_id)
        if config is None:
            return None
        config.last_run_at = utc_now_naive()
        config.last_result = "running"
        config.last_message = "조건식 조회 중"
        config.last_error = None
        db.commit()
        db.refresh(config)
        return _config_to_dict(config)


def _record_run_result(
    config_id: int,
    result: str,
    message: str,
    error: Optional[str] = None,
) -> None:
    with SessionLocal() as db:
        config = db.get(ConditionAlertConfig, config_id)
        if config is None:
            raise LookupError(f"조건식 알림 설정을 찾을 수 없습니다: {config_id}")
        config.last_result = result
        config.last_message = message
        config.last_error = error
        db.commit()


class ConditionAlertScheduler:
    def __init__(self, api):
        self.api = api
        self._stop = asyncio.Event()
        self._run_lock = asyncio.Lock()

    async def run_config(self, config_id: int) -> Dict:
        async with self._run_lock:
            config = _record_run_start(config_id)
            if config is None:
                raise LookupError("조건식 알림 설정을 찾을 수 없습니다.")
            notifier = TelegramNotifier()
            try:
                result = await send_condition_alert(
                    self.api,
                    notifier,
                    names=config["condition_names"],
                    notification_key="condition.polling",
                    alert_name=config["name"],
                    skip_market_hours_check=True,
                )
                if result.get("skipped"):
                    state = "skipped"
                    message = result.get("skip_reason") or "알림을 건너뛰었습니다."
                    error = None
                elif result.get("sent"):
                    state = "sent"
                    message = (
                        f"조건식 {result.get('condition_count', 0)}개 · "
                        f"종목 {result.get('stock_count', 0)}개 전송 완료"
                    )
                    error = None
                else:
                    state = "failed"
                    message = "텔레그램 알림 전송 실패"
                    error = message
                _record_run_result(config_id, state, message, error)
                return {
                    "success": state in {"sent", "skipped"},
                    "result": state,
                    "message": message,
                }
            except Exception as exc:
                message = f"{type(exc).__name__} (로그 확인)"
                _record_run_result(config_id, "failed", "조건식 알림 실행 오류", message)
                logger.exception("조건식 알림 실행 실패 (config_id=%s)", config_id)
                raise

    async def run(self) -> None:
        logger.info("조건식 알림 스케줄러 시작")
        while not self._stop.is_set():
            try:
                now = utc_now_naive()
                for config in list_condition_alert_configs():
                    if not config["is_enabled"]:
                        continue
                    if not _is_due(config, now):
                        continue
                    try:
                        await self.run_config(config["id"])
                    except Exception:
                        logger.exception(
                            "조건식 알림 실행을 건너뜁니다 (config_id=%s)",
                            config["id"],
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("조건식 알림 스케줄러 반복 처리 실패")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
        logger.info("조건식 알림 스케줄러 종료")

    def stop(self) -> None:
        self._stop.set()
