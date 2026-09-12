"""Send a production source-sync result to Telegram."""
from __future__ import annotations

import argparse
import sys

from notifications.telegram_notifier import TelegramNotifier


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", choices=("success", "failure"), required=True)
    parser.add_argument("--message", required=True)
    args = parser.parse_args()

    notifier = TelegramNotifier()
    if not notifier.is_configured():
        print("telegram not configured", file=sys.stderr)
        return 2

    icon = "✅" if args.status == "success" else "❌"
    message = f"{icon} 실서버 소스 동기화\n{args.message}"
    return 0 if notifier.send_message(message) else 1


if __name__ == "__main__":
    raise SystemExit(main())