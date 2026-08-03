"""역매공파(ymgp) 일봉 상태 판정 · 기준봉 · 진입/무효화 헬퍼.

유니버스(조건식)는 호출측이 모으고, 이 모듈은 소수 후보의 일봉만 평가한다.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from utils.datetime_kst import kst_now_iso, now_kst

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
YMGP_STATE_FILE = os.path.join(LOG_DIR, "_ymgp_state.json")

STAGES = (
    "NONE",
    "FILTERED",
    "READY",
    "ARMED",
    "ENTERED_1",
    "ENTERED_2",
    "MANAGING",
    "STOPPED",
    "DONE",
)

DEFAULTS = {
    "ymgp_ma_fast": 120,
    "ymgp_ma_mid": 240,
    "ymgp_ma_slow": 480,
    "ymgp_box_days": 15,
    "ymgp_box_width_pct": 15.5,
    "ymgp_accum_vol_mult": 2.0,
    "ymgp_accum_body_pct": 7.0,
    "ymgp_accum_wick_vol_mult": 4.0,
    "ymgp_accum_wick_body_mult": 1.5,
    "ymgp_ma_near_pct": 3.0,
    "ymgp_pivot_tol_pct": 2.0,
    "ymgp_drop_lookback": 60,
    "ymgp_drop_pct": -20.0,
    "ymgp_stop_ma_mode": "ma60",
    "ymgp_entry_mode": "ref_high",
    "ymgp_max_change_pct": 10.0,
    "ymgp_pullback_tol_pct": 2.0,
    "ymgp_reentry_lock_days": 5,
    "ymgp_tp1_pct_of_pos": 0.35,
    "ymgp_tp2_pct_of_pos": 0.35,
    "ymgp_enable_pullback_add": True,
    "ymgp_enable_partial_tp": True,
}


def _as_int(settings: Any, key: str, default: int) -> int:
    raw = getattr(settings, key, None) if settings is not None else None
    if raw is None or raw == "":
        return int(DEFAULTS.get(key, default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(DEFAULTS.get(key, default))


def _as_float(settings: Any, key: str, default: float) -> float:
    raw = getattr(settings, key, None) if settings is not None else None
    if raw is None or raw == "":
        return float(DEFAULTS.get(key, default))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(DEFAULTS.get(key, default))


def _as_bool(settings: Any, key: str, default: bool) -> bool:
    raw = getattr(settings, key, None) if settings is not None else None
    if raw is None:
        return bool(DEFAULTS.get(key, default))
    return bool(raw)


def _bar_date(bar: Dict[str, Any]) -> str:
    return str(bar.get("timestamp") or bar.get("date") or "")[:10]


def bars_for_ymgp_eval(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """시뮬/마트 일봉(date)을 ymgp 엔진이 쓰는 timestamp 형식으로 정규화."""
    out: List[Dict[str, Any]] = []
    for b in bars or []:
        row = dict(b)
        d = _bar_date(row)
        if d and not row.get("timestamp"):
            row["timestamp"] = f"{d} 15:30:00"
        out.append(row)
    return out


def evaluate_ymgp_entry_from_daily(
    bars: List[Dict[str, Any]],
    settings: Any = None,
    *,
    current_price: Optional[int] = None,
    change_rate: Optional[float] = None,
    asof_idx: Optional[int] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """일봉 기준 역매공파 1차 진입 판정 (시뮬용, 상태파일 미갱신).

    asof_idx: 이 인덱스까지의 봉으로 단계·기준봉 판정(포함). None이면 전체.
    진입 당일 봉을 빼려면 asof_idx = entry_idx - 1.
    """
    norm = bars_for_ymgp_eval(bars)
    if asof_idx is not None:
        end = max(0, min(int(asof_idx) + 1, len(norm)))
        slice_bars = norm[:end]
    else:
        slice_bars = norm
    evaled = evaluate_ymgp_from_daily(
        slice_bars,
        settings,
        current_price=current_price,
        change_rate=change_rate,
        prior_stage=None,
        stopped_lock=False,
    )
    meta: Dict[str, Any] = {
        "ymgp_stage": evaled.get("stage"),
        "ymgp_checks": evaled.get("checks") or [],
        "ymgp_mas": evaled.get("mas") or {},
        "ymgp_box": evaled.get("box"),
        "ymgp_ref": evaled.get("ref"),
        "ymgp_reason": evaled.get("reason"),
        "overheat": bool(evaled.get("overheat")),
        "gate_checks": evaled.get("checks") or [],
    }
    ref = dict(evaled.get("ref") or {})
    if len(slice_bars) >= 2:
        try:
            ref["prev_high"] = int(slice_bars[-2].get("high") or 0)
        except (TypeError, ValueError):
            pass
    meta["ymgp_ref"] = ref or None
    if ref.get("high"):
        meta["level_kind"] = "ymgp_ref_high"
        meta["level_price"] = int(ref.get("high") or 0)
        meta["breakout_level_price"] = int(ref.get("low") or 0)

    price = int(current_price or 0)
    stage = evaled.get("stage")
    if stage != "ARMED" and not (ref and ref.get("high")):
        brief = format_ymgp_fail_brief(evaled)
        base = f"단계 미달 ({stage})"
        return False, f"{base} · {brief}" if brief else base, meta
    if evaled.get("overheat"):
        return False, "과열 컷", meta
    ok, reason = entry1_breakout_ok(price, ref, settings)
    return ok, reason, meta


def _closes(bars: List[Dict[str, Any]]) -> List[float]:
    out: List[float] = []
    for b in bars:
        try:
            out.append(float(b.get("close") or 0))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def sma_at(closes: List[float], period: int, end_idx: Optional[int] = None) -> Optional[float]:
    """end_idx 포함 직전 period개 종가 SMA. end_idx=None → 마지막 봉."""
    if period <= 0 or not closes:
        return None
    i = len(closes) - 1 if end_idx is None else int(end_idx)
    if i < period - 1:
        return None
    window = closes[i - period + 1 : i + 1]
    if len(window) < period or any(c <= 0 for c in window):
        return None
    return sum(window) / period


def compute_mas(
    bars: List[Dict[str, Any]],
    settings: Any = None,
    *,
    end_idx: Optional[int] = None,
) -> Dict[str, Optional[float]]:
    closes = _closes(bars)
    fast = _as_int(settings, "ymgp_ma_fast", 120)
    mid = _as_int(settings, "ymgp_ma_mid", 240)
    slow = _as_int(settings, "ymgp_ma_slow", 480)
    return {
        "ma_fast": sma_at(closes, fast, end_idx),
        "ma_mid": sma_at(closes, mid, end_idx),
        "ma_slow": sma_at(closes, slow, end_idx),
        "ma20": sma_at(closes, 20, end_idx),
        "ma60": sma_at(closes, 60, end_idx),
        "ma112": sma_at(closes, 112, end_idx),
        "ma224": sma_at(closes, 224, end_idx),
        "ma448": sma_at(closes, 448, end_idx),
        "ma_fast_n": fast,
        "ma_mid_n": mid,
        "ma_slow_n": slow,
    }


def is_reverse_array(mas: Dict[str, Any]) -> bool:
    """역배열: 단기 < 중기 < 장기 (하락 구조). 정배열(단기>중기>장기)과 반대."""
    a, b, c = mas.get("ma_fast"), mas.get("ma_mid"), mas.get("ma_slow")
    if a is None or b is None or c is None:
        return False
    return float(a) < float(b) < float(c)


def _box_stats(bars: List[Dict[str, Any]], box_days: int) -> Optional[Dict[str, float]]:
    if len(bars) < box_days:
        return None
    window = bars[-box_days:]
    highs, lows = [], []
    for b in window:
        try:
            highs.append(float(b.get("high") or 0))
            lows.append(float(b.get("low") or 0))
        except (TypeError, ValueError):
            return None
    if not highs or not lows or min(lows) <= 0:
        return None
    hi, lo = max(highs), min(lows)
    mid = (hi + lo) / 2.0
    width_pct = ((hi - lo) / mid) * 100.0 if mid > 0 else 999.0
    return {"high": hi, "low": lo, "mid": mid, "width_pct": width_pct}


def _has_double_bottom(bars: List[Dict[str, Any]], box_days: int, tol_pct: float) -> bool:
    if len(bars) < max(5, box_days):
        return False
    window = bars[-box_days:]
    lows = []
    for b in window:
        try:
            lows.append(float(b.get("low") or 0))
        except (TypeError, ValueError):
            return False
    if not lows or min(lows) <= 0:
        return False
    floor = min(lows)
    near = [i for i, lo in enumerate(lows) if abs(lo - floor) / floor * 100.0 <= tol_pct]
    if len(near) < 2:
        return False
    return (near[-1] - near[0]) >= 2


def _had_drop_then_sideways(
    bars: List[Dict[str, Any]],
    lookback: int,
    drop_pct: float,
    box_days: int,
) -> Tuple[bool, str]:
    if len(bars) < max(lookback, box_days) + 1:
        return False, f"봉부족(<{max(lookback, box_days) + 1})"
    hist = bars[-(lookback + box_days) : -box_days] or bars[-lookback:]
    highs = []
    for b in hist:
        try:
            highs.append(float(b.get("high") or 0))
        except (TypeError, ValueError):
            continue
    if not highs:
        return False, "고가이력없음"
    peak = max(highs)
    box = _box_stats(bars, box_days)
    if not box or peak <= 0:
        return False, "박스없음"
    dd = (box["mid"] - peak) / peak * 100.0
    ok = dd <= float(drop_pct)
    return ok, f"고점대비 {dd:+.1f}% (기준≤{float(drop_pct):g}%)"


def _volume_revival(bars: List[Dict[str, Any]], box_days: int) -> Tuple[bool, str]:
    if len(bars) < box_days + 5:
        return False, f"봉부족(<{box_days + 5})"
    box = bars[-(box_days + 5) : -5]
    recent = bars[-5:]
    try:
        box_avg = sum(float(b.get("volume") or 0) for b in box) / max(len(box), 1)
        recent_avg = sum(float(b.get("volume") or 0) for b in recent) / max(len(recent), 1)
    except (TypeError, ValueError):
        return False, "거래량파싱실패"
    if box_avg <= 0:
        ok = recent_avg > 0
        return ok, f"최근5일평균 {recent_avg:,.0f} (박스평균0)"
    ratio = recent_avg / box_avg
    ok = recent_avg >= box_avg * 1.2
    return ok, f"최근/박스 {ratio:.2f}× (기준≥1.20×)"


def _ma_support_near(
    close: float,
    mas: Dict[str, Any],
    near_pct: float,
) -> Tuple[bool, str]:
    bits: List[str] = []
    ok = False
    for key, label in (("ma60", "MA60"), ("ma112", "MA112")):
        ma = mas.get(key)
        if ma is None or ma <= 0:
            bits.append(f"{label}=—")
            continue
        ma_f = float(ma)
        dist = (close - ma_f) / ma_f * 100.0
        bits.append(f"{label} {dist:+.2f}%")
        if abs(dist) <= near_pct:
            ok = True
        elif close >= ma_f * (1 - near_pct / 100.0):
            ok = True
    if not bits:
        return False, "MA60/112없음"
    return ok, ", ".join(bits) + f" (근접≤{near_pct:g}%)"


def _double_bottom_detail(
    bars: List[Dict[str, Any]],
    box_days: int,
    tol_pct: float,
) -> Tuple[bool, str]:
    ok = _has_double_bottom(bars, box_days, tol_pct)
    if len(bars) < max(5, box_days):
        return False, f"봉부족(<{max(5, box_days)})"
    window = bars[-box_days:]
    lows = []
    for b in window:
        try:
            lows.append(float(b.get("low") or 0))
        except (TypeError, ValueError):
            return False, "저가파싱실패"
    if not lows or min(lows) <= 0:
        return False, "저가없음"
    floor = min(lows)
    near = [i for i, lo in enumerate(lows) if abs(lo - floor) / floor * 100.0 <= tol_pct]
    span = (near[-1] - near[0]) if len(near) >= 2 else 0
    return ok, f"저점 {floor:,.0f} · 근접{len(near)}개 span={span} (tol≤{tol_pct:g}%)"


def _accum_bar_payload(
    bar: Dict[str, Any],
    *,
    o: float,
    h: float,
    lo: float,
    c: float,
    v: float,
    avg20: float,
    idx: int,
    kind: str,
    body_pct: Optional[float] = None,
) -> Dict[str, Any]:
    return {
        "date": _bar_date(bar),
        "open": int(o),
        "high": int(h),
        "low": int(lo),
        "close": int(c),
        "volume": int(v),
        "vol_mult": round(v / avg20, 2) if avg20 else None,
        "body_pct": round(body_pct, 2) if body_pct is not None else None,
        "bar_index": idx,
        "kind": kind,
    }


def find_accum_bar(
    bars: List[Dict[str, Any]],
    settings: Any = None,
    *,
    lookback: int = 10,
) -> Optional[Dict[str, Any]]:
    """최근 lookback 일봉 중 가장 최근 매집봉. 확정 일봉 기준(당일 진행봉 제외 권장).

    경로 A(장대 양봉): 몸통% ≥ accum_body_pct(기본 7) + 거래량 ≥ avg20×accum_vol_mult(기본 2)
    경로 B(장대 윗꼬리): 거래량 ≥ avg20×accum_wick_vol_mult(기본 4)
      + 윗꼬리 ≥ 몸통×accum_wick_body_mult(기본 1.5) + 윗꼬리 > 아랫꼬리
    """
    vol_mult = _as_float(settings, "ymgp_accum_vol_mult", 2.0)
    body_pct_min = _as_float(settings, "ymgp_accum_body_pct", 7.0)
    wick_vol_mult = _as_float(settings, "ymgp_accum_wick_vol_mult", 4.0)
    wick_body_mult = _as_float(settings, "ymgp_accum_wick_body_mult", 1.5)
    if wick_vol_mult < vol_mult:
        wick_vol_mult = vol_mult
    if len(bars) < 25:
        return None
    # 당일 미완성 봉이 있으면 호출측에서 이미 제외했다고 가정. 여기서는 끝에서부터 탐색.
    search = bars[-lookback:]
    for offset, bar in enumerate(reversed(search)):
        idx = len(bars) - 1 - offset
        if idx < 20:
            continue
        try:
            o = float(bar.get("open") or 0)
            h = float(bar.get("high") or 0)
            lo = float(bar.get("low") or 0)
            c = float(bar.get("close") or 0)
            v = float(bar.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if o <= 0 or c <= 0 or h <= 0 or lo <= 0 or h < lo:
            continue
        avg20 = sum(float(bars[j].get("volume") or 0) for j in range(idx - 20, idx)) / 20.0
        if avg20 <= 0:
            continue

        # A) 장대 양봉: (종가-시가)/시가 ≥ body_pct_min + 거래량 배수
        body_pct = (c - o) / o * 100.0
        if c > o and body_pct >= body_pct_min and v >= avg20 * vol_mult:
            return _accum_bar_payload(
                bar, o=o, h=h, lo=lo, c=c, v=v, avg20=avg20, idx=idx,
                kind="bull", body_pct=body_pct,
            )

        # B) 장대 윗꼬리 + 더 큰 거래량 (음봉 포함)
        body = abs(c - o)
        upper = h - max(o, c)
        lower = min(o, c) - lo
        if (
            body > 0
            and upper >= body * wick_body_mult
            and upper > lower
            and v >= avg20 * wick_vol_mult
        ):
            return _accum_bar_payload(
                bar, o=o, h=h, lo=lo, c=c, v=v, avg20=avg20, idx=idx, kind="wick",
            )
    return None


def gonguri_ok(close: float, mas: Dict[str, Any], near_pct: float) -> Tuple[bool, str]:
    """20/60/112 중 최소 1개 회복 또는 근접."""
    hits = []
    for key, label in (("ma20", "MA20"), ("ma60", "MA60"), ("ma112", "MA112")):
        ma = mas.get(key)
        if ma is None or ma <= 0:
            continue
        ma_f = float(ma)
        if close >= ma_f:
            hits.append(f"{label}회복")
        elif abs(close - ma_f) / ma_f * 100.0 <= near_pct:
            hits.append(f"{label}근접")
    if hits:
        return True, ",".join(hits)
    return False, "공구리 미충족"


def evaluate_ymgp_from_daily(
    bars: List[Dict[str, Any]],
    settings: Any = None,
    *,
    current_price: Optional[int] = None,
    change_rate: Optional[float] = None,
    prior_stage: Optional[str] = None,
    stopped_lock: bool = False,
) -> Dict[str, Any]:
    """일봉으로 단계·체크리스트를 평가. 매수 여부는 별도 entry 판정."""
    checks: List[Dict[str, Any]] = []
    out: Dict[str, Any] = {
        "stage": "NONE",
        "checks": checks,
        "mas": {},
        "ref": None,
        "box": None,
        "reason": "",
    }
    n_bars = len(bars or [])
    if n_bars < 60:
        # 0봉은 대개 장외 조회 생략·API 실패·캐시 없음 (실제 상장일수 부족과 구분)
        out["reason"] = "일봉 조회 없음" if n_bars == 0 else f"일봉 부족({n_bars}<60)"
        checks.append({"key": "bars", "label": "일봉", "passed": False, "actual": str(n_bars)})
        return out

    box_days = _as_int(settings, "ymgp_box_days", 15)
    box_w = _as_float(settings, "ymgp_box_width_pct", 15.5)
    near = _as_float(settings, "ymgp_ma_near_pct", 3.0)
    pivot_tol = _as_float(settings, "ymgp_pivot_tol_pct", 2.0)
    drop_lb = _as_int(settings, "ymgp_drop_lookback", 60)
    drop_pct = _as_float(settings, "ymgp_drop_pct", -20.0)

    # 평가는 직전 확정 일봉 기준(당일 포함 시 마지막이 당일이면 그대로 사용 — 장중 close≈현재가)
    mas = compute_mas(bars, settings)
    out["mas"] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in mas.items()}
    last = bars[-1]
    try:
        close = float(current_price or last.get("close") or 0)
    except (TypeError, ValueError):
        close = float(last.get("close") or 0)

    rev = is_reverse_array(mas)
    ma_fast = mas.get("ma_fast")
    ma_mid = mas.get("ma_mid")
    ma_slow = mas.get("ma_slow")
    rev_actual = (
        f"{ma_fast:,.0f}/{ma_mid:,.0f}/{ma_slow:,.0f}"
        if all(isinstance(x, (int, float)) and x for x in (ma_fast, ma_mid, ma_slow))
        else f"{ma_fast}/{ma_mid}/{ma_slow}"
    )
    checks.append({
        "key": "reverse_array",
        "label": f"역배열 MA{mas.get('ma_fast_n')}/{mas.get('ma_mid_n')}/{mas.get('ma_slow_n')}",
        "passed": rev,
        "actual": rev_actual,
    })
    dropped, drop_actual = _had_drop_then_sideways(bars, drop_lb, drop_pct, box_days)
    checks.append({
        "key": "drop_sideways",
        "label": f"급락({drop_pct}%) 후 횡보",
        "passed": dropped,
        "actual": drop_actual,
    })
    vol_ok, vol_actual = _volume_revival(bars, box_days)
    checks.append({
        "key": "vol_revival",
        "label": "거래량 조짐",
        "passed": vol_ok,
        "actual": vol_actual,
    })

    if stopped_lock:
        out["stage"] = "STOPPED"
        out["reason"] = "손절 후 재진입 락"
        return out

    if not rev:
        out["stage"] = "NONE"
        out["reason"] = "역배열 아님"
        return out

    stage = "FILTERED"
    if not (dropped or vol_ok):
        # 역배열만으로도 FILTERED 유지 (활력은 soft)
        pass

    box = _box_stats(bars, box_days)
    out["box"] = box
    box_ok = bool(box and box["width_pct"] <= box_w)
    if box:
        box_actual = (
            f"{box['width_pct']:.1f}% "
            f"(H{box['high']:,.0f}/L{box['low']:,.0f}, ≤{box_w:g}%)"
        )
    else:
        box_actual = "—"
    checks.append({
        "key": "box",
        "label": f"박스권 ≤{box_w}%",
        "passed": box_ok,
        "actual": box_actual,
    })
    dbl, dbl_actual = _double_bottom_detail(bars, box_days, pivot_tol)
    checks.append({
        "key": "double_bottom",
        "label": "이중 저점",
        "passed": dbl,
        "actual": dbl_actual,
    })
    support, support_actual = _ma_support_near(close, mas, near)
    checks.append({
        "key": "ma_support",
        "label": "60/112 지지·근접",
        "passed": support,
        "actual": support_actual,
    })

    if box_ok and (dbl or support):
        stage = "READY"

    accum = find_accum_bar(bars, settings)
    accum_label = "없음"
    if accum:
        if accum.get("kind") == "wick":
            kind_tag = "윗꼬리"
            extra = f" x{accum.get('vol_mult')}"
        else:
            kind_tag = "장대양봉"
            bp = accum.get("body_pct")
            extra = f" +{bp}% x{accum.get('vol_mult')}" if bp is not None else f" x{accum.get('vol_mult')}"
        accum_label = f"{accum['date']}{extra} ({kind_tag})"
    checks.append({
        "key": "accum_bar",
        "label": "매집봉",
        "passed": accum is not None,
        "actual": accum_label,
    })
    g_ok, g_reason = gonguri_ok(close, mas, near)
    checks.append({
        "key": "gonguri",
        "label": "공구리(MA20/60/112)",
        "passed": g_ok,
        "actual": g_reason,
    })

    if stage == "READY" and accum and g_ok:
        stage = "ARMED"
        out["ref"] = {
            "date": accum["date"],
            "open": accum["open"],
            "high": accum["high"],
            "low": accum["low"],
            "close": accum["close"],
            "vol_mult": accum.get("vol_mult"),
        }

    # 보유 단계가 prior면 승격 유지
    if prior_stage in ("ENTERED_1", "ENTERED_2", "MANAGING", "DONE"):
        stage = prior_stage
    elif prior_stage == "ARMED" and stage in ("FILTERED", "READY") and out.get("ref"):
        stage = "ARMED"

    max_chg = _as_float(settings, "ymgp_max_change_pct", 10.0)
    overheat = change_rate is not None and float(change_rate) >= max_chg
    checks.append({
        "key": "overheat",
        "label": f"과열 컷 <{max_chg}%",
        "passed": not overheat,
        "actual": f"{change_rate}%" if change_rate is not None else "—",
    })

    out["stage"] = stage
    out["overheat"] = overheat
    out["reason"] = {
        "NONE": "필터 탈락",
        "FILTERED": "역배열 후보",
        "READY": "바닥·지지 준비",
        "ARMED": "매집봉·공구리 대기",
        "ENTERED_1": "1차 진입",
        "ENTERED_2": "2차 진입",
        "MANAGING": "익절 관리",
        "STOPPED": "손절 락",
        "DONE": "종료",
    }.get(stage, stage)
    return out


# 종목별 단계 수치 로그 스로틀 (동일 fingerprint는 15분에 1회)
_ymgp_metric_log_at: Dict[str, float] = {}
_ymgp_metric_log_fp: Dict[str, str] = {}
_YMGP_METRIC_LOG_COOLDOWN_SEC = 900.0


def format_ymgp_checks_summary(evaled: Optional[Dict[str, Any]]) -> str:
    """로그용 한 줄 체크 요약: key=✓/✗actual …"""
    if not evaled:
        return ""
    parts: List[str] = []
    stage = evaled.get("stage") or "?"
    parts.append(f"stage={stage}")
    for ch in evaled.get("checks") or []:
        if not isinstance(ch, dict):
            continue
        key = str(ch.get("key") or "")
        if not key:
            continue
        mark = "✓" if ch.get("passed") else "✗"
        actual = str(ch.get("actual") or "").strip() or "—"
        parts.append(f"{key}={mark}{actual}")
    return " | ".join(parts)


def format_ymgp_fail_brief(evaled: Optional[Dict[str, Any]], *, limit: int = 4) -> str:
    """게이트 보류 사유에 붙일 짧은 실패 체크 요약."""
    if not evaled:
        return ""
    fails: List[str] = []
    for ch in evaled.get("checks") or []:
        if not isinstance(ch, dict) or ch.get("passed"):
            continue
        key = str(ch.get("key") or "")
        actual = str(ch.get("actual") or "").strip()
        if not key:
            continue
        fails.append(f"{key}:{actual}" if actual else key)
        if len(fails) >= limit:
            break
    return ", ".join(fails)


def log_ymgp_stage_metrics(
    stock_code: str,
    evaled: Optional[Dict[str, Any]],
    *,
    stock_name: str = "",
    force: bool = False,
) -> Optional[str]:
    """단계·체크 수치를 INFO로 남긴다. 동일 내용이면 cooldown 동안 생략.

    Returns: 이번에 남긴 요약 문자열(또는 None if skipped).
    """
    import time

    code = (stock_code or "").strip()
    if not code or not evaled:
        return None
    summary = format_ymgp_checks_summary(evaled)
    if not summary:
        return None
    now = time.monotonic()
    prev_fp = _ymgp_metric_log_fp.get(code)
    prev_at = float(_ymgp_metric_log_at.get(code) or 0.0)
    changed = prev_fp != summary
    cooled = (now - prev_at) >= _YMGP_METRIC_LOG_COOLDOWN_SEC
    if not force and not changed and not cooled:
        return None
    _ymgp_metric_log_fp[code] = summary
    _ymgp_metric_log_at[code] = now
    label = f"{stock_name}({code})" if stock_name else code
    logger.info(f"📈 [YMGP] 단계수치 {label}: {summary}")
    return summary


def entry1_breakout_ok(
    current_price: int,
    ref: Dict[str, Any],
    settings: Any = None,
) -> Tuple[bool, str]:
    if not current_price or not ref:
        return False, "기준봉/가격 없음"
    mode = str(getattr(settings, "ymgp_entry_mode", None) or "ref_high").strip().lower()
    ref_high = int(ref.get("high") or 0)
    if ref_high <= 0:
        return False, "기준봉 고가 없음"
    if mode in ("ref_high", "either", ""):
        if current_price > ref_high:
            return True, f"기준봉 고점 돌파 ({current_price:,} > {ref_high:,})"
    # prev_high는 호출측이 ref에 prev_high를 넣었을 때
    prev_high = int(ref.get("prev_high") or 0)
    if mode in ("prev_high", "either") and prev_high > 0 and current_price > prev_high:
        return True, f"전일 고점 돌파 ({current_price:,} > {prev_high:,})"
    if mode == "ref_high":
        return False, f"기준봉 고점 미돌파 ({current_price:,} ≤ {ref_high:,})"
    return False, "진입 조건 미충족"


def entry2_pullback_ok(
    current_price: int,
    ref: Dict[str, Any],
    mas: Dict[str, Any],
    settings: Any = None,
) -> Tuple[bool, str]:
    if not _as_bool(settings, "ymgp_enable_pullback_add", True):
        return False, "2차 눌림 비활성"
    if not current_price or not ref:
        return False, "기준봉/가격 없음"
    tol = _as_float(settings, "ymgp_pullback_tol_pct", 2.0)
    ma20 = mas.get("ma20")
    ref_open = int(ref.get("open") or 0)
    anchors = []
    if ma20:
        anchors.append(("MA20", float(ma20)))
    if ref_open > 0:
        anchors.append(("기준봉시가", float(ref_open)))
    if not anchors:
        return False, "눌림 앵커 없음"
    for label, anchor in anchors:
        if anchor <= 0:
            continue
        dist = abs(current_price - anchor) / anchor * 100.0
        if dist <= tol and current_price >= anchor * (1 - tol / 100.0):
            # 재반등: 현재가가 앵커 위(또는 근접)면 OK
            if current_price >= anchor * (1 - 0.3 / 100.0):
                return True, f"눌림 지지 {label} (±{tol}%)"
    return False, "눌림 지지 미확인"


def stop_invalidated(
    current_price: int,
    ref: Dict[str, Any],
    mas: Dict[str, Any],
    settings: Any = None,
    *,
    use_close_vs_ma: bool = True,
) -> Tuple[bool, str]:
    """기준봉 저점 또는 손절 MA 이탈."""
    if not current_price:
        return False, ""
    ref_low = int((ref or {}).get("low") or 0)
    soft = _as_float(settings, "struct_break_soft_pct", 1.0)
    hard = _as_float(settings, "struct_break_hard_pct", 2.0)
    if ref_low > 0:
        hard_line = ref_low * (1 - abs(hard) / 100.0)
        if current_price <= hard_line:
            return True, f"기준봉 저점 HARD ({current_price:,} ≤ {hard_line:,.0f})"
        if current_price < ref_low:
            return True, f"기준봉 저점 이탈 ({current_price:,} < {ref_low:,})"
        # SOFT 구간은 손절 매니저의 연속확인 대신 1차에서는 HARD/이탈만 즉시 청산

    if use_close_vs_ma:
        mode = str(getattr(settings, "ymgp_stop_ma_mode", None) or "ma60").strip().lower()
        candidates = []
        if mode in ("ma60", "either"):
            candidates.append(("MA60", mas.get("ma60")))
        if mode in ("ma112", "either"):
            candidates.append(("MA112", mas.get("ma112")))
        for label, ma in candidates:
            if ma is None:
                continue
            if current_price < float(ma):
                return True, f"{label} 이탈 ({current_price:,} < {float(ma):,.0f})"
    return False, ""


def take_profit_target(
    tp_stage: int,
    box: Optional[Dict[str, float]],
    mas: Dict[str, Any],
) -> Tuple[Optional[float], str]:
    """다음 익절 목표가. tp_stage 0→T1, 1→T2, 2→T3."""
    if tp_stage <= 0:
        if box and box.get("high"):
            return float(box["high"]), "T1 박스고점"
        return None, "T1 없음"
    if tp_stage == 1:
        ma = mas.get("ma224")
        return (float(ma), "T2 MA224") if ma else (None, "T2 없음")
    if tp_stage == 2:
        ma = mas.get("ma448")
        return (float(ma), "T3 MA448") if ma else (None, "T3 없음")
    return None, "익절 완료"


def partial_sell_qty(total_qty: int, tp_stage: int, settings: Any = None) -> int:
    if total_qty <= 0:
        return 0
    p1 = _as_float(settings, "ymgp_tp1_pct_of_pos", 0.35)
    p2 = _as_float(settings, "ymgp_tp2_pct_of_pos", 0.35)
    if tp_stage <= 0:
        q = max(1, int(total_qty * p1))
        return min(q, total_qty)
    if tp_stage == 1:
        q = max(1, int(total_qty * p2))
        return min(q, total_qty)
    return total_qty  # T3 잔량


# ----- watch state (JSON) -----

def load_ymgp_state() -> Dict[str, Any]:
    if not os.path.isfile(YMGP_STATE_FILE):
        return {"stocks": {}}
    try:
        with open(YMGP_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"stocks": {}}
        data.setdefault("stocks", {})
        return data
    except (OSError, json.JSONDecodeError):
        return {"stocks": {}}


def save_ymgp_state(data: Dict[str, Any]) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    payload = dict(data)
    payload["updated_at"] = kst_now_iso()
    tmp = YMGP_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, YMGP_STATE_FILE)


def get_stock_state(code: str) -> Dict[str, Any]:
    code = str(code or "").replace("A", "")
    return dict((load_ymgp_state().get("stocks") or {}).get(code) or {})


def update_stock_state(code: str, **fields: Any) -> Dict[str, Any]:
    code = str(code or "").replace("A", "")
    data = load_ymgp_state()
    stocks = data.setdefault("stocks", {})
    cur = dict(stocks.get(code) or {})
    cur.update(fields)
    cur["updated_at"] = kst_now_iso()
    stocks[code] = cur
    save_ymgp_state(data)
    return cur


def mark_stopped(code: str, settings: Any = None) -> None:
    days = _as_int(settings, "ymgp_reentry_lock_days", 5)
    until = (now_kst() + timedelta(days=max(1, days))).strftime("%Y-%m-%d")
    update_stock_state(
        code,
        stage="STOPPED",
        stopped_at=kst_now_iso(),
        reentry_allowed_after=until,
    )


def is_reentry_locked(code: str, settings: Any = None) -> bool:
    st = get_stock_state(code)
    if st.get("stage") != "STOPPED" and not st.get("reentry_allowed_after"):
        return False
    until = str(st.get("reentry_allowed_after") or "")[:10]
    if not until:
        return st.get("stage") == "STOPPED"
    try:
        return now_kst().strftime("%Y-%m-%d") < until
    except Exception:
        return True


def clear_reentry_if_rearmed(code: str, stage: str) -> None:
    if stage in ("READY", "ARMED"):
        st = get_stock_state(code)
        if st.get("stage") == "STOPPED" and not is_reentry_locked(code):
            update_stock_state(code, stage=stage, stopped_at=None, reentry_allowed_after=None)
