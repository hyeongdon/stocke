"""RSI 저점 분할매수(-5% 사다리) — 순수 계산 + JSON 상태 저장소.

docs/PRD_RSI_LADDER.md 기준.
- 1차 진입: (주봉 RSI 저점 OR 일봉 RSI 저점 에피소드 N회) AND 20/60 GC
- 추가 매수: 15:05 현재가 ≤ 직전 체결가 × (1 - step%) 호가 내림, 하루 1단계, max_steps 까지
- 매도: S0 (무장 전·수익 중 MA20 이탈 50%, 1회), 무장(peak ≥ +15%) 후 S1 MA10 50% / S2 MA15 25% / S3 MA20 잔량
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from utils.rwm_bowl import (
    is_week_closed,
    resample_weekly,
    sma_series,
    wilder_rsi_series,
)

STRATEGY_KEY = "rsi_ladder"
STRATEGY_LABEL = "분할매수"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_PATH = _PROJECT_ROOT / "logs" / "_rsi_ladder_state.json"

PLAN_STATUSES = ("RSI_WAIT", "ACTIVE", "FULL", "EXITING", "PAUSED", "DONE")
OPEN_STATUSES = ("RSI_WAIT", "ACTIVE", "FULL", "EXITING", "PAUSED")

DEFAULT_PROFILE: Dict[str, Any] = {
    "entry_mode": "auto",          # auto | manual
    "rsi_period": 14,
    "rsi_weekly_th": 30.0,
    "rsi_daily_th": 30.0,
    "rsi_reset": 35.0,             # 일봉 저점 에피소드 종료 기준
    "weekly_lookback_weeks": 26,
    "daily_lookback": 120,
    "daily_low_count": 3,
    "gc_fresh_days": 3,
    "gc_near_pct": 2.0,
    "step_pct": 5.0,
    "max_steps": 10,
    "s0_enabled": True,
    "s0_profit_only": True,
    "tp_arm_pct": 15.0,
    "s1_ratio": 0.5,
    "s2_ratio": 0.25,
}

SELL_RULE_LABEL = {
    "S0": "MA20 이탈 50%",
    "S1": "익절 MA10 이탈 50%",
    "S2": "익절 MA15 이탈 25%",
    "S3": "익절 MA20 이탈 잔량",
    "MANUAL_HALF": "수동 50% 매도",
    "MANUAL_ALL": "수동 전량 매도",
}

SELL_REASON_CODE = {
    "S0": "LADDER_S0",
    "S1": "LADDER_S1",
    "S2": "LADDER_S2",
    "S3": "LADDER_S3",
    "MANUAL_HALF": "LADDER_MANUAL",
    "MANUAL_ALL": "LADDER_MANUAL",
}


# ───────────────────────── 기본 계산 ─────────────────────────

def tick_size(price: float) -> int:
    p = float(price)
    if p < 2000:
        return 1
    if p < 5000:
        return 5
    if p < 20000:
        return 10
    if p < 50000:
        return 50
    if p < 200000:
        return 100
    if p < 500000:
        return 500
    return 1000


def tick_floor(price: float) -> int:
    """호가 단위 내림. 경계 가격대의 단위 변화도 처리."""
    p = float(price)
    if p <= 0:
        return 0
    t = tick_size(p)
    v = int(p // t) * t
    # 내림 결과가 더 작은 단위 구간으로 떨어진 경우 그 구간 단위로 다시 맞춤
    t2 = tick_size(v)
    if t2 != t:
        v = int(p // t2) * t2
    return int(v)


def merge_profile(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    p = dict(DEFAULT_PROFILE)
    for k, v in (overrides or {}).items():
        if k in DEFAULT_PROFILE and v is not None:
            base = DEFAULT_PROFILE[k]
            try:
                if isinstance(base, bool):
                    p[k] = bool(v)
                elif isinstance(base, int):
                    p[k] = int(v)
                elif isinstance(base, float):
                    p[k] = float(v)
                else:
                    p[k] = v
            except (TypeError, ValueError):
                pass
    p["max_steps"] = max(1, min(20, int(p["max_steps"])))
    p["step_pct"] = max(0.5, min(50.0, float(p["step_pct"])))
    if p["entry_mode"] not in ("auto", "manual"):
        p["entry_mode"] = "auto"
    return p


def step_budget(plan: Dict[str, Any]) -> int:
    prof = plan.get("profile") or {}
    steps = max(1, int(prof.get("max_steps") or 10))
    return int(int(plan.get("budget") or 0) // steps)


def step_qty(plan: Dict[str, Any], price: float) -> int:
    if not price or price <= 0:
        return 0
    return int(step_budget(plan) // float(price))


def next_trigger(last_fill_price: float, step_pct: float) -> int:
    return tick_floor(float(last_fill_price) * (1.0 - float(step_pct) / 100.0))


def ladder_preview(base_price: float, budget: int, step_pct: float, max_steps: int) -> List[Dict[str, Any]]:
    """기준가에서 매 단계 -step% 가정한 예상 사다리 (UI 미리보기)."""
    out: List[Dict[str, Any]] = []
    per = int(budget // max(1, max_steps))
    px = tick_floor(base_price)
    cum_qty = 0
    cum_amt = 0
    for i in range(1, max_steps + 1):
        if i > 1:
            px = next_trigger(px, step_pct)
        qty = int(per // px) if px > 0 else 0
        cum_qty += qty
        cum_amt += qty * px
        out.append({
            "step": i,
            "price": px,
            "qty": qty,
            "amount": qty * px,
            "cum_qty": cum_qty,
            "cum_amount": cum_amt,
            "avg": round(cum_amt / cum_qty) if cum_qty else 0,
            "drop_pct": round((px / base_price - 1) * 100, 2) if base_price else 0,
        })
    return out


# ───────────────────────── 일봉 구성 ─────────────────────────

def bars_with_today(bars: Sequence[Dict[str, Any]], today: date, price: Optional[float]) -> List[Dict[str, Any]]:
    """확정 일봉 + 당일 임시봉(종가=현재가). 당일 봉이 이미 있으면 종가만 교체."""
    out = [dict(b) for b in bars if b["date"] < today]
    if price and price > 0:
        out.append({"date": today, "open": float(price), "high": float(price), "low": float(price),
                    "close": float(price), "volume": 0.0})
    return out


# ───────────────────────── 진입 판정 ─────────────────────────

def count_low_episodes(rsi: Sequence[Optional[float]], lookback: int, th: float, reset: float) -> Dict[str, Any]:
    """최근 lookback 봉에서 RSI ≤ th 저점 에피소드 수. RSI > reset 회복 시 에피소드 종료."""
    n = len(rsi)
    start = max(0, n - int(lookback))
    episodes: List[Dict[str, Any]] = []
    in_low = False
    for i in range(start, n):
        v = rsi[i]
        if v is None:
            continue
        if not in_low and v <= th:
            in_low = True
            episodes.append({"idx": i, "min": v})
        elif in_low:
            if v < episodes[-1]["min"]:
                episodes[-1]["min"] = v
            if v > reset:
                in_low = False
    return {"count": len(episodes), "episodes": episodes, "in_low": in_low}


def gc_state(closes: Sequence[float], fresh_days: int, near_pct: float) -> Dict[str, Any]:
    """20/60 SMA 골든크로스 상태. today | recent | above | near | none | na."""
    ma20 = sma_series(closes, 20)
    ma60 = sma_series(closes, 60)
    n = len(closes)
    if n < 61 or ma20[-1] is None or ma60[-1] is None:
        return {"state": "na", "ma20": ma20[-1] if n else None, "ma60": ma60[-1] if n else None, "gap_pct": None}
    a, b = float(ma20[-1]), float(ma60[-1])
    gap = (a / b - 1.0) * 100.0 if b else 0.0
    res = {"state": "none", "ma20": a, "ma60": b, "gap_pct": round(gap, 2), "days_ago": None}
    if a > b:
        for j in range(n - 1, max(1, n - max(1, int(fresh_days))) - 1, -1):
            p20, p60, c20, c60 = ma20[j - 1], ma60[j - 1], ma20[j], ma60[j]
            if None in (p20, p60, c20, c60):
                continue
            if p20 <= p60 and c20 > c60:
                res["days_ago"] = n - 1 - j
                res["state"] = "today" if j == n - 1 else "recent"
                return res
        res["state"] = "above"
        return res
    if abs(gap) <= float(near_pct):
        res["state"] = "near"
    return res


def evaluate_entry(bars: Sequence[Dict[str, Any]], profile: Dict[str, Any], *,
                   weekly_bars: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """1차 자동 진입 판정.

    bars: 당일 임시봉 포함 일봉 (GC·일봉 RSI 용).
    weekly_bars: 주봉 계산용 확정 일봉 (없으면 bars 에서 마지막 봉 제외).
    """
    p = merge_profile(profile)
    closes = [float(b["close"]) for b in bars]
    period = int(p["rsi_period"])
    d_rsi = wilder_rsi_series(closes, period)
    low = count_low_episodes(d_rsi, int(p["daily_lookback"]), float(p["rsi_daily_th"]), float(p["rsi_reset"]))

    wsrc = list(weekly_bars) if weekly_bars is not None else list(bars[:-1])
    w_min = None
    w_last = None
    if wsrc:
        weeks = resample_weekly(wsrc, last_week_closed=is_week_closed(wsrc[-1]["date"]))
        w_rsi = wilder_rsi_series([w.close for w in weeks], period)
        recent = [v for v in w_rsi[-int(p["weekly_lookback_weeks"]):] if v is not None]
        if recent:
            w_min = min(recent)
        w_last = w_rsi[-1] if w_rsi else None

    r1 = w_min is not None and w_min <= float(p["rsi_weekly_th"])
    r2 = low["count"] >= int(p["daily_low_count"])
    gc = gc_state(closes, int(p["gc_fresh_days"]), float(p["gc_near_pct"]))
    g = gc["state"] in ("today", "recent")
    return {
        "r1": r1,
        "r2": r2,
        "g": g,
        "ok": bool((r1 or r2) and g),
        "weekly_rsi_min": round(w_min, 2) if w_min is not None else None,
        "weekly_rsi": round(w_last, 2) if w_last is not None else None,
        "daily_rsi": round(d_rsi[-1], 2) if d_rsi and d_rsi[-1] is not None else None,
        "low_count": low["count"],
        "in_low": low["in_low"],
        "gc": gc,
    }


# ───────────────────────── 매도 판정 ─────────────────────────

def moving_averages(closes: Sequence[float]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for n in (10, 15, 20, 60):
        s = sma_series(closes, n)
        out[f"ma{n}"] = float(s[-1]) if s and s[-1] is not None else None
    return out


def evaluate_exit(plan: Dict[str, Any], *, price: float, avg: float, hold: int,
                  ma: Dict[str, Optional[float]]) -> Dict[str, Any]:
    """S0~S3 판정. plan 은 변경하지 않고 결정만 반환한다.

    반환: {"sells": [{"rule", "qty"}], "armed", "peak_pct", "base_qty", "flags"}
    """
    prof = merge_profile(plan.get("profile"))
    flags = dict(plan.get("sell_flags") or {})
    armed = bool(plan.get("armed"))
    base_qty = int(plan.get("base_qty") or 0)
    peak = float(plan.get("peak_pnl_pct") or 0.0)
    sells: List[Dict[str, Any]] = []
    if hold <= 0 or avg <= 0 or price <= 0:
        return {"sells": sells, "armed": armed, "peak_pct": peak, "base_qty": base_qty, "flags": flags}

    pnl_pct = (price / avg - 1.0) * 100.0
    peak = max(peak, pnl_pct)
    if not armed and peak >= float(prof["tp_arm_pct"]):
        armed = True

    ma10, ma15, ma20 = ma.get("ma10"), ma.get("ma15"), ma.get("ma20")
    remaining = int(hold)

    if not armed:
        if (prof["s0_enabled"] and not flags.get("S0") and ma20 is not None and price < ma20
                and (price > avg or not prof["s0_profit_only"])):
            qty = max(1, remaining // 2) if remaining > 1 else remaining
            sells.append({"rule": "S0", "qty": qty})
            flags["S0"] = True
        return {"sells": sells, "armed": armed, "peak_pct": peak, "base_qty": base_qty, "flags": flags}

    def _base() -> int:
        return base_qty if base_qty > 0 else int(hold)

    if not flags.get("S1") and ma10 is not None and price < ma10:
        base_qty = _base()
        qty = min(remaining, max(1, int(base_qty * float(prof["s1_ratio"]))))
        if qty > 0:
            sells.append({"rule": "S1", "qty": qty})
            remaining -= qty
        flags["S1"] = True
    if not flags.get("S2") and ma15 is not None and price < ma15:
        base_qty = _base()
        qty = min(remaining, max(1, int(base_qty * float(prof["s2_ratio"]))))
        if qty > 0:
            sells.append({"rule": "S2", "qty": qty})
            remaining -= qty
        flags["S2"] = True
    if not flags.get("S3") and ma20 is not None and price < ma20:
        base_qty = _base()
        if remaining > 0:
            sells.append({"rule": "S3", "qty": remaining})
            remaining = 0
        flags["S3"] = True
    return {"sells": sells, "armed": armed, "peak_pct": peak, "base_qty": base_qty, "flags": flags}


# ───────────────────────── 일일 판정 ─────────────────────────

def auto_fills(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [f for f in plan.get("fills") or [] if not f.get("extra")]


def last_fill_price(plan: Dict[str, Any]) -> Optional[int]:
    fills = plan.get("fills") or []
    if not fills:
        return None
    return int(fills[-1]["price"])


def judge_plan(plan: Dict[str, Any], *, price: float, bars: Sequence[Dict[str, Any]],
               weekly_bars: Optional[Sequence[Dict[str, Any]]], hold: int, avg: float,
               today: date) -> Dict[str, Any]:
    """하루 한 번(15:05) 판정 — 실행할 행동만 반환 (매도 우선, 매도한 날 매수 없음).

    반환: {"buy": {"step", "qty", "price", "reason"} | None, "sells": [...], "exit": {...},
           "entry": {...} | None, "trigger": int | None, "notes": [..]}
    """
    prof = merge_profile(plan.get("profile"))
    status = plan.get("status")
    closes = [float(b["close"]) for b in bars]
    ma = moving_averages(closes)
    out: Dict[str, Any] = {"buy": None, "sells": [], "exit": None, "entry": None, "trigger": None,
                           "ma": ma, "notes": []}
    if status in ("PAUSED", "DONE"):
        return out

    if status == "RSI_WAIT":
        ent = evaluate_entry(bars, prof, weekly_bars=weekly_bars)
        out["entry"] = ent
        if prof["entry_mode"] == "auto" and ent["ok"] and not plan.get("pending_buy"):
            qty = step_qty(plan, price)
            if qty > 0:
                out["buy"] = {"step": 1, "qty": qty, "price": int(price), "reason": "1차 자동 진입"}
            else:
                out["notes"].append("1차 수량 0 (예산 부족)")
        return out

    if hold > 0 and avg > 0:
        ex = evaluate_exit(plan, price=price, avg=avg, hold=hold, ma=ma)
        out["exit"] = ex
        out["sells"] = ex["sells"]

    if status != "ACTIVE" or out["sells"] or plan.get("pending_buy") or plan.get("pending_sell"):
        return out
    if plan.get("last_buy_date") == today.isoformat():
        return out
    n_auto = len(auto_fills(plan))
    if n_auto >= int(prof["max_steps"]):
        return out
    lp = last_fill_price(plan)
    if not lp:
        return out
    trig = next_trigger(lp, prof["step_pct"])
    out["trigger"] = trig
    if price <= trig:
        qty = step_qty(plan, price)
        if qty > 0:
            out["buy"] = {"step": n_auto + 1, "qty": qty, "price": int(price),
                          "reason": f"{n_auto + 1}차 추가 (현재가 {int(price):,} ≤ 트리거 {trig:,})"}
        else:
            out["notes"].append("추가 수량 0 (예산 부족)")
    return out


# ───────────────────────── 상태 저장소 ─────────────────────────

def _now_iso() -> str:
    try:
        from utils.datetime_kst import now_kst

        return now_kst().replace(microsecond=0).isoformat()
    except Exception:
        return datetime.now().replace(microsecond=0).isoformat()


class LadderStore:
    """logs/_rsi_ladder_state.json — enabled · plans · events · last_judge."""

    MAX_EVENTS = 300

    def __init__(self, path: Optional[Path] = None):
        self.path = path or _STATE_PATH
        self.lock = threading.RLock()
        self.data: Dict[str, Any] = {}
        self.load()

    @staticmethod
    def _empty() -> Dict[str, Any]:
        return {"enabled": False, "plans": {}, "events": [], "last_judge": {}, "last_summary": ""}

    def load(self) -> None:
        data = self._empty()
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data.update(raw)
        except Exception:
            pass
        self.data = data

    def save(self) -> None:
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                tmp.replace(self.path)
            except Exception:
                pass

    # enabled
    @property
    def enabled(self) -> bool:
        return bool(self.data.get("enabled"))

    def set_enabled(self, on: bool) -> None:
        self.data["enabled"] = bool(on)
        self.add_event(None, "SYSTEM", f"자동 감시 {'ON' if on else 'OFF'}")

    # plans
    def plans(self) -> Dict[str, Dict[str, Any]]:
        return self.data.setdefault("plans", {})

    def get(self, plan_id: str) -> Optional[Dict[str, Any]]:
        return self.plans().get(str(plan_id))

    def open_plan_for(self, code: str) -> Optional[Dict[str, Any]]:
        for p in self.plans().values():
            if p.get("code") == code and p.get("status") in OPEN_STATUSES:
                return p
        return None

    def create_plan(self, *, code: str, name: str, budget: int, profile: Dict[str, Any],
                    memo: str = "") -> Dict[str, Any]:
        pid = uuid.uuid4().hex[:10]
        plan = {
            "id": pid,
            "code": code,
            "name": name,
            "budget": int(budget),
            "profile": merge_profile(profile),
            "status": "RSI_WAIT",
            "created_at": _now_iso(),
            "memo": memo,
            "fills": [],
            "sells": [],
            "sell_flags": {},
            "armed": False,
            "base_qty": 0,
            "peak_pnl_pct": 0.0,
            "pending_buy": None,
            "pending_sell": None,
            "last_buy_date": None,
            "paused_from": None,
            "last_judge": None,
        }
        self.plans()[pid] = plan
        self.add_event(pid, "CREATE", f"{name}({code}) 사다리 생성 · 예산 {int(budget):,}원")
        return plan

    def delete_plan(self, plan_id: str) -> bool:
        if self.plans().pop(str(plan_id), None) is None:
            return False
        self.save()
        return True

    # events
    def add_event(self, plan_id: Optional[str], kind: str, msg: str, *, save: bool = True) -> None:
        ev = {"ts": _now_iso(), "plan_id": plan_id, "kind": kind, "msg": msg}
        if plan_id:
            p = self.get(plan_id)
            if p:
                ev["code"] = p.get("code")
                ev["name"] = p.get("name")
        events = self.data.setdefault("events", [])
        events.append(ev)
        if len(events) > self.MAX_EVENTS:
            del events[: len(events) - self.MAX_EVENTS]
        if save:
            self.save()


_STORE: Optional[LadderStore] = None


def get_ladder_store() -> LadderStore:
    global _STORE
    if _STORE is None:
        _STORE = LadderStore()
    return _STORE


def reset_ladder_store() -> None:
    global _STORE
    _STORE = None
