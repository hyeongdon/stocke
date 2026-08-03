"""분봉 전략 진입·청산 시뮬레이션 (기본 15분, bar_minutes=5 지원)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

from api.kiwoom_api import KiwoomAPI
from core.models import AutoTradeSettings, Position
from utils.auto_trade_engine import get_auto_trade_settings_sync
from utils.buy_condition_checks import build_buy_condition_checklist, checklist_summary
from utils.datetime_kst import kst_today
from utils.sell_condition_checks import build_sell_condition_checklist, sell_checklist_summary
from utils.stock_exit_replay import (
    STRATEGY_LABELS,
    _ReplayState,
    _breakout_level_from_bars,
    _build_stop_candidates,
    _change_rate,
    _check_ymgp_structure_exit,
    _check_ymgp_take_profit,
    _evaluate_entry,
    _exit_fill_price,
    _load_daily_bars,
    _normalize_strategy,
    _num,
    _parse_date,
    _peak_rate_pct,
    _reason_label,
    _resolve_trailing_state,
    _settings_to_dict,
    _snap_entry_index,
    _strategy_time_window,
)
from utils.technical_mart_store import latest_as_of_date

# 검증 차트와 동일하게 최대 7거래일 분봉
MAX_INTRADAY_DAYS = 7


def _parse_bar_dt(ts: str) -> Optional[datetime]:
    raw = (ts or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw[:19] if len(raw) >= 19 else raw[:16], fmt)
        except ValueError:
            continue
    return None


def _hhmm(s: str) -> Tuple[int, int]:
    parts = str(s or "00:00").split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def _in_time_window(dt: datetime, start: str, end: str) -> bool:
    sh, sm = _hhmm(start)
    eh, em = _hhmm(end)
    t = dt.time()
    return dt_time(sh, sm) <= t <= dt_time(eh, em)


def _past_liq_time(dt: datetime, liq: str) -> bool:
    try:
        h, m = _hhmm(liq)
        return dt.time() >= dt_time(h, m)
    except Exception:
        return False


@dataclass
class _DayAccum:
    date: str
    day_open: int = 0
    day_high: int = 0
    day_low: int = 0
    day_volume: int = 0
    vwap_num: float = 0.0
    vwap_den: float = 0.0

    def update(self, o: int, h: int, l: int, c: int, v: int) -> None:
        if not self.day_open and o:
            self.day_open = o
        self.day_high = max(self.day_high, h) if self.day_high else h
        self.day_low = min(self.day_low, l) if self.day_low else l
        self.day_volume += max(0, v)
        typ = (h + l + c) / 3.0 if h and l and c else float(c or 0)
        if v > 0 and typ > 0:
            self.vwap_num += typ * v
            self.vwap_den += v

    @property
    def vwap(self) -> Optional[float]:
        if self.vwap_den <= 0:
            return None
        return self.vwap_num / self.vwap_den


def _ctx_from_day(
    day: _DayAccum,
    *,
    prev_close: int,
    prev_volume: int,
    level_price: int,
    level_kind: str,
    market_cap: Any,
    strategy: str,
) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {
        "day_open": day.day_open,
        "day_high": day.day_high,
        "day_low": day.day_low,
        "day_volume": day.day_volume,
        "prev_close": prev_close,
        "prev_volume": prev_volume,
        "vwap": day.vwap,
    }
    if prev_close:
        from utils.auto_trade_engine import estimate_upper_limit_price
        ctx["upper_limit_price"] = estimate_upper_limit_price(prev_close)
    if strategy == "breakout":
        ctx["level_price"] = level_price
        ctx["level_kind"] = level_kind
        ctx["breakout_level_price"] = level_price
    if strategy == "ymgp":
        ctx["level_price"] = level_price
        ctx["level_kind"] = level_kind or "ymgp_ref_high"
        ctx["breakout_level_price"] = level_price
    if strategy == "sangtta" and market_cap is not None:
        ctx["market_cap"] = market_cap
    return ctx


def _sangtta_band_price_range(
    prev_close: int,
    settings: Dict[str, Any],
) -> Optional[Tuple[int, int]]:
    """등락 밴드 가격 구간 (원)."""
    if not prev_close or prev_close <= 0:
        return None
    lo = float(settings.get("sangtta_change_min") or 12.0)
    hi = float(settings.get("sangtta_change_max") or 15.0)
    if hi < lo:
        lo, hi = hi, lo
    lo_px = int(round(prev_close * (1.0 + lo / 100.0)))
    hi_px = int(round(prev_close * (1.0 + hi / 100.0)))
    if hi_px < lo_px:
        lo_px, hi_px = hi_px, lo_px
    return lo_px, hi_px


def _bar_range_intersects(lo: int, hi: int, band_lo: int, band_hi: int) -> bool:
    return hi >= band_lo and lo <= band_hi


def _estimate_sangtta_band_fill(
    *,
    open_px: int,
    high: int,
    low: int,
    band_lo: int,
    band_hi: int,
) -> Optional[int]:
    """봉 OHLC가 등락 밴드를 가로지르면 추정 체결가.

    - 시가가 이미 밴드 안 → 시가
    - 아래에서 상향 돌파 → 밴드 하단(첫 진입)
    - 위에서 하향 진입 → 밴드 상단(첫 진입)
    """
    if not _bar_range_intersects(low, high, band_lo, band_hi):
        return None
    if band_lo <= open_px <= band_hi:
        return int(open_px)
    if open_px < band_lo and high >= band_lo:
        return int(band_lo)
    if open_px > band_hi and low <= band_hi:
        return int(band_hi)
    # 시가는 밴드 밖이지만 고저가 구간이 겹침 — 교집합 중점
    mid_lo = max(low, band_lo)
    mid_hi = min(high, band_hi)
    if mid_hi >= mid_lo:
        return int(round((mid_lo + mid_hi) / 2.0))
    return None


async def _load_intraday_for_dates(
    code: str,
    dates: List[str],
    *,
    tic_scope: str = "15",
) -> Tuple[List[Dict[str, Any]], List[str], str]:
    """거래일 목록에 대해 분봉을 순차 조회."""
    scope = str(tic_scope or "15").strip()
    api = KiwoomAPI()
    if not api.token_manager.get_valid_token():
        api.authenticate()

    all_bars: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for d in dates:
        result = await api.get_intraday_chart_for_date(
            code, d, tic_scope=scope, max_pages=2,
        )
        bars = result.get("bars") or []
        if bars:
            all_bars.extend(bars)
        else:
            err = result.get("error") or result.get("warning") or "분봉 없음"
            warnings.append(f"{d}: {err}")
        if result.get("warning"):
            warnings.append(str(result["warning"]))

    # 중복 제거·정렬
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for b in sorted(all_bars, key=lambda x: str(x.get("timestamp") or "")):
        ts = b.get("timestamp")
        if not ts or ts in seen:
            continue
        seen.add(ts)
        uniq.append({
            "timestamp": ts,
            "open": int(b.get("open") or 0),
            "high": int(b.get("high") or 0),
            "low": int(b.get("low") or 0),
            "close": int(b.get("close") or 0),
            "volume": int(b.get("volume") or 0),
        })
    return uniq, warnings, f"kiwoom_{scope}m"


def _check_sangtta_exit_15m(
    settings: Dict[str, Any],
    *,
    price: int,
    bar_low: int,
    peak: int,
    prev_close: int,
    soft_count: int,
) -> Tuple[Optional[Tuple[str, float, str]], int]:
    from utils.auto_trade_engine import estimate_upper_limit_price

    lim_soft = _num(settings.get("limit_break_soft_pct")) or 2.0
    lim_hard = _num(settings.get("limit_break_hard_pct")) or 3.0
    drop_soft = _num(settings.get("sharp_drop_soft_pct")) or 3.0
    drop_hard = _num(settings.get("sharp_drop_hard_pct")) or 5.0
    soft_need = max(1, int(settings.get("soft_confirm_polls") or 2))

    ul = estimate_upper_limit_price(prev_close) if prev_close > 0 else None
    touched = bool(ul and peak >= int(ul * 0.999))

    # HARD는 low 기준, SOFT는 close(현재가) 기준 — 실전 폴링에 가깝게
    if ul and touched:
        hard_px = int(ul * (1 - lim_hard / 100.0))
        soft_px = int(ul * (1 - lim_soft / 100.0))
        if bar_low <= hard_px or price <= hard_px:
            return (
                ("STOP_LOSS", float(hard_px),
                 f"상한가 이탈(HARD): {min(price, bar_low):,} ≤ {hard_px:,} (상한가 {ul:,})"),
                0,
            )
        if price <= soft_px:
            soft_count += 1
            if soft_count >= soft_need:
                return (
                    ("STOP_LOSS", float(soft_px),
                     f"상한가 이탈(SOFT≧{soft_need}): {price:,} ≤ {soft_px:,} (상한가 {ul:,})"),
                    soft_count,
                )
            return None, soft_count
        soft_count = 0

    if peak > 0:
        hard_px2 = int(peak * (1 - drop_hard / 100.0))
        soft_px2 = int(peak * (1 - drop_soft / 100.0))
        if bar_low <= hard_px2 or price <= hard_px2:
            return (
                ("STOP_LOSS", float(hard_px2),
                 f"급락(HARD): {min(price, bar_low):,} ≤ {hard_px2:,} (고점 {peak:,})"),
                0,
            )
        if price <= soft_px2:
            soft_count += 1
            if soft_count >= soft_need:
                return (
                    ("STOP_LOSS", float(soft_px2),
                     f"급락(SOFT≧{soft_need}): {price:,} ≤ {soft_px2:,} (고점 {peak:,})"),
                    soft_count,
                )
            return None, soft_count
        soft_count = 0

    return None, soft_count


def _check_breakout_exit_15m(
    settings: Dict[str, Any],
    *,
    price: int,
    bar_low: int,
    level_price: int,
    soft_count: int,
) -> Tuple[Optional[Tuple[str, float, str]], int]:
    if level_price <= 0:
        return None, 0
    soft_pct = _num(settings.get("struct_break_soft_pct")) or 1.0
    hard_pct = _num(settings.get("struct_break_hard_pct")) or 2.0
    soft_need = max(1, int(settings.get("soft_confirm_polls") or 2))
    hard_line = level_price * (1 - abs(hard_pct) / 100.0)
    soft_line = level_price * (1 - abs(soft_pct) / 100.0)

    if bar_low <= hard_line or price <= hard_line:
        return (
            ("STOP_LOSS", float(hard_line),
             f"구조 이탈(HARD): {min(price, bar_low):,} ≤ {int(hard_line):,} (돌파레벨 {level_price:,})"),
            0,
        )
    if price <= soft_line:
        soft_count += 1
        if soft_count >= soft_need:
            return (
                ("STOP_LOSS", float(soft_line),
                 f"구조 이탈(SOFT≧{soft_need}): {price:,} ≤ {int(soft_line):,} (돌파레벨 {level_price:,})"),
                soft_count,
            )
        return None, soft_count
    return None, 0


async def run_stock_exit_replay_15m_async(
    stock_code: str,
    entry_date: str,
    *,
    strategy: str = "legacy",
    days: int = 5,
    force_exit: bool = True,
    settings_override: Optional[Dict[str, Any]] = None,
    intraday_bars_override: Optional[List[Dict[str, Any]]] = None,
    daily_bars_override: Optional[List[Dict[str, Any]]] = None,
    bar_minutes: int = 15,
) -> Dict[str, Any]:
    """분봉으로 진입·청산 시뮬 (기본 15분, bar_minutes=5 지원)."""
    code = KiwoomAPI.normalize_stock_code(stock_code) or str(stock_code).strip().zfill(6)
    if not code or len(code) != 6:
        return {"success": False, "error": "유효하지 않은 종목코드"}

    try:
        entry_d = _parse_date(entry_date)
    except ValueError:
        return {"success": False, "error": "entry_date는 YYYY-MM-DD 형식이어야 합니다."}

    strategy_key = _normalize_strategy(strategy)
    hold_days = max(1, min(int(days or 5), MAX_INTRADAY_DAYS))
    bar_min = 5 if int(bar_minutes or 15) <= 5 else 15
    tic_scope = str(bar_min)
    res_label = f"{bar_min}m"
    close_mode = f"{bar_min}m_close"
    close_note = f"{bar_min}분봉 종가 체결"

    db_settings = get_auto_trade_settings_sync()
    if not db_settings and not settings_override:
        return {"success": False, "error": "AutoTradeSettings 없음"}

    settings = _settings_to_dict(db_settings) if db_settings else {}
    if settings_override:
        settings.update(settings_override)

    class _S:
        pass

    settings_obj = db_settings or _S()
    if settings_override or not db_settings:
        for k, v in settings.items():
            setattr(settings_obj, k, v)

    # 일봉: 레벨·전일종가·거래일 스냅용 (역매공파는 MA480용 장기)
    warmup = max(40, int(settings.get("breakout_n_day") or 10) + 5)
    if strategy_key == "ymgp":
        warmup = max(warmup, 800)
    fetch_start = entry_d - timedelta(days=warmup)
    fetch_end = min(kst_today(), entry_d + timedelta(days=hold_days + 14))

    if daily_bars_override is not None:
        daily, data_source_daily = daily_bars_override, "override"
    else:
        daily, data_source_daily = await _load_daily_bars(
            code, fetch_start, fetch_end, min_bars=120 if strategy_key == "ymgp" else 20,
        )
    if not daily:
        return {
            "success": False,
            "error": (
                f"일봉 데이터 없음 ({code}) — 분봉 시뮬에 전일종가·거래일 스냅용 일봉이 필요합니다. "
                f"technical_mart 미비이거나, 로컬 분당 API 한도/키움 일시 실패로 조회가 비었을 수 있습니다. "
                f"잠시 후 다시 시도하세요."
            ),
            "hint": "retry_api_or_mart",
        }

    from utils.technical_mart_store import get_latest_map_by_codes
    snap = get_latest_map_by_codes([code]).get(code) or {}
    stock_name = str(daily[-1].get("stock_name") or snap.get("stock_name") or code)
    daily_dates = [_parse_date(b["date"]) for b in daily]
    requested_entry = entry_d
    entry_idx, entry_d, snap_note = _snap_entry_index(daily_dates, entry_d)
    if entry_idx is None:
        return {
            "success": False,
            "error": f"진입일 {requested_entry.isoformat()} 일봉 없음",
        }

    # 보유 가능 거래일 목록
    trade_dates = [
        daily_dates[i].isoformat()
        for i in range(entry_idx, min(len(daily_dates), entry_idx + hold_days))
    ]
    if not trade_dates:
        return {"success": False, "error": "시뮬레이션 거래일 없음"}

    prev_close = int(daily[entry_idx - 1].get("close") or 0) if entry_idx > 0 else 0
    prev_volume = int(daily[entry_idx - 1].get("volume") or 0) if entry_idx > 0 else 0
    level_price, level_kind = 0, "prev_high"
    if strategy_key == "breakout":
        # 일봉 레벨은 표시용 fallback. 실제 진입은 분봉 resolve 사용.
        level_price, level_kind = _breakout_level_from_bars(daily, entry_idx, settings)

    market_cap = None
    if strategy_key == "sangtta":
        try:
            from utils.fundamental_mart_store import get_latest_by_code
            fund = get_latest_by_code(code) or {}
            market_cap = fund.get("market_cap")
        except Exception:
            pass

    chart_warnings: List[str] = []
    load_dates = list(trade_dates)
    # 돌파 5분: MA20·N봉 레벨용으로 진입일 직전 거래일 분봉도 로드
    if strategy_key == "breakout" and bar_min == 5 and intraday_bars_override is None:
        warmup_n = 3
        for j in range(max(0, entry_idx - warmup_n), entry_idx):
            d = daily_dates[j].isoformat()
            if d not in load_dates:
                load_dates.insert(0, d)

    if intraday_bars_override is not None:
        m_bars, data_source = intraday_bars_override, "override"
    else:
        m_bars, chart_warnings, data_source = await _load_intraday_for_dates(
            code, load_dates, tic_scope=tic_scope,
        )

    if not m_bars:
        return {
            "success": False,
            "error": f"{bar_min}분봉 데이터 없음 — 키움 API/날짜를 확인하세요",
            "warnings": chart_warnings,
        }

    win_start, win_end = _strategy_time_window(settings, strategy_key)
    is_breakout = strategy_key == "breakout"
    is_ymgp = strategy_key == "ymgp"
    if is_ymgp:
        trail_start = _num(settings.get("ymgp_trailing_start_pct"))
    elif is_breakout:
        trail_start = _num(settings.get("breakout_trailing_start_pct"))
    else:
        trail_start = _num(settings.get("take_profit_rate"))
    trail_start_val = trail_start if trail_start and trail_start > 0 else None
    entry_atr = _num(daily[entry_idx].get("atr14"))

    # 역매공파: 진입일 직전 일봉으로 단계·기준봉 선계산
    ymgp_ref: Dict[str, Any] = {}
    ymgp_box = None
    ymgp_pre: Dict[str, Any] = {}
    if is_ymgp:
        from utils.ymgp_engine import evaluate_ymgp_entry_from_daily
        _ok0, _r0, ymgp_pre = evaluate_ymgp_entry_from_daily(
            daily,
            settings_obj,
            current_price=int(daily[entry_idx].get("close") or 0),
            change_rate=None,
            asof_idx=max(0, entry_idx - 1),
        )
        ymgp_ref = dict(ymgp_pre.get("ymgp_ref") or {})
        ymgp_box = ymgp_pre.get("ymgp_box")
        if ymgp_ref.get("high"):
            level_price = int(ymgp_ref["high"])
            level_kind = "ymgp_ref_high"

    # --- 진입 스캔 ---
    day_map: Dict[str, _DayAccum] = {}
    entry_ok = False
    entry_reason = "시간대 내 게이트 미통과"
    buy_price = 0
    buy_ts: Optional[str] = None
    buy_idx = -1
    gate_ctx: Dict[str, Any] = {}
    change_rate: Optional[float] = None
    entry_fill_mode = close_mode
    entry_fill_note = f"{bar_min}분봉 게이트 통과 시각(봉 종가 체결 가정)"

    band_range = (
        _sangtta_band_price_range(prev_close, settings)
        if strategy_key == "sangtta" and prev_close > 0
        else None
    )

    from utils.auto_trade_engine import resolve_breakout_level_from_minute_bars

    for i, bar in enumerate(m_bars):
        ts = str(bar.get("timestamp") or "")
        dt = _parse_bar_dt(ts)
        if not dt:
            continue
        dkey = ts[:10]
        if dkey not in trade_dates:
            continue
        # 진입은 첫 거래일(스냅된 진입일)만
        if dkey != trade_dates[0]:
            break

        o, h, l, c = int(bar["open"]), int(bar["high"]), int(bar["low"]), int(bar["close"])
        v = int(bar.get("volume") or 0)
        if h <= 0 or l <= 0 or c <= 0:
            continue

        if dkey not in day_map:
            day_map[dkey] = _DayAccum(date=dkey)
        day = day_map[dkey]
        day.update(o, h, l, c, v)

        if not _in_time_window(dt, win_start, win_end):
            continue

        ctx = _ctx_from_day(
            day,
            prev_close=prev_close,
            prev_volume=prev_volume,
            level_price=level_price,
            level_kind=level_kind,
            market_cap=market_cap,
            strategy=strategy_key,
        )

        # 돌파: 5분봉 resolve로 레벨·MA20·장대·거래량 (라이브와 동일)
        if strategy_key == "breakout":
            hist = m_bars[: i + 1]
            resolved, err = resolve_breakout_level_from_minute_bars(
                hist, settings_obj, exclude_forming=False,
            )
            if err or not resolved:
                entry_reason = err or "분봉 레벨 불가"
                continue
            ctx.update(resolved)
            level_price = int(resolved.get("level_price") or level_price)
            level_kind = str(resolved.get("level_kind") or level_kind)
            # 시뮬은 스캔 폴링 대신 봉 SOFT로 충족 처리
            soft_need = max(1, int(getattr(settings_obj, "breakout_entry_soft_polls", None) or 3))
            if int(resolved.get("soft_bar_streak") or 0) >= soft_need:
                ctx["entry_soft_streak"] = soft_need
            else:
                ctx["entry_soft_streak"] = 0

        if strategy_key == "ymgp":
            ctx["daily_bars"] = daily
            ctx["ymgp_asof_idx"] = max(0, entry_idx - 1)
            ctx.update({k: v for k, v in ymgp_pre.items() if k.startswith("ymgp_") or k in (
                "level_kind", "level_price", "breakout_level_price", "overheat", "gate_checks",
            )})

        # 1) 종가 기준 게이트
        candidates: List[Tuple[int, str, str]] = [
            (c, close_mode, close_note),
        ]
        # 2) 상따: 고가·저가가 등락 밴드를 가로지르면 추정 체결가
        if strategy_key == "sangtta" and band_range:
            blo, bhi = band_range
            fill = _estimate_sangtta_band_fill(
                open_px=o, high=h, low=l, band_lo=blo, band_hi=bhi,
            )
            if fill and fill != c:
                candidates.append((
                    fill,
                    "band_cross",
                    f"{bar_min}분봉 고저가 밴드({blo:,}~{bhi:,}) 횡단 → 추정 체결 {fill:,}",
                ))
        # 3) 역매공파: 봉 고가가 기준봉 고점 돌파 시 돌파가 근사 체결
        if strategy_key == "ymgp":
            ref_hi = int((ymgp_ref or {}).get("high") or 0)
            if ref_hi > 0 and h > ref_hi:
                fill = max(ref_hi + 1, o) if o > 0 else ref_hi + 1
                fill = min(fill, h)
                if fill != c:
                    candidates.append((
                        fill,
                        "ref_break",
                        f"기준봉 고점({ref_hi:,}) 돌파 → 추정 체결 {fill:,}",
                    ))

        bar_ok = False
        for price, mode, note in candidates:
            cr = _change_rate(price, prev_close)
            ok, reason = _evaluate_entry(
                strategy_key, settings, settings_obj, price, cr, ctx,
            )
            entry_reason = reason
            if ok:
                entry_ok = True
                entry_reason = reason
                buy_price = price
                buy_ts = ts
                buy_idx = i
                gate_ctx = dict(ctx)
                change_rate = cr
                entry_fill_mode = mode
                entry_fill_note = note
                bar_ok = True
                break
        if bar_ok:
            break

    # 돌파 구조손절: 돌파봉 저가 우선 (파세코형)
    struct_level_price = level_price
    if is_breakout and entry_ok:
        bar_lo = int(gate_ctx.get("breakout_bar_low") or gate_ctx.get("confirm_low") or 0)
        if bar_lo > 0:
            struct_level_price = bar_lo
    if is_ymgp and ymgp_ref.get("low"):
        struct_level_price = int(ymgp_ref["low"])

    # 게이트 미통과 시에도 첫 윈도우 봉으로 가정 진입(청산 시뮬용) — 플래그 유지
    assumed_entry = False
    if not entry_ok:
        for i, bar in enumerate(m_bars):
            ts = str(bar.get("timestamp") or "")
            dt = _parse_bar_dt(ts)
            if not dt or ts[:10] != trade_dates[0]:
                continue
            if not _in_time_window(dt, win_start, win_end):
                continue
            c = int(bar.get("close") or 0)
            if c <= 0:
                continue
            buy_price = c
            buy_ts = ts
            buy_idx = i
            assumed_entry = True
            entry_fill_mode = "assumed_close"
            entry_fill_note = "게이트 미통과 — 시간대 첫 봉 종가로 가정 진입"
            dkey = ts[:10]
            day = day_map.get(dkey) or _DayAccum(date=dkey)
            gate_ctx = _ctx_from_day(
                day,
                prev_close=prev_close,
                prev_volume=prev_volume,
                level_price=level_price,
                level_kind=level_kind,
                market_cap=market_cap,
                strategy=strategy_key,
            )
            change_rate = _change_rate(buy_price, prev_close)
            break

    if buy_idx < 0 or buy_price <= 0:
        return {
            "success": False,
            "error": f"진입 후보 봉 없음 (시간대 {win_start}~{win_end})",
            "entry": {"passed": False, "reason": entry_reason},
        }

    source_map = {
        "legacy": "screener",
        "sangtta": "sangtta",
        "breakout": "breakout",
        "ymgp": "ymgp",
    }
    buy_meta = {
        "strategy": strategy_key,
        "source": source_map[strategy_key],
        "current_price": buy_price,
        "change_rate": change_rate,
        "level_kind": level_kind if (is_breakout or is_ymgp) else None,
        "level_price": level_price if (is_breakout or is_ymgp) else None,
        "breakout_level_price": level_price if (is_breakout or is_ymgp) else None,
        "volume_ratio": gate_ctx.get("volume_ratio"),
        "gate_pack": {
            "legacy": "legacy_momentum",
            "sangtta": "sangtta_breakout",
            "breakout": "oversold_breakout",
            "ymgp": "yeokmaegongpa",
        }[strategy_key],
        "ymgp_stage": gate_ctx.get("ymgp_stage") or ymgp_pre.get("ymgp_stage"),
        "ymgp_ref": gate_ctx.get("ymgp_ref") or ymgp_ref,
    }
    if is_breakout and buy_meta.get("volume_ratio") is None:
        pv = int(gate_ctx.get("prev_volume") or 0)
        dv = int(gate_ctx.get("day_volume") or 0)
        if pv > 0:
            buy_meta["volume_ratio"] = dv / pv

    buy_checks = build_buy_condition_checklist(
        settings,
        meta=buy_meta,
        price=buy_price,
        change_rate=change_rate,
        fill_amount=buy_price if entry_ok else None,
        gate_ctx=gate_ctx,
    )
    for item in buy_checks:
        if item.get("key") in ("candidate_source", "breakout_universe"):
            item["passed"] = None
            item["note"] = "조건식 이력 없음 — 종목 직접 지정"
            item["actual"] = "직접 지정"

    buy_checks.insert(0, {
        "group": "진입 판정",
        "key": "entry_gate_pack",
        "label": f"{STRATEGY_LABELS[strategy_key]} 게이트",
        "enabled": True,
        "passed": entry_ok,
        "actual": entry_reason,
        "required": f"{bar_min}분봉 AND 통과",
        "note": "가정 진입" if assumed_entry else (buy_ts or ""),
    })

    # --- 청산 스캔 (매수 다음 봉부터) ---
    state = _ReplayState(peak=buy_price)
    soft_count = 0
    timeline: List[Dict[str, Any]] = []
    exit_event: Optional[Dict[str, Any]] = None
    exit_steps: List[Dict[str, Any]] = []
    reason_detail: Optional[str] = None

    assumptions = [
        f"전략: {STRATEGY_LABELS[strategy_key]} ({strategy_key})",
        f"해상도: {bar_min}분봉 (전략 시뮬)",
        f"일봉 출처: {data_source_daily} / 분봉: {data_source}",
        "종목 직접 지정 — 조건식 편입 이력 미재현",
        f"매수 시간대: {win_start}~{win_end}",
        f"최대 보유 {hold_days}거래일 분봉 (≤{MAX_INTRADAY_DAYS}일)",
        f"SOFT 확인 = 연속 {bar_min}분봉 횟수 (soft_confirm_polls)",
        "갭 하락 시 청산가 = min(손절선, 시가)",
        f"상따: 종가 미충족이어도 봉 고저가가 등락 밴드를 가로지르면 밴드 진입가 추정 체결",
    ]
    if snap_note:
        assumptions.insert(1, snap_note)
    if entry_fill_mode == "band_cross":
        assumptions.append(entry_fill_note)
        assumptions.append(
            "밴드횡단 진입 봉: 체결가 이전 저가(시가 쪽 wick)는 급락·손절에 쓰지 않음 "
            "(같은 봉 OHLC를 ‘고점→저가’로 뒤집지 않음)"
        )
    if assumed_entry:
        assumptions.append("진입 게이트 미통과 — 시간대 첫 봉으로 가정 진입 후 청산만 시뮬")
    if is_breakout:
        assumptions.append("수급 돌파: 오버나잇 허용 · ATR 미적용")
    if is_ymgp:
        assumptions.append("역매공파: 진입일 직전 일봉 ARMED·기준봉 + 분봉 고점 돌파")
        assumptions.append("역매공파: 오버나잇 허용 · T1 박스고점은 시뮬 전량 익절 단순화")

    # 밴드 횡단 진입은 같은 봉에서도 청산 가능(고가·저가 반영)
    # 단, 체결가보다 낮은 구간은 진입 전 wick으로 보고 클램핑한다.
    exit_start = buy_idx if (entry_ok and entry_fill_mode == "band_cross") else buy_idx + 1

    for i in range(exit_start, len(m_bars)):
        bar = m_bars[i]
        ts = str(bar.get("timestamp") or "")
        dt = _parse_bar_dt(ts)
        if not dt:
            continue
        dkey = ts[:10]
        o, h, l, c = int(bar["open"]), int(bar["high"]), int(bar["low"]), int(bar["close"])
        v = int(bar.get("volume") or 0)
        if h <= 0 or l <= 0:
            continue

        if dkey not in day_map:
            day_map[dkey] = _DayAccum(date=dkey)
        day_map[dkey].update(o, h, l, c, v)

        same_entry_bar = (
            i == buy_idx
            and entry_ok
            and entry_fill_mode == "band_cross"
            and buy_price > 0
        )
        exit_low = max(l, buy_price) if same_entry_bar else l
        exit_open = max(o, buy_price) if same_entry_bar else o

        state.peak = max(state.peak, h)
        armed, floor = _resolve_trailing_state(
            trailing_armed=state.trailing_armed,
            trailing_floor=state.trailing_floor,
            buy_price=buy_price,
            peak=state.peak,
            trail_start_rate=trail_start_val,
        )
        if armed and floor:
            if not state.trailing_armed or (floor and int(floor) > int(state.trailing_floor or 0)):
                state.trailing_armed = True
                state.trailing_floor = int(floor)

        special = None
        if strategy_key == "sangtta":
            special, soft_count = _check_sangtta_exit_15m(
                settings,
                price=c,
                bar_low=exit_low,
                peak=state.peak,
                prev_close=prev_close,
                soft_count=soft_count,
            )
        elif strategy_key == "breakout":
            special, soft_count = _check_breakout_exit_15m(
                settings,
                price=c,
                bar_low=exit_low,
                level_price=struct_level_price,
                soft_count=soft_count,
            )
        elif strategy_key == "ymgp":
            from utils.ymgp_engine import compute_mas, bars_for_ymgp_eval
            # 진입일까지의 일봉 + 이후 확정일만 MA 갱신 (당일 미완 봉은 종가 대용)
            asof_d = dkey
            daily_asof = [b for b in daily if str(b.get("date") or "")[:10] <= asof_d]
            mas_i = compute_mas(bars_for_ymgp_eval(daily_asof), settings_obj)
            special = _check_ymgp_structure_exit(
                settings, price=c, bar_low=exit_low, ref=ymgp_ref, mas=mas_i,
            )
            if special is None:
                special = _check_ymgp_take_profit(
                    settings, bar_high=h, box=ymgp_box, mas=mas_i,
                )

        # 장마감 청산 (breakout·ymgp 제외 — 오버나잇 허용)
        if (
            special is None
            and not is_breakout
            and not is_ymgp
            and settings.get("liquidate_before_close")
            and _past_liq_time(dt, str(settings.get("liquidate_time") or "15:10"))
        ):
            special = (
                "MARKET_CLOSE",
                float(c),
                f"장마감 전 전량청산 ({settings.get('liquidate_time') or '15:10'})",
            )

        candidates = _build_stop_candidates(
            settings,
            buy_price,
            state.peak,
            entry_atr,
            trailing_armed=state.trailing_armed,
            trailing_floor_price=state.trailing_floor,
            strategy_key=strategy_key,
        )
        eff_stop: Optional[float] = None
        eff_reason: Optional[str] = None
        if candidates:
            eff_reason, eff_stop, _ = max(candidates, key=lambda x: x[1])
            state.stop_loss_price = int(eff_stop)

        peak_rate = _peak_rate_pct(buy_price, state.peak)
        pl_rate = _peak_rate_pct(buy_price, c)
        row = {
            "timestamp": ts,
            "date": dkey,
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
        timeline.append(row)

        if special is not None:
            sp_reason, sp_line, sp_detail = special
            sell_px = int(c) if sp_reason == "MARKET_CLOSE" else _exit_fill_price(sp_line, exit_open, exit_low)
            pl_pct = (sell_px - buy_price) / buy_price * 100.0
            reason_detail = sp_detail
            label_key = sp_reason
            if "급락" in sp_detail:
                label_key = "SANGTTA_DROP"
            elif "상한가" in sp_detail:
                label_key = "SANGTTA_LIMIT"
            elif "구조" in sp_detail or "기준봉" in sp_detail or "MA60" in sp_detail or "MA112" in sp_detail:
                label_key = "YMGP_STRUCTURE" if strategy_key == "ymgp" else "BREAKOUT_STRUCTURE"
            elif sp_reason == "TAKE_PROFIT":
                label_key = "TAKE_PROFIT"
            exit_steps.append({"rule": sp_detail, "price": int(sp_line), "note": ts})
            exit_event = {
                "date": dkey,
                "time": ts,
                "reason": sp_reason,
                "reason_label": _reason_label(label_key),
                "price": sell_px,
                "profit_loss_rate_pct": round(pl_pct, 2),
                "bar_low": exit_low,
                "stop_line": int(sp_line),
                "detail": sp_detail,
            }
            break

        if eff_stop is not None and exit_low <= eff_stop:
            sell_px = _exit_fill_price(eff_stop, exit_open, exit_low)
            pl_pct = (sell_px - buy_price) / buy_price * 100.0
            exit_steps.append({
                "rule": f"{eff_reason}",
                "price": int(eff_stop),
                "note": f"{ts} low {exit_low:,} ≤ {int(eff_stop):,}",
            })
            exit_event = {
                "date": dkey,
                "time": ts,
                "reason": eff_reason,
                "reason_label": _reason_label(eff_reason or ""),
                "price": sell_px,
                "profit_loss_rate_pct": round(pl_pct, 2),
                "bar_low": exit_low,
                "stop_line": int(eff_stop),
            }
            break

    if exit_event is None and force_exit and timeline:
        last = timeline[-1]
        sell_px = int(last["close"] or buy_price)
        pl_pct = (sell_px - buy_price) / buy_price * 100.0
        exit_event = {
            "date": last["date"],
            "time": last["timestamp"],
            "reason": "END_OF_PERIOD",
            "reason_label": _reason_label("END_OF_PERIOD"),
            "price": sell_px,
            "profit_loss_rate_pct": round(pl_pct, 2),
            "bar_low": last.get("low"),
            "stop_line": last.get("effective_stop"),
        }
    elif exit_event is None and force_exit and buy_ts:
        # 매수 직후 봉 없음
        exit_event = {
            "date": buy_ts[:10],
            "time": buy_ts,
            "reason": "END_OF_PERIOD",
            "reason_label": _reason_label("END_OF_PERIOD"),
            "price": buy_price,
            "profit_loss_rate_pct": 0.0,
        }

    closed = exit_event is not None
    sell_reason = (exit_event or {}).get("reason") or "HOLDING"
    sell_price = (exit_event or {}).get("price")
    sell_pl_rate = (exit_event or {}).get("profit_loss_rate_pct")
    sell_ts = (exit_event or {}).get("time")

    pos_sl = float(
        (
            settings.get("ymgp_stop_loss_pct") if is_ymgp
            else settings.get("breakout_stop_loss_pct") if is_breakout
            else settings.get("stop_loss_rate")
        )
        or 0
    )
    pos_tp = float(
        (
            settings.get("ymgp_trailing_start_pct") if is_ymgp
            else settings.get("breakout_trailing_start_pct") if is_breakout
            else settings.get("take_profit_rate")
        )
        or 0
    )

    pos = Position(
        stock_code=code,
        stock_name=stock_name,
        buy_price=buy_price,
        buy_quantity=1,
        buy_amount=buy_price,
        stop_loss_rate=pos_sl,
        take_profit_rate=pos_tp,
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
        strategy_key=strategy_key,
        breakout_level_kind=level_kind if (is_breakout or is_ymgp) else None,
        breakout_level_price=level_price if (is_breakout or is_ymgp) else None,
    )

    sell_checks = build_sell_condition_checklist(
        settings,
        pos,
        buy_price=buy_price,
        qty=1,
        sell_price=int(sell_price) if sell_price else None,
        trigger_reason=sell_reason if closed else None,
        exit_steps=exit_steps,
        has_sell_order=False,
        reason_detail=reason_detail or (exit_event or {}).get("detail"),
        strategy_key=strategy_key,
    )
    for item in sell_checks:
        if item.get("key") == "sell_order_db":
            item["note"] = "시뮬레이션 — 실제 주문 없음"

    # 차트: 매수일~매도일 봉만 (없으면 전체)
    sell_date = (exit_event or {}).get("date") or trade_dates[-1]
    chart_bars = [
        b for b in m_bars
        if trade_dates[0] <= str(b.get("timestamp") or "")[:10] <= sell_date
    ]
    level_lines: List[Dict[str, Any]] = []
    if state.stop_loss_price:
        level_lines.append({
            "kind": "stop",
            "price": state.stop_loss_price,
            "label": f"손절 {state.stop_loss_price:,}",
        })
    if state.trailing_floor:
        level_lines.append({
            "kind": "take",
            "price": state.trailing_floor,
            "label": f"익절바닥 {state.trailing_floor:,}",
        })
    if (is_breakout or is_ymgp) and level_price:
        level_lines.append({
            "kind": "trail",
            "price": level_price,
            "label": f"돌파레벨 {level_price:,}",
        })

    warn_text = " · ".join(dict.fromkeys(chart_warnings)) if chart_warnings else None

    latest = latest_as_of_date("1D")

    return {
        "success": True,
        "resolution": res_label,
        "stock_code": code,
        "stock_name": stock_name,
        "strategy": {
            "key": strategy_key,
            "label": STRATEGY_LABELS[strategy_key],
            "gate_pack": buy_meta["gate_pack"],
            "time_window": f"{win_start}~{win_end}",
        },
        "entry": {
            "date": (buy_ts or "")[:10],
            "time": buy_ts,
            "requested_date": requested_entry.isoformat(),
            "snapped": bool(snap_note),
            "snap_note": snap_note,
            "price_mode": entry_fill_mode,
            "price_label": (
                f"{buy_ts} 밴드횡단 추정" if entry_fill_mode == "band_cross"
                else (f"{buy_ts} 기준봉 돌파 추정" if entry_fill_mode == "ref_break"
                      else (f"{buy_ts} 종가" if buy_ts else ""))
            ),
            "price": buy_price,
            "passed": entry_ok,
            "reason": entry_reason,
            "time_approx": buy_ts,
            "time_note": entry_fill_note,
            "change_rate": round(change_rate, 2) if change_rate is not None else None,
            "level_price": level_price if (is_breakout or is_ymgp) else None,
            "level_kind": level_kind if (is_breakout or is_ymgp) else None,
            "ymgp_stage": buy_meta.get("ymgp_stage"),
            "ymgp_ref": buy_meta.get("ymgp_ref"),
            "assumed": assumed_entry,
            "fill_mode": entry_fill_mode,
        },
        "simulation": {
            "days_requested": hold_days,
            "bars_simulated": len(timeline),
            "start_date": timeline[0]["date"] if timeline else (buy_ts or "")[:10],
            "end_date": timeline[-1]["date"] if timeline else (buy_ts or "")[:10],
            "data_through": m_bars[-1]["timestamp"][:10] if m_bars else None,
            "data_source": data_source,
            "mart_latest_date": latest.isoformat() if latest else None,
            "assumptions": assumptions,
            "resolution": res_label,
        },
        "exit": exit_event,
        "holding": exit_event is None,
        "summary": {
            "buy_price": buy_price,
            "sell_price": sell_price,
            "profit_loss_rate_pct": sell_pl_rate,
            "reason": sell_reason,
            "reason_label": (exit_event or {}).get("reason_label") or _reason_label(sell_reason),
            "peak_price": state.peak,
            "peak_rate_pct": round(_peak_rate_pct(buy_price, state.peak), 2),
            "closed": closed and sell_reason not in ("HOLDING",),
            "entry_passed": entry_ok,
            "buy_time": buy_ts,
            "sell_time": sell_ts,
        },
        "settings_used": settings,
        "timeline": timeline,
        "intraday_chart": {
            "success": True,
            "bars": chart_bars,
            "markers": {
                "buy": (
                    {"time": buy_ts, "price": buy_price, "assumed": assumed_entry}
                    if buy_ts else None
                ),
                "sell": (
                    {
                        "time": sell_ts,
                        "price": sell_price,
                        "assumed": assumed_entry,
                    }
                    if sell_ts and sell_price else None
                ),
            },
            "level_lines": level_lines,
            "date_range": [trade_dates[0], sell_date],
            "warning": (
                ("실제 매수 없음(게이트 미통과) — 차트 마커는 가정 진입 · " + warn_text)
                if assumed_entry and warn_text
                else (
                    "실제 매수 없음(게이트 미통과) — 차트 마커는 가정 진입"
                    if assumed_entry
                    else warn_text
                )
            ),
        },
        "buy_condition_checks": buy_checks,
        "buy_condition_summary": checklist_summary(buy_checks),
        "sell_condition_checks": sell_checks,
        "sell_condition_summary": sell_checklist_summary(sell_checks),
    }
