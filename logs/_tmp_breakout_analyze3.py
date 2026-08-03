# -*- coding: utf-8 -*-
import re, json
from collections import Counter, defaultdict
from pathlib import Path

log = Path(r"c:\Users\MiniPC\stocke\stock_pipeline.log")

# Focus: cases that almost passed - soft 3/3, volume close, body close, and any buy path for breakout
re_soft = re.compile(r"\[돌파\] 진입확인 SOFT (\d+):\s*(\d+)/(\d+)\s*\((.+)\)")
re_gate = re.compile(r"진입 보류 \[돌파\] \[게이트\] (.+?)\((\d+)\):\s*(.+)$")
re_eval = re.compile(r"\[돌파\] 평가 (.+?)\((\d+)\) 가격=([\d,]+) 등락=([+\-\d.%]+)")
re_hold = re.compile(r"\[돌파\] 진입확인 HOLD")
re_watch = re.compile(r"진입 보류 \[돌파\] \[관측\]")
re_slot = re.compile(r"돌파 슬롯")
re_any_buy = re.compile(r"(매수 주문|주문 요청|buy_order|execute_buy|시그널 생성|신호 생성|watching)")

soft_done = []
soft_prog = Counter()
vol_near = []  # vol ratio >= 1.0
body_near = [] # body >= 1.5
level_above = []
ma_fail = []
gate_after_soft3 = []
eval_unique = {}
cand_names = Counter()
slot_lines = []
watch_lines = []
all_strategy_buys = []

# also extract candidate stock names from collect lines
re_names = re.compile(r"\[돌파\] 편입 \d+종목: (.+)")

with log.open("r", encoding="utf-8", errors="replace") as f:
    prev_soft3 = None
    for line in f:
        if not line.startswith("2026-07-27"):
            continue
        if "매수 주문" in line or "매수 실행" in line or "[BUY]" in line:
            all_strategy_buys.append(line.strip()[:350])
        if "돌파" not in line and "breakout" not in line.lower():
            continue
        m = re_soft.search(line)
        if m:
            code, a, b, det = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
            soft_prog[f"{a}/{b}"] += 1
            if a >= b:
                soft_done.append({"t": line[11:19], "code": code, "detail": det, "line": line.strip()[:280]})
                prev_soft3 = (line[11:19], code)
        m = re_gate.search(line)
        if m:
            name, code, reason = m.group(1), m.group(2), m.group(3)
            # volume
            mv = re.search(r"거래량 부족 \(([0-9.]+)배", reason)
            if mv:
                ratio = float(mv.group(1))
                if ratio >= 1.0:
                    vol_near.append({"t": line[11:19], "stock": f"{name}({code})", "ratio": ratio, "reason": reason})
            mb = re.search(r"장대 부족 \(몸통 ([+\-0-9.]+)%", reason)
            if mb:
                body = float(mb.group(1))
                if body >= 1.5:
                    body_near.append({"t": line[11:19], "stock": f"{name}({code})", "body": body, "reason": reason})
            if "MA20" in reason:
                ma_fail.append({"t": line[11:19], "stock": f"{name}({code})", "reason": reason})
            if "돌파 전" in reason or "레벨" in reason:
                # extract how close
                level_above.append({"t": line[11:19], "stock": f"{name}({code})", "reason": reason[:120]})
            if prev_soft3 and prev_soft3[1] == code:
                gate_after_soft3.append({"soft_t": prev_soft3[0], "t": line[11:19], "stock": f"{name}({code})", "reason": reason})
                prev_soft3 = None
        m = re_eval.search(line)
        if m:
            eval_unique[m.group(2)] = {"name": m.group(1), "price": m.group(3), "chg": m.group(4), "t": line[11:19]}
        m = re_names.search(line)
        if m:
            # parse "NAME(CODE) price +x%, ..."
            for sm in re.finditer(r"([^\s,]+)\((\d+)\)", m.group(1)):
                cand_names[f"{sm.group(1)}({sm.group(2)})"] += 1
        if re_slot.search(line):
            slot_lines.append(line.strip()[:280])
        if re_watch.search(line):
            watch_lines.append(line.strip()[:280])

