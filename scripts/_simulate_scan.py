"""Simulate one scanner pass — why no buy signals?"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.models import AutoTradeSettings, get_db
from api.kiwoom_api import KiwoomAPI
from utils.auto_trade_engine import (
    check_entry_gate,
    effective_min_change_rate,
    has_buy_conditions,
    new_buy_block_reason,
    passes_buy_price_conditions,
)


async def main():
    settings = None
    for db in get_db():
        settings = db.query(AutoTradeSettings).first()
        break
    if not settings:
        print("NO SETTINGS")
        return

    print("=== block checks ===")
    print("has_buy_conditions:", has_buy_conditions(settings))
    print("min_change_rate:", effective_min_change_rate(settings))
    print("buy_below_price:", settings.buy_below_price)
    print("new_buy_block:", new_buy_block_reason(settings))
    print("use_entry_gate:", settings.use_entry_gate)
    print("require_above_vwap:", settings.require_above_vwap)
    print("watchlist:", (settings.watchlist_codes or "")[:80])

    api = KiwoomAPI()
    res = await api.get_volume_rank(market="000", sort_tp="3", limit=30)
    print("\n=== volume rank ===")
    print("success:", res.get("success"), "msg:", res.get("message") or res.get("error"))
    items = res.get("items") or []
    print("raw count:", len(items))

    targets = []
    for it in items:
        name = it.get("stock_name", "")
        if not KiwoomAPI._is_screener_stock(name, it.get("product_type")):
            continue
        code = it.get("stock_code", "")
        if code:
            targets.append(it)

    print("screener targets:", len(targets))
    if not targets:
        return

    price_pass = 0
    gate_pass = 0
    reasons = {"price": {}, "gate": {}}

    for it in targets[:20]:
        code = KiwoomAPI.normalize_stock_code(it.get("stock_code", ""))
        name = it.get("stock_name") or code
        price = it.get("current_price")
        change_rate = it.get("change_rate")
        if not price or price <= 0:
            snap = await api.get_stock_snapshot(code)
            if snap.get("success"):
                s = snap.get("snapshot") or {}
                price = s.get("current_price") or price
                if change_rate is None:
                    try:
                        change_rate = float(str(s.get("change_rate", "0")).replace(",", ""))
                    except (TypeError, ValueError):
                        change_rate = None
        if not price:
            reasons["price"]["no_price"] = reasons["price"].get("no_price", 0) + 1
            continue

        if not passes_buy_price_conditions(settings, price, change_rate):
            key = f"rate={change_rate}% price={price}"
            reasons["price"]["fail"] = reasons["price"].get("fail", 0) + 1
            print(f"  PRICE FAIL {name}({code}) chg={change_rate}% price={price}")
            continue
        price_pass += 1

        ok, reason = await check_entry_gate(api, settings, code, price)
        if not ok:
            reasons["gate"][reason.split("(")[0].strip()] = reasons["gate"].get(reason.split("(")[0].strip(), 0) + 1
            print(f"  GATE FAIL {name}({code}) chg={change_rate}% — {reason}")
            continue
        gate_pass += 1
        print(f"  PASS {name}({code}) chg={change_rate}% price={price} — {reason}")
        await asyncio.sleep(0.3)

    print("\n=== summary (top 20) ===")
    print("price_pass:", price_pass, "gate_pass:", gate_pass)
    print("price reasons:", reasons["price"])
    print("gate reasons:", reasons["gate"])


if __name__ == "__main__":
    asyncio.run(main())
