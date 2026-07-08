"""만료/중복 매도 주문 일괄 CANCELLED 처리."""
import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parents[1] / "stock_pipeline.db"
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute(
    "UPDATE sell_orders SET status='CANCELLED' "
    "WHERE status IN ('PENDING', 'ORDERED')"
)
print(f"cancelled {cur.rowcount} open sell orders")
conn.commit()
for row in cur.execute(
    "SELECT id, position_id, stock_code, status, sell_reason FROM sell_orders ORDER BY id"
):
    print(row)
