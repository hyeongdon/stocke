from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
BATCH_SCRIPT = ROOT / "scripts" / "stock_news_daily_batch.py"
PROGRESS_URL = "http://127.0.0.1:8000/batch-status/stock-news-progress"


def active_chunk_pids() -> list[str]:
    ps = (
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        "Where-Object { "
        "$_.Name -match '^(python|pythonw)\\.exe$' "
        "-and $_.CommandLine -like '*stock_news_daily_batch.py*' "
        "-and $_.CommandLine -like '*--max-stocks-per-run 100*' "
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


def read_progress() -> dict:
    with urllib.request.urlopen(PROGRESS_URL, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    while True:
        while active_chunk_pids():
            time.sleep(10)

        try:
            progress = read_progress()
        except Exception as exc:
            print(f"AUTO_LOOP_PROGRESS_ERROR={exc}", flush=True)
            time.sleep(10)
            continue

        pending = int(progress.get("pending_count") or 0)
        if progress.get("status") == "all_done" or pending <= 0:
            print("AUTO_LOOP_DONE", flush=True)
            return 0

        print(
            f"AUTO_LOOP_START pending={pending} done={progress.get('done_count')}",
            flush=True,
        )
        result = subprocess.run(
            [str(PYTHON), str(BATCH_SCRIPT), "--max-stocks-per-run", "100"],
            cwd=str(ROOT),
        )
        print(f"AUTO_LOOP_EXIT_CODE={result.returncode}", flush=True)
        if result.returncode != 0:
            return result.returncode
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
