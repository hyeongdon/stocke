# -*- coding: utf-8 -*-
import re, json
from collections import Counter, defaultdict
from pathlib import Path

log = Path(r"c:\Users\MiniPC\stocke\stock_pipeline.log")
day = "2026-07-27"

# extract all unique ymgp-related message patterns
msg_types = Counter()
gate_reasons = Counter()
hold_reasons = Counter()
cands = []
evals = []
stage_events = []
errors = []
pass_or_buy = []
samples = defaultdict(list)

# broader patterns
re_gate = re.compile(r"진입 보류 \[역매공파\] \[([^\]]+)\] (.+?)\((\d+)\):\s*(.+)$")
re_any_hold = re.compile(r"\[AUTO_SCANNER\].*역매공파.*")
re_ymgp_engine = re.compile(r"\[(?:YMGP|역매공파)\]")

with log.open("r", encoding="utf-8", errors="replace") as f:
    for line in f:
        if not line.startswith(day):
            continue
        low = line.lower()
        if "역매공파" not in line and "ymgp" not in low:
            continue
        # classify
        if "후보 수집" in line:
            msg_types["후보수집"] += 1
            m = re.search(r"(\d+)개", line)
            if m:
                cands.append((line[11:19], int(m.group(1))))
        elif "편입 종목 없음" in line:
            msg_types["편입없음"] += 1
            cands.append((line[11:19], 0))
        elif "편입" in line and "종목" in line:
            msg_types["편입목록"] += 1
        elif "진입 보류" in line:
            msg_types["진입보류"] += 1
            m = re_gate.search(line)
            if m:
                kind, name, code, reason = m.groups()
                # normalize reason head
                key = reason.split("(")[0].strip()
                if len(key) > 40:
                    key = key[:40]
                gate_reasons[f"[{kind}] {key}"] += 1
                gate_reasons[reason[:80]] += 0  # keep raw separately below
                hold_reasons[reason] += 1
                if len(samples[key]) < 3:
                    samples[key].append({"t": line[11:19], "stock": f"{name}({code})", "reason": reason[:160]})
        elif "평가" in line:
            msg_types["평가"] += 1
            evals.append(line.strip()[:220])
        elif any(x in line for x in ("매수", "시그널", "ORDER", "FILLED", "통과")):
            msg_types["매수관련"] += 1
            pass_or_buy.append(line.strip()[:260])
        elif "ERROR" in line or "오류" in line or "실패" in line:
            msg_types["오류"] += 1
            errors.append(line.strip()[:260])
        elif "stage" in low or "스테이지" in line or "매집" in line or "공구리" in line or "박스" in line:
            msg_types["스테이지/구조"] += 1
            stage_events.append(line.strip()[:260])
        else:
            msg_types["기타로그"] += 1

# top raw hold reasons
top_hold = hold_reasons.most_common(40)

# also read ymgp state file
state_path = Path(r"c:\Users\MiniPC\stocke\logs\_ymgp_state.json")
state = None
if state_path.exists():
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as e:
        state = {"error": str(e)}

# settings from earlier json
prev = json.loads(Path(r"c:\Users\MiniPC\stocke\logs\_tmp_ymgp_report.json").read_text(encoding="utf-8"))
settings = prev.get("settings") or {}

# peak cand names
cand_names = Counter()
re_names = re.compile(r"\[역매공파\] 편입 \d+종목: (.+)")
with log.open("r", encoding="utf-8", errors="replace") as f:
    for line in f:
        if not line.startswith(day):
            continue
        m = re_names.search(line)
        if not m:
            continue
        for sm in re.finditer(r"([^\s,]+)\((\d+)\)", m.group(1)):
            cand_names[f"{sm.group(1)}({sm.group(2)})"] += 1

hourly = defaultdict(list)
for t,n in cands:
    hourly[t[:2]].append(n)

out = {
    "msg_types": msg_types.most_common(),
    "gate_reason_heads": Counter({k:v for k,v in gate_reasons.items() if v>0}).most_common(30),
    "hold_raw_top": [(r,c) for r,c in top_hold],
    "samples": {k:v for k,v in list(samples.items())[:20]},
    "cand_peak": max((n for _,n in cands), default=0),
    "cand_scans": len(cands),
    "hourly": {h: {"scans": len(v), "avg": round(sum(v)/len(v),1), "max": max(v), "zero": sum(1 for x in v if x==0)} for h,v in sorted(hourly.items())},
    "top_cands": cand_names.most_common(20),
    "pass_or_buy": pass_or_buy[:30],
    "errors": errors[:20],
    "stage_sample": stage_events[:25],
    "eval_sample": evals[:20],
    "settings": settings,
    "state_summary": None,
}
if isinstance(state, dict):
    # summarize state
    if "error" in state:
        out["state_summary"] = state
    else:
        # try common shapes
        keys = list(state.keys())[:20]
        out["state_keys"] = keys
        if isinstance(state.get("stocks"), dict):
            out["state_summary"] = {"n_stocks": len(state["stocks"]), "sample": list(state["stocks"].items())[:10]}
        elif isinstance(state, dict) and all(isinstance(v, dict) for v in list(state.values())[:3]):
            out["state_summary"] = {"n": len(state), "sample_codes": list(state.keys())[:15], "sample": {k: state[k] for k in list(state.keys())[:5]}}
        else:
            out["state_summary"] = {"type": str(type(state)), "preview": str(state)[:500]}

Path(r"c:\Users\MiniPC\stocke\logs\_tmp_ymgp_report2.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"msg": msg_types.most_common(), "peak": out["cand_peak"], "scans": out["cand_scans"], "heads": out["gate_reason_heads"][:15], "n_hold": sum(hold_reasons.values()), "top_cands": out["top_cands"][:8], "settings_use": settings.get("use_ymgp"), "pass_n": len(pass_or_buy)}, ensure_ascii=False, indent=2))
