"""전체 종목 뉴스/키워드 배치 진행률 (파일 + 로그 + DB)."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.models import TagArticle
from utils.datetime_kst import as_kst, kst_now_iso, now_kst

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
PROGRESS_FILE = os.path.join(LOG_DIR, "_stock_news_progress.json")
STOCK_NEWS_LOG = os.path.join(LOG_DIR, "stock_news_daily_batch.log")


def write_stock_news_progress(data: Dict[str, Any]) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    payload = dict(data)
    payload["updated_at"] = kst_now_iso()
    tmp = PROGRESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, PROGRESS_FILE)


def _read_progress_file() -> Dict[str, Any]:
    if not os.path.isfile(PROGRESS_FILE):
        return {}
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_log_tail() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "biz_date": None,
        "universe_total": None,
        "done_count": None,
        "pending_count": None,
        "run_done": None,
        "run_total": None,
        "running": False,
        "status": None,
    }
    if not os.path.isfile(STOCK_NEWS_LOG):
        return result
    try:
        with open(STOCK_NEWS_LOG, "r", encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-120:]
    except OSError:
        return result

    started_idx = None
    for i in range(len(tail) - 1, -1, -1):
        if "stock_news_daily_batch" in tail[i] and "시작" in tail[i]:
            started_idx = i
            break

    for line in reversed(tail):
        m = re.search(r"biz_date=(\d{4}-\d{2}-\d{2})", line)
        if m and not result["biz_date"]:
            result["biz_date"] = m.group(1)
        m = re.search(r"(?:스킵\s+)?set size=(\d+)", line)
        if m and result["done_count"] is None:
            result["done_count"] = int(m.group(1))
        m = re.search(r"이미완료=(\d+)", line)
        if m and result["done_count"] is None:
            result["done_count"] = int(m.group(1))
        m = re.search(r"유니버스=(\d+)", line)
        if m and result["universe_total"] is None:
            result["universe_total"] = int(m.group(1))
        m = re.search(r"미처리=(\d+)", line)
        if m and result["pending_count"] is None:
            result["pending_count"] = int(m.group(1))
        m = re.search(r"이번_실행=(\d+)", line)
        if m and result["run_total"] is None:
            result["run_total"] = int(m.group(1))
        m = re.search(r"진행 stock=(\d+)/(\d+)", line)
        if m:
            result["run_done"] = int(m.group(1))
            result["run_total"] = int(m.group(2))
            result["running"] = True
        if "완료 biz_date=" in line and result["status"] is None:
            result["status"] = "run_done"
        if "처리할 미완료 종목이 없습니다" in line:
            result["status"] = "all_done"

    if started_idx is not None:
        completed = any(
            "완료 biz_date=" in tail[j] or "처리할 미완료 종목이 없습니다" in tail[j]
            for j in range(started_idx, len(tail))
        )
        if not completed:
            result["running"] = True
            result["status"] = "running"

    return result


def _db_done_count(session: Session, biz_date: date | None) -> int:
    if not biz_date:
        return 0
    return int(
        session.query(func.count(func.distinct(TagArticle.stock_code)))
        .filter(TagArticle.source == "naver_news", TagArticle.biz_date == biz_date)
        .scalar()
        or 0
    )


def get_stock_news_progress(session: Session | None = None) -> Dict[str, Any]:
    file_data = _read_progress_file()
    log_data = _parse_log_tail()

    biz_raw = file_data.get("biz_date") or log_data.get("biz_date")
    biz_date: date | None = None
    if biz_raw:
        try:
            biz_date = datetime.strptime(str(biz_raw)[:10], "%Y-%m-%d").date()
        except ValueError:
            biz_date = None

    db_done = _db_done_count(session, biz_date) if session and biz_date else 0

    universe = file_data.get("universe_total") or log_data.get("universe_total")
    done = file_data.get("done_count") or log_data.get("done_count") or db_done
    if db_done and (not done or db_done > done):
        done = db_done

    run_total = file_data.get("run_total") or log_data.get("run_total")
    run_done = file_data.get("run_done") or log_data.get("run_done")
    pending = file_data.get("pending_count") or log_data.get("pending_count")
    if universe and done is not None and pending is None:
        pending = max(0, int(universe) - int(done))

    running = bool(file_data.get("running"))
    if log_data.get("running"):
        running = True
    # stale file: updated > 3min ago and log not running
    updated_at = file_data.get("updated_at")
    if running and updated_at:
        try:
            updated_dt = as_kst(datetime.fromisoformat(str(updated_at)))
            if (now_kst() - updated_dt).total_seconds() > 180 and not log_data.get("running"):
                running = False
        except ValueError:
            pass

    pct = None
    if universe and done is not None and int(universe) > 0:
        pct = round(min(100.0, (int(done) / int(universe)) * 100), 1)

    run_pct = None
    if run_total and run_done is not None and int(run_total) > 0:
        run_pct = round(min(100.0, (int(run_done) / int(run_total)) * 100), 1)

    eta_seconds = None
    started_at = file_data.get("started_at")
    if running and started_at and done is not None and universe:
        try:
            started_dt = as_kst(datetime.fromisoformat(str(started_at)))
            elapsed = max(1.0, (now_kst() - started_dt).total_seconds())
            processed = max(1, int(done) - int(file_data.get("done_at_start") or 0))
            per_stock = elapsed / processed
            remain = max(0, int(universe) - int(done))
            eta_seconds = int(per_stock * remain)
        except (ValueError, TypeError, ZeroDivisionError):
            eta_seconds = None

    status = file_data.get("status") or log_data.get("status")
    if status == "running" and not running:
        status = None
    if not status:
        if running:
            status = "running"
        elif universe and done is not None and int(done) >= int(universe):
            status = "all_done"
        elif done:
            status = "idle"
        else:
            status = "idle"

    return {
        "biz_date": biz_date.isoformat() if biz_date else None,
        "running": running,
        "status": status,
        "universe_total": universe,
        "done_count": done,
        "pending_count": pending,
        "percent": pct,
        "run_total": run_total,
        "run_done": run_done,
        "run_percent": run_pct,
        "ok_count": file_data.get("ok_count"),
        "fail_count": file_data.get("fail_count"),
        "current_stock_code": file_data.get("current_stock_code"),
        "current_stock_name": file_data.get("current_stock_name"),
        "started_at": file_data.get("started_at"),
        "updated_at": file_data.get("updated_at") or updated_at,
        "eta_seconds": eta_seconds,
        "db_done_count": db_done,
    }
