"""자동매매 실시간 활동 로그 — 대시보드 표시용 링 버퍼."""
from __future__ import annotations

from collections import deque
from utils.datetime_kst import kst_now_iso
from threading import Lock
from typing import Any, Dict, List, Optional


class AutoTradeActivityLog:
    def __init__(self, max_size: int = 300):
        self._entries: deque = deque(maxlen=max_size)
        self._lock = Lock()

    def log(
        self,
        source: str,
        message: str,
        level: str = "info",
        **extra: Any,
    ) -> None:
        entry = {
            "ts": kst_now_iso(),
            "source": source,
            "level": level,
            "message": message,
            **extra,
        }
        with self._lock:
            if (
                self._entries
                and source == "SYNC"
                and self._entries[0].get("source") == "SYNC"
                and self._entries[0].get("message") == message
                and self._entries[0].get("ts", "")[:19] == entry["ts"][:19]
            ):
                return
            self._entries.appendleft(entry)

    def get_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._entries)[:limit]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


activity_log = AutoTradeActivityLog()


def log_activity(source: str, message: str, level: str = "info", **extra: Any) -> None:
    activity_log.log(source, message, level, **extra)
