"""
조건식 조회 → 텔레그램 알림 (독립 실행 스크립트)

서버(FastAPI)를 띄우지 않고도 단독으로 실행할 수 있습니다.
키움 조건식 목록을 조회한 뒤, 각 조건식에 편입된 종목을 검색하여
텔레그램으로 전송합니다.

사용 예:
  # 편입 종목이 1개 이상일 때만 텔레그램 전송 (0종이면 스킵)
  python scripts/condition_telegram_alert.py --names "1592매매"

  # 실시간 편입 알림 (돌파 직후 이탈해도 편입 순간 전송, Ctrl+C 종료)
  python scripts/condition_telegram_alert.py --realtime --names "1592매매"

  # 반복 실행 (600초 주기) - 종료는 Ctrl+C
  python scripts/condition_telegram_alert.py --loop --interval 600

  # 매 정시(00분)마다 실행 - 종료는 Ctrl+C
  python scripts/condition_telegram_alert.py --hourly

환경변수(.env):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (필수)
  TELEGRAM_ALERT_CONDITION_NAMES        (선택, --names 미지정 시 기본 필터)
  TELEGRAM_ALERT_INTERVAL               (선택, --interval 미지정 시 기본 주기)
  TELEGRAM_ALERT_MAX_STOCKS             (선택, 조건식별 표시 종목 수)
  TELEGRAM_CONDITION_REALTIME_DEDUP_SEC (선택, 동일 종목 재알림 간격 기본 300)
"""
import argparse
import asyncio
import io
import logging
import os
import sys
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

# 어디서 실행해도 프로젝트 루트를 import 경로에 포함시킨다.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Windows 콘솔 UTF-8 인코딩 설정
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from core.config import Config  # noqa: E402
from api.kiwoom_api import KiwoomAPI  # noqa: E402
from notifications.telegram_notifier import TelegramNotifier  # noqa: E402
from notifications.condition_alert import send_condition_alert  # noqa: E402
from notifications.condition_realtime_alert import run_realtime_condition_alerts  # noqa: E402
from utils.notification_status import (  # noqa: E402
    finish_notification_job,
    heartbeat_notification_job,
    record_notification_result,
    register_notification_job,
)

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "condition_telegram_alert.log")


