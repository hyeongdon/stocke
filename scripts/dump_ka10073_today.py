"""오늘 ka10073 원본 행 전체 필드 덤프."""
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.kiwoom_api import KiwoomAPI


async def main():
    end_dt = datetime.now().strftime("%Y%m%d")
    strt_dt = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
    today_raw = end_dt

    api = KiwoomAPI()
    res = await api.get_daily_stock_realized_pnl(strt_dt, end_dt, stk_cd="")
    items = res.get("items") or []

    today_items = [r for r in items if str(r.get("dt", "")).strip() == today_raw]
    print(f"period {strt_dt}~{end_dt}")
    print(f"total ka10073 rows: {len(items)}")
    print(f"today ({today_raw}) rows: {len(today_items)}\n")

    for i, r in enumerate(today_items, 1):
        print(f"--- [{i}] ---")
        print(json.dumps(r, ensure_ascii=False, indent=2))
        print()

    # 종목별 집계
    from collections import defaultdict

    by_stock = defaultdict(list)
    for r in today_items:
        code = str(r.get("stk_cd", "")).replace("A", "").strip()
        by_stock[code].append(r)

    print("=== 종목별 행 수 ===")
    for code, rows in sorted(by_stock.items(), key=lambda x: -len(x[1])):
        name = rows[0].get("stk_nm", "")
        print(f"  {name} ({code}): {len(rows)} rows")


if __name__ == "__main__":
    asyncio.run(main())
