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
}
_TRIG_GROUP = {
    "TAKE_PROFIT": "TRAILING",
}


def _exit_trigger_group(trig: str) -> str:
    t = (trig or "").upper()
    return _TRIG_GROUP.get(t, t)


def _apply_exit_rule_context(items: List[Dict[str, Any]], closed: bool, trig: str) -> List[Dict[str, Any]]:
    """다른 규칙으로 청산된 경우, 미사용 규칙의 ✗를 해당 없음(?)으로."""
    if not closed or not trig:
        return items
    group = _exit_trigger_group(trig)
    reason_ko = SELL_REASON_KO.get(trig.upper(), trig)
    skip = {"position_closed", "sell_price", "sell_order_db", "trigger_reason", "atr"}
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
) -> List[Dict[str, Any]]:
    """청산 규칙별 체크 — 매도 사유와 임계값 대조."""
    exit_steps = exit_steps or []
    items: List[Dict[str, Any]] = []
    closed = _position_closed(pos)
    peak = int(pos.peak_price or buy_price or 0)
    trig = (trigger_reason or "").upper()
    sell_px = int(sell_price or 0) or None

    # --- 청산 상태 ---
    if closed:
        reason_label = SELL_REASON_KO.get(trig, trig or str(pos.status or "청산"))
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

    # --- 장마감 ---
    if settings.get("liquidate_before_close"):
        liq_time = settings.get("liquidate_time") or "15:10"
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
    tp = float(pos.take_profit_rate or settings.get("take_profit_rate") or 0)
    trail_pct = settings.get("trailing_stop_pct")
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
    sl = float(pos.stop_loss_rate or settings.get("stop_loss_rate") or 0)
    if sl:
        sl_price = int(pos.stop_loss_price or 0) or _step_price(exit_steps, "손절") or int(
            buy_price * (1 - abs(sl) / 100)
        )
        triggered = trig == "STOP_LOSS"
        hit = bool(sell_px and sell_px <= sl_price) if closed and sell_px else None
        items.append(_chk(
            "손절", "손절 %",
            passed=triggered if closed else hit,
            actual=f"선 {sl_price:,}원" + (f" · 체결 {sell_px:,}원" if sell_px else ""),
            required=f"매수가 − {abs(sl)}%",
            note="청산 사유" if triggered else "",
            key="stop_loss",
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
        items.append(_chk(
            "매도 실행", "청산 사유 일치",
            passed=True,
            actual=SELL_REASON_KO.get(trig, trig),
            required="규칙 트리거",
            key="trigger_reason",
        ))

    return _apply_exit_rule_context(items, closed, trig)


def sell_checklist_summary(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    return checklist_summary(checks)
