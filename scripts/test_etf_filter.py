"""ETF 분류·후처리 및 ka10030 API 필터 동작 확인."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.kiwoom_api import KiwoomAPI, SCREENER_VOLUME_RANK_FILTERS


def test_classification():
    names = [
        "KODEX 인버스",
        "KODEX 200선물인버스2X",
        "KODEX SK하이닉스단일종목레버리지",
        "삼성전자",
        "TIGER 200",
        "SOL SK하이닉스선물단일종목인버스2X",
    ]
    print("=== 분류/후처리 ===")
    for n in names:
        pt = KiwoomAPI.classify_product_type(n)
        etf = KiwoomAPI._is_etf_like_name(n)
        kept, _ex = KiwoomAPI._post_filter_screener_items([{"stock_name": n, "product_type": pt}])
        print(f"  {n:35} type={pt:16} etf_like={etf} kept={len(kept)}")


async def compare_mang(api: KiwoomAPI, mang: str, top_n: int = 15):
    await asyncio.sleep(1.2)
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
        print(f"  mang={mang:2} FAIL: {res.get('error')}")
        return
    items = res.get("items") or []
    etf = [it for it in items if KiwoomAPI._is_etf_like_name(it.get("stock_name", ""))]
    print(f"  mang={mang:2} rows={len(items):2} etf_like={len(etf):2}", end="")
    if etf:
        print(f"  leaks: {', '.join(it['stock_name'] for it in etf[:4])}")
    else:
        sample = ", ".join(it["stock_name"] for it in items[:3])
        print(f"  sample: {sample}")


async def test_live_api():
    print("\n=== ka10030 mang_stk_incls 비교 (KRX, 20만주+, raw top 15) ===")
    api = KiwoomAPI()
    for mang in ("0", "16", "14", "4", "15"):
        await compare_mang(api, mang)

    print("\n=== ka10030 스크리너 경로 (mang=16 + 후처리, limit 50) ===")
    print("body defaults:", SCREENER_VOLUME_RANK_FILTERS)
    await asyncio.sleep(1.5)
    res = await api.get_volume_rank(market="000", sort_tp="1", limit=50, screener_filters=True)
    if not res.get("success"):
        print("FAIL:", res.get("error"))
        return
    items = res.get("items") or []
    etf_leaks = [it for it in items if KiwoomAPI._is_etf_like_name(it.get("stock_name", ""))]
    print(
        f"  filtered={len(items)} raw={res.get('raw_count')} "
        f"excluded_etf={res.get('excluded_etf_count')} etf_leaks={len(etf_leaks)}"
    )
    if etf_leaks:
        print("  LEAKED:")
        for it in etf_leaks[:10]:
            print(f"    {it['stock_name']} ({it['stock_code']}) {it['product_type']}")
    else:
        print("  OK - no ETF-like in filtered result")
        for it in items[:8]:
            print(f"    {it['stock_name']} ({it['stock_code']}) {it['product_type']}")


if __name__ == "__main__":
    test_classification()
    asyncio.run(test_live_api())
