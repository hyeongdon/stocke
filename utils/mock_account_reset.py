"""모의투자 계좌 교체 감지 및 계좌 연동 데이터 초기화.

모의투자 기간이 만료되어 재신청하면 앱키/시크릿(계좌번호)이 바뀐다.
이전 계좌의 포지션·체결·신호·잔고 스냅샷이 남아 있으면 새 계좌와 대사가 어긋나므로,
식별값 해시가 바뀐 것을 감지하면 백업 후 정리한다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import text

from core.config import Config
from utils.datetime_kst import kst_now_iso

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = _PROJECT_ROOT / "logs" / "_mock_account_state.json"
BACKUP_ROOT = _PROJECT_ROOT / "backups"

_MOCK_POSITIONS = "SELECT id FROM positions WHERE account_mode = 'mock'"

# (테이블, WHERE) — 실전(real) 데이터는 건드리지 않는다
_RESET_TARGETS = [
    ("sell_orders", f"position_id IN ({_MOCK_POSITIONS}) OR position_id NOT IN (SELECT id FROM positions)"),
    ("position_buy_fills", f"position_id IN ({_MOCK_POSITIONS}) OR position_id NOT IN (SELECT id FROM positions)"),
    ("positions", "account_mode = 'mock'"),
    ("pending_buy_signals", "account_mode = 'mock'"),
    ("account_balance_snapshots", "account_type = '모의투자'"),
]

# 이전 계좌 매매 활동을 담은 파일 — 백업 폴더로 이동
_MOVE_FILES = [
    _PROJECT_ROOT / "logs" / "_ma1592_ledger_activity.jsonl",
]
_RWM_STATE_PATH = _PROJECT_ROOT / "logs" / "_rwm_bowl_state.json"
_LADDER_STATE_PATH = _PROJECT_ROOT / "logs" / "_rsi_ladder_state.json"


def _mask(value: str, head: int = 4) -> str:
    value = str(value or "")
    return value[:head] + "****" if len(value) > head else value


def current_identity() -> Dict[str, str]:
    key = Config.KIWOOM_MOCK_APP_KEY or ""
    secret = Config.KIWOOM_MOCK_APP_SECRET or ""
    account = Config.KIWOOM_MOCK_ACCOUNT_NUMBER or ""
    fp = hashlib.sha256(f"{key}|{secret}|{account}".encode("utf-8")).hexdigest()[:16]
    return {"fingerprint": fp, "app_key": _mask(key, 6), "account": _mask(account, 4)}


def load_state() -> Dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("모의계좌 상태 파일 읽기 실패: %s", e)
        return {}


def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _json_default(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def _backup_and_delete(db, backup_dir: Path, apply: bool) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for table, where in _RESET_TARGETS:
        rows = db.execute(text(f"SELECT * FROM {table} WHERE {where}")).mappings().all()
        counts[table] = len(rows)
        if not apply or not rows:
            continue
        (backup_dir / f"{table}.json").write_text(
            json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1, default=_json_default),
            encoding="utf-8",
        )
    if apply:
        for table, where in _RESET_TARGETS:
            if counts[table]:
                db.execute(text(f"DELETE FROM {table} WHERE {where}"))
        db.commit()
    return counts


def _cleanup_files(backup_dir: Path, apply: bool) -> List[str]:
    touched: List[str] = []
    for path in _MOVE_FILES:
        if path.exists():
            touched.append(path.name)
            if apply:
                shutil.move(str(path), str(backup_dir / path.name))

    try:
        rwm = json.loads(_RWM_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        rwm = None
    if isinstance(rwm, dict) and rwm.get("exits"):
        touched.append(f"{_RWM_STATE_PATH.name}(exits {len(rwm['exits'])}건)")
        if apply:
            shutil.copy2(_RWM_STATE_PATH, backup_dir / _RWM_STATE_PATH.name)
            rwm["exits"] = {}
            _RWM_STATE_PATH.write_text(json.dumps(rwm, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        ladder = json.loads(_LADDER_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        ladder = None
    plans = (ladder or {}).get("plans") if isinstance(ladder, dict) else None
    mock_ids = [pid for pid, p in (plans or {}).items() if (p or {}).get("account_mode") == "mock"]
    if mock_ids:
        touched.append(f"{_LADDER_STATE_PATH.name}(모의 계획 {len(mock_ids)}건)")
        if apply:
            shutil.copy2(_LADDER_STATE_PATH, backup_dir / _LADDER_STATE_PATH.name)
            for pid in mock_ids:
                plans.pop(pid, None)
            _LADDER_STATE_PATH.write_text(json.dumps(ladder, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                from utils.rsi_ladder import reset_ladder_store

                reset_ladder_store()
            except Exception:
                pass
    return touched


def check_and_reset(db, *, apply: bool = True, force: bool = False) -> Dict[str, Any]:
    """모의 계좌 식별값이 바뀌었으면 이전 계좌 데이터를 백업 후 정리.

    반환 report["action"]: skipped_real | baseline | unchanged | reset | dry_run
    """
    ident = current_identity()
    report: Dict[str, Any] = {"identity": ident, "action": "unchanged"}

    if not Config.KIWOOM_USE_MOCK_ACCOUNT:
        report["action"] = "skipped_real"
        return report

    state = load_state()
    prev_fp = state.get("fingerprint")
    report["previous"] = {k: state.get(k) for k in ("fingerprint", "app_key", "account", "recorded_at")}

    if not prev_fp and not force:
        if apply:
            save_state({**ident, "recorded_at": kst_now_iso(), "resets": []})
        report["action"] = "baseline"
        return report

    if prev_fp == ident["fingerprint"] and not force:
        return report

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / f"mock_reset_{stamp}"
    if apply:
        backup_dir.mkdir(parents=True, exist_ok=True)

    report["deleted"] = _backup_and_delete(db, backup_dir, apply)
    report["files"] = _cleanup_files(backup_dir, apply)
    report["backup_dir"] = str(backup_dir) if apply else None

    if not apply:
        report["action"] = "dry_run"
        return report

    (backup_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    resets = list(state.get("resets") or [])[-19:]
    resets.append({
        "at": kst_now_iso(),
        "from": report["previous"],
        "deleted": report["deleted"],
        "backup_dir": str(backup_dir),
        "forced": bool(force and prev_fp == ident["fingerprint"]),
    })
    save_state({**ident, "recorded_at": kst_now_iso(), "resets": resets})
    report["action"] = "reset"
    return report


def format_reset_report(report: Dict[str, Any]) -> str:
    ident = report.get("identity") or {}
    prev = report.get("previous") or {}
    action = report.get("action")
    if action == "skipped_real":
        return "실전 모드 — 모의계좌 초기화 점검 생략"
    if action == "baseline":
        return f"모의계좌 기준값 등록: 계좌 {ident.get('account')} / 앱키 {ident.get('app_key')}"
    if action == "unchanged":
        return f"모의계좌 변경 없음: 계좌 {ident.get('account')} / 앱키 {ident.get('app_key')}"

    title = "모의계좌 교체 감지 — 데이터 초기화" if action == "reset" else "모의계좌 초기화 미리보기(dry-run)"
    lines = [
        f"🔄 {title}",
        f"이전: 계좌 {prev.get('account') or '-'} / 앱키 {prev.get('app_key') or '-'}",
        f"신규: 계좌 {ident.get('account')} / 앱키 {ident.get('app_key')}",
    ]
    for table, n in (report.get("deleted") or {}).items():
        lines.append(f"- {table}: {n:,}건")
    if report.get("files"):
        lines.append(f"- 파일: {', '.join(report['files'])}")
    if report.get("backup_dir"):
        lines.append(f"백업: {report['backup_dir']}")
    return "\n".join(lines)
