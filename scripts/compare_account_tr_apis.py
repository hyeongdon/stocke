"""계좌 TR 비교: ka10076(당일체결) vs ka10074(일자별실현손익) vs kt00004(잔고) vs 로컬 DB."""
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
from core.models import SellOrder, SessionLocal, init_db


async def call_tr(api_id: str, body: dict) -> dict:
    api = KiwoomAPI()
    token = api.token_manager.get_valid_token()
    use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
    host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
    account = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if use_mock else Config.KIWOOM_ACCOUNT_NUMBER
    if "acnt_no" not in body and account:
        body = {**body, "acnt_no": account}
    url = host + "/api/dostk/acnt"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "cont-yn": "N",
        "next-key": "",
        "api-id": api_id,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
            text = await resp.text()
            try:
                data = json.loads(text)
            except Exception:
                data = {"_raw": text[:500]}
            return {"status": resp.status, "api_id": api_id, "body": body, "data": data}


def list_len(data: dict, *keys):
    for k in keys:
        v = data.get(k)
        if isinstance(v, list):
            return k, len(v)
    return None, 0


async def main():
    use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
    account = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if use_mock else Config.KIWOOM_ACCOUNT_NUMBER
    today = datetime.now()
    end_dt = today.strftime("%Y%m%d")
    start_dt = (today - timedelta(days=30)).strftime("%Y%m%d")

    print("=" * 70)
    print(f"계좌: {account}  mock={use_mock}  기간: {start_dt}~{end_dt}")
    print("=" * 70)

    # 1) ka10076 — 당일 체결 (종목코드 불필요, qry_tp=0 전체)
    r76 = await call_tr("ka10076", {
        "stk_cd": "", "qry_tp": "0", "sell_tp": "0", "ord_no": "", "stex_tp": "0",
    })
    d76 = r76["data"]
    k76, n76 = list_len(d76, "cntr", "output", "filled_list")
    print(f"\n[ka10076] today fills - HTTP {r76['status']} return_code={d76.get('return_code')} {k76 or 'list'}={n76}")
    if n76:
        print("  샘플:", json.dumps(d76.get(k76, [])[0], ensure_ascii=False)[:300])

    # 2) ka10074 — 일자별 실현손익 (기간 조회, stk_cd 없음)
    await asyncio.sleep(1.2)
    r74 = await call_tr("ka10074", {"strt_dt": start_dt, "end_dt": end_dt})
    d74 = r74["data"]
    k74, n74 = list_len(d74, "dt_rlzt_pl", "output")
    print(f"\n[ka10074] daily realized P/L - HTTP {r74['status']} return_code={d74.get('return_code')}")
    print(f"  rlzt_pl(실현손익)={d74.get('rlzt_pl')} tot_buy={d74.get('tot_buy_amt')} tot_sell={d74.get('tot_sell_amt')}")
    print(f"  dt_rlzt_pl 건수={n74}")
    if n74:
        print("  일자별:", json.dumps(d74.get(k74 or "dt_rlzt_pl", [])[:3], ensure_ascii=False, indent=2))

    # 3) kt00004 — 계좌평가/보유종목
    await asyncio.sleep(1.2)
    r04 = await call_tr("kt00004", {"qry_tp": "0", "dmst_stex_tp": "KRX"})
    d04 = r04["data"]
    holdings = d04.get("stk_acnt_evlt_prst") or []
    if isinstance(holdings, dict):
        holdings = [holdings]
    print(f"\n[kt00004] account - HTTP {r04['status']} return_code={d04.get('return_code')} holdings={len(holdings)}")
    print(f"  추정자산={d04.get('aset_amt') or d04.get('tot_evlt_amt')} 예수금={d04.get('entr')}")

    # 4) ka10075 — 미체결
    await asyncio.sleep(1.2)
    r75 = await call_tr("ka10075", {
        "all_stk_tp": "0", "trde_tp": "0", "stk_cd": "", "stex_tp": "0",
    })
    d75 = r75["data"]
    k75, n75 = list_len(d75, "oso", "output", "unfilled_list")
    print(f"\n[ka10075] unfilled - HTTP {r75['status']} return_code={d75.get('return_code')} {k75 or 'list'}={n75}")

    # 5) 로컬 DB sell_orders
    init_db()
    db = SessionLocal()
    try:
        rows = db.query(SellOrder).filter(SellOrder.status == "COMPLETED").all()
        sample = sum(1 for r in rows if (r.stock_code or "").startswith("SAMPLE_"))
        real = len(rows) - sample
        print(f"\n[local DB] sell_orders COMPLETED={len(rows)} (real={real}, sample={sample})")
        for r in rows[:5]:
            print(f"  {r.stock_code} {r.stock_name} pl={r.profit_loss} {r.completed_at}")
    finally:
        db.close()

    print("\n" + "=" * 70)
    print("해석:")
    print("  ka10076 = today fills only (0 if no trade today)")
    print("  ka10074 = realized P/L by date range")
    print("  performance stats uses ka10073 → DB → ka10074")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
