"""ka10076 체결 내역 조회 진단 — 성과통계 API 데이터 존재 여부 확인."""
import asyncio
import json
import logging
import os
import sys

import aiohttp

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from api.kiwoom_api import KiwoomAPI
from core.config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("check_ka10076")


async def main():
    use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
    host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
    account = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if use_mock else Config.KIWOOM_ACCOUNT_NUMBER

    print("=" * 60)
    print("[1] 설정")
    print(f"  mock 계좌 사용: {use_mock}")
    print(f"  API host: {host}")
    print(f"  계좌번호: {account}")
    print("=" * 60)

    api = KiwoomAPI()
    token = api.token_manager.get_valid_token()
    token_ok = "OK" if token else "FAIL"
    token_len = len(token) if token else 0
    print(f"[2] 토큰: {token_ok} (length={token_len})")

    url = host + "/api/dostk/acnt"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "api-id": "ka10076",
        "cont-yn": "N",
        "next-key": "",
    }
    body = {
        "stk_cd": "",
        "qry_tp": "0",
        "sell_tp": "0",
        "ord_no": "",
        "stex_tp": "0",
        "acnt_no": account or "",
    }
    print("[3] ka10076 요청 body:", json.dumps(body, ensure_ascii=False))

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
            text = await resp.text()
            print(f"[4] HTTP status: {resp.status}")
            print(f"    cont-yn header: {resp.headers.get('cont-yn') or resp.headers.get('Cont-Yn')}")
            print(f"    next-key header: {resp.headers.get('next-key') or resp.headers.get('Next-Key')}")
            data = json.loads(text)
            print("[5] 응답 JSON (전체):")
            print(json.dumps(data, ensure_ascii=False, indent=2))
            cntr = data.get("cntr") or data.get("output") or []
            print(f"[6] cntr 건수: {len(cntr)}")
            if cntr:
                print("[6-1] 첫 건 필드:", list(cntr[0].keys()))
                print("[6-1] 첫 건:", json.dumps(cntr[0], ensure_ascii=False))

    print("=" * 60)
    print("[7] get_executions() 래퍼 호출")
    res = await api.get_executions(sell_tp="0", stex_tp="0")
    items = res.get("items") or []
    print(f"  success={res.get('success')} error={res.get('error')} items={len(items)}")

    for sell_tp, label in [("0", "전체"), ("1", "매도만"), ("2", "매수만")]:
        r = await api.get_executions(sell_tp=sell_tp, stex_tp="0")
        n = len(r.get("items") or [])
        print(f"[9] sell_tp={sell_tp}({label}): items={n} success={r.get('success')} err={r.get('error')}")

    print("=" * 60)
    print("결론: cntr=0 이면 키움 API 기준 체결 내역 없음 (성과통계 0이 정상)")


if __name__ == "__main__":
    asyncio.run(main())
