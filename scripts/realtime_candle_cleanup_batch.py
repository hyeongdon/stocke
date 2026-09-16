"""
장 종료 후 실시간 체결 구독 해제 + 봉 히스토리 초기화 배치

서버(localhost:8000 또는 원격)의 API 엔드포인트를 호출해
메모리 내 실시간 구독 상태를 정리한다.

사용:
  python scripts/realtime_candle_cleanup_batch.py
  python scripts/realtime_candle_cleanup_batch.py --host http://localhost:8000
  python scripts/realtime_candle_cleanup_batch.py --host http://my-server:8000

배치 스케줄러 권장 시각: 평일 20:00 이후
  - 15:30 NXT 마감, 16:00 마지막 손절 루프 완료
  - 20:00: 서버 자동 종료 전 정리 (서버 종료 시엔 자동 해제되므로 안전망 역할)
"""
from __future__ import annotations

import io
import logging
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 로깅 설정 ────────────────────────────────────────────────────────
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "realtime_candle_cleanup_batch.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def run(host: str = "http://localhost:8000") -> bool:
    """서버 API 호출 → 구독 해제 + 히스토리 초기화."""
    import requests  # noqa: PLC0415

    url = f"{host.rstrip('/')}/api/realtime-candles/market-close-cleanup"
    logger.info("=" * 56)
    logger.info("🧹 실시간 3분봉 장종료 정리 배치 시작")
    logger.info(f"   대상 서버: {host}")
    logger.info(f"   실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 56)

    try:
        resp = requests.post(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        before = data.get("before", {})
        after  = data.get("after", {})

        logger.info(f"✅ 정리 완료: {data.get('message', '')}")
        logger.info(
            f"   해제 전: 구독 {before.get('subscribed_count', 0)}종목 "
            f"/ 종목: {before.get('subscribed', [])}"
        )
        logger.info(
            f"   해제 후: 구독 {after.get('subscribed_count', 0)}종목"
        )
        return True

    except requests.exceptions.ConnectionError:
        logger.warning("⚠️  서버가 실행 중이지 않음 (접속 불가) — 정리 건너뜀")
        logger.warning("   (서버 종료 시 메모리 구독은 자동 해제됩니다)")
        return True   # 서버 미실행은 오류가 아님

    except requests.exceptions.HTTPError as e:
        logger.error(f"❌ HTTP 오류: {e}")
        return False

    except Exception as e:
        logger.error(f"❌ 예상치 못한 오류: {e}")
        return False


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="실시간 3분봉 장종료 정리 배치")
    parser.add_argument(
        "--host",
        default="http://localhost:8000",
        help="서버 주소 (기본: http://localhost:8000)",
    )
    args = parser.parse_args()

    ok = run(host=args.host)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
