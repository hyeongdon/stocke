import asyncio, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.models import get_db, AutoTradeSettings
from api.kiwoom_api import KiwoomAPI
from utils.auto_trade_engine import passes_buy_price_conditions, check_entry_gate, effective_min_change_rate

async def main():
    for db in get_db():
        s = db.query(AutoTradeSettings).first()
        break
    api = KiwoomAPI()
    code = "005930"
    snap = await api.get_stock_snapshot(code)
    print("snapshot success:", snap.get("success"))
    ss = snap.get("snapshot") or {}
    price = ss.get("current_price")
    chg = float(str(ss.get("change_rate","0")).replace(",",""))
    print(f"삼성전자 price={price} chg={chg}% min_rate={effective_min_change_rate(s)}")
    print("price_cond:", passes_buy_price_conditions(s, price, chg))
    ok, reason = await check_entry_gate(api, s, code, price)
    print("gate:", ok, reason)
    # volume rank in scanner context
    res = await api.get_volume_rank(market="000", sort_tp="3", limit=50)
    print("volume_rank success:", res.get("success"), "items:", len(res.get("items") or []))

if __name__ == "__main__":
    asyncio.run(main())
