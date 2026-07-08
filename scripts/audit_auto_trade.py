#!/usr/bin/env python3
"""자동매매 기능 점검 — 장외 시간에도 설정·연결·서브시스템 상태 확인."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.models import AutoTradeSettings, get_db, init_db
from utils.auto_trade_engine import check_daily_limits, effective_min_change_rate, has_buy_conditions, in_trade_hours


def _check(name: str, ok: bool, detail: str = "") -> dict:
    status = "PASS" if ok else "FAIL"
    line = f"  [{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    return {"name": name, "ok": ok, "detail": detail}


async def main():
    print("=" * 60)
    print("자동매매 기능 점검 (audit_auto_trade)")
    print("=" * 60)

    init_db()
    checks = []

    # 1) 환경변수
    mock = Config.KIWOOM_USE_MOCK_ACCOUNT
    acct = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if mock else Config.KIWOOM_ACCOUNT_NUMBER
    checks.append(_check("모의/실전 모드", True, f"{'모의' if mock else '실전'}"))
    checks.append(_check("계좌번호 설정", bool(acct), acct or "미설정"))

    app_key = Config.KIWOOM_MOCK_APP_KEY if mock else Config.KIWOOM_APP_KEY
    checks.append(_check("API 앱키 설정", bool(app_key), "설정됨" if app_key else "미설정"))

    # 2) DB 설정
    settings = None
    for db in get_db():
        settings = db.query(AutoTradeSettings).first()
        break

    if not settings:
        checks.append(_check("자동매매 DB 설정", False, "레코드 없음"))
    else:
        checks.append(_check("자동매매 DB 설정", True, f"is_enabled={settings.is_enabled}"))
        checks.append(_check(
            "매수 조건 설정",
            has_buy_conditions(settings),
            f"buy_below_price={settings.buy_below_price}, "
            f"min_change_rate={effective_min_change_rate(settings)}",
        ))
        checks.append(_check(
            "매매 시간대",
            in_trade_hours(settings) or mock,
            f"{settings.trade_start_time}~{settings.trade_end_time} (장외{'·모의우회' if mock else ''})",
        ))
        phase2 = []
        if settings.use_entry_gate:
            phase2.append("entry_gate")
        if (settings.sizing_method or "").upper() == "PYRAMIDING":
            phase2.append("pyramiding")
        if settings.daily_loss_limit or settings.daily_profit_target:
            phase2.append("daily_limits")
        if (settings.order_method or "MARKET").upper() == "LIMIT":
            phase2.append("limit_orders")
        checks.append(_check("Phase2 설정", True, ", ".join(phase2) or "기본(FIXED/MARKET)"))

        daily_halt = check_daily_limits(settings)
        checks.append(_check("일일 한도", daily_halt is None, daily_halt or "정상"))

    # 3) API 연결
    from api.kiwoom_api import KiwoomAPI
    api = KiwoomAPI()
    authed = api.authenticate()
    checks.append(_check("키움 API 인증", authed, "토큰 발급 성공" if authed else "인증 실패 — .env 확인"))

    if authed and acct:
        bal = await api.get_account_balance(acct)
        checks.append(_check("계좌 잔고 조회", bool(bal), "성공" if bal else "실패"))
    else:
        checks.append(_check("계좌 잔고 조회", False, "인증/계좌 없음"))

    # 4) 서브시스템 — 실행 중 서버의 /trading/readiness 로 확인
    enabled = bool(settings and settings.is_enabled)
    # 서버(uvicorn) 기동 시에만 running=True — 스크립트 단독 실행 시 False는 정상
    import urllib.request
    server_up = False
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/trading/settings", timeout=2)
        server_up = True
    except Exception:
        pass

    if server_up:
        import json
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/trading/readiness", timeout=3) as resp:
                readiness = json.loads(resp.read().decode())
            rc = readiness.get("checks", {})
            checks.append(_check("스캐너 실행", rc.get("scanner_running") == enabled,
                                 f"running={rc.get('scanner_running')}, enabled={enabled}"))
            checks.append(_check("매수 실행기", rc.get("buy_executor_running") == enabled,
                                 f"running={rc.get('buy_executor_running')}"))
            checks.append(_check("손절 모니터", rc.get("stop_loss_running") == enabled,
                                 f"running={rc.get('stop_loss_running')}"))
            checks.append(_check("종합 준비", readiness.get("ready"), "API /trading/readiness"))
        except Exception as e:
            checks.append(_check("서버 readiness", False, str(e)))
    else:
        checks.append(_check("서버 기동", False, "uvicorn 미실행 — running 상태는 서버 재시작 후 확인"))

    # 5) 종합
    failed = [c for c in checks if not c["ok"]]
    print("-" * 60)
    if not failed:
        print("✅ 기능적으로 자동매매 파이프라인 준비 완료")
        if not enabled:
            print("   → 대시보드에서 '자동매매 ON' 후 장중에 동작합니다.")
    else:
        print(f"⚠️  {len(failed)}개 항목 점검 필요:")
        for f in failed:
            print(f"   - {f['name']}: {f['detail']}")
    print("=" * 60)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
