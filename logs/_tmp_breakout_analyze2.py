# -*- coding: utf-8 -*-
import re, json, sqlite3
from pathlib import Path

# 1) Dump settings from DB if possible
db_candidates = list(Path(r"c:\Users\MiniPC\stocke").rglob("*.db"))
print("DB files:", [str(p) for p in db_candidates[:20]])

settings = None
for db in db_candidates:
    try:
        con = sqlite3.connect(str(db))
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        if "auto_trade_settings" in tables:
            cur.execute("PRAGMA table_info(auto_trade_settings)")
            cols = [r[1] for r in cur.fetchall()]
            bo_cols = [c for c in cols if "breakout" in c or c in ("use_breakout","is_enabled","use_entry_gate","market_risk_enabled","market_risk_block_breakout")]
            cur.execute("SELECT * FROM auto_trade_settings LIMIT 1")
            row = cur.fetchone()
            if row:
                allcols = cols
                d = dict(zip(allcols, row))
                settings = {k:d[k] for k in d if "breakout" in k or k in ("use_breakout","is_enabled","use_entry_gate","market_risk_enabled","market_risk_block_breakout","max_concurrent_positions","max_daily_buys")}
                print("SETTINGS from", db)
                print(json.dumps(settings, ensure_ascii=False, indent=2, default=str))
            con.close()
            break
        con.close()
    except Exception as e:
        print("err", db, e)

# 2) Re-parse gate reasons with UTF-8 print to file
log = Path(r"c:\Users\MiniPC\stocke\stock_pipeline.log")
re_gate = re.compile(r"진입 보류 \[돌파\] \[(게이트|관측|쿨다운|슬롯)\] (.+?)\((\d+)\):\s*(.+)$")
re_buy_ok = re.compile(r"(돌파).*(매수 주문|매수 실행|주문 전송|시그널 생성|BUY)|\[BUY\].*breakout|strategy.?=.?breakout.*매수|매수.*strategy.?=.?breakout", re.I)
re_signal = re.compile(r"\[돌파\].*(시그널|신호|watching|관측 등록|매수 시도)")
re_near = re.compile(r"진입확인 SOFT .+?: (\d+)/(\d+)")

gate_raw = {}
near_miss = []
buys = []
signals = []
soft_complete = []
with log.open("r", encoding="utf-8", errors="replace") as f:
    for line in f:
        if not line.startswith("2026-07-27"):
            continue
        if "돌파" not in line and "breakout" not in line.lower():
            continue
        m = re_gate.search(line)
        if m:
            reason = m.group(4).strip()
            gate_raw[reason] = gate_raw.get(reason, 0) + 1
        m = re_near.search(line)
        if m:
            a,b = int(m.group(1)), int(m.group(2))
            if a >= b:
                soft_complete.append(line.strip()[:250])
            elif a == b-1:
                near_miss.append(line.strip()[:250])
        if re_buy_ok.search(line) or ("[BUY]" in line and "breakout" in line.lower()):
            buys.append(line.strip()[:300])
        if "시그널" in line and "돌파" in line:
            signals.append(line.strip()[:300])
        if "매수 주문" in line or "place_buy" in line or "execute_buy" in line:
            if "돌파" in line or "breakout" in line.lower() or "oversold" in line.lower():
                buys.append(line.strip()[:300])

# cluster reasons better
from collections import Counter
def bucket(r):
    rules = [
        ("레벨 미돌파/미유지", ["레벨"]),
        ("장대(몸통) 부족", ["장대", "몸통"]),
        ("거래량 부족", ["거래량"]),
        ("MA20 미충족", ["MA20"]),
        ("SOFT 확인 대기", ["SOFT", "연속"]),
        ("HOLD 대기", ["HOLD", "armed", "다음봉"]),
        ("과열/등락컷", ["과열", "등락", "max_change"]),
        ("시간외", ["시간"]),
        ("슬롯", ["슬롯"]),
        ("쿨다운", ["쿨다운"]),
        ("분봉 데이터", ["분봉", "데이터"]),
        ("수급/모멘텀", ["모멘텀", "수급", "RSI"]),
        ("시가/VWAP", ["시가", "VWAP"]),
        ("범위확장", ["범위"]),
    ]
    for name, keys in rules:
        if any(k in r for k in keys):
            return name
    return "기타: " + r[:40]

bc = Counter(bucket(r) for r in gate_raw for _ in range(gate_raw[r]))
# detailed top raw
top_raw = sorted(gate_raw.items(), key=lambda x:-x[1])[:40]

out = {
    "settings": settings,
    "gate_buckets": bc.most_common(),
    "gate_raw_top": top_raw,
    "soft_complete_count": len(soft_complete),
    "soft_complete_sample": soft_complete[:20],
    "near_miss_2of3": len(near_miss),
    "near_miss_sample": near_miss[:15],
    "buys": buys[:30],
    "buy_count": len(buys),
    "signals": signals[:30],
    "signal_count": len(signals),
}
Path(r"c:\Users\MiniPC\stocke\logs\_tmp_breakout_report2.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("buckets:")
for k,v in bc.most_common():
    print(f"{v:5d} {k}")
print("soft_complete", len(soft_complete), "near", len(near_miss), "buys", len(buys), "signals", len(signals))
print("top raw:")
for k,v in top_raw[:15]:
    print(f"{v:5d} {k}")
