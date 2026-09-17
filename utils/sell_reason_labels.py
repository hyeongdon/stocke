"""청산 사유 한글 라벨 · 손익 기반 결과 분류.

트레일링·수익잠금·상따/구조 이탈(STOP_LOSS)은 메커니즘 코드로 기록될 수 있으나,
실현 손익이 +이면 결과 분류는 익절(TAKE_PROFIT)로 본다.
"""
from __future__ import annotations

import re
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
    "STOP_3M_BEARISH_BELOW_MA15": "3분 음봉 MA15 이탈",
    "MAX_HOLD": "보유만기",
    "EOD": "장종료 청산",
}

# 손익 부호로 재분류하는 메커니즘 (트레일·잠금)
_PROFIT_MECHANISMS = frozenset({"TRAILING", "PROFIT_LOCK"})
# 상세 문자열에서 꺼낼 때 너무 뭉개진 코드는 후순위
_GENERIC_MECHANISMS = frozenset({"STOP_LOSS", "TAKE_PROFIT", "MANUAL", "MANUAL_SELL"})
_MECH_PREFIX_RE = re.compile(r"^([A-Z][A-Z0-9_]{1,40})(?:→[A-Z][A-Z0-9_]{1,40})?\b")

# Position.status 는 VARCHAR(20). 긴 매도 사유는 여기서 줄인다.
_COARSE_POSITION_STATUS = {
    "TAKE_PROFIT": "TAKE_PROFIT",
    "TP1_HIGH": "TAKE_PROFIT",
    "TP1_GAP": "TAKE_PROFIT",
    "TP1_FALLBACK": "TAKE_PROFIT",
    "MARKET_CLOSE": "MARKET_CLOSE",
    "EOD": "MARKET_CLOSE",
    "MAX_HOLD": "MAX_HOLD",
    "MANUAL": "MANUAL_SELL",
    "MANUAL_SELL": "MANUAL_SELL",
    "TRAILING": "TRAILING",
    "PROFIT_LOCK": "PROFIT_LOCK",
    "INDICATOR": "INDICATOR",
    "DUPLICATE_HOLDING": "DUPLICATE_HOLDING",
    "STOP_LOSS": "STOP_LOSS",
}


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


def mechanism_from_detail(detail: Optional[str]) -> Optional[str]:
    """sell_reason_detail 에서 구체 메커니즘 코드 추출.

    예: 'TP1_GAP | MA1592 TP1_GAP · …', 'TRAILING→STOP_LOSS | TRAILING 청산: …'
    """
    text = (detail or "").strip()
    if not text:
        return None
    m = _MECH_PREFIX_RE.match(text)
    prefix = m.group(1) if m else None
    if prefix and prefix in SELL_REASON_KO and prefix not in _GENERIC_MECHANISMS:
        return prefix
    found: list[str] = []
    for code in SELL_REASON_KO:
        if code in _GENERIC_MECHANISMS:
            continue
        if re.search(rf"\b{re.escape(code)}\b", text):
            found.append(code)
    if found:
        found.sort(key=lambda k: text.find(k))
        return found[0]
    if prefix in SELL_REASON_KO:
        return prefix
    return None


def coarse_position_status(reason: Optional[str]) -> str:
    """SellOrder.sell_reason → Position.status (최대 20자)."""
    r = (reason or "").strip().upper() or "MANUAL_SELL"
    if r in _COARSE_POSITION_STATUS:
        return _COARSE_POSITION_STATUS[r]
    if r.startswith("TP1"):
        return "TAKE_PROFIT"
    if len(r) <= 20:
        return r
    return "STOP_LOSS"


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
    - 그 외(장마감·수동·TP1·STOP_MA 등)는 메커니즘 코드 유지
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
    detail: Optional[str] = None,
) -> str:
    """표시용 한글 사유. 과거 STOP_LOSS(+수익)·TRAILING(+수익) 기록도 익절로 보이게 함.

    detail 이 있으면 구체 코드(TP1_GAP, STOP_MA_DC_WIDEN, TRAILING 등)를 우선한다.
    """
    mech = mechanism_from_detail(detail)
    raw = (mech or reason or "").strip().upper()
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
