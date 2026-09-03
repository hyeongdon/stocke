"""자동매매 실시간 활동 로그 — 대시보드 표시용 링 버퍼."""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from utils.datetime_kst import kst_now_iso

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_LEDGER_ACTIVITY_PATH = _PROJECT_ROOT / "logs" / "_ma1592_ledger_activity.jsonl"
_LEDGER_ACTIVITY_MAX_LINES = 200
_ledger_file_lock = Lock()

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


def _trim_ledger_activity_file(path: Path, max_lines: int = _LEDGER_ACTIVITY_MAX_LINES) -> None:
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= max_lines:
        return
    path.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")


def _append_ledger_activity_file(entry: Dict[str, Any]) -> None:
    path = _LEDGER_ACTIVITY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _ledger_file_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        _trim_ledger_activity_file(path)


def read_ma1592_ledger_activity(limit: int = 80) -> List[Dict[str, Any]]:
    path = _LEDGER_ACTIVITY_PATH
    if not path.exists():
        legacy = path.with_name("_ma1590_ledger_activity.jsonl")
        if legacy.exists():
            path = legacy
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for raw in lines[-max(limit * 2, limit):]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    out.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
    return out[:limit]


def _activity_event_key(entry: Dict[str, Any]) -> tuple:
    return (
        str(entry.get("ts") or "")[:19],
        str(entry.get("message") or ""),
        str(entry.get("stock_code") or ""),
    )


def merge_activity_events(
    in_memory: List[Dict[str, Any]],
    limit: int = 80,
    *,
    include_ledger: bool = True,
) -> List[Dict[str, Any]]:
    merged = list(in_memory or [])
    if include_ledger:
        merged.extend(read_ma1592_ledger_activity(limit))
    merged.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
    seen = set()
    out: List[Dict[str, Any]] = []
    for entry in merged:
        key = _activity_event_key(entry)
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def log_ma1592_ledger_insert(
    stock_code: str,
    stock_name: str,
    *,
    condition_label: str = "",
    insert_source: str = "",
) -> None:
    """MA1592 장부 신규 편입 — 별도 프로세스(실시간 조건)에서도 대시보드에 보이도록 파일에도 기록."""
    code = str(stock_code or "").replace("A", "").strip()
    name = str(stock_name or code or "").strip() or code
    detail = f" · {condition_label}" if condition_label else ""
    if insert_source and insert_source not in ("condition", "condition_realtime"):
        detail = f"{detail} · {insert_source}" if detail else f" · {insert_source}"
    msg = f"[MA1592] 장부 편입: {name}({code}){detail}"
    entry = {
        "ts": kst_now_iso(),
        "source": "SCANNER",
        "level": "info",
        "message": msg,
        "strategy": "ma1592",
        "stock_code": code,
        "stock_name": name,
        "ledger_insert": True,
    }
    _append_ledger_activity_file(entry)
    log_activity(
        "SCANNER",
        msg,
        "info",
        strategy="ma1592",
        stock_code=code,
        stock_name=name,
        ledger_insert=True,
    )


# Backward-compat aliases
read_ma1590_ledger_activity = read_ma1592_ledger_activity
log_ma1590_ledger_insert = log_ma1592_ledger_insert
