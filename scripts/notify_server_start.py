"""스케줄/ensure 스크립트용 서버 기동 텔레그램 알림 (헬스체크 통과 시에만 전송)."""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.config import Config  # noqa: E402
from notifications.telegram_notifier import TelegramNotifier  # noqa: E402


def _server_port() -> int:
    try:
        return int(getattr(Config, "PORT", None) or os.getenv("PORT", "8000"))
    except (TypeError, ValueError):
        return 8000


def verify_server_up(port: int, *, stable_checks: int = 2, attempts: int = 15, delay_sec: float = 2.0) -> bool:
    """연속 stable_checks회 /docs 200 응답 확인."""
    url = f"http://127.0.0.1:{port}/docs"
    ok_streak = 0
    for _ in range(attempts):
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                ok_streak += 1
                if ok_streak >= stable_checks:
                    return True
            else:
                ok_streak = 0
        except Exception:
            ok_streak = 0
        time.sleep(delay_sec)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Stocke 서버 기동 텔레그램 알림")
    parser.add_argument(
        "--reason",
        default="morning_schedule",
        help="알림 사유 태그 (morning_schedule 등)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="헬스체크 생략 (개발용, 사용 비권장)",
    )
    args = parser.parse_args()

    port = _server_port()

    if not args.force:
        if not verify_server_up(port):
            print(f"server health check failed (port {port}), telegram skipped")
            return 1

    notifier = TelegramNotifier()
    if not notifier.is_configured():
        print("telegram not configured")
        return 0

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    label = "아침 스케줄" if args.reason == "morning_schedule" else args.reason
    msg = (
        f"✅ Stocke 서버 기동 확인\n"
        f"시각: {now}\n"
        f"경로: {label}\n"
        f"포트: {port} (헬스체크 OK)\n"
        f"대시보드: http://127.0.0.1:{port}/dashboard"
    )
    ok = notifier.send_message(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
