# -*- coding: utf-8 -*-
import re, json, sqlite3
from collections import Counter, defaultdict
from pathlib import Path

log = Path(r"c:\Users\MiniPC\stocke\stock_pipeline.log")
day = "2026-07-27"

# --- log scan ---
cand_counts = []
gate = Counter()
gate_kind = Counter()
gate_raw = Counter()
evals = Counter()
buys = []
ymgp_lines = 0
slot_lines = []
stage_lines = []
sample_by_reason = defaultdict(list)

re_collect = re.compile(r"역매공파 후보 수집 — (\d+)개|\[역매공파\].*편입 (\d+)종목|YMGP.*후보.*?(\d+)")
re_cand0 = re.compile(r"\[역매공파\] 편입 종목 없음")
re_gate = re.compile(r"진입 보류 \[역매공파\] \[(게이트|관측|쿨다운|슬롯)\] (.+?)\((\d+)\):\s*(.+)$")
re_eval = re.compile(r"\[역매공파\] (?:평가|점검|스테이지|단계) .+?\((\d+)\)")
re_buy = re.compile(r"매수 주문.*|BUY_EXECUTOR.*매수")

with log.open("r", encoding="utf-8", errors="replace") as f:
    for line in f:
        if not line.startswith(day):
            continue
        if "역매공파" not in line and "ymgp" not in line.lower() and "YMGP" not in line:
            # still capture buys separately later
            if "매수 주문 성공" in line or "매수 주문 시도" in line:
                buys.append(line.strip()[:280])
            continue
        ymgp_lines += 1
        m = re_collect.search(line)
        if m:
            n = int(next(g for g in m.groups() if g))
            cand_counts.append((line[11:19], n))
        if re_cand0.search(line):
            cand_counts.append((line[11:19], 0))
        # alternate collect patterns from scanner
        m2 = re.search(r"역매공파 후보 수집 — (\d+)개", line)
        if m2:
            cand_counts.append((line[11:19], int(m2.group(1))))
        m3 = re.search(r"\[역매공파\] 편입 (\d+)종목", line)
        if m3:
            pass
        m = re_gate.search(line)
        if m:
            kind, name, code, reason = m.group(1), m.group(2), m.group(3), m.group(4).strip()
            gate_kind[kind] += 1
            gate_raw[reason] += 1
            # bucket
            rules = [
                ("박스/레벨", ["박스", "레벨", "고가", "저가", "돌파", "이탈"]),
                ("거래량", ["거래량"]),
                ("장대/몸통", ["장대", "몸통"]),
                ("MA/이평", ["MA", "이평"]),
                ("낙폭/드롭", ["낙폭", "drop", "하락"]),
                ("시간외", ["시간"]),
                ("슬롯", ["슬롯"]),
                ("쿨다운", ["쿨다운"]),
                ("과열", ["과열", "등락"]),
                ("스테이지/모드", ["stage", "스테이지", "모드", "진입"]),
                ("피봇/눌림", ["피봇", "눌림", "pullback"]),
                ("재진입잠금", ["재진입", "잠금"]),
                ("분봉데이터", ["분봉", "데이터"]),
            ]
            b = "기타"
            for nm, keys in rules:
                if any(k.lower() in reason.lower() for k in keys):
                    b = nm
                    break
            gate[b] += 1
            if len(sample_by_reason[b]) < 4:
                sample_by_reason[b].append({"t": line[11:19], "stock": f"{name}({code})", "reason": reason[:140]})
        if "슬롯" in line and "역매공파" in line:
            slot_lines.append(line.strip()[:260])
        if any(k in line for k in ("스테이지", "stage", "accum", "entry_mode", "ymgp_stage")):
            if len(stage_lines) < 40:
                stage_lines.append(line.strip()[:260])

# hourly
hourly = defaultdict(list)
for t,n in cand_counts:
    hourly[t[:2]].append(n)