def setup_logging():
    """콘솔 + 파일(logs/condition_telegram_alert.log) 동시 로깅 설정.

    키움 API / 텔레그램 모듈의 내부 logger 출력도 함께 파일에 기록되어
    작업 스케줄러 등 화면이 없는 환경에서도 원인 추적이 가능하다.
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # 중복 핸들러 방지
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)


logger = logging.getLogger("condition_telegram_alert")


class _TrackingTelegramNotifier(TelegramNotifier):
    def __init__(self, job_key: str):
        super().__init__()
        self.job_key = job_key

    def send_message(
        self, text: str, parse_mode=None, reply_markup=None, *, notification_key=None,
    ) -> bool:
        sent = super().send_message(
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            notification_key=notification_key or "condition.polling",
        )
        record_notification_result(
            self.job_key,
            "sent" if sent else "failed",
            "텔레그램 알림 전송 완료" if sent else "텔레그램 알림 전송 실패",
            None if sent else "텔레그램 메시지 전송 실패",
        )
        return sent


def _log(msg: str):
    """화면 출력과 로그 파일 기록을 동시에 수행."""
    print(msg)
    logger.info(msg)


def _seconds_until_next_hour() -> float:
    """다음 정시(00분 00초)까지 남은 초."""
    now = datetime.now()
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return (next_hour - now).total_seconds()


async def run_once(api: KiwoomAPI, notifier: TelegramNotifier, names, max_stocks, job_key: str) -> bool:
    """조건식 조회 후 편입 종목이 있을 때만 텔레그램 전송."""
    _log(f"[{datetime.now().strftime('%H:%M:%S')}] 조건식 목록 조회 중...")
    result = await send_condition_alert(
        api, notifier, names=names, max_stocks=max_stocks,
    )
    if result.get("skipped"):
        message = result.get("skip_reason") or result.get("message") or "알림 스킵"
        record_notification_result(job_key, "no_alert", message)
        _log(f"⏸️ {message}")
        return True
    if result.get("sent"):
        record_notification_result(
            job_key,
            "sent",
            f"조건식 {result.get('condition_count', 0)}개 · 종목 {result.get('stock_count', 0)}개 전송 완료",
        )
        _log(
            f"✅ 텔레그램 전송 완료 "
            f"(조건식 {result.get('condition_count', 0)} · 종목 {result.get('stock_count', 0)})"
        )
        preview = result.get("message") or ""
        if preview:
            _log("\n----- 전송 메시지 미리보기 -----")
            _log(preview)
            _log("--------------------------------\n")
        return True
    record_notification_result(job_key, "failed", "텔레그램 알림 전송 실패", "메시지 전송 실패")
    _log("❌ 텔레그램 전송 실패 (logs 파일의 텔레그램 오류 확인)")
    return False


async def _heartbeat_periodically(job_key: str) -> None:
    while True:
        await asyncio.sleep(20)
        heartbeat_notification_job(job_key)


async def _run_registered(
    args: argparse.Namespace,
    job_key: str,
    notifier: TelegramNotifier,
    names,
    max_stocks: int,
    interval: int,
) -> int:
    if not notifier.is_configured():
        _log("❌ 텔레그램 설정이 없습니다. .env에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 를 설정하세요.")
        return 1

    api = KiwoomAPI()
    _log("키움 API 인증 중...")
    if not api.authenticate():
        _log("❌ 키움 API 인증 실패 (앱키/시크릿, 모의/실전 설정 확인)")
        return 1
    _log("✅ 인증 성공")
    _log(f"   대상: {'전체 조건식' if not names else names}")
    if args.realtime:
        _log("   모드: 실시간 편입")
        _log("실시간 편입 알림 시작 (Ctrl+C 로 종료)")
        try:
            await run_realtime_condition_alerts(api, notifier, names=names)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            _log(f"❌ 실시간 알림 오류: {e}")
            logger.exception("실시간 알림 예외")
            return 2
        return 0

    if args.hourly:
        _log("   모드: 매 정시")
    elif args.loop:
        _log(f"   모드: 반복({interval}초)")
    else:
        _log("   모드: 1회")

    # 매 정시 실행 모드
    if args.hourly:
        _log("매 정시 실행 시작 (Ctrl+C 로 종료)")
        while True:
            wait = _seconds_until_next_hour()
            next_run = (datetime.now() + timedelta(seconds=wait)).strftime("%H:%M:%S")
            _log(f"다음 정시({next_run})까지 {int(wait)}초 대기...\n")
            await asyncio.sleep(wait)
            try:
                await run_once(api, notifier, names, max_stocks, job_key)
            except Exception as e:
                record_notification_result(
                    job_key, "failed", "조건식 정기 조회 오류", type(e).__name__,
                )
                _log(f"❌ 실행 중 오류: {e}")
                logger.exception("정시 실행 예외")

    # 단발 실행 모드
    if not args.loop:
        ok = await run_once(api, notifier, names, max_stocks, job_key)
        return 0 if ok else 2

    # 고정 주기 반복 실행 모드
    _log("반복 실행 시작 (Ctrl+C 로 종료)")
    while True:
        try:
            await run_once(api, notifier, names, max_stocks, job_key)
        except Exception as e:
            record_notification_result(
                job_key, "failed", "조건식 정기 조회 오류", type(e).__name__,
            )
            _log(f"❌ 실행 중 오류: {e}")
            logger.exception("반복 실행 예외")
        _log(f"다음 실행까지 {interval}초 대기...\n")
        await asyncio.sleep(interval)


async def run(args: argparse.Namespace) -> int:
    names = (
        [s.strip() for s in args.names.split(",") if s.strip()]
        if args.names is not None
        else list(Config.TELEGRAM_ALERT_CONDITION_NAMES)
    )
    max_stocks = args.max_stocks if args.max_stocks is not None else Config.TELEGRAM_ALERT_MAX_STOCKS
    interval = args.interval if args.interval is not None else Config.TELEGRAM_ALERT_INTERVAL
    if args.realtime:
        job_key = "condition_telegram_realtime"
        job_name = "조건식 실시간 편입 알림"
        mode = "realtime"
        interval_seconds = None
    else:
        job_key = "condition_telegram_polling"
        job_name = "조건식 정기 조회 알림"
        mode = "hourly" if args.hourly else ("loop" if args.loop else "once")
        interval_seconds = 3600 if args.hourly else (interval if args.loop else None)

    is_continuous = bool(args.realtime or args.loop or args.hourly)
    register_notification_job(
        job_key,
        job_name,
        mode,
        is_continuous,
        interval_seconds,
        names,
    )
    notifier = _TrackingTelegramNotifier(job_key)
    heartbeat_task = asyncio.create_task(_heartbeat_periodically(job_key)) if is_continuous else None
    exit_code = None
    error = None
    try:
        exit_code = await _run_registered(
            args, job_key, notifier, names, max_stocks, interval,
        )
        if exit_code:
            error = f"알림 프로세스 종료 코드: {exit_code}"
            record_notification_result(job_key, "failed", "알림 프로세스 실행 실패", error)
        return exit_code
    except Exception as exc:
        error = f"{type(exc).__name__} (로그 확인)"
        record_notification_result(job_key, "failed", "알림 프로세스 실행 오류", error)
        raise
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        finish_notification_job(job_key, error)


def main() -> int:
    setup_logging()
    logger.info("=" * 50)
    logger.info("condition_telegram_alert 시작")
    p = argparse.ArgumentParser(description="조건식 조회 → 텔레그램 알림")
    p.add_argument("--names", default=None, help="조건식 이름 필터 (부분일치, 쉼표 구분)")
    p.add_argument("--realtime", action="store_true", help="실시간 편입 알림 (search_type=1)")
    p.add_argument("--loop", action="store_true", help="고정 주기 반복 실행")
    p.add_argument("--hourly", action="store_true", help="매 정시(00분)마다 실행")
    p.add_argument("--interval", type=int, default=None, help="반복 주기(초)")
    p.add_argument("--max-stocks", type=int, default=None, help="조건식별 표시 종목 수")
    args = p.parse_args()
    if args.realtime and (args.loop or args.hourly):
        p.error("--realtime 은 --loop/--hourly 와 함께 쓸 수 없습니다")

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        _log("\n종료합니다.")
        return 0
    except Exception as e:
        _log(f"❌ 치명적 오류로 종료: {e}")
        logger.exception("최상위 예외")
        return 99


if __name__ == "__main__":
    raise SystemExit(main())
