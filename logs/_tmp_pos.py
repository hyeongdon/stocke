# -*- coding: utf-8 -*-
import sqlite3, json
con=sqlite3.connect(r"c:\Users\MiniPC\stocke\stock_pipeline.db")
con.row_factory=sqlite3.Row
cur=con.cursor()
print("strategy_key positions:")
for r in cur.execute("SELECT stock_code, stock_name, status, strategy_key FROM positions"):
    print(dict(r))
# count filled breakout ever
n=0
for r in cur.execute("SELECT additional_data, status FROM pending_buy_signals WHERE detected_date='2026-07-27'"):
    m=json.loads(r[0] or "{}")
    if m.get("strategy")=="breakout" and r[1]=="FILLED":
        n+=1
print("breakout filled today", n)