# summarize volume distribution
vol_all = []
body_all = []
level_pct = []
with log.open("r", encoding="utf-8", errors="replace") as f:
    for line in f:
        if not line.startswith("2026-07-27"):
            continue
        m = re_gate.search(line)
        if not m:
            continue
        reason = m.group(3)
        mv = re.search(r"거래량 부족 \(([0-9.]+)배", reason)
        if mv:
            vol_all.append(float(mv.group(1)))
        mb = re.search(r"장대 부족 \(몸통 ([+\-0-9.]+)%", reason)
        if mb:
            body_all.append(float(mb.group(1)))
        ml = re.search(r"돌파 전 \(([\d,]+) < ([\d,]+)", reason)
        if ml:
            p = int(ml.group(1).replace(",",""))
            lv = int(ml.group(2).replace(",",""))
            if lv>0:
                level_pct.append((p/lv-1)*100)

def pctile(arr, p):
    if not arr: return None
    s=sorted(arr)
    i=int(round((len(s)-1)*p/100))
    return s[i]

out = {
  "soft_prog": dict(soft_prog),
  "soft_done": soft_done,
  "gate_after_soft3": gate_after_soft3,
  "vol_near_ge_1": sorted(vol_near, key=lambda x:-x["ratio"])[:25],
  "body_near_ge_1_5": sorted(body_near, key=lambda x:-x["body"])[:25],
  "vol_stats": {
    "n": len(vol_all),
    "max": max(vol_all) if vol_all else None,
    "p50": pctile(vol_all,50),
    "p75": pctile(vol_all,75),
    "p90": pctile(vol_all,90),
    "ge_1_0": sum(1 for x in vol_all if x>=1.0),
    "ge_1_2": sum(1 for x in vol_all if x>=1.2),
    "ge_1_4": sum(1 for x in vol_all if x>=1.4),
  },
  "body_stats": {
    "n": len(body_all),
    "max": max(body_all) if body_all else None,
    "p50": pctile(body_all,50),
    "p75": pctile(body_all,75),
    "ge_1_5": sum(1 for x in body_all if x>=1.5),
    "ge_1_8": sum(1 for x in body_all if x>=1.8),
    "ge_2_0": sum(1 for x in body_all if x>=2.0),
  },
  "level_gap_stats": {
    "n": len(level_pct),
    "median_pct_below": pctile(level_pct,50),
    "p75_closest": pctile(level_pct,75),  # less negative = closer
    "within_1pct": sum(1 for x in level_pct if x>=-1),
    "within_2pct": sum(1 for x in level_pct if x>=-2),
  },
  "ma_fail_sample": ma_fail[:15],
  "top_candidates_freq": cand_names.most_common(20),
  "unique_eval": len(eval_unique),
  "slot_lines": slot_lines[:10],
  "watch_lines": watch_lines[:10],
  "all_buys_today_sample": all_strategy_buys[:40],
  "all_buys_today_count": len(all_strategy_buys),
}
Path(r"c:\Users\MiniPC\stocke\logs\_tmp_breakout_report3.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k:out[k] for k in ["soft_prog","vol_stats","body_stats","level_gap_stats","unique_eval","all_buys_today_count"]}, ensure_ascii=False, indent=2))
print("SOFT DONE", len(soft_done))
for s in soft_done:
    print(s["t"], s["code"], s["detail"][:100])
print("GATE AFTER SOFT3", len(gate_after_soft3))
for g in gate_after_soft3:
    print(g)
print("VOL NEAR")
for v in out["vol_near_ge_1"][:10]:
    print(v)
print("BODY NEAR")
for v in out["body_near_ge_1_5"][:10]:
    print(v)
print("BUYS sample:")
for b in all_strategy_buys[:15]:
    print(b[:200])
