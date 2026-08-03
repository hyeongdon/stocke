# -*- coding: utf-8 -*-
import re, json
from collections import Counter, defaultdict
from pathlib import Path

log = Path(r"c:\Users\MiniPC\stocke\stock_pipeline.log")
day = "2026-07-27"

# per-stock stage outcomes
stock_stage = defaultdict(Counter)
stock_times = defaultdict(list)
re_gate = re.compile(r"진입 보류 \[역매공파\] \[게이트\] (.+?)\((\d+)\):\s*(.+)$")
re_engine = re.compile(r"\[(?:YMGP|역매공파)\].*")

engine_samples = []
filtered_detail = []

with log.open("r", encoding="utf-8", errors="replace") as f:
    for line in f:
        if not line.startswith(day):
            continue
        if "역매공파" not in line and "ymgp" not in line.lower():
            continue
        m = re_gate.search(line)
        if m:
            name, code, reason = m.group(1), m.group(2), m.group(3)
            stock_stage[f"{name}({code})"][reason] += 1
            stock_times[f"{name}({code)}"].append(line[11:19])
        if "utils.ymgp" in line or "ymgp_engine" in line:
            if len(engine_samples) < 50:
                engine_samples.append(line.strip()[:300])
        # look for why FILTERED
        if "FILTERED" in line or "filtered" in line.lower():
            if len(filtered_detail) < 40 and "진입 보류" not in line:
                filtered_detail.append(line.strip()[:300])

# summarize each stock
summary = []
for stock, ctr in stock_stage.items():
    summary.append({
        "stock": stock,
        "n": sum(ctr.values()),
        "reasons": dict(ctr),
        "first": stock_times[stock][0],
        "last": stock_times[stock][-1],
    })
summary.sort(key=lambda x: -x["n"])

out = {"stocks": summary, "engine_samples": engine_samples, "filtered_detail": filtered_detail}
Path(r"c:\Users\MiniPC\stocke\logs\_tmp_ymgp_stocks.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
print("---ENGINE---", len(engine_samples))
for e in engine_samples[:15]:
    print(e[:200])
print("---FILTERED DETAIL---")
for e in filtered_detail[:15]:
    print(e[:200])
