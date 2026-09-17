"""앱·배치 로그를 최근 N일만 남기고 정리.

계속 append 되는 stock_pipeline.log / cron.log 는 mtime이 항상 오늘이라
파일 삭제만으로는 안 줄어든다. 타임스탬프 기준으로 본문을 자른다.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Sequence

from utils.datetime_kst import KST, as_kst, now_kst

logger = logging.getLogger(__name__)

DEFAULT_KEEP_DAYS = 7
UNTIMESTAMPED_TAIL_BYTES = 8 * 1024 * 1024
UNTIMESTAMPED_TRIM_AFTER = 32 * 1024 * 1024

LIVE_LOG_NAMES = {
    "app.log",
    "cron.log",
    "log_cleanup_batch.log",
    "server.log",
    "stock_pipeline.log",
    "uvicorn.log",
}

_SKIP_DIR_NAMES = {".git", ".venv", "venv", "node_modules", "__pycache__", "backup"}

# 줄 앞부분 시각: 2026-09-17 08:51:06 / 2026-09-17T00:31:28
_TS_PREFIX = re.compile(
    r"^[\[=#*\-\s]*"
    r"(?P<date>\d{4}-\d{2}-\d{2})[ T]"
    r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})"
)


@dataclass
class FileCleanupResult:
    path: str
    action: str
    before_bytes: int = 0
    after_bytes: int = 0
    note: str = ""

    @property
    def freed_bytes(self) -> int:
        return max(0, self.before_bytes - self.after_bytes)


@dataclass
class CleanupSummary:
    keep_days: int
    cutoff: datetime
    results: List[FileCleanupResult] = field(default_factory=list)

    @property
    def freed_bytes(self) -> int:
        return sum(r.freed_bytes for r in self.results)

    @property
    def deleted(self) -> int:
        return sum(1 for r in self.results if r.action == "delete")

    @property
    def trimmed(self) -> int:
        return sum(1 for r in self.results if r.action == "trim")


def parse_log_line_ts(line: str) -> Optional[datetime]:
    m = _TS_PREFIX.match(line)
    if not m:
        return None
    try:
        dt = datetime.strptime(
            f"{m.group('date')} {m.group('h')}:{m.group('m')}:{m.group('s')}",
            "%Y-%m-%d %H:%M:%S",
        )
    except ValueError:
        return None
    return dt.replace(tzinfo=KST)


def cutoff_kst(keep_days: int = DEFAULT_KEEP_DAYS, now: Optional[datetime] = None) -> datetime:
    days = max(1, int(keep_days))
    return as_kst(now) - timedelta(days=days)


def iter_log_files(root: Path) -> List[Path]:
    """프로젝트 루트·logs/ 의 .log · .jsonl (상태 JSON·DB는 제외)."""
    root = root.resolve()
    found: List[Path] = []
    logs_dir = root / "logs"

    for path in root.glob("*.log"):
        if path.is_file():
            found.append(path)

    if logs_dir.is_dir():
        for dirpath, dirnames, filenames in os.walk(logs_dir):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
            for name in filenames:
                lower = name.lower()
                if lower.endswith(".log") or lower.endswith(".jsonl") or ".log." in lower:
                    found.append(Path(dirpath) / name)

    uniq = []
    seen = set()
    for p in found:
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    uniq.sort(key=lambda p: str(p))
    return uniq


def _is_live_log(path: Path) -> bool:
    return path.name.lower() in LIVE_LOG_NAMES


def _file_mtime_kst(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=KST)


def _replace_keep_inode(original: Path, tmp: Path) -> None:
    """같은 inode에 덮어써 uvicorn 등이 열어 둔 핸들을 유지한다."""
    with original.open("r+b") as dest, tmp.open("rb") as src:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dest.write(chunk)
        dest.truncate()
    tmp.unlink(missing_ok=True)


def _write_filtered(src: Path, dest: Path, cutoff: datetime) -> tuple[int, int, bool]:
    """src 를 cutoff 이후만 dest 에 쓴다. (kept_lines, bytes_out, saw_timestamp)."""
    kept_lines = 0
    bytes_out = 0
    keeping = False
    saw_ts = False
    with src.open("r", encoding="utf-8", errors="replace", newline="") as inf, dest.open(
        "w", encoding="utf-8", errors="replace", newline="",
    ) as out:
        for line in inf:
            ts = parse_log_line_ts(line)
            if ts is not None:
                saw_ts = True
                keeping = ts >= cutoff
            if keeping:
                out.write(line)
                kept_lines += 1
                bytes_out += len(line.encode("utf-8", errors="replace"))
    return kept_lines, bytes_out, saw_ts


def _tail_bytes(src: Path, dest: Path, nbytes: int) -> int:
    size = src.stat().st_size
    keep = min(size, max(0, nbytes))
    with src.open("rb") as inf, dest.open("wb") as out:
        if keep < size:
            inf.seek(size - keep)
        while True:
            chunk = inf.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return keep


def cleanup_log_file(
    path: Path,
    *,
    cutoff: datetime,
    dry_run: bool = False,
) -> FileCleanupResult:
    path = path.resolve()
    before = path.stat().st_size if path.exists() else 0
    rel = str(path)

    if not path.exists() or not path.is_file():
        return FileCleanupResult(rel, "skip", before, before, "없음")

    mtime = _file_mtime_kst(path)
    live = _is_live_log(path)

    if before == 0:
        if not live and mtime < cutoff:
            if not dry_run:
                path.unlink(missing_ok=True)
            return FileCleanupResult(rel, "delete", 0, 0, "빈 파일")
        return FileCleanupResult(rel, "skip", 0, 0, "빈 파일")

    tmp_fd, tmp_name = tempfile.mkstemp(prefix="logtrim_", suffix=".tmp", dir=str(path.parent))
    os.close(tmp_fd)
    tmp = Path(tmp_name)
    try:
        kept_lines, bytes_out, saw_ts = _write_filtered(path, tmp, cutoff)
        if saw_ts:
            if bytes_out >= before and kept_lines > 0:
                tmp.unlink(missing_ok=True)
                return FileCleanupResult(rel, "skip", before, before, "이미 7일 이내")
            if dry_run:
                tmp.unlink(missing_ok=True)
                after = bytes_out
                action = "trim" if after > 0 or live else "delete"
                return FileCleanupResult(rel, action, before, after if action == "trim" else 0)
            if bytes_out == 0 and not live:
                path.unlink(missing_ok=True)
                tmp.unlink(missing_ok=True)
                return FileCleanupResult(rel, "delete", before, 0, "보관 기간 이전")
            _replace_keep_inode(path, tmp)
            after = path.stat().st_size
            return FileCleanupResult(rel, "trim", before, after, f"{kept_lines}줄 유지")

        # 타임스탬프 없음: 오래된 파일은 삭제, 큰 파일은 꼬리만
        tmp.unlink(missing_ok=True)
        if mtime < cutoff and not live:
            if not dry_run:
                path.unlink(missing_ok=True)
            return FileCleanupResult(rel, "delete", before, 0, "mtime 초과·시각 없음")
        if before > UNTIMESTAMPED_TRIM_AFTER:
            if dry_run:
                after = min(before, UNTIMESTAMPED_TAIL_BYTES)
                return FileCleanupResult(rel, "trim", before, after, "시각 없음·꼬리 유지")
            tmp_fd, tmp_name = tempfile.mkstemp(prefix="logtail_", suffix=".tmp", dir=str(path.parent))
            os.close(tmp_fd)
            tmp = Path(tmp_name)
            _tail_bytes(path, tmp, UNTIMESTAMPED_TAIL_BYTES)
            _replace_keep_inode(path, tmp)
            after = path.stat().st_size
            return FileCleanupResult(rel, "trim", before, after, "시각 없음·꼬리 유지")
        return FileCleanupResult(rel, "skip", before, before, "시각 없음·최근 파일")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def cleanup_logs(
    root: Path,
    *,
    keep_days: int = DEFAULT_KEEP_DAYS,
    dry_run: bool = False,
    now: Optional[datetime] = None,
    skip: Optional[Sequence[Path]] = None,
) -> CleanupSummary:
    cutoff = cutoff_kst(keep_days, now=now)
    skip_res = {p.resolve() for p in (skip or [])}
    summary = CleanupSummary(keep_days=max(1, int(keep_days)), cutoff=cutoff)
    for path in iter_log_files(root):
        if path.resolve() in skip_res:
            continue
        try:
            summary.results.append(
                cleanup_log_file(path, cutoff=cutoff, dry_run=dry_run)
            )
        except Exception as e:
            logger.warning("로그 정리 실패 %s: %s", path, e)
            summary.results.append(
                FileCleanupResult(str(path), "error", 0, 0, str(e))
            )
    return summary


def format_bytes(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(v)}{unit}"
            return f"{v:.1f}{unit}"
        v /= 1024
    return f"{n}B"


def format_summary(summary: CleanupSummary, *, dry_run: bool = False) -> str:
    prefix = "[미리보기] " if dry_run else ""
    lines = [
        f"{prefix}보관 {summary.keep_days}일 · 기준 {summary.cutoff.strftime('%Y-%m-%d %H:%M')} KST",
        f"삭제 {summary.deleted} · 잘라냄 {summary.trimmed} · 확보 {format_bytes(summary.freed_bytes)}",
    ]
    for r in summary.results:
        if r.action in ("skip",) and r.freed_bytes == 0:
            continue
        lines.append(
            f"  [{r.action}] {r.path} {format_bytes(r.before_bytes)} → {format_bytes(r.after_bytes)}"
            + (f" ({r.note})" if r.note else "")
        )
    return "\n".join(lines)
