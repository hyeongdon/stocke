"""청산 사유 한글 라벨 · 손익 기반 결과 분류.

트레일링·수익잠금·상따/구조 이탈(STOP_LOSS)은 메커니즘 코드로 기록될 수 있으나,
실현 손익이 +이면 결과 분류는 익절(TAKE_PROFIT)로 본다.
"""
from __future__ import annotations

from typing import Optional, Union

SELL_REASON_KO = {
    "STOP_LOSS": "손절",
    "TAKE_PROFIT": "익절",
    "TRAILING": "트레일링 스탑",
    "PROFIT_LOCK": "수익 잠금",
    "MARKET_CLOSE": "장마감 청산",
    "MANUAL": "수동 매도",
    "MANUAL_SELL": "수동 매도",
    "INDICATOR": "지표 매도",
    "DUPLICATE_HOLDING": "중복 보유 정리",
    "TP1_HIGH": "전고 반익절",
    "TP1_GAP": "전고 갭 반익절",
    "TP1_FALLBACK": "폴백% 반익절",
    "STOP_MA_DC_WIDEN": "DC+이격 확대",
    "STOP_MA_DC_CRASH": "DC+급락 손절",
    "STOP_MA_CRASH": "급락+큰이탈",
    "STOP_PCT": "%손절",
    "MAX_HOLD": "보유만기",
    "EOD": "장종료 청산",
}

# 손익 부호로 재분류하는 메커니즘 (트레일·잠금)
_PROFIT_MECHANISMS = frozenset({"TRAILING", "PROFIT_LOCK"})


def _sign_of_profit(
    profit_loss: Optional[Union[int, float]] = None,
    profit_loss_rate: Optional[Union[int, float]] = None,
) -> Optional[int]:
    """+1 / 0 / -1, 알 수 없으면 None."""
    if profit_loss is not None:
        v = float(profit_loss)
        if v > 0:
            return 1
        if v < 0:
            return -1
        return 0
    if profit_loss_rate is not None:
        v = float(profit_loss_rate)
        if v > 0:
            return 1
        if v < 0:
            return -1
        return 0
    return None


def classify_exit_reason(
    mechanism: Optional[str],
    *,
    profit_loss: Optional[Union[int, float]] = None,
    profit_loss_rate: Optional[Union[int, float]] = None,
) -> str:
    """메커니즘 사유를 손익 결과에 맞게 분류.

    - TRAILING / PROFIT_LOCK + 수익(+) → TAKE_PROFIT
    - TRAILING / PROFIT_LOCK + 손실(−) → STOP_LOSS
    - STOP_LOSS + 수익(+) → TAKE_PROFIT  (상따 이탈·구조 이탈 등 수익 청산)
    - TAKE_PROFIT + 손실(−) → STOP_LOSS
    - 그 외(장마감·수동 등)는 메커니즘 코드 유지
    """
    mech = (mechanism or "").strip().upper() or "MANUAL"
    sign = _sign_of_profit(profit_loss, profit_loss_rate)

    if mech in _PROFIT_MECHANISMS:
        if sign is None:
            return mech
        if sign > 0:
            return "TAKE_PROFIT"
        if sign < 0:
            return "STOP_LOSS"
        return mech

    # 고정 손절/익절 코드도 손익 부호를 우선 (상따 HARD/SOFT 수익 청산 등)
    if mech == "STOP_LOSS" and sign is not None and sign > 0:
        return "TAKE_PROFIT"
    if mech == "TAKE_PROFIT" and sign is not None and sign < 0:
        return "STOP_LOSS"
    return mech


def sell_reason_ko(
    reason: Optional[str],
    *,
    profit_loss: Optional[Union[int, float]] = None,
    profit_loss_rate: Optional[Union[int, float]] = None,
) -> str:
    """표시용 한글 사유. 과거 STOP_LOSS(+수익)·TRAILING(+수익) 기록도 익절로 보이게 함."""
    raw = (reason or "").strip().upper()
    if not raw:
        return "기타"
    classified = classify_exit_reason(
        raw, profit_loss=profit_loss, profit_loss_rate=profit_loss_rate
    )
    if raw == "TRAILING" and classified == "TAKE_PROFIT":
        return "익절 (트레일)"
    if raw == "TRAILING" and classified == "STOP_LOSS":
        return "손절 (트레일)"
    if raw == "PROFIT_LOCK" and classified == "TAKE_PROFIT":
        return "익절 (수익잠금)"
    if raw == "PROFIT_LOCK" and classified == "STOP_LOSS":
        return "손절 (수익잠금)"
    # 상따 상한가/급락 이탈·구조 이탈 등으로 STOP_LOSS 기록됐지만 실현 수익(+)
    if raw == "STOP_LOSS" and classified == "TAKE_PROFIT":
        return "익절 (이탈)"
    if raw == "TAKE_PROFIT" and classified == "STOP_LOSS":
        return "손절"
    return SELL_REASON_KO.get(classified, SELL_REASON_KO.get(raw, reason or "기타"))
