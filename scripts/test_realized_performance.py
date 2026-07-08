"""ka10073 기반 매도기준 실현손익 성과통계 검증."""
import asyncio
import os
import sys
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from api.kiwoom_api import KiwoomAPI
from utils.performance_stats import compute_performance, trades_from_realized_pnl


async def main():
    api = KiwoomAPI()
    end_dt = datetime.now().strftime("%Y%m%d")
    strt_dt = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")

    res = await api.get_daily_stock_realized_pnl(strt_dt, end_dt, stk_cd="")
    print(f"API success={res.get('success')} error={res.get('error')} rows={len(res.get('items') or [])}")

    trades = trades_from_realized_pnl(res.get("items") or [])
    print(f"매도기준 청산 trade={len(trades)}건")
    out = compute_performance(trades, 10000000, "api_stock", "kiwoom")
    print("\n=== KPI ===")
    for k in ("trade_count", "net_pnl", "gross_pnl", "total_cost", "win_rate",
              "wins", "losses", "payoff", "profit_factor", "expected",
              "mdd", "best", "worst", "trading_days"):
        print(f"  {k}: {out.get(k)}")
    for t in trades[:8]:
        print(f"  {t['date']} {t['stock_name']}({t['stock_code']}) net={t['net']:.0f}")
    print("\n일별:")
    for d in out.get("daily", [])[:10]:
        print(f"  {d['date']} 청산 {d['count']} 승 {d['wins']} 손익 {d['pnl']:,}")


if __name__ == "__main__":
    asyncio.run(main())
