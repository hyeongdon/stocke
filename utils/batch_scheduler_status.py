"""Windows 작업 스케줄러·실행 프로세스 기준 배치 상태."""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from utils.datetime_kst import KST

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

# 표시 순서 + 메타데이터 (작업 스케줄러 TaskName 과 일치)
KNOWN_BATCHES: List[Dict[str, Any]] = [
    {
        "id": "morning_server",
        "label": "장전 서버 감시",
        "task_name": "Stocke-MorningServerWatch",
        "process_needles": ["ensure_server_running.ps1", "MorningServerWatch"],
        "log_file": os.path.join(LOG_DIR, "server_watch.log"),
        "default_schedule": "평일 07:55~08:20",
        "description": "손절 모니터(08:00) 전 서버 기동·헬스체크 (07:55~08:20 5분 간격, 이후 19:30 자동 종료)",
    },
    {
        "id": "daily_server",
        "label": "일일 서버 기동",
        "task_name": "Stocke-DailyServerStart",
        "process_needles": ["ensure_server_running.ps1", "DailyServerStart"],
        "log_file": os.path.join(LOG_DIR, "server_start.log"),
        "default_schedule": "매일 08:00",
        "description": "Stocke 서버 자동 기동 · 야간 19:30 자동 종료(SERVER_AUTO_SHUTDOWN_TIME)",
    },
    {
        "id": "telegram_alert",
        "label": "조건식 텔레그램 알림",
        "task_name": "StockeConditionTelegramAlert",
        "process_needles": ["condition_telegram_alert.py", "run_condition_alert.bat"],
        "log_file": os.path.join(LOG_DIR, "condition_telegram_alert.log"),
        "default_schedule": "장중 매시 (12:00~)",
        "description": "키움 조건식 조회 → 텔레그램 전송",
    },
    {
        "id": "telegram_realtime_alert",
        "label": "조건식 실시간 편입 알림",
        "task_name": "StockeConditionRealtimeAlert",
        "process_needles": [
            "condition_telegram_alert.py",
            "--realtime",
            "ensure_condition_realtime_alert.ps1",
            "run_condition_alert_realtime.bat",
        ],
        "log_file": os.path.join(LOG_DIR, "condition_telegram_alert.log"),
        "default_schedule": "평일 08:50~09:10",
        "description": "키움 조건식 실시간 편입(REAL) → 텔레그램 (장중 상시)",
    },

    {
        "id": "kiwoom_db_pnl_sync",
        "label": "NXT마감 키움↔DB 손익",
        "task_name": "stocke-kiwoom-db-pnl-sync",
        "process_needles": ["kiwoom_db_pnl_sync_batch.py", "run_kiwoom_db_pnl_sync_batch.bat"],
        "log_file": os.path.join(LOG_DIR, "kiwoom_db_pnl_sync_batch.log"),
        "default_schedule": "평일 19:50",
        "description": "NXT 마감 후 키움 당일 실현손익·수수료·잔고 → DB 동기화 (매매 일지보다 먼저)",
    },
    {
        "id": "daily_trade_journal",
        "label": "장마감 매매 일지",
        "task_name": "stocke-daily-trade-journal",
        "process_needles": ["daily_trade_journal_batch.py", "run_daily_trade_journal_batch.bat"],
        "log_file": os.path.join(LOG_DIR, "daily_trade_journal_batch.log"),
        "default_schedule": "평일 19:52",
        "description": "당일 매수·매도·실현손익 → 텔레그램 매매 일지",
    },
    {
        "id": "failed_buy_signals",
        "label": "장마감 매수 실패",
        "task_name": "stocke-failed-buy-signals",
        "process_needles": ["failed_buy_signals_batch.py", "run_failed_buy_signals_batch.bat"],
        "log_file": os.path.join(LOG_DIR, "failed_buy_signals_batch.log"),
        "default_schedule": "평일 15:42",
        "description": "당일 FAILED 매수 신호 → 전략/사유 표 텔레그램",
    },

    {
        "id": "theme_mart",
        "label": "테마/키워드 매핑",
        "task_name": "stocke-theme-mart-batch",
        "process_needles": ["theme_mart_batch.py"],
        "log_file": os.path.join(LOG_DIR, "theme_mart_batch.log"),
        "default_schedule": "매일 18:00",
        "description": "네이버 테마 전체(266개) 종목 매핑 스냅샷",
    },
    {
        "id": "fundamental",
        "label": "기본적분석 마트",
        "task_name": "Stocke-FundamentalBatch",
        "process_needles": ["fundamental_mart_batch", "run_fundamental_batch.bat"],
        "log_file": os.path.join(LOG_DIR, "fundamental_mart_batch.log"),
        "default_schedule": "매일 18:00",
        "description": "네이버 기본적분석 데이터 → DB 마트",
    },
    {
        "id": "stock_news",
        "label": "전체 종목 뉴스/키워드",
        "task_name": "stocke-stock-news-batch",
        "process_needles": ["stock_news_daily_batch.py"],
        "log_file": os.path.join(LOG_DIR, "stock_news_daily_batch.log"),
        "default_schedule": "비활성 (키워드 수집 중단)",
        "description": "전체 종목 네이버 뉴스→키워드 수집 — 현재 스케줄 비활성",
        "has_progress": True,
    },
]

