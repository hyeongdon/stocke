"""CPU 과부하 텔레그램 알림 (트레이/워처에서 호출).

장중 제한 없이 시스템 알림으로 전송합니다.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.config import Config  # noqa: E402
from notifications.telegram_notifier import TelegramNotifier  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="CPU 과부하 텔레그램 알림")
    p.add_argument("--cpu", type=float, required=True, help="현재 CPU 사용률(%)")
    p.add_argument("--sustain", type=int, default=60, help="지속 초")
    p.add_argument("--note", default="", help="추가 메모")
    args = p.parse_args()

    if not Config.CPU_ALERT_TELEGRAM:
        print("cpu telegram alert disabled")
        return 0

    notifier = TelegramNotifier()
    if not notifier.is_configured():
        print("telegram not configured")
        return 0

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "⚠️ Stocke CPU 과부하",
        f"사용률: {args.cpu:.0f}%",
        f"지속: {args.sustain}초 이상 ≥{Config.CPU_ALERT_THRESHOLD:g}%",
        f"시각: {now}",
    ]
    if args.note:
        lines.append(f"참고: {args.note}")
    lines.append("뉴스/테마 KeyBERT 배치 중일 수 있습니다.")
    ok = notifier.send_message("\n".join(lines))
    print("telegram ok" if ok else "telegram fail")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
