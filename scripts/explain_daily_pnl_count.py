"""일별 손익 '청산' 건수가 어떻게 집계되는지 출력."""
import asyncio
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.kiwoom_api import KiwoomAPI
from utils.performance_stats import trades_from_realized_pnl, compute_performance


async def main():
    end_dt = datetime.now().strftime("%Y%m%d")
    strt_dt = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
    today = datetime.now().strftime("%Y-%m-%d")

    api = KiwoomAPI()
    res = await api.get_daily_stock_realized_pnl(strt_dt, end_dt, stk_cd="")
    items = res.get("items") or []
    trades = trades_from_realized_pnl(items)

    by_date = defaultdict(list)
    for t in trades:
        by_date[t["date"]].append(t)

    today_rows = by_date.get(today, [])
    print(f"=== ka10073 원본 → trade 변환 (오늘 {today}) ===")
    print(f"API 행 수: {len(today_rows)}  (= 일별표 '청산' 건수)\n")
    for i, t in enumerate(today_rows, 1):
        print(
            f"  {i:2d}. dt={t['date']}  {t['stock_name']} ({t['stock_code']})"
            f"  순실현={int(t['net']):+,}원"
        )

    perf = compute_performance(trades, 10_000_000, "api_stock", "kiwoom")
    for row in perf["daily"]:
        if row["date"] == today:
            print(f"\n=== compute_performance 일별 집계 (오늘) ===")
            print(json.dumps(row, ensure_ascii=False, indent=2))
            print("  count = 해당 날짜 trade 행 개수")
            print("  wins  = net > 0 인 행 개수")
            print("  pnl   = net 합계")

    try:
        r = urllib.request.urlopen(
            "http://127.0.0.1:8000/performance/stats?source=auto&seed=10000000",
            timeout=90,
        )
        d = json.loads(r.read())
        print(f"\n=== 대시보드 API (/performance/stats) ===")
        print(f"pipeline: {d.get('pipeline')}  trade_count: {d.get('trade_count')}")
    except Exception as e:
        print(f"\n(API 호출 생략: {e})")


if __name__ == "__main__":
    asyncio.run(main())
