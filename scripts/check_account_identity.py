"""현재 .env 기준으로 토큰이 어느 계좌의 데이터를 반환하는지 확인."""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta

import aiohttp

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from api.kiwoom_api import KiwoomAPI
from core.config import Config


async def main():
    use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
    host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
    acnt = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if use_mock else Config.KIWOOM_ACCOUNT_NUMBER
    app_key = Config.KIWOOM_MOCK_APP_KEY if use_mock else Config.KIWOOM_APP_KEY

    print("=" * 60)
    print(f".env 계좌번호      : {acnt}")
    print(f".env APP_KEY(앞8)  : {app_key[:8]}...")
    print(f"mock 사용          : {use_mock}")
    print("=" * 60)

    api = KiwoomAPI()
    token = api.token_manager.get_valid_token()
    print(f"토큰 발급          : {'OK' if token else 'FAIL'}")

    # 1) kt00004 계좌평가 — 어떤 계좌가 잡히는지
    url = host + "/api/dostk/acnt"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "cont-yn": "N", "next-key": "", "api-id": "kt00004",
    }
    body = {"qry_tp": "0", "dmst_stex_tp": "KRX", "acnt_no": acnt}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=headers, json=body) as r:
            d = json.loads(await r.text())
    print(f"\n[kt00004] 계좌평가 return_code={d.get('return_code')}")
    print(f"  계좌명={d.get('acnt_nm')} 예수금={d.get('entr')} 추정자산={d.get('tot_evlt_amt') or d.get('prsm_dpst_aset_amt')}")
    hold = d.get("stk_acnt_evlt_prst") or []
    if isinstance(hold, dict):
        hold = [hold]
    print(f"  보유종목={len(hold)}")

    await asyncio.sleep(1.2)

    # 2) ka10073 실현손익 — 옛 데이터가 나오는지
    end_dt = datetime.now().strftime("%Y%m%d")
    strt_dt = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
    res = await api.get_daily_stock_realized_pnl(strt_dt, end_dt, stk_cd="")
    rows = res.get("items") or []
    print(f"\n[ka10073] 실현손익 success={res.get('success')} rows={len(rows)} 기간={strt_dt}~{end_dt}")
    if rows:
        print("  (이 데이터가 옛 계좌 것이면, 앱키가 옛 계좌에 묶여 있는 것)")
        for r in rows[:3]:
            print(f"   {r.get('dt')} {r.get('stk_nm')}({r.get('stk_cd')}) 손익={r.get('tdy_sel_pl')}")
    else:
        print("  → 실현손익 0건 (새 계좌 정상)")


if __name__ == "__main__":
    asyncio.run(main())
