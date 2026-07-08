import sqlite3
import json
import urllib.request

DB = r"C:\Users\MiniPC\stocke\stock_pipeline.db"
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

print("=== settings ===")
row = c.execute("select * from auto_trade_settings limit 1").fetchone()
if row:
    keys = row.keys()
    for k in keys:
        if k not in ("id", "updated_at"):
            print(f"  {k}: {row[k]}")

print("\n=== holdings ===")
rows = c.execute("select stock_code, stock_name, status from positions where status='HOLDING'").fetchall()
print(f"  count={len(rows)}")
for r in rows:
    print(f"  {r['stock_code']} {r['stock_name']}")

print("\n=== pending_buy_signals by status ===")
for row in c.execute("select status, count(*) as n from pending_buy_signals group by status"):
    print(f"  {row['status']}: {row['n']}")

print("\n=== today signals ===")
for row in c.execute(
    "select stock_name, status, failure_reason, detected_at from pending_buy_signals "
    "where detected_date='2026-07-07' order by detected_at desc"
):
    print(f"  {row['stock_name']} {row['status']} {row['failure_reason']} {row['detected_at']}")

print("\n=== last 15 signals ===")
for row in c.execute(
    "select detected_date, stock_name, status, failure_reason, detected_at "
    "from pending_buy_signals order by detected_at desc limit 15"
):
    print(f"  {row['detected_date']} {row['stock_name']} {row['status']} {row['failure_reason']}")

print("\n=== buy_conditions count ===")
print("  ", c.execute("select count(*) from buy_conditions").fetchone()[0])

for url in [
    "http://127.0.0.1:8000/trading/readiness",
    "http://127.0.0.1:8000/trading/activity-log?limit=30",
    "http://127.0.0.1:8000/screener/candidates?limit=5",
]:
    print(f"\n=== GET {url} ===")
    try:
        r = urllib.request.urlopen(url, timeout=15)
        d = json.loads(r.read())
        print(json.dumps(d, ensure_ascii=False, indent=2)[:3000])
    except Exception as e:
        print(f"  ERROR: {e}")
