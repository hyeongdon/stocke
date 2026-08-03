# -*- coding: utf-8 -*-
import sqlite3, json
from collections import Counter
from pathlib import Path

con = sqlite3.connect(r"c:\Users\MiniPC\stocke\stock_pipeline.db")
con.row_factory = sqlite3.Row
cur = con.cursor()

print("=== POSITIONS strategy_key ===")
for r in cur.execute("SELECT stock_code, stock_name, status, strategy_key, buy_time, buy_price, buy_quantity FROM positions ORDER BY id"):
    print(dict(r))

print("\n=== TODAY pending_buy_signals by strategy ===")
rows = list(cur.execute("SELECT id, stock_code, stock_name, detected_at, status, failure_reason, additional_data FROM pending_buy_signals WHERE detected_date='2026-07-27' ORDER BY id"))
by_strat = Counter()
by_status = Counter()
breakout = []
for r in rows:
    meta = {}
    try:
        meta = json.loads(r["additional_data"] or "{}")
    except Exception:
        pass
    strat = meta.get("strategy") or meta.get("source") or "?"
    by_strat[strat] += 1
    by_status[(strat, r["status"])] += 1
    if strat == "breakout":
        breakout.append({
            "id": r["id"], "code": r["stock_code"], "name": r["stock_name"],
            "at": str(r["detected_at"]), "status": r["status"],
            "fail": r["failure_reason"],
            "order_ready": meta.get("order_ready"),
            "wait_kind": meta.get("wait_kind"),
            "vol_ratio": meta.get("volume_ratio"),
            "level": meta.get("breakout_level_price") or meta.get("level_price"),
            "price": meta.get("current_price"),
            "body_related": meta.get("ma20_grace_breakout_body_pct"),
        })

print("by_strat", dict(by_strat))
print("by_status", {f"{a}/{b}":c for (a,b),c in by_status.items()})
print("breakout signals", len(breakout))
for b in breakout[:40]:
    print(b)

# early morning slot holders
print("\n=== early pending breakout (before 01:00 UTC = 10:00 KST?) ===")
# detected_at appears UTC-ish: 06:14 for evening? Actually 09:04 KST buy was 00:04? 
# buy_time '2026-07-27 05:50:03' for AD tech - that's UTC = 14:50 KST. Yes UTC storage.
# So 09:10 KST = 00:10 UTC
early = [b for b in breakout if b["at"] < "2026-07-27 01:00"]
print("early count", len(early))
for b in early[:20]:
    print(b["at"], b["status"], b["code"], b["name"], b["fail"])

# status counts for breakout
print("\nbreakout status", Counter(b["status"] for b in breakout))

Path(r"c:\Users\MiniPC\stocke\logs\_tmp_bo_db.json").write_text(
    json.dumps({"breakout": breakout, "by_strat": dict(by_strat), "by_status": {f"{a}/{b}":c for (a,b),c in by_status.items()}}, ensure_ascii=False, indent=2),
    encoding="utf-8"
)
print("wrote")
