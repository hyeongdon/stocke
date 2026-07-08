"""매수 조건 항목별 체크리스트 (검증 페이지 · 체결 이력용)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.auto_trade_engine import compute_buy_amount


def _eff_min_rate(settings: Dict[str, Any]) -> Optional[float]:
    if settings.get("min_change_rate_buy") is not None:
        return float(settings["min_change_rate_buy"])
    if settings.get("signal_min_threshold") is not None:
        return float(settings["signal_min_threshold"])
    return None


def _chk(
    group: str,
    label: str,
    *,
    passed: Optional[bool],
    actual: str = "",
    required: str = "",
    note: str = "",
    key: str = "",
    enabled: bool = True,
) -> Dict[str, Any]:
    return {
        "group": group,
        "key": key or label,
        "label": label,
        "enabled": enabled,
        "passed": passed,
        "actual": actual,
        "required": required,
        "note": note,
    }


def entry_gate_check_items(
    settings: Dict[str, Any],
    current_price: int,
    ctx: Optional[Dict[str, Any]] = None,
    *,
    infer_pass_if_ordered: bool = False,
) -> List[Dict[str, Any]]:
    """진입 게이트 하위 조건 목록. ctx 없으면 infer_pass_if_ordered 시 통과 추정."""
    ctx = ctx or {}
    items: List[Dict[str, Any]] = []

    if not settings.get("use_entry_gate"):
        return [
            _chk("진입 게이트", "진입 게이트", passed=True, actual="비활성", required="—", enabled=False),
        ]

    def _pass_or_infer(computed: Optional[bool]) -> Optional[bool]:
        if computed is not None:
            return computed
        return True if infer_pass_if_ordered else None

    def _note(computed: Optional[bool]) -> str:
        if computed is None and infer_pass_if_ordered:
            return "신호·체결 완료 → 당시 통과 추정"
        if computed is None:
            return "당시 시세 데이터 없음"
        return ""

    day_open = ctx.get("day_open")
    day_high = ctx.get("day_high")
    day_low = ctx.get("day_low")
    vwap = ctx.get("vwap")
    day_volume = ctx.get("day_volume")
    prev_volume = ctx.get("prev_volume")

    if settings.get("require_above_open"):
        computed = None
        if day_open and current_price:
            computed = current_price >= int(day_open)
        items.append(_chk(
            "진입 게이트", "현재가 ≥ 당일 시가",
            passed=_pass_or_infer(computed),
            actual=f"{current_price:,}원" if current_price else "—",
            required=f"≥ {int(day_open):,}원" if day_open else "시가 이상",
            note=_note(computed),
            key="require_above_open",
        ))

    if settings.get("require_above_vwap"):
        computed = None
        if vwap is not None and current_price:
            computed = current_price >= float(vwap)
        items.append(_chk(
            "진입 게이트", "현재가 ≥ VWAP",
            passed=_pass_or_infer(computed),
            actual=f"{current_price:,}원" if current_price else "—",
            required=f"≥ {float(vwap):,.0f}원" if vwap is not None else "VWAP 이상",
            note=_note(computed),
            key="require_above_vwap",
        ))

    pos_min = settings.get("day_position_min")
    if pos_min is not None:
        computed = None
        actual = "—"
        if day_high and day_low and int(day_high) > int(day_low) and current_price:
            position = (current_price - int(day_low)) / (int(day_high) - int(day_low))
            computed = position >= float(pos_min)
            actual = f"{position:.2f}"
        items.append(_chk(
            "진입 게이트", "당일 가격 위치",
            passed=_pass_or_infer(computed),
            actual=actual,
            required=f"≥ {pos_min}",
            note=_note(computed),
            key="day_position_min",
        ))

    pos_max = settings.get("day_position_max")
    if pos_max is not None:
        computed = None
        actual = "—"
        if day_high and day_low and int(day_high) > int(day_low) and current_price:
            position = (current_price - int(day_low)) / (int(day_high) - int(day_low))
            computed = position <= float(pos_max)
            actual = f"{position:.2f}"
        items.append(_chk(
            "진입 게이트", "당일 가격 위치 상한",
            passed=_pass_or_infer(computed),
            actual=actual,
            required=f"≤ {pos_max}",
            note=_note(computed),
            key="day_position_max",
        ))

    vol_ratio_min = settings.get("volume_ratio_min")
    if vol_ratio_min is not None:
        computed = None
        actual = "—"
        if prev_volume and int(prev_volume) > 0 and day_volume is not None:
            ratio = int(day_volume) / int(prev_volume) * 100
            computed = ratio >= float(vol_ratio_min)
            actual = f"{ratio:.0f}%"
        items.append(_chk(
            "진입 게이트", "전일 대비 거래량",
            passed=_pass_or_infer(computed),
            actual=actual,
            required=f"≥ {vol_ratio_min}%",
            note=_note(computed),
            key="volume_ratio_min",
        ))

    if not items:
        items.append(_chk(
            "진입 게이트", "진입 게이트",
            passed=True,
            actual="활성(세부 조건 없음)",
            required="통과",
            key="gate_empty",
        ))
    return items


def build_buy_condition_checklist(
    settings: Dict[str, Any],
    *,
    signal: Any = None,
    meta: Optional[Dict[str, Any]] = None,
    price: Optional[int] = None,
    change_rate: Optional[float] = None,
    is_add_buy: bool = False,
    fill_amount: Optional[int] = None,
    gate_ctx: Optional[Dict[str, Any]] = None,
    stored_checks: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """매수 조건 체크리스트. stored_checks 있으면 우선 사용."""
    if stored_checks:
        return stored_checks

    meta = meta or {}
    price = price or int(meta.get("current_price") or 0) or None
    if change_rate is None and meta.get("change_rate") is not None:
        try:
            change_rate = float(meta["change_rate"])
        except (TypeError, ValueError):
            change_rate = None

    ordered = bool(signal and getattr(signal, "status", None) in ("ORDERED", "PROCESSING"))
    infer = ordered or bool(fill_amount)

    items: List[Dict[str, Any]] = []

    # --- 경로 ---
    source = meta.get("source") or ""
    cid = getattr(signal, "condition_id", None) if signal else None
    if is_add_buy or meta.get("is_add_buy"):
        items.append(_chk(
            "매수 경로", "피라미딩 추가매수",
            passed=True,
            actual="보유 종목 수익률 트리거",
            required=f"+{settings.get('add_buy_trigger')}% 이상",
            key="pyramiding_add",
        ))
    elif cid in (0, 99999) or source in ("screener", "watchlist"):
        src_label = "거래대금순 스크리너" if source == "screener" else (
            "관심종목" if source == "watchlist" else "자동매매 스캐너"
        )
        items.append(_chk(
            "매수 경로", "후보 종목",
            passed=infer,
            actual=src_label,
            required="관심종목 또는 스크리너(개별주·ETF 제외)",
            key="candidate_source",
        ))
    elif signal:
        items.append(_chk(
            "매수 경로", "매수 신호",
            passed=infer,
            actual=getattr(signal, "signal_type", "") or "—",
            required="신호 발생",
            key="signal_path",
        ))

    if is_add_buy:
        trig = settings.get("add_buy_trigger")
        cr = change_rate
        passed_add = None
        if cr is not None and trig is not None:
            passed_add = float(cr) >= float(trig)
        items.append(_chk(
            "추가매수", "수익률 트리거",
            passed=passed_add if passed_add is not None else infer,
            actual=f"{cr:+.2f}%" if cr is not None else "—",
            required=f"≥ +{trig}%" if trig is not None else "—",
            key="add_buy_trigger",
        ))
        amt = settings.get("add_buy_amount")
        items.append(_chk(
            "추가매수", "추가매수 금액",
            passed=True if fill_amount else infer,
            actual=f"{int(fill_amount):,}원" if fill_amount else "—",
            required=f"{int(amt):,}원/회" if amt else "—",
            key="add_buy_amount",
        ))
        return items

    # --- 가격·등락률 ---
    has_price_cond = bool(settings.get("buy_below_price")) or _eff_min_rate(settings) is not None
    if settings.get("buy_below_price"):
        cap = int(settings["buy_below_price"])
        passed_p = price <= cap if price else None
        items.append(_chk(
            "가격·등락률", "매수가 상한",
            passed=passed_p if passed_p is not None else infer,
            actual=f"{price:,}원" if price else "—",
            required=f"≤ {cap:,}원",
            key="buy_below_price",
        ))

    min_rate = _eff_min_rate(settings)
    if min_rate is not None:
        passed_r = float(change_rate or 0) >= min_rate if change_rate is not None else None
        items.append(_chk(
            "가격·등락률", "최소 등락률",
            passed=passed_r if passed_r is not None else infer,
            actual=f"{change_rate:+.2f}%" if change_rate is not None else "—",
            required=f"≥ +{min_rate}%",
            key="min_change_rate",
        ))

    if not has_price_cond:
        items.append(_chk(
            "가격·등락률", "가격/등락 조건",
            passed=True,
            actual="미설정",
            required="—",
            enabled=False,
        ))

    items.extend(entry_gate_check_items(settings, price or 0, gate_ctx, infer_pass_if_ordered=infer))

    # --- 사이징 ---
    sizing = (settings.get("sizing_method") or "FIXED").upper()
    if sizing == "PYRAMIDING":
        smin = settings.get("signal_min_threshold", 2)
        smax = settings.get("signal_max_threshold", 10)
        imin = int(settings.get("initial_min_amount") or 0)
        imax = int(settings.get("initial_max_amount") or 0)
        planned = None
        if change_rate is not None:
            try:
                class _S:
                    pass
                s = _S()
                for k, v in settings.items():
                    setattr(s, k, v)
                planned = compute_buy_amount(s, change_rate, False)
            except Exception:
                planned = None
        passed_sz = None
        if planned and fill_amount:
            # 시장가 체결·호가 단위로 계획금액 대비 소폭 초과 가능
            passed_sz = abs(int(fill_amount) - planned) <= max(100000, planned * 0.2)
        if planned and fill_amount and passed_sz is False:
            imax = int(settings.get("initial_max_amount") or 0)
            if imax and int(fill_amount) <= imax * 1.05 and int(fill_amount) >= imax * 0.9:
                passed_sz = True
        items.append(_chk(
            "사이징", "역피라미딩 초기 금액",
            passed=passed_sz if passed_sz is not None else infer,
            actual=f"체결 {int(fill_amount):,}원" if fill_amount else (f"계획 {planned:,}원" if planned else "—"),
            required=f"등락 {smin}%→{imax:,}원 · {smax}%→{imin:,}원",
            key="pyramiding_size",
        ))
    else:
        cap = int(settings.get("max_invest_amount") or settings.get("initial_max_amount") or 0)
        items.append(_chk(
            "사이징", "고정 매수 금액",
            passed=True if fill_amount else infer,
            actual=f"{int(fill_amount):,}원" if fill_amount else "—",
            required=f"최대 {cap:,}원" if cap else "—",
            key="fixed_size",
        ))

    reserve = settings.get("cash_reserve_pct")
    if reserve is not None:
        items.append(_chk(
            "계좌·리스크", "예수금 현금 보유",
            passed=infer,
            actual=f"체결 완료",
            required=f"예수금 {reserve}% 현금 유지",
            key="cash_reserve",
        ))

    if signal and getattr(signal, "status", None) == "ORDERED":
        items.append(_chk(
            "주문 실행", "매수 주문·체결",
            passed=True,
            actual="ORDERED → 체결",
            required="주문 성공",
            key="order_executed",
        ))
    elif signal and getattr(signal, "status", None) == "FAILED":
        items.append(_chk(
            "주문 실행", "매수 주문·체결",
            passed=False,
            actual="FAILED",
            required="주문 성공",
            note=getattr(signal, "failure_reason", None) or "",
            key="order_executed",
        ))

    return items


def checklist_summary(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    enabled = [c for c in checks if c.get("enabled", True)]
    passed = sum(1 for c in enabled if c.get("passed") is True)
    failed = sum(1 for c in enabled if c.get("passed") is False)
    unknown = sum(1 for c in enabled if c.get("passed") is None)
    return {
        "total": len(enabled),
        "passed": passed,
        "failed": failed,
        "unknown": unknown,
        "all_passed": failed == 0 and unknown == 0 and passed > 0,
    }


async def build_buy_condition_checklist_at_buy(
    kiwoom_api,
    settings: Dict[str, Any],
    signal,
    meta: Dict[str, Any],
    price: int,
    change_rate: Optional[float],
    is_add_buy: bool,
    fill_amount: int,
) -> List[Dict[str, Any]]:
    """매수 체결 직전·직후 스냅샷 (게이트 컨텍스트 포함)."""
    from utils.auto_trade_engine import fetch_entry_gate_context

    gate_ctx: Dict[str, Any] = {}
    if settings.get("use_entry_gate") and price and signal:
        try:
            gate_ctx = await fetch_entry_gate_context(
                kiwoom_api, signal.stock_code, price,
            )
        except Exception:
            gate_ctx = {}

    return build_buy_condition_checklist(
        settings,
        signal=signal,
        meta=meta,
        price=price,
        change_rate=change_rate,
        is_add_buy=is_add_buy,
        fill_amount=fill_amount,
        gate_ctx=gate_ctx,
    )

