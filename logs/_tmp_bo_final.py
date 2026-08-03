# -*- coding: utf-8 -*-
import sqlite3, json, re
from collections import Counter
from pathlib import Path

con = sqlite3.connect(r"c:\Users\MiniPC\stocke\stock_pipeline.db")
con.row_factory = sqlite3.Row
cur = con.cursor()
rows = list(cur.execute("SELECT id, stock_code, stock_name, detected_at, status, failure_reason, additional_data FROM pending_buy_signals WHERE detected_date='2026-07-27' ORDER BY id"))

def bucket(fail):
    if not fail: return "none"
    f = fail
    rules = [
        ("일거래량(분봉) 부족", ["일 거래량", "일거래량"]),
        ("장대 부족", ["장대", "몸통"]),
        ("거래량 부족(5분)", ["거래량 부족"]),
        ("레벨/HOLD 대기", ["돌파 전", "HOLD"]),
        ("MA20", ["MA20"]),
        ("과열컷", ["과열"]),
        ("슬롯", ["슬롯"]),
        ("쿨다운", ["쿨다운"]),
        ("시간외", ["시간"]),
        ("분봉데이터", ["분봉 데이터", "데이터 없음"]),
        ("시장리스크/기타게이트", ["진입 게이트", "리스크"]),
        ("이미체결", ["이미 체결"]),
    ]
    for name, keys in rules:
        if any(k in f for k in keys):
            return name
    return "기타: " + f[:50]

bo = []
for r in rows:
    meta = json.loads(r["additional_data"] or "{}")
    if meta.get("strategy") != "breakout":
        continue
    fail = r["failure_reason"] or ""
    # strip prefix
    fail_clean = fail
    for p in ("진입 게이트: ", "진입 보류: ", "게이트: "):
        if fail_clean.startswith(p):
            fail_clean = fail_clean[len(p):]
    bo.append({
        "id": r["id"], "code": r["stock_code"], "name": r["stock_name"],
        "at_utc": str(r["detected_at"]), "status": r["status"],
        "fail": fail_clean,
        "bucket": bucket(fail_clean),
        "vol": meta.get("volume_ratio"),
        "price": meta.get("current_price"),
        "chg": meta.get("change_rate"),
        "level": meta.get("breakout_level_price") or meta.get("level_price"),
        "order_ready": meta.get("order_ready"),
        "wait_kind": meta.get("wait_kind"),
        "ma20": meta.get("ma20"),
        "confirm_close": meta.get("confirm_close"),
        "body": meta.get("ma20_grace_breakout_body_pct"),
    })

bc = Counter(x["bucket"] for x in bo)
# high quality near misses: vol>=1.5 and somehow failed
hq = [x for x in bo if (x["vol"] or 0) >= 1.5]
hq_b = Counter(x["bucket"] for x in hq)

# very high vol
vh = sorted([x for x in bo if (x["vol"] or 0) >= 3], key=lambda x: -(x["vol"] or 0))

# positions strategy
pos = [dict(r) for r in cur.execute("SELECT stock_code, stock_name, status, strategy_key, buy_time FROM positions")]

out = {
  "breakout_n": len(bo),
  "buckets": bc.most_common(),
  "hq_vol_ge_1_5_n": len(hq),
  "hq_buckets": hq_b.most_common(),
  "hq_sample": hq[:25],
  "very_high_vol": vh[:20],
  "watching": [x for x in bo if x["status"]=="WATCHING"],
  "positions": pos,
}
Path(r"c:\Users\MiniPC\stocke\logs\_tmp_bo_final.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"n":len(bo),"buckets":bc.most_common(),"hq":len(hq),"hq_b":hq_b.most_common(),"vh_n":len(vh)}, ensure_ascii=False, indent=2))
