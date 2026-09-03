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


def _log(msg: str):
    """화면 출력과 로그 파일 기록을 동시에 수행."""
    print(msg)
    logger.info(msg)


def _seconds_until_next_hour() -> float:
    """다음 정시(00분 00초)까지 남은 초."""
    now = datetime.now()
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return (next_hour - now).total_seconds()


async def run_once(api: KiwoomAPI, notifier: TelegramNotifier, names, max_stocks) -> bool:
    """조건식 조회 후 편입 종목이 있을 때만 텔레그램 전송."""
    _log(f"[{datetime.now().strftime('%H:%M:%S')}] 조건식 목록 조회 중...")
    result = await send_condition_alert(
        api, notifier, names=names, max_stocks=max_stocks,
    )
    if result.get("skipped"):
        _log(f"⏸️ {result.get('skip_reason') or result.get('message') or '알림 스킵'}")
        return True
    if result.get("sent"):
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
    _log("❌ 텔레그램 전송 실패 (logs 파일의 텔레그램 오류 확인)")
    return False


async def run(args: argparse.Namespace) -> int:
    notifier = TelegramNotifier()
    if not notifier.is_configured():
        _log("❌ 텔레그램 설정이 없습니다. .env에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 를 설정하세요.")
        return 1

    # --names 우선, 없으면 환경변수 기본값
    if args.names is not None:
        names = [s.strip() for s in args.names.split(",") if s.strip()]
    else:
        names = Config.TELEGRAM_ALERT_CONDITION_NAMES

    max_stocks = args.max_stocks if args.max_stocks is not None else Config.TELEGRAM_ALERT_MAX_STOCKS
    interval = args.interval if args.interval is not None else Config.TELEGRAM_ALERT_INTERVAL

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
        mode = "매 정시"
    elif args.loop:
        mode = f"반복({interval}초)"
    else:
        mode = "1회"
    _log(f"   모드: {mode}")

    # 매 정시 실행 모드
    if args.hourly:
        _log("매 정시 실행 시작 (Ctrl+C 로 종료)")
        while True:
            wait = _seconds_until_next_hour()
            next_run = (datetime.now() + timedelta(seconds=wait)).strftime("%H:%M:%S")
            _log(f"다음 정시({next_run})까지 {int(wait)}초 대기...\n")
            await asyncio.sleep(wait)
            try:
                await run_once(api, notifier, names, max_stocks)
            except Exception as e:
                _log(f"❌ 실행 중 오류: {e}")
                logger.exception("정시 실행 예외")

    # 단발 실행 모드
    if not args.loop:
        ok = await run_once(api, notifier, names, max_stocks)
        return 0 if ok else 2

    # 고정 주기 반복 실행 모드
    _log("반복 실행 시작 (Ctrl+C 로 종료)")
    while True:
        try:
            await run_once(api, notifier, names, max_stocks)
        except Exception as e:
            _log(f"❌ 실행 중 오류: {e}")
            logger.exception("반복 실행 예외")
        _log(f"다음 실행까지 {interval}초 대기...\n")
        await asyncio.sleep(interval)


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
