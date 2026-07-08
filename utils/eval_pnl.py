"""평가손익 — 키움 kt00004 API 값 기준."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from api.kiwoom_api import KiwoomAPI, _parse_kiwoom_float, _parse_kiwoom_int


def holdings_by_code(balance: Optional[dict]) -> Dict[str, dict]:
    """잔고 응답 → {정규화 종목코드: 보유 row}."""
    out: Dict[str, dict] = {}
    if not balance or balance.get("_error"):
        return out
    for h in balance.get("stk_acnt_evlt_prst") or []:
        code = KiwoomAPI.normalize_stock_code(h.get("stk_cd", ""))
        qty = _parse_kiwoom_int(h.get("qty"))
        if code and qty > 0:
            out[code] = h
    return out


def pl_from_holding(holding: dict) -> Tuple[int, float]:
    """키움 보유종목 row → (평가손익, 수익률%). lspft_amt 없으면 evlt_amt−pur_amt."""
    pl = _parse_kiwoom_int(holding.get("lspft_amt") or holding.get("pl_amt"))
    rate = _parse_kiwoom_float(holding.get("lspft_rt") or holding.get("pl_rt"))
    if pl == 0:
        pur = _parse_kiwoom_int(holding.get("pur_amt"))
        evlt = _parse_kiwoom_int(holding.get("evlt_amt"))
        if pur > 0 and evlt > 0:
            pl = evlt - pur
            rate = (pl / pur) * 100
    return pl, rate


def pl_from_amounts(
    pur_amt: int,
    quantity: int,
    current_price: int,
    evlt_amt: int | None = None,
) -> Tuple[int, float]:
    """평가손익 폴백 — evlt_amt(키움 평가금액) 우선, 없을 때만 현재가×수량."""
    if pur_amt <= 0:
        return 0, 0.0
    evlt = int(evlt_amt or 0)
    if evlt > 0:
        pl = evlt - pur_amt
    elif quantity > 0 and current_price > 0:
        pl = current_price * quantity - pur_amt
    else:
        return 0, 0.0
    rate = (pl / pur_amt) * 100
    return int(pl), rate


def apply_holding_to_position(position, holding: dict) -> None:
    """키움 kt00004 보유 1종목 → Position 금액·수량·손익 전체 동기화."""
    pur = _parse_kiwoom_int(holding.get("pur_amt"))
    qty = _parse_kiwoom_int(holding.get("qty"))
    cur = _parse_kiwoom_int(holding.get("cur_pr"))
    avg = _parse_kiwoom_int(holding.get("avg_pr"))
    pl, rate = pl_from_holding(holding)

    if qty > 0:
        position.buy_quantity = qty
    if avg > 0:
        position.buy_price = avg
    elif pur > 0 and qty > 0:
        position.buy_price = int(round(pur / qty))
    if pur > 0:
        position.actual_buy_amount = pur
        position.buy_amount = pur
    if cur > 0:
        position.current_price = cur
    position.current_profit_loss = pl
    position.current_profit_loss_rate = rate


def calc_profit_for_position(position, current_price: int, holding: dict | None = None) -> Tuple[int, float]:
    """포지션 평가손익 — holding(API lspft_amt) 우선, 없으면 DB 동기화값, 최후 폴백만 계산."""
    if holding:
        return pl_from_holding(holding)
    if getattr(position, "actual_buy_amount", None) and position.current_profit_loss is not None:
        return int(position.current_profit_loss), float(position.current_profit_loss_rate or 0)
    pur = int(getattr(position, "actual_buy_amount", None) or position.buy_amount or 0)
    qty = int(position.buy_quantity or 0)
    price = int(current_price or position.current_price or 0)
    if pur > 0 and qty > 0 and price > 0:
        return pl_from_amounts(pur, qty, price)
    if position.current_profit_loss is not None:
        return int(position.current_profit_loss), float(position.current_profit_loss_rate or 0)
    return 0, 0.0
