"""자동매매 매매내역 검증 리포트 — DB 기반 라운드트립·계산식 설명."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.models import AutoTradeSettings, PendingBuySignal, Position, PositionBuyFill, SellOrder
from utils.auto_trade_engine import compute_buy_amount, parse_signal_meta
from utils.buy_condition_checks import build_buy_condition_checklist, checklist_summary
from utils.sell_condition_checks import (
    SANGTTA_EXIT_KO,
    build_sell_condition_checklist,
    classify_sangtta_exit_detail,
    sell_checklist_summary,
)
from utils.position_buy_fills import (
    buy_fills_summary,
    effective_buy_stats,
    order_and_filled_totals,
    serialize_buy_fill,
)
from utils.datetime_kst import kst_today, now_kst

KST = timezone(timedelta(hours=9))

SIGNAL_TYPE_KO = {
    "condition": "키움 조건식",
    "reference": "기준봉 전략",
    "strategy": "차트 전략",
    "auto_trade": "자동매매 스캐너",
}

SELL_REASON_KO = {
    "STOP_LOSS": "손절",
    "TAKE_PROFIT": "익절",
    "TRAILING": "트레일링 스탑",
    "PROFIT_LOCK": "수익 잠금",
    "MARKET_CLOSE": "장마감 청산",
    "MANUAL": "수동 매도",
    "INDICATOR": "지표 매도",
}

SELL_STATUS_KO = {
    "PENDING": "대기",
    "ORDERED": "주문접수",
    "COMPLETED": "체결완료",
    "FAILED": "실패",
}


def _fmt_dt(v: Any) -> Optional[str]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v).replace("T", " ")[:19]


def _fmt_dt_kst(v: Any) -> Optional[str]:
    """DB naive UTC → KST 표시 (차트·마커 정렬용)."""
    if not v:
        return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "")[:19])
        except ValueError:
            return str(v).replace("T", " ")[:19]
    if not isinstance(v, datetime):
        return str(v).replace("T", " ")[:19]
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


def _utc_from_db(v: Any, *, stored_as: str = "utc") -> Optional[datetime]:
    """DB naive datetime → UTC naive (비교용).
    stored_as: 'utc' | 'kst' — 레거시 detected_at(KST naive) 보정용.
    """
    if not v:
        return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "")[:26])
        except ValueError:
            return None
    if not isinstance(v, datetime):
        return None
    if v.tzinfo is not None:
        return v.astimezone(timezone.utc).replace(tzinfo=None)
    if stored_as == "kst":
        return v.replace(tzinfo=KST).astimezone(timezone.utc).replace(tzinfo=None)
    return v


def _fmt_dt_signal(v: Any) -> Optional[str]:
    """신호 시각 표시를 KST 기준으로 정규화.

    과거 데이터는 UTC naive, 일부 데이터는 KST naive로 저장되어 혼재할 수 있어
    양쪽 해석(UTC/KST)을 비교해 '현재 시각과 더 가까운 값'을 표시한다.
    """
    if not v:
        return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "")[:26])
        except ValueError:
            return str(v).replace("T", " ")[:19]
    if not isinstance(v, datetime):
        return str(v).replace("T", " ")[:19]

    if v.tzinfo is not None:
        return v.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")

    # naive 혼재(UTC/KST) 자동 보정
    ref_kst = now_kst()
    as_utc = v.replace(tzinfo=timezone.utc).astimezone(KST)
    as_kst = v.replace(tzinfo=KST)
    pick = as_utc if abs((as_utc - ref_kst).total_seconds()) <= abs((as_kst - ref_kst).total_seconds()) else as_kst
    return pick.strftime("%Y-%m-%d %H:%M:%S")


def _signal_buy_delay_sec(signal_at: Any, buy_at: Any) -> Optional[int]:
    """신호 → 매수 체결 지연(초). 양수 = 신호 후 매수.

    과거/신규 데이터의 UTC/KST naive 혼재를 흡수하기 위해
    (signal, buy) 각각 UTC/KST 두 가지 해석으로 후보를 만들고,
    가장 현실적인 지연값(0~12시간 우선, 그중 최소값)을 선택한다.
    """
    candidates: List[int] = []
    for buy_mode in ("utc", "kst"):
        buy_utc = _utc_from_db(buy_at, stored_as=buy_mode)
        if not buy_utc:
            continue
        for sig_mode in ("kst", "utc"):
            sig_utc = _utc_from_db(signal_at, stored_as=sig_mode)
            if not sig_utc:
                continue
            candidates.append(int(round((buy_utc - sig_utc).total_seconds())))

    if not candidates:
        return None

    realistic = sorted(v for v in candidates if 0 <= v <= 12 * 3600)
    if realistic:
        return realistic[0]

    non_negative = sorted(v for v in candidates if v >= 0)
    if non_negative:
        return non_negative[0]
    return max(candidates)


def _format_delay_sec(sec: int) -> str:
    if sec < 0:
        return f"시각 데이터 불일치 ({sec}초)"
    if sec < 60:
        return f"{sec}초"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m}분 {s}초" if s else f"{m}분"
    h, rem = divmod(m, 60)
    return f"{h}시간 {rem}분"


def _kst_date_of(v: Any) -> Optional[date]:
    """DB naive UTC → KST 날짜."""
    if not v:
        return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "")[:19])
        except ValueError:
            return None
    if not isinstance(v, datetime):
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(KST).date()


def _parse_trade_date(s: Optional[str]) -> Optional[date]:
    if not s or not str(s).strip():
        return None
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _kst_today() -> date:
    return kst_today()


def _kst_this_week_range(ref: Optional[date] = None) -> Tuple[date, date]:
    """KST 기준 이번 주 월요일~금요일 (주말이면 해당 주의 월~금)."""
    ref = ref or _kst_today()
    monday = ref - timedelta(days=ref.weekday())
    friday = monday + timedelta(days=4)
    return monday, friday


class TradeDateFilter:
    __slots__ = ("mode", "day", "week_start", "week_end", "label", "raw")

    def __init__(
        self,
        mode: str,
        *,
        day: Optional[date] = None,
        week_start: Optional[date] = None,
        week_end: Optional[date] = None,
        label: str = "전체",
        raw: Optional[str] = None,
    ):
        self.mode = mode
        self.day = day
        self.week_start = week_start
        self.week_end = week_end
        self.label = label
        self.raw = raw


def _parse_trade_filter(s: Optional[str]) -> TradeDateFilter:
    if not s or not str(s).strip():
        return TradeDateFilter("all", label="전체")
    raw = str(s).strip()
    low = raw.lower()
    if low == "all":
        return TradeDateFilter("all", label="전체", raw=raw)
    if low in ("this_week", "week", "이번주"):
        mon, fri = _kst_this_week_range()
        return TradeDateFilter(
            "week",
            week_start=mon,
            week_end=fri,
            label=f"이번주 ({mon.strftime('%m/%d')}~{fri.strftime('%m/%d')})",
            raw=raw,
        )
    d = _parse_trade_date(raw)
    if d:
        return TradeDateFilter("day", day=d, label=str(d), raw=raw)
    return TradeDateFilter("all", label="전체")


def _date_in_filter(d: Optional[date], flt: TradeDateFilter) -> bool:
    if flt.mode == "all":
        return True
    if d is None:
        return False
    if flt.mode == "day":
        return d == flt.day
    if flt.mode == "week" and flt.week_start and flt.week_end:
        return flt.week_start <= d <= flt.week_end
    return False


def _trade_matches_filter(
    flt: TradeDateFilter,
    buy_time: Any,
    sell_completed: Any,
) -> bool:
    if flt.mode == "all":
        return True
    return _date_in_filter(_kst_date_of(buy_time), flt) or _date_in_filter(
        _kst_date_of(sell_completed), flt,
    )


def _trade_on_date(
    target: date,
    buy_time: Any,
    sell_completed: Any,
) -> bool:
    buy_d = _kst_date_of(buy_time)
    sell_d = _kst_date_of(sell_completed)
    return buy_d == target or sell_d == target


def _condition_name(session: Session, condition_id: Optional[int]) -> str:
    if condition_id is None:
        return ""
    if condition_id in (0, 99999):
        return "자동매매 스캐너"
    from core.models import AutoTradeCondition

    row = session.query(AutoTradeCondition).filter(AutoTradeCondition.id == condition_id).first()
    if row:
        return row.condition_name
    row = session.query(AutoTradeCondition).filter(
        AutoTradeCondition.api_condition_id == str(condition_id)
    ).first()
    return row.condition_name if row else f"조건식#{condition_id}"


def _load_buy_fills(
    session: Session,
    pos: Position,
    settings: Dict[str, Any],
    signal: Optional[PendingBuySignal],
) -> List[Dict[str, Any]]:
    rows = (
        session.query(PositionBuyFill)
        .filter(PositionBuyFill.position_id == pos.id)
        .order_by(PositionBuyFill.filled_at.asc())
        .all()
    )
    signal_meta = parse_signal_meta(signal) if signal else {}
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = serialize_buy_fill(row, settings)
        if item.get("change_rate") is None and signal_meta.get("change_rate") is not None:
            cr = float(signal_meta["change_rate"])
            item["change_rate"] = cr
            if not item.get("detail"):
                item["detail"] = f"등락 {cr:+.2f}%"
            elif "등락" not in item["detail"]:
                item["detail"] = f"등락 {cr:+.2f}% · {item['detail']}"
        if item.get("planned_amount") is None and settings:
            cr = item.get("change_rate")
            is_add = row.fill_type == "ADD"
            if cr is not None or is_add:
                try:
                    class _S:
                        pass
                    s = _S()
                    for k, v in settings.items():
                        setattr(s, k, v)
                    planned = compute_buy_amount(s, cr, is_add)
                    item["planned_amount"] = planned
                    if planned and item.get("detail"):
                        item["detail"] += f" · 계획 {planned:,}원"
                    elif planned:
                        item["detail"] = f"계획 {planned:,}원"
                except Exception:
                    pass
        if item.get("is_backfill") and item.get("change_rate") is None:
            imax = int(settings.get("initial_max_amount") or 0)
            imin = int(settings.get("initial_min_amount") or 0)
            amt = int(item.get("amount") or 0)
            smin = settings.get("signal_min_threshold")
            smax = settings.get("signal_max_threshold")
            hint = None
            if imax and amt >= imax * 0.92:
                hint = f"금액 {amt:,}원 ≈ 약한 신호({smin}%+) {imax:,}원"
            elif imin and imin * 0.92 <= amt <= imin * 1.08:
                hint = f"금액 {amt:,}원 ≈ 강한 신호({smax}%+) {imin:,}원"
            if hint:
                item["detail"] = f"{item['detail']} · {hint}" if item.get("detail") else hint
        out.append(item)
    return out


def _load_buy_condition_checks(
    session: Session,
    pos: Position,
    settings: Dict[str, Any],
    signal: Optional[PendingBuySignal],
    buy_fills: List[Dict[str, Any]],
) -> tuple:
    rows = (
        session.query(PositionBuyFill)
        .filter(PositionBuyFill.position_id == pos.id)
        .order_by(PositionBuyFill.filled_at.asc())
        .all()
    )
    for row in rows:
        if row.condition_checks:
            checks = row.condition_checks
            if isinstance(checks, list) and checks:
                return checks, checklist_summary(checks)

    meta = parse_signal_meta(signal) if signal else {}
    sk = getattr(pos, "strategy_key", None) or meta.get("strategy")
    if sk and not meta.get("strategy"):
        meta = {**meta, "strategy": sk}
    if sk == "sangtta" and not meta.get("source"):
        meta = {**meta, "source": "sangtta"}
    if sk == "breakout":
        meta = {
            **meta,
            "source": meta.get("source") or "breakout",
            "level_kind": meta.get("level_kind") or getattr(pos, "breakout_level_kind", None),
            "level_price": (
                meta.get("level_price")
                or getattr(pos, "breakout_level_price", None)
            ),
        }
    price = int(pos.buy_price or 0) or None
    change_rate = None
    if buy_fills and buy_fills[0].get("change_rate") is not None:
        change_rate = buy_fills[0]["change_rate"]
    elif meta.get("change_rate") is not None:
        change_rate = float(meta["change_rate"])
    fill_amount = sum(int(f.get("amount") or 0) for f in buy_fills) if buy_fills else int(pos.buy_amount or 0)
    is_add = bool(buy_fills and buy_fills[0].get("fill_type") == "ADD")

    checks = build_buy_condition_checklist(
        settings,
        signal=signal,
        meta=meta,
        price=price,
        change_rate=change_rate,
        is_add_buy=is_add,
        fill_amount=fill_amount or None,
    )
    return checks, checklist_summary(checks)


def _settings_dict(s: Optional[AutoTradeSettings]) -> Dict[str, Any]:
    if not s:
        return {}
    keys = [
        "is_enabled", "max_invest_amount", "stop_loss_rate", "take_profit_rate",
        "buy_below_price", "min_change_rate_buy", "trailing_stop_pct",
        "atr_mult_stop", "atr_mult_trail", "atr_period",
        "profit_lock_trigger", "profit_lock_floor",
        "use_entry_gate", "require_above_open", "require_above_vwap",
        "day_position_min", "day_position_max", "volume_ratio_min",
        "sizing_method", "initial_min_amount", "initial_max_amount",
        "signal_min_threshold", "signal_max_threshold",
        "add_buy_amount", "add_buy_trigger", "max_concurrent_positions",
        "cash_reserve_pct", "max_daily_buys", "daily_loss_limit", "daily_profit_target",
        "liquidate_before_close", "liquidate_time", "order_method",
        "trade_start_time", "trade_end_time", "watchlist_codes", "scan_interval_sec",
        "limit_break_soft_pct", "limit_break_hard_pct",
        "sharp_drop_soft_pct", "sharp_drop_hard_pct", "soft_confirm_polls",
        "sangtta_buy_amount", "sangtta_max_slots",
        "sangtta_trade_start_time", "sangtta_trade_end_time",
        "use_breakout", "breakout_condition_names",
        "breakout_buy_amount", "breakout_max_slots",
        "breakout_trade_start_time", "breakout_trade_end_time",
        "breakout_level_mode", "breakout_n_day", "breakout_vol_mult",
        "breakout_max_change_pct", "breakout_stop_loss_pct",
        "breakout_trailing_start_pct", "breakout_trailing_pct",
        "struct_break_soft_pct", "struct_break_hard_pct",
    ]
    out = {}
    for k in keys:
        v = getattr(s, k, None)
        if v is not None:
            out[k] = v
    return out


def _build_buy_condition_text(
    settings: Dict[str, Any],
    signal: Optional[PendingBuySignal],
    condition_label: str = "",
) -> str:
    parts: List[str] = []
    if signal:
        st = signal.signal_type or ""
        parts.append(SIGNAL_TYPE_KO.get(st, st or "알 수 없음"))
        label = condition_label or _condition_name_from_signal(signal)
        if label:
            parts.append(f"조건: {label}")
        if signal.reference_candle_high:
            parts.append(f"기준봉 고가 {signal.reference_candle_high:,}원")
        if signal.target_price:
            parts.append(f"목표가 {signal.target_price:,}원")
    if settings:
        buy_bits: List[str] = []
        if settings.get("buy_below_price"):
            buy_bits.append(f"현재가 ≤ {int(settings['buy_below_price']):,}원")
        if settings.get("min_change_rate_buy") is not None:
            buy_bits.append(f"등락률 ≥ {settings['min_change_rate_buy']}%")
        if settings.get("use_entry_gate"):
            gates = []
            if settings.get("require_above_open"):
                gates.append("시가 이상")
            if settings.get("require_above_vwap"):
                gates.append("VWAP 이상")
            if settings.get("day_position_min") is not None:
                gates.append(f"당일위치 ≥ {settings['day_position_min']}")
            if settings.get("day_position_max") is not None:
                gates.append(f"당일위치 ≤ {settings['day_position_max']}")
            if settings.get("volume_ratio_min") is not None:
                gates.append(f"거래량비 ≥ {settings['volume_ratio_min']}%")
            if gates:
                buy_bits.append("진입게이트: " + ", ".join(gates))
        sizing = (settings.get("sizing_method") or "FIXED").upper()
        if sizing == "PYRAMIDING":
            buy_bits.append(
                f"역피라미딩 (등락 {settings.get('signal_min_threshold', 2)}%→"
                f"{int(settings.get('initial_max_amount') or 0):,}원 · "
                f"{settings.get('signal_max_threshold', 10)}%→"
                f"{int(settings.get('initial_min_amount') or 0):,}원, "
                f"추가 {int(settings.get('add_buy_amount') or 0):,}원 @ +{settings.get('add_buy_trigger')}%)"
            )
        else:
            buy_bits.append(f"고정금액 (최대 {int(settings.get('max_invest_amount') or 0):,}원)")
        if settings.get("cash_reserve_pct"):
            buy_bits.append(f"예수금 {settings['cash_reserve_pct']}% 현금 보유")
        if buy_bits:
            parts.append("매수조건: " + " · ".join(buy_bits))
    return " | ".join(p for p in parts if p)


def _condition_name_from_signal(signal: PendingBuySignal) -> str:
    cid = signal.condition_id
    if cid in (0, 99999):
        return "자동매매 스캐너 (거래대금순·관심종목)"
    return f"조건식 ID {cid}"


def _build_exit_rule_text(settings: Dict[str, Any], pos: Position) -> str:
    parts: List[str] = []
    strategy = (getattr(pos, "strategy_key", None) or "").strip().lower()
    if strategy == "sangtta":
        lim_s = settings.get("limit_break_soft_pct", 2.0)
        lim_h = settings.get("limit_break_hard_pct", 3.0)
        drop_s = settings.get("sharp_drop_soft_pct", 3.0)
        drop_h = settings.get("sharp_drop_hard_pct", 5.0)
        soft_n = settings.get("soft_confirm_polls", 2)
        parts.append(
            f"상따 우선: 상한가 이탈 HARD {lim_h}% / SOFT {lim_s}%×{soft_n}회 · "
            f"급락 HARD {drop_h}% / SOFT {drop_s}%×{soft_n}회 (고점 대비)"
        )
    elif strategy == "breakout":
        parts.append(
            f"돌파 구조: {getattr(pos, 'breakout_level_kind', None) or 'level'} "
            f"{int(getattr(pos, 'breakout_level_price', None) or 0):,}원 · "
            f"HARD {settings.get('struct_break_hard_pct', 2.0)}% / "
            f"SOFT {settings.get('struct_break_soft_pct', 1.0)}%"
            f"×{settings.get('soft_confirm_polls', 2)}회"
        )
    trail_start_rate = (
        settings.get("breakout_trailing_start_pct")
        if strategy == "breakout" else pos.take_profit_rate
    )
    if trail_start_rate is not None:
        parts.append(f"트레일B 시작+바닥 +{trail_start_rate}%")
    stop_rate = (
        settings.get("breakout_stop_loss_pct")
        if strategy == "breakout" else pos.stop_loss_rate
    )
    if stop_rate is not None:
        parts.append(f"손절 {stop_rate}%")
    trail_rate = (
        settings.get("breakout_trailing_pct")
        if strategy == "breakout" else settings.get("trailing_stop_pct")
    )
    if trail_rate:
        parts.append(f"트레일 {trail_rate}% (고점 대비)")
    if strategy != "breakout" and (settings.get("atr_mult_stop") or settings.get("atr_mult_trail")):
        atr_bits = []
        if settings.get("atr_mult_stop"):
            atr_bits.append(f"손절×{settings['atr_mult_stop']}")
        if settings.get("atr_mult_trail"):
            atr_bits.append(f"트레일×{settings['atr_mult_trail']}")
        period = settings.get("atr_period") or 14
        parts.append(f"ATR({period}일) " + ", ".join(atr_bits))
    if settings.get("profit_lock_trigger") is not None:
        parts.append(
            f"수익잠금 +{settings['profit_lock_trigger']}%→바닥 {settings.get('profit_lock_floor')}%"
        )
    if settings.get("liquidate_before_close"):
        if strategy == "breakout":
            parts.append("장마감 청산 제외(오버나잇 허용)")
        else:
            parts.append(f"장마감 {settings.get('liquidate_time', '15:10')} 전량청산")
    return " · ".join(parts)


def _exit_calc_steps(
    settings: Dict[str, Any],
    pos: Position,
    *,
    atr: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """매수 시점 기준 이론적 청산 가격 계산식."""
    buy = int(pos.buy_price or 0)
    peak = int(pos.peak_price or buy)
    steps: List[Dict[str, Any]] = []
    if not buy:
        return steps
    is_breakout = (getattr(pos, "strategy_key", None) or "").strip().lower() == "breakout"

    if is_breakout and getattr(pos, "breakout_level_price", None):
        level = int(pos.breakout_level_price)
        soft = float(settings.get("struct_break_soft_pct") or 1.0)
        hard = float(settings.get("struct_break_hard_pct") or 2.0)
        steps.extend([
            {
                "rule": "돌파 구조 이탈 HARD",
                "formula": f"레벨 {level:,} × (1 − {hard}%)",
                "price": int(level * (1 - hard / 100)),
            },
            {
                "rule": "돌파 구조 이탈 SOFT",
                "formula": f"레벨 {level:,} × (1 − {soft}%) · 연속 확인",
                "price": int(level * (1 - soft / 100)),
            },
        ])

    tp = float(
        (settings.get("breakout_trailing_start_pct") or 0)
        if is_breakout
        else (pos.take_profit_rate or settings.get("take_profit_rate") or 0)
    )
    if tp:
        floor_price = int(buy * (1 + tp / 100))
        steps.append({
            "rule": "트레일링 시작 + 익절 바닥",
            "formula": f"고점 수익률 ≥ {tp}% → armed · 바닥 {buy:,}×{1 + tp/100:.4f}={floor_price:,}",
            "price": floor_price,
            "note": "바닥 이하로 트레일링선 하락 없음",
        })

    sl = float(
        (settings.get("breakout_stop_loss_pct") or 0)
        if is_breakout else (pos.stop_loss_rate or settings.get("stop_loss_rate") or 0)
    )
    if sl:
        price = int(buy * (1 - abs(sl) / 100))
        steps.append({
            "rule": "손절 % (STOP_LOSS)",
            "formula": f"매수가 × (1 − {abs(sl)}%) = {buy:,} × {1 - abs(sl)/100:.4f}",
            "price": price,
        })

    tr = (
        settings.get("breakout_trailing_pct")
        if is_breakout else settings.get("trailing_stop_pct")
    )
    if tr and peak:
        raw = int(peak * (1 - float(tr) / 100))
        floor_price = int(buy * (1 + tp / 100)) if tp else raw
        price = max(raw, floor_price) if tp else raw
        steps.append({
            "rule": "트레일링 % (TRAILING)",
            "formula": f"max(고점 {peak:,}×(1−{tr}%), 바닥) → {price:,}",
            "price": price,
            "note": f"armed 후 peak_price={peak:,}",
        })

    atr_stop = None if is_breakout else settings.get("atr_mult_stop")
    atr_trail = None if is_breakout else settings.get("atr_mult_trail")
    period = int(settings.get("atr_period") or 14)
    if atr_stop or atr_trail:
        if atr and float(atr) > 0:
            atr_val = float(atr)
            period = int(getattr(pos, "buy_atr_period", None) or settings.get("atr_period") or 14)
            snap_note = "매수 시점 스냅샷" if getattr(pos, "buy_atr", None) else f"ATR({period}일) 일봉"
            if atr_stop:
                stop_px = int(buy - atr_val * float(atr_stop))
                steps.append({
                    "rule": "ATR 손절 (STOP_LOSS)",
                    "formula": f"매수가 {buy:,} − ATR {atr_val:,.0f}×{atr_stop} = {stop_px:,}",
                    "price": stop_px,
                    "note": f"{snap_note} · %손절과 비교해 높은 선 적용",
                })
            if atr_trail and peak:
                raw = int(peak - atr_val * float(atr_trail))
                floor_price = int(getattr(pos, "trailing_floor_price", None) or 0) or (
                    int(buy * (1 + tp / 100)) if tp else 0
                )
                armed = bool(getattr(pos, "trailing_armed", False))
                if not armed and tp and buy:
                    armed = peak >= int(buy * (1 + tp / 100))
                trail_px = max(raw, floor_price) if armed and floor_price else raw
                trail_formula = f"고점 {peak:,} − ATR {atr_val:,.0f}×{atr_trail}"
                if armed and floor_price and trail_px > raw:
                    trail_formula += f" → max({raw:,}, 바닥 {floor_price:,}) = {trail_px:,}"
                else:
                    trail_formula += f" = {trail_px:,}"
                note = f"{snap_note} · armed 후 peak={peak:,}"
                if not armed and tp:
                    note += f" · 시작 +{tp}% 도달 전에는 트레일 미적용"
                steps.append({
                    "rule": "ATR 트레일 (TRAILING)",
                    "formula": trail_formula,
                    "price": trail_px if armed else None,
                    "note": note,
                })
        else:
            steps.append({
                "rule": "ATR 기반",
                "formula": f"손절: 매수가 − ATR×{atr_stop or '-'} / 트레일: 고점 − ATR×{atr_trail or '-'}",
                "price": None,
                "note": (
                    "매수 시점 ATR 미기록 (이전 포지션)"
                    if getattr(pos, "buy_atr", None) is None
                    else f"ATR({period}일) 조회 실패 — 토큰·API 상태 확인"
                ),
            })

    if pos.stop_loss_price:
        steps.append({
            "rule": "DB 저장 손절가",
            "formula": "진입 시 계산되어 stop_loss_price에 저장",
            "price": int(pos.stop_loss_price),
        })

    return steps


def _pnl_calc(buy_price: int, sell_price: int, qty: int, recorded: Optional[int]) -> Dict[str, Any]:
    calc = (sell_price - buy_price) * qty if buy_price and sell_price and qty else None
    rate = (calc / (buy_price * qty) * 100) if calc is not None and buy_price * qty else None
    return {
        "formula": f"(매도가 − 매수가) × 수량 = ({sell_price:,} − {buy_price:,}) × {qty}",
        "calculated_pnl": calc,
        "calculated_rate_pct": round(rate, 2) if rate is not None else None,
        "recorded_pnl": recorded,
        "match": recorded is None or calc is None or abs((recorded or 0) - calc) <= max(1, abs(calc) * 0.02),
    }


def _serialize_sell_order(so: SellOrder) -> Dict[str, Any]:
    return {
        "id": so.id,
        "time": _fmt_dt_kst(so.completed_at or so.ordered_at or so.created_at),
        "ordered_at": _fmt_dt_kst(so.ordered_at),
        "completed_at": _fmt_dt_kst(so.completed_at),
        "price": int(so.sell_price),
        "quantity": int(so.sell_quantity),
        "amount": int(so.sell_amount),
        "reason": SELL_REASON_KO.get(so.sell_reason, so.sell_reason),
        "reason_code": so.sell_reason,
        "reason_detail": so.sell_reason_detail,
        "status": so.status,
        "status_label": SELL_STATUS_KO.get(so.status, so.status),
        "order_id": so.sell_order_id,
        "profit_loss": int(so.profit_loss) if so.profit_loss is not None else None,
        "profit_loss_rate": float(so.profit_loss_rate) if so.profit_loss_rate is not None else None,
        "is_backfill": False,
    }


def _infer_sell_reason_from_position(pos: Position) -> Optional[str]:
    st = str(pos.status or "")
    if st in SELL_REASON_KO:
        return st
    if st == "MANUAL_SELL":
        return "MANUAL"
    if pos.sell_time and st != "HOLDING":
        return st
    return None


def _synthetic_sell_dict(pos: Position, buy_price: int, qty: int) -> Dict[str, Any]:
    """sell_orders 없이 포지션만 청산된 경우 추정 매도 이력."""
    reason_code = _infer_sell_reason_from_position(pos) or "UNKNOWN"
    sell_price = None
    if pos.current_price and int(pos.current_price) > 0:
        sell_price = int(pos.current_price)
    pnl = int(pos.current_profit_loss) if pos.current_profit_loss is not None else None
    if sell_price is None and pnl is not None and qty and buy_price:
        sell_price = buy_price + pnl // qty
    if pnl is None and sell_price and buy_price and qty:
        pnl = (sell_price - buy_price) * qty
    pl_rate = float(pos.current_profit_loss_rate) if pos.current_profit_loss_rate is not None else None
    if pl_rate is None and pnl is not None and buy_price and qty:
        pl_rate = pnl / (buy_price * qty) * 100
    amount = (sell_price * qty) if sell_price and qty else None
    return {
        "id": None,
        "time": _fmt_dt_kst(pos.sell_time),
        "ordered_at": _fmt_dt_kst(pos.sell_time),
        "completed_at": _fmt_dt_kst(pos.sell_time),
        "price": sell_price,
        "quantity": qty or int(pos.buy_quantity or 0) or None,
        "amount": amount,
        "reason": SELL_REASON_KO.get(reason_code, reason_code),
        "reason_code": reason_code,
        "reason_detail": "포지션 청산 기록 (sell_orders 없음 — reconcile 전 또는 수동 동기화)",
        "status": "COMPLETED",
        "status_label": "체결완료",
        "order_id": None,
        "profit_loss": pnl,
        "profit_loss_rate": round(pl_rate, 2) if pl_rate is not None else None,
        "is_backfill": True,
    }


def _effective_sell_orders(
    sells: List[SellOrder],
    pos: Position,
    buy_price: int,
    qty: int,
) -> List[Dict[str, Any]]:
    rows = [_serialize_sell_order(s) for s in sells]
    if rows:
        return rows
    if pos.sell_time or str(pos.status or "") != "HOLDING":
        return [_synthetic_sell_dict(pos, buy_price, qty)]
    return []


def _sell_fills_summary_from_rows(rows: List[Dict[str, Any]]) -> Optional[str]:
    if not rows:
        return None
    completed = [r for r in rows if r.get("status") == "COMPLETED"]
    if completed:
        if len(completed) == 1:
            s = completed[0]
            return (
                f"체결 1건 · {int(s.get('quantity') or 0):,}주 · "
                f"{int(s.get('amount') or 0):,}원 · {s.get('reason') or '-'}"
                f"{' (추정)' if s.get('is_backfill') else ''}"
            )
        total_qty = sum(int(s.get("quantity") or 0) for s in completed)
        total_amt = sum(int(s.get("amount") or 0) for s in completed)
        return f"체결 {len(completed)}건 · {total_qty:,}주 · {total_amt:,}원"
    pending = [r for r in rows if r.get("status") in ("PENDING", "ORDERED")]
    if pending:
        s = pending[-1]
        return (
            f"매도 {s.get('status_label') or s.get('status')} · "
            f"{s.get('reason') or '-'} · "
            f"{int(s.get('price') or 0):,}원 × {int(s.get('quantity') or 0):,}주"
        )
    return None


def _exit_snapshot(pos: Position) -> Dict[str, Any]:
    return {
        "position_status": pos.status,
        "stop_loss_rate": float(pos.stop_loss_rate) if pos.stop_loss_rate is not None else None,
        "take_profit_rate": float(pos.take_profit_rate) if pos.take_profit_rate is not None else None,
        "stop_loss_price": int(pos.stop_loss_price) if pos.stop_loss_price else None,
        "take_profit_price": int(pos.take_profit_price) if pos.take_profit_price else None,
        "peak_price": int(pos.peak_price) if pos.peak_price else None,
        "trailing_armed": bool(pos.trailing_armed),
        "trailing_floor_price": int(pos.trailing_floor_price) if pos.trailing_floor_price else None,
        "buy_atr": float(pos.buy_atr) if getattr(pos, "buy_atr", None) is not None else None,
        "buy_atr_period": int(pos.buy_atr_period) if getattr(pos, "buy_atr_period", None) else None,
        "current_price": int(pos.current_price) if pos.current_price else None,
        "current_profit_loss_rate": (
            float(pos.current_profit_loss_rate) if pos.current_profit_loss_rate is not None else None
        ),
    }


def _exit_notes(pos: Position, sells: List[SellOrder], sell_rows: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    notes: List[str] = []
    rows = sell_rows or []
    completed = [s for s in sells if s.status == "COMPLETED"]
    pending = [s for s in sells if s.status in ("PENDING", "ORDERED")]
    failed = [s for s in sells if s.status == "FAILED"]

    if rows:
        primary = next((r for r in reversed(rows) if r.get("status") == "COMPLETED"), rows[-1])
        if primary.get("status") == "COMPLETED":
            notes.append(
                f"매도 체결: {int(primary.get('quantity') or 0):,}주 · "
                f"{int(primary.get('amount') or 0):,}원 @ {int(primary.get('price') or 0):,}원"
            )
            notes.append(f"청산 사유: {primary.get('reason') or '-'}")
            if primary.get("reason_detail"):
                notes.append(f"상세: {primary['reason_detail']}")
            if primary.get("profit_loss") is not None:
                notes.append(f"실현 손익: {int(primary['profit_loss']):+,}원")
            if primary.get("is_backfill"):
                notes.append("sell_orders 없음 — 포지션 청산 기록으로 추정")
            sell_t = primary.get("completed_at") or primary.get("time")
            if pos.buy_time and sell_t:
                try:
                    sell_dt = datetime.strptime(str(sell_t).replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
                    hrs = _hold_hours(pos.buy_time, sell_dt)
                    if hrs is not None:
                        notes.append(f"보유 기간: {hrs}시간")
                except ValueError:
                    pass
            return notes

    if completed:
        s = completed[-1]
        notes.append(
            f"매도 체결: {int(s.sell_quantity):,}주 · {int(s.sell_amount or 0):,}원 "
            f"@ {int(s.sell_price):,}원"
        )
        notes.append(f"청산 사유: {SELL_REASON_KO.get(s.sell_reason, s.sell_reason)}")
        if s.sell_reason_detail:
            notes.append(f"상세: {s.sell_reason_detail}")
        if s.profit_loss is not None:
            notes.append(f"실현 손익: {int(s.profit_loss):+,}원")
        if pos.buy_time and s.completed_at:
            hrs = _hold_hours(pos.buy_time, s.completed_at)
            if hrs is not None:
                notes.append(f"보유 기간: {hrs}시간")
        if len(completed) > 1:
            notes.append(f"매도 체결 이력 {len(completed)}건 (분할·재주문 가능)")
    elif pending:
        s = pending[-1]
        notes.append(
            f"매도 주문 {SELL_STATUS_KO.get(s.status, s.status)}: "
            f"{SELL_REASON_KO.get(s.sell_reason, s.sell_reason)}"
        )
        notes.append(f"주문 {int(s.sell_price):,}원 × {int(s.sell_quantity):,}주")
        if s.sell_reason_detail:
            notes.append(f"상세: {s.sell_reason_detail}")
    elif failed:
        s = failed[-1]
        notes.append(f"매도 실패: {s.sell_reason_detail or '사유 없음'}")
    elif pos.status == "HOLDING":
        notes.append("아직 청산 전 — 보유 중")
        if pos.peak_price:
            notes.append(f"진입 후 고점: {int(pos.peak_price):,}원")
        if pos.trailing_armed:
            notes.append("트레일링 armed (시작% 도달)")
            if pos.trailing_floor_price:
                notes.append(f"익절 바닥: {int(pos.trailing_floor_price):,}원")
        if pos.stop_loss_price:
            notes.append(f"기록된 손절가: {int(pos.stop_loss_price):,}원")
        if pos.current_profit_loss_rate is not None:
            notes.append(f"마지막 기록 수익률: {float(pos.current_profit_loss_rate):+.2f}%")
    elif pos.sell_time:
        notes.append(f"포지션 상태: {pos.status} · 매도 시각 기록 있음")

    return notes


def _hold_hours(buy_t: Any, sell_t: Any) -> Optional[float]:
    if not buy_t or not sell_t:
        return None
    if isinstance(buy_t, str):
        buy_t = datetime.fromisoformat(buy_t.replace("Z", ""))
    if isinstance(sell_t, str):
        sell_t = datetime.fromisoformat(sell_t.replace("Z", ""))
    if not isinstance(buy_t, datetime) or not isinstance(sell_t, datetime):
        return None
    return round((sell_t - buy_t).total_seconds() / 3600, 2)


def _sell_context(
    sells: List[SellOrder],
    pos: Position,
) -> Tuple[Optional[SellOrder], Optional[datetime]]:
    """포지션의 대표 매도 주문·청산 시각."""
    completed = [s for s in sells if s.status == "COMPLETED"]
    sell = completed[-1] if completed else (sells[-1] if sells else None)
    sell_completed_at = (sell.completed_at if sell else None) or pos.sell_time
    return sell, sell_completed_at


def _positions_for_verification_report(
    positions: List[Position],
    sells_by_pos: Dict[int, List[SellOrder]],
    date_filter,
    limit: int,
) -> List[Position]:
    """리포트에 실제로 포함될 포지션만 선별 — ATR 등 API는 이 목록 기준으로만 호출."""
    out: List[Position] = []
    for pos in positions:
        sells = sorted(
            sells_by_pos.get(pos.id, []),
            key=lambda s: s.completed_at or s.ordered_at or s.created_at or datetime.min,
        )
        _, sell_completed_at = _sell_context(sells, pos)
        if date_filter.mode != "all" and not _trade_matches_filter(
            date_filter, pos.buy_time, sell_completed_at,
        ):
            continue
        out.append(pos)
        if date_filter.mode == "all" and len(out) >= limit:
            break
    return out


def _entry_verdict(
    signal: Optional[PendingBuySignal],
    pos: Position,
    settings: Dict[str, Any],
    buy_fills: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, List[str]]:
    notes: List[str] = []
    order_qty, filled_qty = order_and_filled_totals(pos, buy_fills)
    fill_amt = int(getattr(pos, "actual_buy_amount", None) or pos.buy_amount or 0)
    if fill_amt <= 0 and buy_fills:
        fill_amt = sum(int(f.get("amount") or 0) for f in buy_fills)
    has_fill = filled_qty > 0 and fill_amt > 0
    verdict = "OK"

    if signal and signal.status == "FAILED":
        notes.append(f"신호 실패: {signal.failure_reason or '사유 없음'}")
        verdict = "FAIL"
    elif order_qty > 0 and filled_qty > 0 and order_qty != filled_qty:
        notes.append(f"주문 {order_qty:,}주 · 체결 {filled_qty:,}주 — 부분체결 또는 미체결 잔량 확인")
        verdict = "CHECK"
    elif has_fill:
        notes.append(f"체결 확인: {filled_qty:,}주 · {fill_amt:,}원")
        if order_qty > 0 and order_qty == filled_qty:
            notes.append("주문 수량 전량 체결")
        if signal and signal.status == "ORDERED":
            notes.append("신호 → 주문 접수(ORDERED) 후 체결")
    elif not signal:
        notes.append("매수 신호 레코드 없음 (수동/backfill 가능)")
        verdict = "CHECK"
    elif signal.status == "ORDERED":
        notes.append("신호 → 주문 접수(ORDERED) — 체결 이력 없음")
        verdict = "CHECK"
    else:
        notes.append(f"신호 상태: {signal.status}")
        verdict = "CHECK"

    buy_time_for_delay = pos.buy_time
    if buy_fills and buy_fills[0].get("time"):
        try:
            buy_time_for_delay = datetime.strptime(
                str(buy_fills[0]["time"]).replace("T", " ")[:19],
                "%Y-%m-%d %H:%M:%S",
            )
        except ValueError:
            pass

    if signal and buy_time_for_delay and signal.detected_at:
        delay = _signal_buy_delay_sec(signal.detected_at, buy_time_for_delay)
        if delay is not None:
            notes.append(f"신호→매수 지연: {_format_delay_sec(delay)}")
            if delay < 0 and not has_fill:
                verdict = "CHECK"
                notes.append("신호·매수 시각 기록 방식 불일치 — 확인 필요")
            elif delay > 600 and not has_fill:
                verdict = "CHECK"
                notes.append("신호 후 10분 초과 — 체결 지연 확인")

    planned_max = int(settings.get("initial_max_amount") or settings.get("max_invest_amount") or 0)
    actual = fill_amt if has_fill else int(pos.actual_buy_amount or pos.buy_amount or 0)
    if planned_max and actual < planned_max * 0.1:
        notes.append(f"소액 체결: {actual:,}원 (설정 상한 {planned_max:,}원 대비) — 동시 보유·예수금 부족 가능")
        if verdict == "OK":
            verdict = "CHECK"

    if pos.buy_order_id == "backfill" and not has_fill:
        notes.append("backfill로 생성된 포지션 — 자동매매 신호 경로 아님")
        verdict = "CHECK"

    return verdict, notes


def calculation_guide() -> Dict[str, List[Dict[str, str]]]:
    return {
        "buy_pipeline": [
            {"step": "1. 후보", "desc": "관심종목 + 거래대금순 스크리너(개별주식) 2분 주기 스캔"},
            {"step": "2. 필터", "desc": "장중 시간 · 진입게이트(시가/VWAP 등) · 가격/등락률 조건"},
            {"step": "3. 신호", "desc": "PendingBuySignal 생성 (condition_id=99999 = 스캐너)"},
            {"step": "4. 사이징", "desc": "FIXED 또는 역피라미딩(등락↑ 금액↓) · 예수금 cash_reserve_pct 제외"},
            {"step": "5. 주문", "desc": "시장가(MARKET) 기본 · 동시보유·일일한도 체크 후 매수"},
        ],
        "exit_priority": [
            {"step": "상따 이탈/급락", "desc": "strategy=sangtta: 상한가 이탈 HARD/SOFT · 고점 급락 HARD/SOFT (SOFT는 soft_confirm_polls 연속) — STOP_LOSS로 기록"},
            {"step": "트레일링(B)", "desc": "고점≥시작% → armed + 바닥잠금 · 매도선=max(고점−트레일, 바닥)"},
            {"step": "손절", "desc": "매수가 대비 stop_loss_rate% 또는 매수가−ATR×배수 (유효선은 후보 중 최고가) — 상따는 백업"},
            {"step": "수익잠금", "desc": "profit_lock_trigger% 도달 후 최소 profit_lock_floor% 선 확보"},
            {"step": "장마감", "desc": "liquidate_time 이후 전량 시장가 MARKET_CLOSE"},
        ],
        "pnl": [
            {"step": "실현손익", "desc": "(매도체결가 − 매수단가) × 수량 (키움 수수료 제외 근사)"},
            {"step": "수익률", "desc": "실현손익 ÷ (매수단가 × 수량) × 100"},
        ],
    }


async def build_verification_report(
    session: Session,
    limit: int = 100,
    trade_date: Optional[str] = None,
) -> Dict[str, Any]:
    settings_row = session.query(AutoTradeSettings).first()
    settings = _settings_dict(settings_row)
    date_filter = _parse_trade_filter(trade_date)

    fetch_limit = 500 if date_filter.mode != "all" else min(max(limit, 1), 500)
    positions = (
        session.query(Position)
        .order_by(Position.buy_time.desc())
        .limit(fetch_limit)
        .all()
    )

    sells_by_pos: Dict[int, List[SellOrder]] = {}
    for so in session.query(SellOrder).order_by(SellOrder.completed_at.desc()).all():
        sells_by_pos.setdefault(so.position_id, []).append(so)

    report_positions = _positions_for_verification_report(
        positions, sells_by_pos, date_filter, limit,
    )

    trades: List[Dict[str, Any]] = []
    total_realized = 0
    win = loss = 0
    holding = 0

    for pos in report_positions:
        signal = None
        if pos.signal_id:
            signal = session.query(PendingBuySignal).filter(PendingBuySignal.id == pos.signal_id).first()

        sells = sorted(
            sells_by_pos.get(pos.id, []),
            key=lambda s: s.completed_at or s.ordered_at or s.created_at or datetime.min,
        )
        sell, sell_completed_at = _sell_context(sells, pos)

        buy_fills = _load_buy_fills(session, pos, settings, signal)

        verdict, entry_notes = _entry_verdict(signal, pos, settings, buy_fills)

        eff_buy = effective_buy_stats(buy_fills, pos)
        buy_price = int(eff_buy["price"] or 0)
        qty = int(eff_buy["quantity"] or 0)

        sell_rows = _effective_sell_orders(sells, pos, buy_price, qty)
        primary_sell_row = next(
            (r for r in reversed(sell_rows) if r.get("status") == "COMPLETED"),
            sell_rows[-1] if sell_rows else None,
        )

        sell_price = int(sell.sell_price) if sell else (
            int(primary_sell_row["price"]) if primary_sell_row and primary_sell_row.get("price") else None
        )
        recorded_pnl = int(sell.profit_loss) if sell and sell.profit_loss is not None else (
            int(primary_sell_row["profit_loss"]) if primary_sell_row and primary_sell_row.get("profit_loss") is not None else None
        )

        sell_on_target = (
            date_filter.mode == "all"
            or _date_in_filter(_kst_date_of(sell_completed_at), date_filter)
        )
        if recorded_pnl is not None and (
            (sell and sell.status == "COMPLETED")
            or (primary_sell_row and primary_sell_row.get("status") == "COMPLETED")
        ):
            if date_filter.mode == "all" or sell_on_target:
                total_realized += recorded_pnl
                if recorded_pnl > 0:
                    win += 1
                elif recorded_pnl < 0:
                    loss += 1
        elif pos.status == "HOLDING" and (
            date_filter.mode == "all"
            or _date_in_filter(_kst_date_of(pos.buy_time), date_filter)
        ):
            holding += 1

        pnl_block = None
        if sell_price and buy_price and qty:
            pnl_block = _pnl_calc(buy_price, sell_price, qty, recorded_pnl)

        fills_summary = buy_fills_summary(buy_fills, settings)
        buy_condition_checks, buy_condition_summary = _load_buy_condition_checks(
            session, pos, settings, signal, buy_fills,
        )
        exit_steps = _exit_calc_steps(
            settings, pos,
            atr=float(pos.buy_atr) if getattr(pos, "buy_atr", None) else None,
        )
        trigger_reason = (
            (sell.sell_reason if sell else None)
            or (primary_sell_row.get("reason_code") if primary_sell_row else None)
            or _infer_sell_reason_from_position(pos)
        )
        reason_detail = (
            (sell.sell_reason_detail if sell else None)
            or (primary_sell_row.get("reason_detail") if primary_sell_row else None)
        )
        signal_meta = parse_signal_meta(signal) if signal else {}
        strategy_key = (
            getattr(pos, "strategy_key", None)
            or signal_meta.get("strategy")
            or (signal_meta.get("source") if signal_meta.get("source") == "sangtta" else None)
        )
        sell_condition_checks = build_sell_condition_checklist(
            settings,
            pos,
            buy_price=buy_price,
            qty=qty,
            sell_price=sell_price,
            trigger_reason=trigger_reason,
            exit_steps=exit_steps,
            has_sell_order=bool(sells),
            reason_detail=reason_detail,
            strategy_key=strategy_key,
        )
        sell_condition_summary = sell_checklist_summary(sell_condition_checks)

        sangtta_exit = classify_sangtta_exit_detail(reason_detail) if strategy_key == "sangtta" else None
        sell_reason_label = (
            SELL_REASON_KO.get(sell.sell_reason, sell.sell_reason) if sell
            else (primary_sell_row.get("reason") if primary_sell_row else None)
        )
        if sangtta_exit and sell_reason_label:
            sell_reason_label = f"{sell_reason_label} · {SANGTTA_EXIT_KO[sangtta_exit]}"
        elif sangtta_exit:
            sell_reason_label = SANGTTA_EXIT_KO[sangtta_exit]

        trades.append({
            "position_id": pos.id,
            "stock_code": pos.stock_code,
            "stock_name": pos.stock_name,
            "status": pos.status,
            "strategy_key": strategy_key,
            "strategy_label": (
                "상따" if strategy_key == "sangtta"
                else ("과매도 돌파" if strategy_key == "breakout"
                else ("레거시" if strategy_key in ("legacy", "scanner", "screener", "condition", "both") else (strategy_key or None))
                )
            ),
            "breakout_level_kind": (
                getattr(pos, "breakout_level_kind", None) or signal_meta.get("level_kind")
            ),
            "breakout_level_price": (
                getattr(pos, "breakout_level_price", None)
                or signal_meta.get("breakout_level_price")
                or signal_meta.get("level_price")
            ),
            "sangtta_exit_kind": sangtta_exit,
            "sangtta_exit_label": SANGTTA_EXIT_KO.get(sangtta_exit) if sangtta_exit else None,
            "condition_name": _condition_name(session, pos.condition_id),
            "signal": {
                "id": signal.id if signal else None,
                "detected_at": _fmt_dt_signal(signal.detected_at if signal else None),
                "status": signal.status if signal else None,
                "signal_type": SIGNAL_TYPE_KO.get(signal.signal_type, signal.signal_type) if signal else None,
                "failure_reason": signal.failure_reason if signal else None,
                "strategy": signal_meta.get("strategy") or strategy_key,
                "source": signal_meta.get("source"),
                "gate_pack": signal_meta.get("gate_pack"),
                "level_kind": signal_meta.get("level_kind"),
                "level_price": signal_meta.get("level_price"),
            } if signal else None,
            "buy": {
                "time": _fmt_dt_kst(pos.buy_time),
                "time_kst": _fmt_dt_kst(pos.buy_time),
                "price": buy_price,
                "quantity": qty,
                "order_quantity": int(eff_buy.get("order_quantity") or 0) or None,
                "amount": int(eff_buy["amount"] or 0),
                "actual_amount": int(eff_buy["amount"] or 0),
                "from_fills": bool(eff_buy.get("from_fills")),
                "order_id": pos.buy_order_id,
            },
            "sell": {
                "time": _fmt_dt_kst(sell.completed_at if sell else pos.sell_time),
                "time_kst": _fmt_dt_kst(sell.completed_at if sell else pos.sell_time),
                "price": sell_price,
                "quantity": int(sell.sell_quantity) if sell else (
                    int(primary_sell_row["quantity"]) if primary_sell_row and primary_sell_row.get("quantity") else None
                ),
                "amount": int(sell.sell_amount) if sell and sell.sell_amount else (
                    int(primary_sell_row["amount"]) if primary_sell_row and primary_sell_row.get("amount") else None
                ),
                "reason": sell_reason_label,
                "reason_code": (
                    sell.sell_reason if sell
                    else (primary_sell_row.get("reason_code") if primary_sell_row else _infer_sell_reason_from_position(pos))
                ),
                "reason_detail": (
                    sell.sell_reason_detail if sell
                    else (primary_sell_row.get("reason_detail") if primary_sell_row else None)
                ),
                "status": sell.status if sell else (
                    primary_sell_row.get("status") if primary_sell_row else None
                ),
                "order_id": sell.sell_order_id if sell else None,
                "profit_loss": recorded_pnl,
                "profit_loss_rate": (
                    float(sell.profit_loss_rate) if sell and sell.profit_loss_rate is not None
                    else (primary_sell_row.get("profit_loss_rate") if primary_sell_row else None)
                ),
            } if sell or pos.sell_time or sell_rows else None,
            "hold_hours": _hold_hours(pos.buy_time, sell_completed_at),
            "peak_price": int(pos.peak_price) if pos.peak_price else None,
            "entry_summary": _build_buy_condition_text(
                settings, signal, _condition_name(session, pos.condition_id)
            ),
            "exit_rules": _build_exit_rule_text(settings, pos),
            "exit_calc_steps": exit_steps,
            "pnl_calc": pnl_block,
            "entry_verdict": verdict,
            "entry_notes": entry_notes,
            "buy_date_kst": str(_kst_date_of(pos.buy_time) or ""),
            "sell_date_kst": str(_kst_date_of(sell_completed_at) or ""),
            "buy_fills": buy_fills,
            "buy_fills_summary": fills_summary,
            "buy_condition_checks": buy_condition_checks,
            "buy_condition_summary": buy_condition_summary,
            "sell_orders": sell_rows,
            "sell_fills_summary": _sell_fills_summary_from_rows(sell_rows),
            "exit_notes": _exit_notes(pos, sells, sell_rows),
            "exit_snapshot": _exit_snapshot(pos),
            "sell_condition_checks": sell_condition_checks,
            "sell_condition_summary": sell_condition_summary,
        })

    failed_signals = []
    failed_q = (
        session.query(PendingBuySignal)
        .filter(PendingBuySignal.status == "FAILED")
        .order_by(PendingBuySignal.detected_at.desc())
    )
    for sig in failed_q.limit(100).all():
        if date_filter.mode != "all":
            sig_d = _kst_date_of(sig.detected_at) or (
                sig.detected_date if isinstance(sig.detected_date, date) else None
            )
            if not _date_in_filter(sig_d, date_filter):
                continue
        failed_signals.append({
            "id": sig.id,
            "stock_code": sig.stock_code,
            "stock_name": sig.stock_name,
            "detected_at": _fmt_dt_signal(sig.detected_at),
            "reason": sig.failure_reason,
            "entry_summary": _build_buy_condition_text(
                settings, sig, _condition_name(session, sig.condition_id)
            ),
        })

    closed = sum(
        1 for t in trades
        if t.get("sell") and (
            t["sell"].get("status") == "COMPLETED"
            or t.get("sell_orders")
        )
        and (date_filter.mode == "all" or _date_in_filter(
            _parse_trade_date(t.get("sell_date_kst")), date_filter,
        ))
    )

    return {
        "success": True,
        "generated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "filter": {
            "trade_date": date_filter.raw or (
                str(date_filter.day) if date_filter.mode == "day" else None
            ),
            "trade_date_label": date_filter.label,
            "trade_date_from": (
                str(date_filter.week_start) if date_filter.mode == "week" else None
            ),
            "trade_date_to": (
                str(date_filter.week_end) if date_filter.mode == "week" else None
            ),
        },
        "settings": settings,
        "calculation_guide": calculation_guide(),
        "summary": {
            "positions": len(trades),
            "closed_trades": closed,
            "holding": holding,
            "wins": win,
            "losses": loss,
            "total_realized_pnl": total_realized,
            "win_rate_pct": round(win / closed * 100, 1) if closed else None,
        },
        "trades": trades,
        "failed_signals": failed_signals,
    }
