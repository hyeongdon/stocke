"""프랙탈 스캘핑 헬퍼 함수 모음.

전략 요약:
- 타임프레임: 1분봉 시리즈에서 동작하는 경량 스캘핑 보조 로직.
- 핵심 아이디어: EMA(20)·EMA(50)·EMA(100) 정배열(상향) 상태에서
  Williams 5-봉 프랙탈(저점)을 바탕으로 눌림(pullback)이 발생한 뒤
  EMA20 종가의 '재돌파'가 확인되면 진입을 고려한다.
- 손절: 기본적으로 EMA50(또는 구성된 stop_ema_period) 아래에 tick 버퍼(최소 1틱)로 설정.
- 익절: 진입과 손절 간격 × RR(손익비)로 계산.

이 모듈은 순수 함수들로 구성되어 있으며, 외부에서 차트 데이터를 전달하면
전략 통과(pass) / 대기(wait) / 탈락(fail) 여부와 진입·손절·익절 가격을 반환한다.
유닛테스트와 검증(리플레이) 환경에서 재현 가능한 결과를 제공하도록 설계되었습니다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from utils.datetime_kst import KST, as_kst


def ema_series(values: Sequence[float], period: int) -> List[Optional[float]]:
    """표준 EMA 시리즈 생성.

    반환값은 입력 시계열과 같은 길이의 리스트로, 기간 미만 인덱스에는 None을 둡니다.
    시드(seed)는 기간 첫 구간의 단순평균으로 초기화합니다(전형적인 EMA 초기화 방식).
    """
    n = len(values)
    out: List[Optional[float]] = [None] * n
    if period <= 0 or n < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(float(values[i]) for i in range(period)) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = float(values[i]) * k + prev * (1.0 - k)
        out[i] = prev
    return out


def krx_tick_size(price: int) -> int:
    p = abs(int(price or 0))
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


def floor_to_tick(price: float, tick: int) -> int:
    t = max(1, int(tick or 1))
    px = int(price)
    return (px // t) * t


def stop_below_ema(ema50: float, *, ticks: int = 1, tick_size: Optional[int] = None) -> int:
    buf = max(1, int(ticks or 1))
    tick = int(tick_size) if tick_size else krx_tick_size(int(ema50))
    raw = float(ema50) - buf * tick
    return max(tick, floor_to_tick(raw, tick))


def take_profit_from_rr(entry: int, stop: int, rr: float = 1.5) -> int:
    risk = max(0, int(entry) - int(stop))
    if risk <= 0:
        return int(entry)
    return int(entry) + int(round(float(rr) * risk))


def risk_qty(equity: float, risk_pct: float, entry: int, stop: int, *, qty_cap: int = 0) -> int:
    risk_won = float(equity or 0) * (float(risk_pct or 0) / 100.0)
    per_share = int(entry) - int(stop)
    if risk_won <= 0 or per_share <= 0:
        return 0
    qty = int(risk_won // per_share)
    cap = int(qty_cap or 0)
    if cap > 0:
        qty = min(qty, cap)
    return max(0, qty)


def confirmed_buy_fractals(lows: Sequence[float]) -> List[bool]:
    """Williams 5-봉 저점 프랙탈 판정.

    Williams 프랙탈은 현재 봉의 좌우 각각 2봉보다 낮아야 '저점 프랙탈(매수 가능한 녹색 프랙탈)'으로 본다.
    이 구현은 확정(fractal confirmed)만 표시하므로 리페인트(repaint)가 발생하지 않습니다:
    - 배열의 처음/끝 2봉은 판단 불가하므로 False.
    - 충분한 봉이 없으면 모두 False.
    """
    n = len(lows)
    flags = [False] * n
    if n < 5:
        return flags
    for i in range(2, n - 2):
        c = float(lows[i])
        if (
            c < float(lows[i - 2])
            and c < float(lows[i - 1])
            and c < float(lows[i + 1])
            and c < float(lows[i + 2])
        ):
            flags[i] = True
    return flags


def drop_forming_minute_bar(
    bars: Sequence[Dict[str, Any]],
    now: Optional[datetime] = None,
    *,
    interval_minutes: int = 1,
) -> List[Dict[str, Any]]:
    """현재 시각이 속한 미완성 분봉은 제외."""
    # 차트 공급자에 따라 마지막 봉이 아직 완성되지 않은 '진행중' 봉일 수 있으므로,
    # 봉 타임스탬프가 현재 봉 구간에 있으면 마지막 봉을 제외합니다.
    rows = list(bars or [])
    if not rows:
        return rows
    kst = as_kst(now)
    raw = str(rows[-1].get("timestamp") or "").strip()[:19]
    try:
        bar_ts = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except ValueError:
        return rows
    interval = max(1, int(interval_minutes or 1))
    current_start = kst.replace(
        minute=(kst.minute // interval) * interval,
        second=0,
        microsecond=0,
    )
    if bar_ts >= current_start:
        return rows[:-1]
    return rows


def _alignment_stack(e20: float, e50: float, e100: float) -> bool:
    return e20 > e50 > e100


def evaluate_fractal_setup(
    bars: Sequence[Dict[str, Any]],
    *,
    lookback: int = 20,
    stop_ema_period: int = 50,
    stop_ticks: int = 1,
    rr: float = 1.5,
) -> Dict[str, Any]:
    """확정 1분봉 기준 체크리스트.

    status: pass | wait | fail
    """
    closes = [float(b.get("close") or 0) for b in bars]
    lows = [float(b.get("low") or 0) for b in bars]
    n = len(closes)
    if n < 102:
        return {
            "status": "fail",
            "reason": "프랙탈 탈락: 1분봉 부족(EMA100)",
            "checks": {"bars": n},
        }
    e20 = ema_series(closes, 20)
    e50 = ema_series(closes, 50)
    e100 = ema_series(closes, 100)
    i = n - 1
    c, a20, a50, a100 = closes[i], e20[i], e50[i], e100[i]
    if a20 is None or a50 is None or a100 is None:
        return {
            "status": "fail",
            "reason": "프랙탈 탈락: EMA 미산출",
            "checks": {"bars": n},
        }
    checks = {
        "bars": n,
        "close": c,
        "ema20": round(a20, 2),
        "ema50": round(a50, 2),
        "ema100": round(a100, 2),
        "stack": _alignment_stack(a20, a50, a100),
        "close_gt_ema20": c > a20,
        "close_gt_ema100": c > a100,
        "pullback": False,
        "buy_fractal": False,
        "reclaim": False,
    }
    if c < a100 or a50 < a100:
        return {
            "status": "fail",
            "reason": "프랙탈 탈락: 정배열 붕괴(100EMA)",
            "checks": checks,
        }
    if not _alignment_stack(a20, a50, a100):
        return {
            "status": "wait",
            "reason": "프랙탈 대기: 1분 EMA 정배열 아님",
            "checks": checks,
        }

    start = max(0, n - max(5, int(lookback or 20)))
    fractals = confirmed_buy_fractals(lows)
    pullback = False
    fractal_i = None
    for j in range(start, n):
        ej = e20[j]
        if ej is None:
            continue
        if lows[j] < ej or closes[j] < ej:
            pullback = True
        if fractals[j]:
            fractal_i = j
    checks["pullback"] = pullback
    checks["buy_fractal"] = fractal_i is not None
    checks["fractal_index"] = fractal_i

    prev_c = closes[i - 1]
    prev_e = e20[i - 1]
    reclaim = prev_e is not None and prev_c <= prev_e and c > a20
    checks["reclaim"] = reclaim

    if not pullback:
        return {
            "status": "wait",
            "reason": "프랙탈 대기: 20EMA 눌림 없음",
            "checks": checks,
        }
    if fractal_i is None:
        return {
            "status": "wait",
            "reason": "프랙탈 대기: 녹색 프랙탈 없음",
            "checks": checks,
        }
    if fractal_i >= i:
        return {
            "status": "wait",
            "reason": "프랙탈 대기: 프랙탈 미확정",
            "checks": checks,
        }
    if not reclaim:
        return {
            "status": "wait",
            "reason": "프랙탈 대기: 20EMA 재돌파 종가 대기",
            "checks": checks,
        }

    entry = int(round(c))
    stop_period = 50 if int(stop_ema_period or 50) == 50 else int(stop_ema_period)
    stop_ema = a50 if stop_period == 50 else (ema_series(closes, stop_period)[i] or a50)
    stop = stop_below_ema(float(stop_ema), ticks=stop_ticks, tick_size=krx_tick_size(entry))
    if stop >= entry:
        return {
            "status": "wait",
            "reason": "프랙탈 대기: 손절가가 진입가 이상",
            "checks": checks,
        }
    tp = take_profit_from_rr(entry, stop, rr=rr)
    return {
        "status": "pass",
        "reason": (
            f"프랙탈 게이트 통과: 정배열·눌림·프랙탈·재돌파 "
            f"(진입 {entry:,} 손절 {stop:,} 익절 {tp:,})"
        ),
        "checks": checks,
        "entry": entry,
        "stop_price": stop,
        "take_profit_price": tp,
        "ema50_at_entry": float(stop_ema),
        "rr": float(rr),
    }


def is_fractal_wait_reason(reason: str) -> bool:
    return str(reason or "").startswith("프랙탈 대기")


def is_fractal_fail_reason(reason: str) -> bool:
    return str(reason or "").startswith("프랙탈 탈락")