_KNOWN_BY_TASK: Dict[str, Dict[str, Any]] = {b["task_name"]: b for b in KNOWN_BATCHES}
_SCHEDULER_CACHE: Dict[str, Any] = {"at": 0.0, "tasks": {}}
_SCHEDULER_CACHE_TTL = 45.0


def _run_cmd(cmd: List[str], timeout: float = 60.0) -> str:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="cp949",
            errors="replace",
            timeout=timeout,
        )
        return (proc.stdout or "").strip()
    except Exception:
        return ""


def _run_powershell(script: str, timeout: float = 15.0) -> str:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return (proc.stdout or "").strip()
    except Exception:
        return ""


def _discover_tasks_from_csv() -> Dict[str, Dict[str, Any]]:
    raw = _run_cmd(["schtasks", "/Query", "/FO", "CSV"], timeout=60.0)
    if not raw:
        return {}
    tasks: Dict[str, Dict[str, Any]] = {}
    try:
        reader = csv.reader(io.StringIO(raw))
        next(reader, None)
        for row in reader:
            if not row:
                continue
            path = row[0].strip().strip('"').lstrip("\\")
            if "stocke" not in path.lower():
                continue
            next_run = row[1].strip().strip('"') if len(row) > 1 else None
            status = row[2].strip().strip('"') if len(row) > 2 else None
            tasks[path] = {
                "task_name": path,
                "registered": True,
                "enabled": status not in ("Disabled", "사용 안 함"),
                "scheduler_state": status,
                "next_run_at": next_run if next_run and next_run.upper() != "N/A" else None,
                "last_run_at": None,
                "schedule": None,
                "command": None,
            }
    except csv.Error:
        pass
    return tasks


def _query_all_stocke_tasks(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    now = time.time()
    if (
        not force_refresh
        and _SCHEDULER_CACHE["tasks"]
        and (now - float(_SCHEDULER_CACHE["at"])) < _SCHEDULER_CACHE_TTL
    ):
        return dict(_SCHEDULER_CACHE["tasks"])

    tasks = _discover_tasks_from_csv()
    known_names = [b["task_name"] for b in KNOWN_BATCHES]
    for name in known_names:
        if name not in tasks:
            tasks[name] = {"task_name": name, "registered": False}

    _SCHEDULER_CACHE["at"] = now
    _SCHEDULER_CACHE["tasks"] = tasks
    return tasks


def _file_mtime_iso(path: str) -> Optional[str]:
    try:
        if not os.path.isfile(path):
            return None
        return datetime.fromtimestamp(os.path.getmtime(path), tz=KST).isoformat(timespec="seconds")
    except OSError:
        return None


def _running_process_lines() -> List[str]:
    script = """
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match '^(python|pythonw|cmd|powershell)\\.exe$' } |
  ForEach-Object { $_.CommandLine } |
  ConvertTo-Json -Compress
"""
    raw = _run_powershell(script, timeout=12.0)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, str):
            return [parsed]
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except json.JSONDecodeError:
        return [raw]
    return []


def _is_running(needles: List[str], process_lines: List[str]) -> bool:
    for line in process_lines:
        text = line or ""
        if any(n and n in text for n in needles):
            return True
    return False


def _ordered_task_names(tasks: Dict[str, Dict[str, Any]]) -> List[str]:
    ordered: List[str] = []
    seen: Set[str] = set()
    for batch in KNOWN_BATCHES:
        name = batch["task_name"]
        if name not in seen:
            ordered.append(name)
            seen.add(name)
    for name in sorted(tasks.keys(), key=lambda x: x.lower()):
        if name not in seen:
            ordered.append(name)
            seen.add(name)
    return ordered


