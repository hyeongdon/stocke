"""ka10073(일자별종목별실현손익_기간) 동작 확인 — 빈 종목코드/특정 종목 비교."""
import asyncio
import json
import os
import sys

import aiohttp

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from api.kiwoom_api import KiwoomAPI
from core.config import Config


async def call(stk_cd: str, strt_dt: str, end_dt: str):
    api = KiwoomAPI()
    token = api.token_manager.get_valid_token()
    host = Config.KIWOOM_MOCK_API_URL if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_REAL_API_URL
    url = host + "/api/dostk/acnt"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "cont-yn": "N",
        "next-key": "",
        "api-id": "ka10073",
    }
    body = {"stk_cd": stk_cd, "strt_dt": strt_dt, "end_dt": end_dt}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=headers, json=body) as r:
            data = json.loads(await r.text())
    rows = data.get("dt_stk_rlzt_pl") or []
    msg = (data.get("return_msg") or "").strip()
    print(f'stk_cd="{stk_cd}" code={data.get("return_code")} rows={len(rows)} msg={msg[:50]}')
    codes = {}
    for row in rows:
        codes.setdefault(row.get("stk_cd", ""), 0)
        codes[row.get("stk_cd", "")] += 1
    if codes:
        print("  종목별 행수:", codes)
        print("  sample:", json.dumps(rows[0], ensure_ascii=False))
    return rows


async def main():
    strt, end = "20260601", "20260623"
    print("=== ka10073 빈 종목코드(전체 조회 가능?) ===")
    await call("", strt, end)
    await asyncio.sleep(1.5)


if __name__ == "__main__":
    asyncio.run(main())
