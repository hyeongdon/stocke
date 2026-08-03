# -*- coding: utf-8 -*-
import sqlite3, json
from pathlib import Path
con = sqlite3.connect(r"c:\Users\MiniPC\stocke\stock_pipeline.db")
cur = con.cursor()
# tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("tables with signal/pos:", [t for t in tables if "signal" in t.lower() or "pos" in t.lower() or "order" in t.lower() or "trade" in t.lower()])

for t in tables:
    if "signal" in t.lower():
        cur.execute(f"PRAGMA table_info({t})")
        cols = [r[1] for r in cur.fetchall()]
        print(t, cols[:30])
        # try fetch today's breakout
        qcols = ",".join(cols[:20])
        try:
            # find date-like cols
            datecols = [c for c in cols if any(x in c.lower() for x in ("at","time","date","created"))]
            print(" datecols", datecols)
            cur.execute(f"SELECT * FROM {t} WHERE strategy LIKE '%break%' OR strategy_key LIKE '%break%' OR gate_pack LIKE '%break%' OR source LIKE '%break%' LIMIT 5")
        except Exception as e:
            try:
                cur.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 3")
                rows = cur.fetchall()
                print(" sample", rows)
            except Exception as e2:
                print(" err", e, e2)

# positions
for t in tables:
    if t.lower() in ("positions","position","trades","orders","buy_orders"):
        cur.execute(f"PRAGMA table_info({t})")
        cols=[r[1] for r in cur.fetchall()]
        print("TABLE", t, cols)
        try:
            cur.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 10")
            for r in cur.fetchall():
                print(r[:25])
        except Exception as e:
            print(e)

# trading signals specifically
for t in tables:
    if "signal" in t.lower():
        cur.execute(f"PRAGMA table_info({t})")
        cols=[r[1] for r in cur.fetchall()]
        # count by strategy today
        for sc in ("strategy","strategy_key","source","gate_pack"):
            if sc in cols:
                try:
                    cur.execute(f"SELECT {sc}, status, count(*) FROM {t} GROUP BY {sc}, status")
                    print("groupby", t, sc, cur.fetchall()[:40])
                except Exception as e:
                    print("gerr", e)
