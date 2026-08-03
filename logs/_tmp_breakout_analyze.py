# -*- coding: utf-8 -*-
import re, collections, json
from pathlib import Path

log = Path(r"c:\Users\MiniPC\stocke\stock_pipeline.log")
out = Path(r"c:\Users\MiniPC\stocke\logs\_tmp_breakout_report.json")

# stream line by line for today only
pat_day = "2026-07-27"
re_gate = re.compile(r"진입 보류 \[돌파\] \[(게이트|관측|쿨다운|슬롯)\] (.+?)\((\d+)\):\s*(.+)$")
re_cand = re.compile(r"\[돌파\] 편입 (\d+)종목:")
re_cand0 = re.compile(r"\[돌파\] 편입 종목 없음")
re_collect = re.compile(r"돌파 후보 수집 — (\d+)개")
re_eval = re.compile(r"\[돌파\] 평가 (.+?)\((\d+)\) 가격=([\d,]+) 등락=([+\-\d.%]+)")
re_soft = re.compile(r"\[돌파\] 진입확인 (SOFT|HOLD|HARD) (.+?):\s*(.+)$")
re_buy = re.compile(r"(매수|BUY|주문).*(돌파|breakout)|\[돌파\].*(매수|주문|체결)")
re_pass = re.compile(r"진입 통과|게이트 통과|매수 실행|BUY_OK|주문 전송")
re_settings = re.compile(r"breakout_[a-z0-9_]+")

gate_reasons = collections.Counter()
gate_by_stock = collections.defaultdict(collections.Counter)
gate_kind = collections.Counter()
cand_counts = []
eval_stocks = collections.Counter()
soft_events = []
buy_related = []
timeline_cands = []
pass_lines = []
sample_gates = collections.defaultdict(list)

# also catch ASCII-safe patterns that might appear
re_gate2 = re.compile(r"진입 보류 .*\[돌파\].*")

n_lines = 0
n_breakout_lines = 0
with log.open("r", encoding="utf-8", errors="replace") as f:
    for line in f:
        if not line.startswith(pat_day):
            continue
        n_lines += 1
        if "돌파" not in line and "breakout" not in line.lower() and "oversold_breakout" not in line:
            continue
        n_breakout_lines += 1
        # candidates
        m = re_collect.search(line)
        if m:
            cand_counts.append((line[11:19], int(m.group(1))))
            timeline_cands.append({"t": line[11:19], "n": int(m.group(1))})
        if re_cand0.search(line):
            cand_counts.append((line[11:19], 0))
        m = re_cand.search(line)
        if m:
            # already have collect
            pass
        m = re_gate.search(line)
        if m:
            kind, name, code, reason = m.group(1), m.group(2), m.group(3), m.group(4).strip()
            # normalize reason key (strip numeric details somewhat)
            reason_key = reason
            # group common patterns
            for pref in ["거래량 부족", "장대 부족", "MA20", "레벨", "SOFT", "HOLD", "과열", "시간", "슬롯", "쿨다운", "몸통", "범위", "RSI", "시가", "VWAP", "관측"]:
                if pref in reason:
                    reason_key = reason.split("(")[0].strip() if "(" in reason else reason
                    break
            gate_reasons[reason_key] += 1
            gate_kind[kind] += 1
            gate_by_stock[f"{name}({code})"][reason_key] += 1
            if len(sample_gates[reason_key]) < 5:
                sample_gates[reason_key].append({"t": line[11:19], "stock": f"{name}({code})", "reason": reason})
        m = re_eval.search(line)
        if m:
            eval_stocks[f"{m.group(1)}({m.group(2)})"] += 1
        m = re_soft.search(line)
        if m:
            soft_events.append({"t": line[11:19], "mode": m.group(1), "code_msg": m.group(2), "detail": m.group(3)[:120]})
        if any(k in line for k in ["매수 실행", "매수 주문", "BUY", "주문 전송", "진입 통과", "게이트 통과", "돌파 매수"]):
            if "돌파" in line or "breakout" in line.lower():
                buy_related.append(line.strip()[:300])
        if "진입 통과" in line or "게이트 통과" in line:
            pass_lines.append(line.strip()[:300])

# unique stocks evaluated
# peak candidates
peak = max((n for _, n in cand_counts), default=0)
nonzero = [n for _, n in cand_counts if n > 0]
avg = sum(nonzero)/len(nonzero) if nonzero else 0

# top blocked stocks
top_stocks = sorted(((k, sum(v.values()), dict(v.most_common(5))) for k,v in gate_by_stock.items()), key=lambda x: -x[1])[:20]

report = {
    "day_lines": n_lines,
    "breakout_lines": n_breakout_lines,
    "candidate_scans": len(cand_counts),
    "candidate_peak": peak,
    "candidate_nonzero_avg": round(avg,1),
    "candidate_zero_scans": sum(1 for _,n in cand_counts if n==0),
    "gate_kind": dict(gate_kind),
    "gate_reasons": gate_reasons.most_common(30),
    "top_blocked_stocks": top_stocks,
    "eval_stock_count": len(eval_stocks),
    "eval_top": eval_stocks.most_common(15),
    "soft_event_count": len(soft_events),
    "soft_sample": soft_events[:30],
    "buy_related_count": len(buy_related),
    "buy_related_sample": buy_related[:40],
    "pass_lines": pass_lines[:20],
    "sample_gates": {k:v for k,v in list(sample_gates.items())[:20]},
    "cand_timeline_hourly": {},
}
# hourly cand avg
hourly = collections.defaultdict(list)
for t,n in cand_counts:
    hourly[t[:2]].append(n)
report["cand_timeline_hourly"] = {h: {"scans": len(v), "avg": round(sum(v)/len(v),1), "max": max(v), "zero": sum(1 for x in v if x==0)} for h,v in sorted(hourly.items())}

out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: report[k] for k in ["day_lines","breakout_lines","candidate_scans","candidate_peak","candidate_nonzero_avg","candidate_zero_scans","gate_kind","eval_stock_count","soft_event_count","buy_related_count"]}, ensure_ascii=False, indent=2))
print("---GATE REASONS---")
for k,v in report["gate_reasons"]:
    print(f"{v:5d}  {k}")
print("---HOURLY---")
print(json.dumps(report["cand_timeline_hourly"], ensure_ascii=False, indent=2))
print("---TOP STOCKS---")
for s,c,d in report["top_blocked_stocks"][:10]:
    print(f"{c:4d} {s} {d}")
