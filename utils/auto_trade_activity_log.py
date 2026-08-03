"""자동매매 실시간 활동 로그 — 대시보드 표시용 링 버퍼."""
from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Any, Dict, List

from utils.datetime_kst import kst_now_iso

# 매수/매도 실패·경고는 스캐너 노이즈와 분리 보관
_PROTECTED_SOURCES = frozenset({"BUY", "SELL"})
_PROTECTED_LEVELS = frozenset({"warn", "error"})


def _is_protected(entry: Dict[str, Any]) -> bool:
    src = str(entry.get("source") or "").upper()
    lvl = str(entry.get("level") or "").lower()
    if src in _PROTECTED_SOURCES and lvl in _PROTECTED_LEVELS:
        return True
    if src in _PROTECTED_SOURCES and any(
        k in str(entry.get("message") or "")
        for k in ("실패", "게이트", "FAILED", "조회 실패", "보류")
    ):
        return True
    return False


class AutoTradeActivityLog:
    def __init__(self, max_size: int = 300, critical_size: int = 80):
        max_size = max(50, int(max_size))
        critical_size = max(10, min(int(critical_size), max_size - 10))
        self._critical: deque = deque(maxlen=critical_size)
        self._normal: deque = deque(maxlen=max_size - critical_size)
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
                self._normal
                and source == "SYNC"
                and self._normal[0].get("source") == "SYNC"
                and self._normal[0].get("message") == message
                and self._normal[0].get("ts", "")[:19] == entry["ts"][:19]
            ):
                return
            if _is_protected(entry):
                self._critical.appendleft(entry)
            else:
                self._normal.appendleft(entry)

    def get_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            merged = list(self._critical) + list(self._normal)
            merged.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
            return merged[:limit]

    def clear(self) -> None:
        with self._lock:
            self._critical.clear()
            self._normal.clear()


activity_log = AutoTradeActivityLog()


def log_activity(source: str, message: str, level: str = "info", **extra: Any) -> None:
    activity_log.log(source, message, level, **extra)
