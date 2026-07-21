"""종목 뉴스 배치 이어달리기 — 미니PC 기본: 테마 종목 + 일일 상한."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
BATCH_SCRIPT = ROOT / "scripts" / "stock_news_daily_batch.py"
PROGRESS_URL = "http://127.0.0.1:8000/batch-status/stock-news-progress"
PROGRESS_FILE = ROOT / "logs" / "_stock_news_progress.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _cfg():
    from core.config import Config
    universe = (Config.STOCK_NEWS_UNIVERSE or "theme").strip().lower()
    if universe not in ("theme", "all"):
        universe = "theme"
    max_day = max(0, int(Config.STOCK_NEWS_MAX_STOCKS_PER_DAY or 120))
    chunk = max(1, int(Config.STOCK_NEWS_CHUNK_SIZE or 40))
    return universe, max_day, chunk


def active_chunk_pids() -> list[str]:
    ps = (
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        "Where-Object { "
        "$_.Name -match '^(python|pythonw)\\.exe$' "
        "-and $_.CommandLine -like '*stock_news_daily_batch.py*' "
        "-and $_.CommandLine -like '*--max-stocks-per-run*' "
        "} | Select-Object -ExpandProperty ProcessId"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def _today_kst() -> str:
    try:
        from utils.datetime_kst import kst_today
        return kst_today().isoformat()
    except Exception:
        from datetime import datetime, timedelta, timezone
        kst = timezone(timedelta(hours=9))
        return datetime.now(kst).date().isoformat()


def read_progress_file() -> dict:
    if not PROGRESS_FILE.is_file():
        return {}
    try:
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_progress() -> dict:
    today = _today_kst()
    try:
        with urllib.request.urlopen(PROGRESS_URL, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict):
                data.setdefault("biz_date", today)
                return data
    except Exception as exc:
        print(f"AUTO_LOOP_PROGRESS_API_ERROR={exc}", flush=True)

    file_data = read_progress_file()
    file_biz = str(file_data.get("biz_date") or "")[:10]
    if file_biz == today:
        return {
            "biz_date": today,
            "status": file_data.get("status"),
            "pending_count": file_data.get("pending_count"),
            "done_count": file_data.get("done_count"),
            "universe_total": file_data.get("universe_total"),
            "running": bool(file_data.get("running")),
            "needs_new_day_run": False,
            "day_cap": file_data.get("day_cap"),
        }
    return {
        "biz_date": today,
        "status": "pending",
        "pending_count": 1,
        "done_count": 0,
        "universe_total": file_data.get("universe_total"),
        "running": False,
        "needs_new_day_run": True,
        "progress_file_biz_date": file_biz or None,
    }


def _should_stop(progress: dict, today: str, max_day: int) -> bool:
    prog_date = str(progress.get("biz_date") or "")[:10]
    file_date = str(progress.get("progress_file_biz_date") or "")[:10]
    if progress.get("needs_new_day_run") or (file_date and file_date != today):
        return False
    if prog_date and prog_date != today:
        return False

    done = progress.get("done_count")
    if max_day > 0 and done is not None and int(done) >= max_day:
        return True

    pending = progress.get("pending_count")
    if pending is None:
        status = progress.get("status")
        universe = progress.get("universe_total")
        if (
            status == "all_done"
            and universe is not None
            and done is not None
            and int(done) >= int(universe)
            and int(universe) > 0
        ):
            return True
        return False
    if int(pending) > 0:
        return False
    if progress.get("status") == "all_done":
        return True
    if int(pending) <= 0 and progress.get("status") in ("all_done", "run_done", "idle"):
        if progress.get("needs_new_day_run"):
            return False
        return True
    return False


def main() -> int:
    today = _today_kst()
    universe, max_day, chunk = _cfg()
    print(
        f"AUTO_LOOP_TODAY={today} universe={universe} max_per_day={max_day} chunk={chunk}",
        flush=True,
    )

    while True:
        while active_chunk_pids():
            time.sleep(10)

        progress = read_progress()
        print(
            "AUTO_LOOP_PROGRESS "
            f"biz={progress.get('biz_date')} file_biz={progress.get('progress_file_biz_date')} "
            f"status={progress.get('status')} pending={progress.get('pending_count')} "
            f"done={progress.get('done_count')} universe={progress.get('universe_total')} "
            f"needs_new_day={progress.get('needs_new_day_run')}",
            flush=True,
        )

        if _should_stop(progress, today, max_day):
            print("AUTO_LOOP_DONE", flush=True)
            return 0

        print(
            f"AUTO_LOOP_START pending={progress.get('pending_count')} done={progress.get('done_count')}",
            flush=True,
        )
        result = subprocess.run(
            [
                str(PYTHON),
                str(BATCH_SCRIPT),
                "--universe",
                universe,
                "--max-stocks-per-day",
                str(max_day),
                "--max-stocks-per-run",
                str(chunk),
            ],
            cwd=str(ROOT),
        )
        print(f"AUTO_LOOP_EXIT_CODE={result.returncode}", flush=True)
        if result.returncode != 0:
            return result.returncode
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
