"""
키움 주문 API 수동 테스트 스크립트

목적:
- 서버/전략 루프와 무관하게 토큰 발급/잔고조회/주문(매수/매도)까지 "실제 API 호출"이 가능한지 검증
- 기본은 DRY-RUN(주문 미실행)이며, --execute 를 붙여야 주문이 나갑니다.

예시(모의투자 권장):
  venv\\Scripts\\python.exe manual_order_test.py --stock-code 005930 --qty 1 --side buy --execute
"""

import argparse
import asyncio
import json
from typing import Any, Dict
from datetime import datetime, time, timedelta, timezone

from config import Config
from kiwoom_api import KiwoomAPI


def _pp(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


async def run(args: argparse.Namespace) -> int:
    api = KiwoomAPI()

    now_local = datetime.now()
    now_utc = datetime.now(timezone.utc)
    kst = timezone(timedelta(hours=9))
    now_kst = now_utc.astimezone(kst)
    market_open = (now_kst.weekday() < 5) and (time(9, 0) <= now_kst.time() <= time(15, 30))

    print("=" * 70)
    print("Kiwoom Manual Order Test")
    print(f"- use_mock_account: {Config.KIWOOM_USE_MOCK_ACCOUNT}")
    print(f"- stock_code: {args.stock_code}")
    print(f"- qty: {args.qty}")
    print(f"- price: {args.price} (0=market)")
    print(f"- side: {args.side}")
    print(f"- execute: {args.execute}")
    print(f"- now_local: {now_local.isoformat(timespec='seconds')}")
    print(f"- now_kst:   {now_kst.isoformat(timespec='seconds')} (market_open={market_open})")
    print("=" * 70)

    # 1) Authenticate / token
    ok = api.authenticate()
    print(f"[1] authenticate(): {ok}")
    token = api.token_manager.get_valid_token()
    print(f"[1] token_valid: {bool(token)}")

    if not token:
        print("ERROR: No token. Check APP_KEY/SECRET.")
        return 2

    # 2) Account balance
    account_no = args.account_no or (Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER)
    print(f"[2] account_no: {account_no}")
    account_pw = Config.KIWOOM_MOCK_ACCOUNT_PASSWORD if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_PASSWORD
    print(f"[2] account_pw_set: {bool(account_pw)}")
    bal = await api.get_account_balance(account_no)
    print("[2] get_account_balance() response:")
    print(_pp(bal))

    if not args.execute:
        print("\nDRY-RUN DONE: No order was placed. (Add --execute to place an order)")
        return 0

    # 3) Place order
    if args.side == "buy":
        res: Dict[str, Any] = await api.place_buy_order(
            stock_code=args.stock_code,
            quantity=args.qty,
            price=args.price,
            order_type=args.order_type,
        )
    else:
        res = await api.place_sell_order(
            stock_code=args.stock_code,
            quantity=args.qty,
            price=args.price,
            order_type=args.order_type,
        )

    print("\n[3] order response:")
    print(_pp(res))

    if res.get("success"):
        print("\nOK: Order API accepted the request.")
        return 0

    print("\nERROR: Order API request failed.")
    return 3


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--stock-code", default="005930")
    p.add_argument("--qty", type=int, default=1)
    p.add_argument("--price", type=int, default=0)
    p.add_argument("--side", choices=["buy", "sell"], default="buy")
    # 키움 kt10000 기준: 3=시장가, 0=보통(지정가)
    p.add_argument("--order-type", default="3")
    p.add_argument("--account-no", default="")
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()

    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())


