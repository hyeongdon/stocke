"""단일 종목 청산 규칙 역사 시뮬레이션 — 일봉 기반 MVP."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from api.kiwoom_api import KiwoomAPI
from core.models import AutoTradeSettings, Position
from utils.auto_trade_engine import get_auto_trade_settings_sync
from utils.datetime_kst import kst_today
from utils.position_peak_since_buy import should_disarm_trailing
from utils.sell_condition_checks import build_sell_condition_checklist, sell_checklist_summary
from utils.technical_mart_store import get_daily_bars_for_code, latest_as_of_date


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _peak_rate_pct(buy_price: int, peak: int) -> float:
    if not buy_price:
        return 0.0
    return (peak - buy_price) / buy_price * 100.0


def _trailing_floor_price(buy_price: int, trail_start_rate: float) -> int:
    return int(buy_price * (1 + trail_start_rate / 100.0))


def _trailing_floor_for_buy(
    buy_price: int,
    trail_start_rate: float,
    stored_floor: Optional[int],
    peak: int,
) -> int:
    target = _trailing_floor_price(buy_price, trail_start_rate)
    old = int(stored_floor or 0)
    if peak < target:
        return old if old > 0 else target
    return max(old, target) if old > 0 else target


def _resolve_trailing_state(
    *,
    trailing_armed: bool,
    trailing_floor: Optional[int],
    buy_price: int,
    peak: int,
    trail_start_rate: Optional[float],
) -> Tuple[bool, Optional[int]]:
    if trail_start_rate is None or trail_start_rate <= 0:
        return True, None

    peak_rate = _peak_rate_pct(buy_price, peak)
    if trailing_armed:
        if peak_rate < trail_start_rate:
            return False, None
        floor = _trailing_floor_for_buy(
            buy_price, trail_start_rate, trailing_floor, peak,
        )
        return True, floor

    if peak_rate >= trail_start_rate:
        return True, _trailing_floor_price(buy_price, trail_start_rate)

    return False, None


def _build_stop_candidates(
    settings: Dict[str, Any],
    buy_price: int,
    peak: int,
    atr: Optional[float],
    *,
    trailing_armed: bool = False,
    trailing_floor_price: Optional[int] = None,
) -> List[Tuple[str, float, str]]:
    candidates: List[Tuple[str, float, str]] = []
    floor = int(trailing_floor_price) if trailing_floor_price else None

    def _apply_trail_floor(raw: float) -> float:
        if floor is not None:
            return max(raw, float(floor))
        return raw

    sl = _num(settings.get("stop_loss_rate"))
    if sl:
        candidates.append(("STOP_LOSS", buy_price * (1 - abs(sl) / 100.0), "PCT"))

    atr_stop_mult = _num(settings.get("atr_mult_stop"))
    if atr and atr_stop_mult:
        candidates.append(("STOP_LOSS", buy_price - atr * atr_stop_mult, "ATR"))

    lock_trigger = _num(settings.get("profit_lock_trigger"))
    if lock_trigger:
        peak_rate = _peak_rate_pct(buy_price, peak)
        if peak_rate >= lock_trigger:
            lock_floor = _num(settings.get("profit_lock_floor"))
            lock_floor = 0.0 if lock_floor is None else lock_floor
            candidates.append(("PROFIT_LOCK", buy_price * (1 + lock_floor / 100.0), "PCT"))

    if trailing_armed:
        tr = _num(settings.get("trailing_stop_pct"))
        if tr:
            raw = peak * (1 - tr / 100.0)
            candidates.append(("TRAILING", _apply_trail_floor(raw), "PCT"))

        atr_trail_mult = _num(settings.get("atr_mult_trail"))
        if atr and atr_trail_mult:
            raw = peak - atr * atr_trail_mult
            candidates.append(("TRAILING", _apply_trail_floor(raw), "PCT"))

    return candidates


def _settings_to_dict(settings: AutoTradeSettings) -> Dict[str, Any]:
    return {
        "stop_loss_rate": settings.stop_loss_rate,
        "take_profit_rate": settings.take_profit_rate,
        "trailing_stop_pct": settings.trailing_stop_pct,
        "atr_mult_stop": settings.atr_mult_stop,
        "atr_mult_trail": settings.atr_mult_trail,
        "atr_period": settings.atr_period,
        "profit_lock_trigger": settings.profit_lock_trigger,
        "profit_lock_floor": settings.profit_lock_floor,
        "liquidate_before_close": settings.liquidate_before_close,
        "liquidate_time": settings.liquidate_time,
    }


def _parse_date(s: str) -> date:
    return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()


def _atr14_series(bars: List[Dict[str, Any]]) -> List[Optional[float]]:
    """일봉 리스트에 대응하는 ATR14 (인덱스 i = bars[i])."""
    period = 14
    if len(bars) < period + 1:
        return [None] * len(bars)
    highs = [float(b.get("high") or 0) for b in bars]
    lows = [float(b.get("low") or 0) for b in bars]
    closes = [float(b.get("close") or 0) for b in bars]
    trs: List[float] = []
    out: List[Optional[float]] = [None] * len(bars)
    prev_close: Optional[float] = None
    for i, bar in enumerate(bars):
        h, l, c = highs[i], lows[i], closes[i]
        if h <= 0 or l <= 0:
            if c > 0:
                prev_close = c
            continue
        tr = (h - l) if prev_close is None else max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
        if len(trs) >= period:
            out[i] = sum(trs[-period:]) / period
    return out


def _normalize_chart_bars(chart: List[Dict[str, Any]], code: str) -> List[Dict[str, Any]]:
    ordered = sorted(chart, key=lambda x: str(x.get("timestamp") or ""))
    out: List[Dict[str, Any]] = []
    for row in ordered:
        d = str(row.get("timestamp") or "")[:10]
        if len(d) != 10:
            continue
        out.append({
            "stock_code": code,
            "date": d,
            "open": int(row.get("open") or 0),
            "high": int(row.get("high") or 0),
            "low": int(row.get("low") or 0),
            "close": int(row.get("close") or 0),
            "volume": int(row.get("volume") or 0),
        })
    atrs = _atr14_series(out)
    for i, bar in enumerate(out):
        if atrs[i] is not None:
            bar["atr14"] = atrs[i]
    return out


def _bars_needed_to_cover(fetch_start: date, fetch_end: date) -> int:
    """키움 일봉은 최신부터 슬라이스되므로, 진입일까지 포함하려면
    (오늘←fetch_start) 구간을 커버할 만큼 max_bars를 크게 잡는다.
    """
    today = kst_today()
    # 거래일 ≈ 달력일의 72% + ATR 워밍업 여유
    span_from_start = max(0, (today - fetch_start).days) + 5
    span_window = max(0, (fetch_end - fetch_start).days) + 40
    need = max(int(span_from_start * 0.78) + 40, span_window, 80)
    return min(need, 900)


def _snap_entry_index(dates: List[date], entry_d: date) -> Tuple[Optional[int], date, Optional[str]]:
    """휴장·주말이면 직전(없으면 직후) 거래일로 스냅."""
    exact = next((i for i, d in enumerate(dates) if d == entry_d), None)
    if exact is not None:
        return exact, entry_d, None
    before = [i for i, d in enumerate(dates) if d <= entry_d]
    if before:
        i = before[-1]
        return i, dates[i], f"진입일 {entry_d.isoformat()} 휴장 → {dates[i].isoformat()}로 조정"
    after = [i for i, d in enumerate(dates) if d >= entry_d]
    if after:
        i = after[0]
        return i, dates[i], f"진입일 {entry_d.isoformat()} 데이터 없음 → {dates[i].isoformat()}로 조정"
    return None, entry_d, None


async def _load_daily_bars(
    code: str,
    fetch_start: date,
    fetch_end: date,
    *,
    min_bars: int,
) -> Tuple[List[Dict[str, Any]], str]:
    """DB technical_snapshots 우선, 부족 시 Kiwoom 일봉 API."""
    db_bars = get_daily_bars_for_code(code, start_date=fetch_start, end_date=fetch_end)
    if len(db_bars) >= min_bars:
        return db_bars, "technical_snapshots"

    need = max(min_bars + 30, _bars_needed_to_cover(fetch_start, fetch_end))
    api = KiwoomAPI()
    if not api.token_manager.get_valid_token():
        api.authenticate()
    chart = await api.get_stock_chart_data(
        code, "1D", max_bars=need, allow_off_hours=True,
    )
    if chart:
        api_bars = _normalize_chart_bars(chart, code)
        filtered = [
            b for b in api_bars
            if fetch_start <= _parse_date(b["date"]) <= fetch_end
        ]
        # 진입일 포함 여부 우선 — 필터만으로 빠지면 전체(최근 N봉) 반환해 스냅/에러 메시지에 활용
        if filtered and any(_parse_date(b["date"]) >= fetch_start for b in filtered):
            has_near_entry = any(
                abs((_parse_date(b["date"]) - fetch_start).days) <= 45 for b in filtered
            )
            if has_near_entry and len(filtered) >= min(5, min_bars):
                return filtered, "kiwoom_api"
        if api_bars:
            # 시뮬레이션 구간만 필요하므로 fetch 범위로 재필터(가능하면)
            in_range = [
                b for b in api_bars
                if fetch_start <= _parse_date(b["date"]) <= fetch_end
            ]
            return (in_range or api_bars), "kiwoom_api"

    if db_bars:
        return db_bars, "technical_snapshots"
    return [], "none"


def _exit_fill_price(stop_line: float, bar_open: int, bar_low: int) -> int:
    """갭 하락 시 시가, 아니면 손절선."""
    line = int(round(stop_line))
    if bar_open and bar_open <= line:
        return int(bar_open)
    if bar_low and bar_low <= line:
        return line
    return line


def _reason_label(reason: str) -> str:
    labels = {
        "STOP_LOSS": "손절",
        "TAKE_PROFIT": "익절",
        "TRAILING": "트레일링 스탑",
        "PROFIT_LOCK": "수익 잠금",
        "HOLDING": "미청산",
        "END_OF_PERIOD": "기간 종료 청산",
    }
    return labels.get(reason, reason)


@dataclass
class _ReplayState:
    peak: int
    trailing_armed: bool = False
    trailing_floor: Optional[int] = None
    stop_loss_price: Optional[int] = None


def run_stock_exit_replay(
    stock_code: str,
    entry_date: str,
    *,
    entry_price_mode: str = "close",
    days: int = 120,
    force_exit: bool = True,
    settings_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """동기 래퍼 — CLI·스크립트용."""
    return asyncio.run(
        run_stock_exit_replay_async(
            stock_code,
            entry_date,
            entry_price_mode=entry_price_mode,
            days=days,
            force_exit=force_exit,
            settings_override=settings_override,
        ),
    )


async def run_stock_exit_replay_async(
    stock_code: str,
    entry_date: str,
    *,
    entry_price_mode: str = "close",
    days: int = 120,
    force_exit: bool = True,
    settings_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """현재 AutoTradeSettings로 단일 진입·일봉 청산 시뮬레이션."""
    code = KiwoomAPI.normalize_stock_code(stock_code) or str(stock_code).strip().zfill(6)
    if not code or len(code) != 6:
        return {"success": False, "error": "유효하지 않은 종목코드"}

    try:
        entry_d = _parse_date(entry_date)
    except ValueError:
        return {"success": False, "error": "entry_date는 YYYY-MM-DD 형식이어야 합니다."}

    mode = (entry_price_mode or "close").strip().lower()
    if mode not in ("close", "next_open"):
        return {"success": False, "error": "entry_price_mode는 close 또는 next_open"}

    days = max(10, min(int(days or 120), 365))

    db_settings = get_auto_trade_settings_sync()
    if not db_settings and not settings_override:
        return {"success": False, "error": "AutoTradeSettings 없음"}

    settings = _settings_to_dict(db_settings) if db_settings else {}
    if settings_override:
        settings.update(settings_override)

    latest = latest_as_of_date("1D")
    end_d = kst_today()
    fetch_start = entry_d - timedelta(days=30)
    fetch_end = min(end_d, entry_d + timedelta(days=days + 10))
    min_bars = max(20, days // 2)

    bars, data_source = await _load_daily_bars(
        code, fetch_start, fetch_end, min_bars=min_bars,
    )
    if not bars:
        return {
            "success": False,
            "error": f"일봉 데이터 없음 ({code}) — technical_mart 배치 또는 Kiwoom API 확인",
        }

    from utils.technical_mart_store import get_latest_map_by_codes
    snap = get_latest_map_by_codes([code]).get(code) or {}
    stock_name = str(bars[-1].get("stock_name") or snap.get("stock_name") or code)
    dates = [_parse_date(b["date"]) for b in bars]
    requested_entry = entry_d
    entry_idx, entry_d, snap_note = _snap_entry_index(dates, entry_d)
    if entry_idx is None:
        return {
            "success": False,
            "error": (
                f"진입일 {requested_entry.isoformat()} 일봉 없음 "
                f"(데이터: {dates[0]} ~ {dates[-1]}, 출처: {data_source})"
            ),
        }

    if mode == "close":
        buy_price = int(bars[entry_idx].get("close") or 0)
        if buy_price <= 0:
            return {"success": False, "error": "진입일 종가 없음"}
        sim_start_idx = entry_idx + 1
        entry_price_label = f"{entry_d.isoformat()} 종가"
    else:
        if entry_idx + 1 >= len(bars):
            return {"success": False, "error": "다음 거래일 일봉 없음 (next_open)"}
        buy_bar = bars[entry_idx + 1]
        buy_price = int(buy_bar.get("open") or 0)
        if buy_price <= 0:
            return {"success": False, "error": "다음 거래일 시가 없음"}
        sim_start_idx = entry_idx + 1
        entry_price_label = f"{buy_bar['date']} 시가"

    sim_end_idx = min(len(bars) - 1, sim_start_idx + days - 1)
    if sim_start_idx >= len(bars):
        return {"success": False, "error": "시뮬레이션 구간 일봉 없음"}

    entry_atr = _num(bars[entry_idx].get("atr14"))
    trail_start = _num(settings.get("take_profit_rate"))
    trail_start_val = trail_start if trail_start and trail_start > 0 else None

    state = _ReplayState(peak=buy_price)
    timeline: List[Dict[str, Any]] = []
    exit_event: Optional[Dict[str, Any]] = None
    exit_steps: List[Dict[str, Any]] = []

    assumptions = [
        f"일봉 출처: {data_source}",
        "일봉 OHLC — 장중 터치는 당일 low/high로 판정",
        "동일 봉에서 여러 규칙 후보 충돌 시 통합 손절선(최고가) 1개만 적용",
        "갭 하락 시 청산가 = min(손절선, 시가)",
        "장마감 전량청산(MARKET_CLOSE)은 일봉 MVP에서 제외",
        "진입 당일(close 모드)은 다음 거래일부터 청산 판정",
    ]
    if snap_note:
        assumptions.insert(1, snap_note)

    for i in range(sim_start_idx, sim_end_idx + 1):
        bar = bars[i]
        bar_date = bar["date"]
        o = int(bar.get("open") or 0)
        h = int(bar.get("high") or 0)
        l = int(bar.get("low") or 0)
        c = int(bar.get("close") or 0)
        if h <= 0 or l <= 0:
            continue

        state.peak = max(state.peak, h)
        armed, floor = _resolve_trailing_state(
            trailing_armed=state.trailing_armed,
            trailing_floor=state.trailing_floor,
            buy_price=buy_price,
            peak=state.peak,
            trail_start_rate=trail_start_val,
        )
        if should_disarm_trailing(
            trailing_armed=state.trailing_armed or armed,
            trail_start_rate=trail_start_val,
            buy_price=buy_price,
            peak=state.peak,
        ):
            armed, floor = False, None

        if armed and floor:
            if not state.trailing_armed or (floor and int(floor) > int(state.trailing_floor or 0)):
                state.trailing_armed = True
                state.trailing_floor = int(floor)

        atr = _num(bar.get("atr14")) or entry_atr
        candidates = _build_stop_candidates(
            settings,
            buy_price,
            state.peak,
            atr,
            trailing_armed=state.trailing_armed,
            trailing_floor_price=state.trailing_floor,
        )

        eff_stop: Optional[float] = None
        eff_reason: Optional[str] = None
        eff_kind = ""
        if candidates:
            eff_reason, eff_stop, eff_kind = max(candidates, key=lambda x: x[1])
            state.stop_loss_price = int(eff_stop)

        peak_rate = _peak_rate_pct(buy_price, state.peak)
        pl_rate = _peak_rate_pct(buy_price, c) if c else 0.0

        day_row: Dict[str, Any] = {
            "date": bar_date,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "peak": state.peak,
            "peak_rate_pct": round(peak_rate, 2),
            "trailing_armed": state.trailing_armed,
            "trailing_floor": state.trailing_floor,
            "effective_stop": int(eff_stop) if eff_stop is not None else None,
            "effective_stop_reason": eff_reason,
            "unrealized_pct": round(pl_rate, 2),
        }
        timeline.append(day_row)

        if eff_stop is not None and l <= eff_stop:
            sell_px = _exit_fill_price(eff_stop, o, l)
            pl_pct = (sell_px - buy_price) / buy_price * 100.0
            rule_note = f"{eff_reason} ({eff_kind})"
            exit_steps.append({
                "rule": rule_note,
                "price": int(eff_stop),
                "note": f"{bar_date} low {l:,} ≤ 선 {int(eff_stop):,}",
            })
            exit_event = {
                "date": bar_date,
                "reason": eff_reason,
                "reason_label": _reason_label(eff_reason or ""),
                "price": sell_px,
                "profit_loss_rate_pct": round(pl_pct, 2),
                "bar_low": l,
                "stop_line": int(eff_stop),
            }
            break

    if exit_event is None and force_exit and timeline:
        last = timeline[-1]
        sell_px = int(last["close"] or buy_price)
        pl_pct = (sell_px - buy_price) / buy_price * 100.0
        exit_event = {
            "date": last["date"],
            "reason": "END_OF_PERIOD",
            "reason_label": _reason_label("END_OF_PERIOD"),
            "price": sell_px,
            "profit_loss_rate_pct": round(pl_pct, 2),
            "bar_low": last.get("low"),
            "stop_line": last.get("effective_stop"),
        }

    closed = exit_event is not None
    sell_reason = (exit_event or {}).get("reason") or "HOLDING"
    sell_price = (exit_event or {}).get("price")
    sell_pl_rate = (exit_event or {}).get("profit_loss_rate_pct")

    pos = Position(
        stock_code=code,
        stock_name=stock_name,
        buy_price=buy_price,
        buy_quantity=1,
        buy_amount=buy_price,
        stop_loss_rate=float(settings.get("stop_loss_rate") or 0),
        take_profit_rate=float(settings.get("take_profit_rate") or 0),
        status=sell_reason if closed else "HOLDING",
        peak_price=state.peak,
        trailing_armed=state.trailing_armed,
        trailing_floor_price=state.trailing_floor,
        stop_loss_price=state.stop_loss_price,
        buy_atr=entry_atr,
        buy_atr_period=int(settings.get("atr_period") or 14),
        current_profit_loss=int(sell_price - buy_price) if sell_price else None,
        current_profit_loss_rate=sell_pl_rate,
        sell_time=datetime.utcnow() if closed else None,
    )

    checks = build_sell_condition_checklist(
        settings,
        pos,
        buy_price=buy_price,
        qty=1,
        sell_price=int(sell_price) if sell_price else None,
        trigger_reason=sell_reason if closed else None,
        exit_steps=exit_steps,
        has_sell_order=False,
    )
    for item in checks:
        if item.get("key") == "sell_order_db":
            item["note"] = "시뮬레이션 — 실제 주문 없음"

    return {
        "success": True,
        "stock_code": code,
        "stock_name": stock_name,
        "entry": {
            "date": entry_d.isoformat(),
            "requested_date": requested_entry.isoformat(),
            "snapped": bool(snap_note),
            "snap_note": snap_note,
            "price_mode": mode,
            "price_label": entry_price_label,
            "price": buy_price,
        },
        "simulation": {
            "days_requested": days,
            "bars_simulated": len(timeline),
            "start_date": timeline[0]["date"] if timeline else None,
            "end_date": timeline[-1]["date"] if timeline else None,
            "data_through": bars[-1]["date"],
            "data_source": data_source,
            "mart_latest_date": latest.isoformat() if latest else None,
            "assumptions": assumptions,
        },
        "exit": exit_event,
        "holding": exit_event is None,
        "summary": {
            "buy_price": buy_price,
            "sell_price": sell_price,
            "profit_loss_rate_pct": sell_pl_rate,
            "reason": sell_reason,
            "reason_label": _reason_label(sell_reason),
            "peak_price": state.peak,
            "peak_rate_pct": round(_peak_rate_pct(buy_price, state.peak), 2),
            "closed": closed and sell_reason not in ("HOLDING",),
        },
        "settings_used": settings,
        "timeline": timeline,
        "sell_condition_checks": checks,
        "sell_condition_summary": sell_checklist_summary(checks),
    }
