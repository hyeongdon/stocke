"""Compare ka10030 mang_stk_incls values — raw API rows before post-filter."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.kiwoom_api import KiwoomAPI

MANG_VALUES = ("0", "16", "14", "1", "4", "15", "3")


async def probe_one(api: KiwoomAPI, mang: str, top_n: int = 20):
    res = await api.get_volume_rank(
        market="000",
        sort_tp="1",
        limit=top_n,
        screener_filters=False,
        mang_stk_incls=mang,
        trde_qty_tp="200",
        stex_tp="1",
    )
    if not res.get("success"):
        return mang, None, res.get("error")
    items = res.get("items") or []
    etf_like = [it for it in items if KiwoomAPI._is_etf_like_name(it.get("stock_name", ""))]
    names = [f"{it['stock_name']}({it['product_type']})" for it in items[:top_n]]
    etf_names = [it["stock_name"] for it in etf_like[:8]]
    return mang, {"count": len(items), "etf_count": len(etf_like), "names": names, "etf_names": etf_names}, None


async def main():
    api = KiwoomAPI()
    print("=== ka10030 mang_stk_incls comparison (KRX, 20manju+, top 20 raw) ===")
    for mang in MANG_VALUES:
        mang, data, err = await probe_one(api, mang)
        if err:
            print(f"  mang={mang:2} FAIL: {err}")
            continue
        print(f"  mang={mang:2} rows={data['count']:2} etf_like={data['etf_count']:2}")
        if data["etf_names"]:
            print(f"         ETF leaks: {', '.join(data['etf_names'])}")
        else:
            print(f"         sample: {', '.join(data['names'][:5])}")

    print("\n=== screener path (mang=16 + post-filter, limit 50) ===")
    res = await api.get_volume_rank(market="000", sort_tp="1", limit=50, screener_filters=True)
    if res.get("success"):
        items = res.get("items") or []
        leaks = [it for it in items if KiwoomAPI._is_etf_like_name(it.get("stock_name", ""))]
        print(f"  filtered={len(items)} etf_leaks={len(leaks)} raw_count={res.get('raw_count')} excluded={res.get('excluded_etf_count')}")
        for it in items[:8]:
            print(f"    {it['stock_name']} ({it['product_type']})")
    else:
        print(f"  FAIL: {res.get('error')}")


if __name__ == "__main__":
    asyncio.run(main())
