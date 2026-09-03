"""키움 API 트래픽 조절 — 스캔 버스트·우선순위·대시보드 live 지연."""
from __future__ import annotations

import time
from enum import IntEnum
from typing import Any, Dict, Optional

SCAN_BURST_DEFER_SEC = 10.0
YIELD_ON_USAGE_PCT = 80.0

_scanner_active = False
_defer_low_until = 0.0
_scan_started_mono: Optional[float] = None


class APIPriority(IntEnum):
  CRITICAL = 0   # 손절 모니터
  HIGH = 1       # 스캐너·매수
  NORMAL = 2     # 일반 REST
  LOW = 3        # 대시보드 live·스파크라인


def mark_scan_start() -> None:
  global _scanner_active, _scan_started_mono
  _scanner_active = True
  _scan_started_mono = time.monotonic()


def mark_scan_end() -> None:
  global _scanner_active, _defer_low_until, _scan_started_mono
  _scanner_active = False
  _scan_started_mono = None
  _defer_low_until = time.monotonic() + SCAN_BURST_DEFER_SEC


def is_scanner_active() -> bool:
  return _scanner_active


def should_defer_dashboard_live() -> bool:
  return _scanner_active or time.monotonic() < _defer_low_until


def effective_max_wait(priority: APIPriority) -> float:
  return {
    APIPriority.CRITICAL: 15.0,
    APIPriority.HIGH: 10.0,
    APIPriority.NORMAL: 6.0,
    APIPriority.LOW: 3.0,
  }.get(priority, 6.0)


def should_yield_low_priority() -> bool:
  if should_defer_dashboard_live():
    return True
  try:
    from api.api_rate_limiter import api_rate_limiter
    info = api_rate_limiter.get_status_info()
    return float(info.get("usage_percent") or 0) > YIELD_ON_USAGE_PCT
  except Exception:
    return False


def get_traffic_status() -> Dict[str, Any]:
  """대시보드·진단용 트래픽 가드 스냅샷.

  live 지연은 종목 수 임계값이 아니라
  (1) 스캔 진행 중 또는 (2) 스캔 직후 SCAN_BURST_DEFER_SEC 초
  이면 발생한다. API 사용률이 YIELD_ON_USAGE_PCT 초과면
  LOW 우선순위 호출만 추가로 양보한다.
  """
  now = time.monotonic()
  scanner_active = _scanner_active
  burst_left = max(0.0, _defer_low_until - now)
  defer = scanner_active or burst_left > 0
  if scanner_active:
    reason = "scan"
    remaining = None
    elapsed = round(now - _scan_started_mono, 1) if _scan_started_mono is not None else None
  elif burst_left > 0:
    reason = "post_scan_burst"
    remaining = round(burst_left, 1)
    elapsed = None
  else:
    reason = None
    remaining = 0.0
    elapsed = None
  return {
    "defer_dashboard_live": defer,
    "scanner_active": scanner_active,
    "defer_reason": reason,
    "defer_remaining_sec": remaining,
    "scan_elapsed_sec": elapsed,
    "post_scan_burst_sec": SCAN_BURST_DEFER_SEC,
    "yield_on_usage_pct": YIELD_ON_USAGE_PCT,
    # 종목 N개 이상이면 지연 — 같은 임계값은 없음 (스캔 1회 시작 시 즉시)
    "defer_stock_threshold": None,
    "defer_trigger": "scan_active_or_post_burst",
  }
