"""키움 API 트래픽 조절 — 스캔 버스트·우선순위·대시보드 live 지연."""
from __future__ import annotations

import time
from enum import IntEnum

SCAN_BURST_DEFER_SEC = 10.0

_scanner_active = False
_defer_low_until = 0.0


class APIPriority(IntEnum):
  CRITICAL = 0   # 손절 모니터
  HIGH = 1       # 스캐너·매수
  NORMAL = 2     # 일반 REST
  LOW = 3        # 대시보드 live·스파크라인


def mark_scan_start() -> None:
  global _scanner_active
  _scanner_active = True


def mark_scan_end() -> None:
  global _scanner_active, _defer_low_until
  _scanner_active = False
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
    return float(info.get("usage_percent") or 0) > 80.0
  except Exception:
    return False
