"""
키움 계좌 잔고 조회 테스트 스크립트

목적:
- 계좌 잔고 조회가 정상 작동하는지 검증
- 계좌 정보, 보유 종목, 평가금액 등 확인

예시:
  python test_account_balance.py
  python test_account_balance.py --account-no 12345678
"""

# Windows 콘솔 UTF-8 인코딩 설정
import sys
import io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import argparse
import asyncio
import json
from datetime import datetime
from typing import Any

from core.config import Config
from api.kiwoom_api import KiwoomAPI


def _pp(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


async def run(args: argparse.Namespace) -> int:
    api = KiwoomAPI()
    
    # 계좌번호 결정
    if args.account_no:
        account_no = args.account_no
    elif Config.KIWOOM_USE_MOCK_ACCOUNT:
        account_no = Config.KIWOOM_MOCK_ACCOUNT_NUMBER
    else:
        account_no = Config.KIWOOM_ACCOUNT_NUMBER
    
    print("=" * 70)
    print("Kiwoom Account Balance Test")
    print(f"- use_mock_account: {Config.KIWOOM_USE_MOCK_ACCOUNT}")
    print(f"- account_no: {account_no}")
    print(f"- current_time: {datetime.now().isoformat()}")
    print("=" * 70)
    
    # 1) 토큰 인증
    print("\n[1] 토큰 인증")
    ok = api.authenticate()
    if not ok:
        print("❌ 인증 실패")
        return 1
    
    token = api.token_manager.get_valid_token()
    if token:
        print(f"✅ 인증 성공 - token: {token[:20]}...")
    else:
        print("❌ 토큰 없음")
        return 2
    
    # 2) 계좌 잔고 조회
    print("\n[2] 계좌 잔고 조회")
    try:
        balance = await api.get_account_balance(account_no)
        
        print("✅ 잔고 조회 성공")
        print("\n📊 계좌 정보:")
        print(_pp(balance))
        
        # 주요 정보 추출
        if balance:
            print("\n💰 주요 잔고 정보:")
            print(f"   - 계좌명: {balance.get('acnt_nm', 'N/A')}")
            print(f"   - 지점명: {balance.get('brch_nm', 'N/A')}")
            print(f"   - 예수금: {balance.get('entr', '0')}")
            print(f"   - 총평가금액: {balance.get('tot_est_amt', '0')}")
            print(f"   - 총매입금액: {balance.get('tot_pur_amt', '0')}")
            print(f"   - 평가손익: {balance.get('lspft_amt', '0')}")
            print(f"   - 수익률: {balance.get('lspft_rt', '0.00')}%")
            
            # 보유 종목 정보
            holdings = balance.get('stk_acnt_evlt_prst', [])
            if holdings:
                print(f"\n📈 보유 종목 ({len(holdings)}개):")
                for i, stock in enumerate(holdings, 1):
                    print(f"   [{i}] {stock.get('prdt_name', 'N/A')} ({stock.get('pdno', 'N/A')})")
                    print(f"       - 보유수량: {stock.get('hldg_qty', '0')}")
                    print(f"       - 매입가: {stock.get('pchs_avg_pric', '0')}")
                    print(f"       - 현재가: {stock.get('prpr', '0')}")
                    print(f"       - 평가금액: {stock.get('evlt_amt', '0')}")
                    print(f"       - 평가손익: {stock.get('evlt_pfls_amt', '0')}")
            else:
                print("\n📈 보유 종목: 없음")
        
        return 0
        
    except Exception as e:
        print(f"❌ 잔고 조회 실패: {e}")
        return 3


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--account-no", default="", help="계좌번호 (미지정시 config에서 자동 선택)")
    args = p.parse_args()
    
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