def _job_from_batch(batch: Dict[str, Any], task: Dict[str, Any], process_lines: List[str]) -> Dict[str, Any]:
    needles = list(batch.get("process_needles") or [])
    is_running = _is_running(needles, process_lines)
    log_file = batch.get("log_file") or ""
    log_at = _file_mtime_iso(log_file)
    registered = bool(task.get("registered"))

    return {
        "id": batch["id"],
        "label": batch["label"],
        "description": batch.get("description") or "",
        "task_name": batch["task_name"],
        "registered": registered,
        "enabled": task.get("enabled"),
        "scheduler_state": task.get("state"),
        "last_run_at": task.get("last_run_at"),
        "next_run_at": task.get("next_run_at"),
        "last_result": task.get("last_result"),
        "schedule": task.get("schedule") or batch.get("default_schedule"),
        "command": task.get("command"),
        "running": is_running,
        "log_last_at": log_at,
        "mode": "scheduled" if registered else ("running" if is_running else "manual"),
    }


def _job_from_discovered(task_name: str, task: Dict[str, Any], process_lines: List[str]) -> Dict[str, Any]:
    cmd = str(task.get("command") or "")
    needles = [task_name]
    if cmd:
        needles.append(os.path.basename(cmd.split()[0] if cmd else ""))
    is_running = _is_running(needles, process_lines)
    registered = bool(task.get("registered"))
    return {
        "id": task_name.lower().replace("-", "_"),
        "label": task_name,
        "description": "Windows 작업 스케줄러 등록 작업",
        "task_name": task_name,
        "registered": registered,
        "enabled": task.get("enabled"),
        "scheduler_state": task.get("state"),
        "last_run_at": task.get("last_run_at"),
        "next_run_at": task.get("next_run_at"),
        "last_result": task.get("last_result"),
        "schedule": task.get("schedule"),
        "command": task.get("command"),
        "running": is_running,
        "log_last_at": None,
        "mode": "scheduled" if registered else ("running" if is_running else "manual"),
    }


def get_batch_jobs_status() -> List[Dict[str, Any]]:
    """등록된/실행 중인 배치 작업 목록 (스케줄러 + 프로세스 + 로그)."""
    try:
        tasks = _query_all_stocke_tasks()
        process_lines = _running_process_lines()
        jobs: List[Dict[str, Any]] = []

        for batch in KNOWN_BATCHES:
            task = tasks.get(batch["task_name"], {"task_name": batch["task_name"], "registered": False})
            job = _job_from_batch(batch, task, process_lines)

            if batch.get("has_progress"):
                try:
                    from utils.stock_news_progress import get_stock_news_progress

                    prog = get_stock_news_progress()
                    job["progress"] = {
                        "universe_total": prog.get("universe_total"),
                        "done_count": prog.get("done_count"),
                        "remaining_count": prog.get("pending_count"),
                        "percent": prog.get("percent"),
                        "run_done": prog.get("run_done"),
                        "run_total": prog.get("run_total"),
                        "run_percent": prog.get("run_percent"),
                        "last_run_status": prog.get("status"),
                        "current_stock_name": prog.get("current_stock_name"),
                        "eta_seconds": prog.get("eta_seconds"),
                    }
                    if prog.get("running"):
                        job["running"] = True
                    if not job.get("log_last_at"):
                        job["log_last_at"] = prog.get("updated_at")
                except Exception:
                    pass

            jobs.append(job)

        known_names = {b["task_name"] for b in KNOWN_BATCHES}
        for task_name in _ordered_task_names(tasks):
            if task_name in known_names:
                continue
            jobs.append(_job_from_discovered(task_name, tasks[task_name], process_lines))

        return jobs
    except Exception:
        # 스케줄러 조회 실패 시에도 알려진 배치 목록은 표시
        return [
            {
                "id": b["id"],
                "label": b["label"],
                "description": b.get("description") or "",
                "task_name": b["task_name"],
                "registered": None,
                "enabled": None,
                "scheduler_state": None,
                "last_run_at": _file_mtime_iso(b.get("log_file") or ""),
                "next_run_at": None,
                "last_result": None,
                "schedule": b.get("default_schedule"),
                "command": None,
                "running": False,
                "log_last_at": _file_mtime_iso(b.get("log_file") or ""),
                "mode": "unknown",
            }
            for b in KNOWN_BATCHES
        ]
