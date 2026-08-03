"""활동 로그 — 매수 실패가 스캐너 노이즈에 밀려 사라지지 않는지."""
from __future__ import annotations

from utils.auto_trade_activity_log import AutoTradeActivityLog


def test_buy_failure_survives_scanner_flood():
    log = AutoTradeActivityLog(max_size=20, critical_size=5)
    log.log("BUY", "게이트 실패 HPSP: 당일 위치 과열 (0.65 > 0.64)", "warn", stock_code="403870")
    for i in range(50):
        log.log("SCANNER", f"진입 보류 [게이트] dummy{i}: VWAP 미만", "warn")
    recent = log.get_recent(20)
    assert any("HPSP" in (e.get("message") or "") for e in recent)
    assert any(e.get("source") == "BUY" for e in recent)


def test_sync_dedupe_same_second():
    log = AutoTradeActivityLog(max_size=10, critical_size=3)
    log.log("SYNC", "손절점검 · 장중", "info")
    log.log("SYNC", "손절점검 · 장중", "info")
    assert len(log.get_recent(10)) == 1
