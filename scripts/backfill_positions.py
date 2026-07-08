"""ORDERED 신호는 있는데 positions 테이블에 없는 경우 — 계좌 잔고 기준으로 복구."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.kiwoom_api import KiwoomAPI, _parse_kiwoom_int
from core.config import Config
from core.models import PendingBuySignal, Position, get_db
from managers.stop_loss_manager import stop_loss_manager


def _qty_price(holding: dict, execution: dict) -> tuple:
    qty = _parse_kiwoom_int((execution or {}).get("cntr_qty") or (holding or {}).get("qty"))
    price = _parse_kiwoom_int((execution or {}).get("cntr_pric") or (holding or {}).get("avg_pr"))
    if qty <= 0 and holding:
        pur = _parse_kiwoom_int(holding.get("pur_amt"))
        if pur > 0 and price > 0:
            qty = pur // price
    if price <= 0 and holding:
        pur = _parse_kiwoom_int(holding.get("pur_amt"))
        if pur > 0 and qty > 0:
            price = pur // qty
    return qty, price


async def main():
    api = KiwoomAPI()
    if not api.authenticate():
        print("Kiwoom auth failed")
        return 1

    acct = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER
    bal = await api.get_account_balance(acct)
    holdings = {
        KiwoomAPI.normalize_stock_code(h.get("stk_cd", "")): h
        for h in (bal or {}).get("stk_acnt_evlt_prst", [])
    }
    exec_res = await api.get_executions(sell_tp="2")
    executions = {}
    for it in exec_res.get("items") or []:
        code = KiwoomAPI.normalize_stock_code(it.get("stk_cd", ""))
        if code:
            executions[code] = it
    print(f"account holdings: {len(holdings)}, buy fills: {len(executions)}")

    ordered = []
    for db in get_db():
        ordered = (
            db.query(PendingBuySignal)
            .filter(PendingBuySignal.status == "ORDERED")
            .order_by(PendingBuySignal.id.desc())
            .all()
        )
        break

    if not ordered:
        print("ORDERED 신호 없음")
        return 0

    created = 0
    for sig in ordered:
        code = KiwoomAPI.normalize_stock_code(sig.stock_code)
        for db in get_db():
            exists = (
                db.query(Position)
                .filter(Position.stock_code == code, Position.status == "HOLDING")
                .first()
            )
            break
        if exists:
            print(f"  skip {sig.stock_name}({code}) - position exists id={exists.id}")
            continue

        h = holdings.get(code)
        ex = executions.get(code)
        if not h and not ex:
            print(f"  skip {sig.stock_name}({code}) - not in account")
            continue

        qty, avg = _qty_price(h or {}, ex or {})
        if qty <= 0 or avg <= 0:
            print(f"  skip {sig.stock_name}({code}) - qty/price unknown")
            continue

        pos = await stop_loss_manager.create_position_from_buy_signal(
            signal_id=sig.id,
            buy_price=avg,
            buy_quantity=qty,
            buy_order_id="backfill",
        )
        if pos:
            created += 1
            print(f"  + {sig.stock_name}({code}) {qty}주 @ {avg:,}원 → position id={pos.id}")
        else:
            print(f"  fail {sig.stock_name}({code})")

    print(f"복구 완료: {created}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
