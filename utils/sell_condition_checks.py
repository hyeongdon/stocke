"""매도·청산 조건 체크리스트 (검증 페이지)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.models import Position, SellOrder

from utils.buy_condition_checks import _chk, checklist_summary

SELL_REASON_KO = {
    "STOP_LOSS": "손절",
    "TAKE_PROFIT": "익절",
    "TRAILING": "트레일링 스탑",
    "PROFIT_LOCK": "수익 잠금",
    "MARKET_CLOSE": "장마감 청산",
    "MANUAL": "수동 매도",
    "MANUAL_SELL": "수동 매도",
    "INDICATOR": "지표 매도",
}

SANGTTA_EXIT_KO = {
    "limit_hard": "상한가 이탈 HARD",
    "limit_soft": "상한가 이탈 SOFT",
    "drop_hard": "급락 HARD",
    "drop_soft": "급락 SOFT",
}
BREAKOUT_EXIT_KO = {
    "structure_hard": "구조 이탈 HARD",
    "structure_soft": "구조 이탈 SOFT",
}


def classify_sangtta_exit_detail(detail: Optional[str]) -> Optional[str]:
    """sell_reason_detail → limit_hard|limit_soft|drop_hard|drop_soft."""
    d = str(detail or "")
    if "상한가 이탈(HARD)" in d:
        return "limit_hard"
    if "상한가 이탈(SOFT" in d:
        return "limit_soft"
    if "급락(HARD)" in d:
        return "drop_hard"
    if "급락(SOFT" in d:
        return "drop_soft"
    return None


def classify_breakout_exit_detail(detail: Optional[str]) -> Optional[str]:
    d = str(detail or "")
    if "구조 이탈(HARD)" in d:
        return "structure_hard"
    if "구조 이탈(SOFT" in d:
        return "structure_soft"
    return None


def _peak_rate_pct(buy_price: int, peak: int) -> float:
    if not buy_price:
        return 0.0
    return (peak - buy_price) / buy_price * 100


def _infer_trigger_reason(pos: Position, sell: Optional[SellOrder]) -> Optional[str]:
    if sell and sell.sell_reason:
        return str(sell.sell_reason)
    st = str(pos.status or "")
    if st in SELL_REASON_KO:
        return st
    if st == "MANUAL_SELL":
        return "MANUAL"
    if pos.sell_time and st != "HOLDING":
        return st
    return None


def _position_closed(pos: Position) -> bool:
    return bool(pos.sell_time) or str(pos.status or "") != "HOLDING"


def _step_price(steps: List[Dict[str, Any]], rule_substr: str) -> Optional[int]:
    for s in steps:
        if rule_substr in str(s.get("rule") or ""):
            p = s.get("price")
            if p is not None:
                return int(p)
    return None


# 청산 사유별로 «이번에 쓴 규칙» — 나머지는 미통과(✗)가 아니라 해당 없음(?)
_RULE_OWNER: Dict[str, str] = {
    "market_close": "MARKET_CLOSE",
    "trail_arm": "TRAILING",
    "trail_floor": "TRAILING",
    "trailing": "TRAILING",
    "stop_loss": "STOP_LOSS",
    "profit_lock_trig": "PROFIT_LOCK",
    "profit_lock": "PROFIT_LOCK",
    "sangtta_limit_hard": "SANGTTA_LIMIT",
    "sangtta_limit_soft": "SANGTTA_LIMIT",
    "sangtta_drop_hard": "SANGTTA_DROP",
    "sangtta_drop_soft": "SANGTTA_DROP",
    "sangtta_exit_class": "SANGTTA",
    "sangtta_strategy": "SANGTTA",
    "breakout_strategy": "BREAKOUT_STRUCTURE",
    "breakout_structure_hard": "BREAKOUT_STRUCTURE",
    "breakout_structure_soft": "BREAKOUT_STRUCTURE",
}
_TRIG_GROUP = {
    "TAKE_PROFIT": "TRAILING",
}


def _exit_trigger_group(trig: str) -> str:
    t = (trig or "").upper()
    return _TRIG_GROUP.get(t, t)


def _sangtta_trigger_group(kind: Optional[str]) -> Optional[str]:
    if kind in ("limit_hard", "limit_soft"):
        return "SANGTTA_LIMIT"
    if kind in ("drop_hard", "drop_soft"):
        return "SANGTTA_DROP"
    return None


def sangtta_exit_check_items(
    settings: Dict[str, Any],
    pos: Position,
    *,
    buy_price: int,
    sell_price: Optional[int],
    trigger_reason: Optional[str],
    reason_detail: Optional[str] = None,
    closed: bool = False,
) -> List[Dict[str, Any]]:
    """상따 전용 청산(이탈/급락 HARD·SOFT) 체크."""
    items: List[Dict[str, Any]] = []
    peak = int(pos.peak_price or buy_price or 0)
    lim_soft = float(settings.get("limit_break_soft_pct") if settings.get("limit_break_soft_pct") is not None else 2.0)
    lim_hard = float(settings.get("limit_break_hard_pct") if settings.get("limit_break_hard_pct") is not None else 3.0)
    drop_soft = float(settings.get("sharp_drop_soft_pct") if settings.get("sharp_drop_soft_pct") is not None else 3.0)
    drop_hard = float(settings.get("sharp_drop_hard_pct") if settings.get("sharp_drop_hard_pct") is not None else 5.0)
    soft_n = int(settings.get("soft_confirm_polls") if settings.get("soft_confirm_polls") is not None else 3)
    soft_px = int(peak * (1 - drop_soft / 100.0)) if peak else None
    hard_px = int(peak * (1 - drop_hard / 100.0)) if peak else None
    trig = (trigger_reason or "").upper()
    kind = classify_sangtta_exit_detail(reason_detail)

    items.append(_chk(
        "상따 청산", "전략 태그",
        passed=True,
        actual=str(getattr(pos, "strategy_key", None) or "sangtta"),
        required="strategy_key=sangtta",
        key="sangtta_strategy",
    ))
    def _sangtta_pass(target: str) -> Optional[bool]:
        if not closed or not kind:
            return None
        return kind == target

    items.append(_chk(
        "상따 청산", "상한가 이탈 HARD",
        passed=_sangtta_pass("limit_hard"),
        actual=(f"발동 · {reason_detail}" if kind == "limit_hard" else ("미발동" if closed and kind else ("대기" if not closed else "—"))),
        required=f"상한가 터치 후 ≤ 상한가×(1−{lim_hard:g}%) 즉시",
        note="청산 사유" if kind == "limit_hard" else "",
        key="sangtta_limit_hard",
    ))
    items.append(_chk(
        "상따 청산", "상한가 이탈 SOFT",
        passed=_sangtta_pass("limit_soft"),
        actual=(f"발동 · {reason_detail}" if kind == "limit_soft" else ("미발동" if closed and kind else ("대기" if not closed else "—"))),
        required=f"≤ 상한가×(1−{lim_soft:g}%) · 연속 {soft_n}회",
        note="청산 사유" if kind == "limit_soft" else "",
        key="sangtta_limit_soft",
    ))
    items.append(_chk(
        "상따 청산", "급락 HARD",
        passed=_sangtta_pass("drop_hard"),
        actual=(
            f"발동 · {reason_detail}" if kind == "drop_hard"
            else (f"선 {hard_px:,}원 · 고점 {peak:,}원" if (hard_px and not (closed and kind)) else ("미발동" if closed and kind else ("대기" if not closed else "—")))
        ),
        required=f"고점 대비 ≤ −{drop_hard:g}% 즉시",
        note="청산 사유" if kind == "drop_hard" else "",
        key="sangtta_drop_hard",
    ))
    items.append(_chk(
        "상따 청산", "급락 SOFT",
        passed=_sangtta_pass("drop_soft"),
        actual=(
            f"발동 · {reason_detail}" if kind == "drop_soft"
            else (f"선 {soft_px:,}원 · 고점 {peak:,}원" if (soft_px and not (closed and kind)) else ("미발동" if closed and kind else ("대기" if not closed else "—")))
        ),
        required=f"고점 대비 ≤ −{drop_soft:g}% · 연속 {soft_n}회",
        note="청산 사유" if kind == "drop_soft" else "",
        key="sangtta_drop_soft",
    ))
    if closed and trig == "STOP_LOSS":
        if kind:
            items.append(_chk(
                "상따 청산", "청산 분류",
                passed=True,
                actual=SANGTTA_EXIT_KO.get(kind, kind),
                required="이탈/급락 HARD·SOFT",
                note=str(reason_detail or ""),
                key="sangtta_exit_class",
            ))
        elif reason_detail:
            items.append(_chk(
                "상따 청산", "청산 분류",
                passed=None,
                actual="STOP_LOSS (상세 미분류)",
                required="이탈/급락 HARD·SOFT",
                note=str(reason_detail),
                key="sangtta_exit_class",
            ))
    return items


def breakout_exit_check_items(
    settings: Dict[str, Any],
    pos: Position,
    *,
    reason_detail: Optional[str],
    closed: bool,
) -> List[Dict[str, Any]]:
    level = int(getattr(pos, "breakout_level_price", None) or 0)
    kind = getattr(pos, "breakout_level_kind", None) or settings.get("breakout_level_mode") or "prev_high"
    soft = float(settings.get("struct_break_soft_pct") or 1.0)
    hard = float(settings.get("struct_break_hard_pct") or 2.0)
    polls = int(settings.get("soft_confirm_polls") or 3)
    exit_kind = classify_breakout_exit_detail(reason_detail)

    def passed(target: str) -> Optional[bool]:
        if not closed or not exit_kind:
            return None
        return exit_kind == target

    return [
        _chk(
            "돌파 청산", "전략 태그",
            passed=True,
            actual=f"breakout · {kind}",
            required="strategy_key=breakout",
            key="breakout_strategy",
        ),
        _chk(
            "돌파 청산", "구조 이탈 HARD",
            passed=passed("structure_hard"),
            actual=f"레벨 {level:,}원" if level else "레벨 없음 · 고정손절 폴백",
            required=f"≤ 레벨×(1−{hard:g}%) 즉시",
            note=str(reason_detail or "") if exit_kind == "structure_hard" else "",
            key="breakout_structure_hard",
        ),
        _chk(
            "돌파 청산", "구조 이탈 SOFT",
            passed=passed("structure_soft"),
            actual=f"레벨 {level:,}원" if level else "레벨 없음 · 고정손절 폴백",
            required=f"≤ 레벨×(1−{soft:g}%) · 연속 {polls}회",
            note=str(reason_detail or "") if exit_kind == "structure_soft" else "",
            key="breakout_structure_soft",
        ),
    ]


def _apply_exit_rule_context(
    items: List[Dict[str, Any]],
    closed: bool,
    trig: str,
    *,
    sangtta_kind: Optional[str] = None,
    breakout_kind: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """다른 규칙으로 청산된 경우, 미사용 규칙의 ✗를 해당 없음(?)으로."""
    if not closed or not trig:
        return items
    group = (
        _sangtta_trigger_group(sangtta_kind)
        or ("BREAKOUT_STRUCTURE" if breakout_kind else None)
        or _exit_trigger_group(trig)
    )
    reason_ko = (
        SANGTTA_EXIT_KO.get(sangtta_kind or "", "")
        or BREAKOUT_EXIT_KO.get(breakout_kind or "", "")
        or SELL_REASON_KO.get(trig.upper(), trig)
    )
    skip = {
        "position_closed", "sell_price", "sell_order_db", "trigger_reason", "atr",
        "sangtta_strategy", "sangtta_exit_class", "breakout_strategy",
    }
    trail_ok = any(
        it.get("key") == "trailing" and it.get("passed") is True
        for it in items
    )
    for item in items:
        key = item.get("key", "")
        if key in skip:
            continue
        owner = _RULE_OWNER.get(key)
        if not owner:
            continue
        owner_group = _exit_trigger_group(owner)
        if owner_group != group and item.get("passed") is False:
            item["passed"] = None
            item["note"] = f"청산 사유 {reason_ko} — 이 규칙 미사용"
        elif key == "trail_arm" and group == "TRAILING" and item.get("passed") is False and trail_ok:
            item["passed"] = None
            item["note"] = "익절% armed 전 · 트레일% 선으로 청산"
        # 상따 이탈/급락으로 나갔으면 미발동 HARD/SOFT 항목은 미사용 처리
        if sangtta_kind and key.startswith("sangtta_") and item.get("passed") is False:
            item["passed"] = None
            item["note"] = f"청산 사유 {reason_ko} — 이 규칙 미사용"
        if breakout_kind and key.startswith("breakout_structure_") and item.get("passed") is False:
            item["passed"] = None
            item["note"] = f"청산 사유 {reason_ko} — 이 규칙 미사용"
    return items


def build_sell_condition_checklist(
    settings: Dict[str, Any],
    pos: Position,
    *,
    buy_price: int,
    qty: int,
    sell_price: Optional[int],
    trigger_reason: Optional[str],
    exit_steps: Optional[List[Dict[str, Any]]] = None,
    has_sell_order: bool = False,
    reason_detail: Optional[str] = None,
    strategy_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """청산 규칙별 체크 — 매도 사유와 임계값 대조."""
    exit_steps = exit_steps or []
    items: List[Dict[str, Any]] = []
    closed = _position_closed(pos)
    peak = int(pos.peak_price or buy_price or 0)
    trig = (trigger_reason or "").upper()
    sell_px = int(sell_price or 0) or None
    strat = (strategy_key or getattr(pos, "strategy_key", None) or "").strip().lower()
    is_sangtta = strat == "sangtta"
    is_breakout = strat == "breakout"
    sangtta_kind = classify_sangtta_exit_detail(reason_detail) if is_sangtta else None
    breakout_kind = classify_breakout_exit_detail(reason_detail) if is_breakout else None

    # --- 청산 상태 ---
    if closed:
        reason_label = SELL_REASON_KO.get(trig, trig or str(pos.status or "청산"))
        if sangtta_kind:
            reason_label = f"{reason_label} · {SANGTTA_EXIT_KO.get(sangtta_kind, sangtta_kind)}"
        items.append(_chk(
            "청산 상태", "포지션 종료",
            passed=True,
            actual=reason_label,
            required="청산 완료",
            key="position_closed",
        ))
        if sell_px:
            pl = int(pos.current_profit_loss) if pos.current_profit_loss is not None else (
                (sell_px - buy_price) * qty if buy_price and qty else None
            )
            pl_rate = (pl / (buy_price * qty) * 100) if pl is not None and buy_price and qty else None
            actual = f"{sell_px:,}원"
            if pl_rate is not None:
                actual += f" ({pl_rate:+.2f}%)"
            items.append(_chk(
                "청산 상태", "매도 체결가",
                passed=True,
                actual=actual,
                required="체결가 기록",
                key="sell_price",
            ))
    else:
        items.append(_chk(
            "청산 상태", "포지션 종료",
            passed=False,
            actual="보유 중",
            required="청산 대기",
            key="position_closed",
        ))

    if is_sangtta:
        items.extend(sangtta_exit_check_items(
            settings, pos,
            buy_price=buy_price,
            sell_price=sell_price,
            trigger_reason=trigger_reason,
            reason_detail=reason_detail,
            closed=closed,
        ))
    if is_breakout:
        items.extend(breakout_exit_check_items(
            settings, pos, reason_detail=reason_detail, closed=closed,
        ))

    # --- 장마감 ---
    if settings.get("liquidate_before_close"):
        liq_time = settings.get("liquidate_time") or "15:10"
        if is_breakout:
            items.append(_chk(
                "장마감", "전량 청산",
                passed=True if closed and trig != "MARKET_CLOSE" else None,
                actual="오버나잇 허용 (breakout 제외)",
                required="수급 돌파 포지션은 장마감 강제청산 비적용",
                note="PRD §10 확정",
                key="market_close",
                enabled=False,
            ))
        else:
            triggered = trig == "MARKET_CLOSE"
            items.append(_chk(
                "장마감", "전량 청산",
                passed=triggered if closed else None,
                actual="청산됨" if triggered else ("—" if closed else "미발동"),
                required=f"{liq_time} 이후 MARKET_CLOSE",
                note="청산 사유" if triggered else "",
                key="market_close",
            ))

    # --- 트레일링 (패턴 B) ---
    tp = float(
        (settings.get("breakout_trailing_start_pct") or 0)
        if is_breakout
        else (pos.take_profit_rate or settings.get("take_profit_rate") or 0)
    )
    trail_pct = (
        settings.get("breakout_trailing_pct")
        if is_breakout else settings.get("trailing_stop_pct")
    )
    if tp or trail_pct:
        peak_rate = _peak_rate_pct(buy_price, peak)
        armed = bool(pos.trailing_armed) or (tp and peak_rate >= tp)
        floor = int(pos.trailing_floor_price or 0) or _step_price(exit_steps, "바닥")
        if tp:
            items.append(_chk(
                "트레일링", "시작% (armed)",
                passed=armed if tp else None,
                actual=f"고점 {peak_rate:.2f}%",
                required=f"≥ +{tp}%",
                note="armed" if armed else "",
                key="trail_arm",
            ))
        if floor:
            items.append(_chk(
                "트레일링", "익절 바닥",
                passed=True if armed and floor else None,
                actual=f"{floor:,}원",
                required=f"매수가×{1 + tp / 100:.4f}" if tp else "바닥가",
                key="trail_floor",
            ))
        if trail_pct and peak:
            raw = int(peak * (1 - float(trail_pct) / 100))
            trail_line = max(raw, floor) if floor else raw
            hit = bool(sell_px and sell_px <= trail_line) if closed else None
            triggered = trig in ("TRAILING", "TAKE_PROFIT")
            items.append(_chk(
                "트레일링", "트레일 % 손절선",
                passed=triggered if closed else hit,
                actual=f"선 {trail_line:,}원 · 고점 {peak:,}원",
                required=f"고점×(1−{trail_pct}%), 바닥 이상",
                note="청산 사유" if triggered else "",
                key="trailing",
            ))

    # --- 손절 % ---
    sl = float(
        (settings.get("breakout_stop_loss_pct") or 0)
        if is_breakout
        else (pos.stop_loss_rate or settings.get("stop_loss_rate") or 0)
    )
    if sl:
        sl_price = int(pos.stop_loss_price or 0) or _step_price(exit_steps, "손절") or int(
            buy_price * (1 - abs(sl) / 100)
        )
        triggered = trig == "STOP_LOSS" and not sangtta_kind and not breakout_kind
        hit = bool(sell_px and sell_px <= sl_price) if closed and sell_px else None
        items.append(_chk(
            "손절", "손절 %",
            passed=triggered if closed else hit,
            actual=f"선 {sl_price:,}원" + (f" · 체결 {sell_px:,}원" if sell_px else ""),
            required=f"매수가 − {abs(sl)}%",
            note=(
                "구조 이탈이 우선" if (closed and breakout_kind)
                else ("상따 이탈/급락이 우선" if (closed and sangtta_kind)
                else ("청산 사유" if triggered else "")
                )
            ),
            key="stop_loss",
            enabled=not bool(sangtta_kind),
        ))

    # --- 수익 잠금 ---
    lock_trig = settings.get("profit_lock_trigger")
    lock_floor = settings.get("profit_lock_floor")
    if lock_trig is not None:
        peak_rate = _peak_rate_pct(buy_price, peak)
        armed_lock = peak_rate >= float(lock_trig)
        floor_px = int(buy_price * (1 + float(lock_floor or 0) / 100)) if buy_price else None
        triggered = trig == "PROFIT_LOCK"
        items.append(_chk(
            "수익 잠금", "트리거 도달",
            passed=armed_lock if closed else None,
            actual=f"고점 {peak_rate:.2f}%",
            required=f"≥ +{lock_trig}%",
            key="profit_lock_trig",
        ))
        if floor_px:
            items.append(_chk(
                "수익 잠금", "잠금 바닥",
                passed=triggered if closed else None,
                actual=f"{floor_px:,}원",
                required=f"바닥 +{lock_floor}%",
                note="청산 사유" if triggered else "",
                key="profit_lock",
            ))

    # --- ATR ---
    atr_stop = settings.get("atr_mult_stop")
    atr_trail = settings.get("atr_mult_trail")
    if atr_stop or atr_trail:
        period = settings.get("atr_period") or 14
        atr_stop_step = next(
            (s for s in exit_steps if s.get("rule") == "ATR 손절 (STOP_LOSS)"),
            None,
        )
        atr_trail_step = next(
            (s for s in exit_steps if s.get("rule") == "ATR 트레일 (TRAILING)"),
            None,
        )
        if atr_stop_step and atr_stop_step.get("price") is not None:
            items.append(_chk(
                "ATR", "손절선",
                passed=None,
                actual=f"{int(atr_stop_step['price']):,}원",
                required=f"매수가 − ATR×{atr_stop}",
                note=atr_stop_step.get("note") or "",
                enabled=True,
                key="atr_stop",
            ))
        if atr_trail_step:
            trail_actual = (
                f"{int(atr_trail_step['price']):,}원"
                if atr_trail_step.get("price") is not None
                else "armed 전"
            )
            items.append(_chk(
                "ATR", "트레일선",
                passed=None,
                actual=trail_actual,
                required=f"고점 − ATR×{atr_trail}",
                note=atr_trail_step.get("note") or "",
                enabled=True,
                key="atr_trail",
            ))
        if not atr_stop_step and not atr_trail_step:
            items.append(_chk(
                "ATR", f"ATR({period}일) 배수",
                passed=None,
                actual="장중 실시간 ATR",
                required=f"손절×{atr_stop or '-'} / 트레일×{atr_trail or '-'}",
                note="ATR 일봉 조회 실패 — 토큰·API 상태 확인",
                enabled=True,
                key="atr",
            ))

    # --- 매도 실행 ---
    if has_sell_order:
        items.append(_chk(
            "매도 실행", "sell_orders 기록",
            passed=True,
            actual="DB 매도 주문 있음",
            required="체결 또는 주문 이력",
            key="sell_order_db",
        ))
    elif closed:
        items.append(_chk(
            "매도 실행", "sell_orders 기록",
            passed=None,
            actual="포지션만 청산 기록",
            required="sell_orders 체결",
            note="reconcile·backfill 추정",
            key="sell_order_db",
        ))
    else:
        items.append(_chk(
            "매도 실행", "sell_orders 기록",
            passed=False,
            actual="없음",
            required="—",
            enabled=False,
            key="sell_order_db",
        ))

    if closed and trig:
        label = SELL_REASON_KO.get(trig, trig)
        if sangtta_kind:
            label = f"{label} · {SANGTTA_EXIT_KO.get(sangtta_kind, sangtta_kind)}"
        elif breakout_kind:
            label = f"{label} · {BREAKOUT_EXIT_KO.get(breakout_kind, breakout_kind)}"
        items.append(_chk(
            "매도 실행", "청산 사유 일치",
            passed=True,
            actual=label,
            required="규칙 트리거",
            key="trigger_reason",
        ))

    return _apply_exit_rule_context(
        items,
        closed,
        trig,
        sangtta_kind=sangtta_kind,
        breakout_kind=breakout_kind,
    )


def sell_checklist_summary(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    return checklist_summary(checks)