# --- DB ---
con = sqlite3.connect(r"c:\Users\MiniPC\stocke\stock_pipeline.db")
con.row_factory = sqlite3.Row
cur = con.cursor()
rows = list(cur.execute(
    "SELECT id, stock_code, stock_name, detected_at, status, failure_reason, additional_data "
    "FROM pending_buy_signals WHERE detected_date=? ORDER BY id", (day,)
))
by_status = Counter()
fail_b = Counter()
fail_raw = Counter()
sigs = []
for r in rows:
    meta = json.loads(r["additional_data"] or "{}")
    if meta.get("strategy") != "ymgp" and meta.get("source") != "ymgp":
        continue
    st = r["status"]
    by_status[st] += 1
    fail = r["failure_reason"] or ""
    for p in ("진입 게이트: ", "진입 보류: ", "관측 종료: ", "돌파 게이트: "):
        if fail.startswith(p):
            fail = fail[len(p):]
    fail_raw[fail[:120] or "(empty)"] += 1
    # bucket fail
    rules = [
        ("박스/레벨/HOLD", ["박스", "레벨", "돌파", "HOLD", "고가", "저가", "이탈"]),
        ("거래량", ["거래량"]),
        ("장대/몸통", ["장대", "몸통", "accum"]),
        ("MA/이평", ["MA", "이평"]),
        ("낙폭", ["낙폭", "drop"]),
        ("시간외", ["시간"]),
        ("슬롯", ["슬롯"]),
        ("과열", ["과열"]),
        ("재진입잠금", ["재진입", "잠금"]),
        ("시장리스크", ["리스크", "market_risk"]),
        ("분봉/데이터", ["분봉", "데이터"]),
        ("이미체결/기타", ["이미", "실패"]),
    ]
    b = "기타: " + (fail[:40] if fail else "none")
    for nm, keys in rules:
        if any(k.lower() in fail.lower() for k in keys):
            b = nm
            break
    if not fail:
        b = "(watching/empty)"
    fail_b[b] += 1
    sigs.append({
        "id": r["id"], "code": r["stock_code"], "name": r["stock_name"],
        "at": str(r["detected_at"]), "status": st, "fail": fail[:160],
        "bucket": b,
        "stage": meta.get("ymgp_stage") or meta.get("stage"),
        "entry_leg": meta.get("ymgp_entry_leg") or meta.get("entry_leg"),
        "price": meta.get("current_price"),
        "chg": meta.get("change_rate"),
        "order_ready": meta.get("order_ready"),
        "ref_high": meta.get("ymgp_ref_high"),
        "ref_low": meta.get("ymgp_ref_low"),
    })

# settings
cur.execute("SELECT * FROM auto_trade_settings LIMIT 1")
row = cur.fetchone()
cols = [d[0] for d in cur.description]
d = dict(zip(cols, row))
settings = {k: d[k] for k in d if "ymgp" in k or k in ("use_ymgp", "is_enabled", "market_risk_block_ymgp", "market_risk_enabled")}

# positions with ymgp
pos = [dict(r) for r in cur.execute(
    "SELECT stock_code, stock_name, status, strategy_key, buy_time, buy_price, buy_quantity FROM positions WHERE strategy_key='ymgp' OR stock_name LIKE '%'"
)]
pos_ymgp = [p for p in pos if p.get("strategy_key") == "ymgp"]

# top stocks by fail count
stock_c = Counter((s["name"], s["code"]) for s in sigs)
top_stocks = stock_c.most_common(15)

out = {
    "ymgp_log_lines": ymgp_lines,
    "candidate_scans": len(cand_counts),
    "candidate_peak": max((n for _,n in cand_counts), default=0),
    "candidate_zero": sum(1 for _,n in cand_counts if n==0),
    "hourly": {h: {"scans": len(v), "avg": round(sum(v)/len(v),1), "max": max(v), "zero": sum(1 for x in v if x==0)} for h,v in sorted(hourly.items())},
    "gate_kind": dict(gate_kind),
    "gate_buckets": gate.most_common(),
    "gate_raw_top": gate_raw.most_common(25),
    "sample_by_reason": {k:v for k,v in sample_by_reason.items()},
    "db_n": len(sigs),
    "db_status": dict(by_status),
    "db_fail_buckets": fail_b.most_common(),
    "db_fail_raw_top": fail_raw.most_common(20),
    "filled": [s for s in sigs if s["status"]=="FILLED"],
    "watching": [s for s in sigs if s["status"]=="WATCHING"],
    "top_stocks": [{"name": a[0], "code": a[1], "n": n} for (a,n) in top_stocks],
    "settings": settings,
    "pos_ymgp": pos_ymgp,
    "buys_today_sample": buys[:20],
    "slot_sample": slot_lines[:10],
    "sigs_sample": sigs[:30],
    "order_ready_true": [s for s in sigs if s.get("order_ready") is True][:20],
}
Path(r"c:\Users\MiniPC\stocke\logs\_tmp_ymgp_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({
    "log_lines": ymgp_lines,
    "scans": len(cand_counts),
    "peak": out["candidate_peak"],
    "gate": gate.most_common(),
    "db_n": len(sigs),
    "status": dict(by_status),
    "fail_b": fail_b.most_common(),
    "filled": len(out["filled"]),
    "watching": len(out["watching"]),
    "settings_keys": list(settings.keys())[:5],
}, ensure_ascii=False, indent=2))
