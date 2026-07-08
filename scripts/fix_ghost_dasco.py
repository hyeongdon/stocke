"""다스코 등 유령 HOLDING 1회 정리 (reconcile 로직 실행)."""
import asyncio
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    from managers.stop_loss_manager import stop_loss_manager
    await stop_loss_manager._reconcile_sell_orders_and_holdings()
    await stop_loss_manager.sync_holdings_from_api()

    c = sqlite3.connect(ROOT / "stock_pipeline.db")
    print("=== after reconcile ===")
    for r in c.execute(
        "SELECT id, stock_code, stock_name, status, buy_quantity, sell_time "
        "FROM positions WHERE stock_code LIKE '%058730%'"
    ):
        print(r)


if __name__ == "__main__":
    asyncio.run(main())
