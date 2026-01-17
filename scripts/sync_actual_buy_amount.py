"""
키움 API에서 실제 매입금액 동기화
"""
import sys
import os
import io
import asyncio

# 프로젝트 루트를 Python 경로에 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# UTF-8 인코딩 설정
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from core.models import get_db, Position
from api.kiwoom_api import KiwoomAPI
from core.config import Config

async def sync_actual_buy_amount():
    """키움 API에서 실제 매입금액 동기화"""
    try:
        print("=" * 60)
        print("🔄 키움 API에서 실제 매입금액 동기화")
        print("=" * 60)
        print()
        
        # 키움 API 초기화
        api = KiwoomAPI()
        
        # 계좌 정보
        account_number = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER
        print(f"📊 계좌: {account_number}")
        print(f"   타입: {'모의투자' if Config.KIWOOM_USE_MOCK_ACCOUNT else '실계좌'}")
        print()
        
        # 키움 API에서 보유종목 정보 조회
        print("🔍 키움 API에서 보유종목 정보 조회 중...")
        balance_data = await api.get_account_balance(account_number)
        
        if not balance_data or 'stk_acnt_evlt_prst' not in balance_data:
            print("❌ 보유종목 정보 조회 실패")
            return
        
        holdings = balance_data.get('stk_acnt_evlt_prst', [])
        print(f"✅ 보유종목 {len(holdings)}개 발견")
        print()
        
        # 매입금액 및 평가손익 맵 생성
        holdings_map = {}
        for holding in holdings:
            stock_code = holding.get('stk_cd', '').replace('A', '')
            stock_name = holding.get('stk_nm', '')
            pur_amt = int(float(holding.get('pur_amt', '0')))
            evlt_amt = int(float(holding.get('evlt_amt', '0')))  # 평가금액 (키움 실제 값)
            lspft_amt = int(float(holding.get('lspft_amt', '0')))  # 평가손익
            lspft_rt = float(holding.get('lspft_rt', '0'))  # 수익률
            if pur_amt > 0:
                holdings_map[stock_code] = {
                    'pur_amt': pur_amt,
                    'evlt_amt': evlt_amt,  # 평가금액 추가
                    'lspft_amt': lspft_amt,
                    'lspft_rt': lspft_rt
                }
                print(f"   {stock_name} ({stock_code}): 매입금액 {pur_amt:,}원, 평가금액 {evlt_amt:,}원, 평가손익 {lspft_amt:+,}원 ({lspft_rt:+.2f}%)")
        
        print()
        print("=" * 60)
        print("📝 포지션 업데이트 중...")
        print("=" * 60)
        print()
        
        # 포지션 업데이트
        updated_count = 0
        for db in get_db():
            session = db
            positions = session.query(Position).filter(Position.status == "HOLDING").all()
            
            for position in positions:
                stock_code = position.stock_code.replace('A', '')
                if stock_code in holdings_map:
                    holding_info = holdings_map[stock_code]
                    actual_buy_amount = holding_info['pur_amt']
                    kiwoom_profit_loss = holding_info['lspft_amt']
                    kiwoom_profit_rate = holding_info['lspft_rt']
                    
                    updated = False
                    old_amount = position.actual_buy_amount
                    old_profit = position.current_profit_loss
                    old_rate = position.current_profit_loss_rate
                    
                    # actual_buy_amount 업데이트
                    if old_amount != actual_buy_amount:
                        position.actual_buy_amount = actual_buy_amount
                        updated = True
                    
                    # 키움 API의 평가손익과 수익률이 있으면 우선 사용 (가장 정확함)
                    if kiwoom_profit_loss != 0 or kiwoom_profit_rate != 0:
                        if old_profit != kiwoom_profit_loss or abs(old_rate - kiwoom_profit_rate) > 0.01:
                            position.current_profit_loss = int(kiwoom_profit_loss)
                            position.current_profit_loss_rate = kiwoom_profit_rate
                            updated = True
                    # 키움 API 값이 0이면 키움 공식으로 계산 (모의투자/실계좌 구분)
                    elif position.current_price:
                        import math
                        from core.config import Config
                        
                        is_mock_account = Config.KIWOOM_USE_MOCK_ACCOUNT
                        
                        if is_mock_account:
                            # 모의투자 계좌: 매도 수수료 0.35%, 제세금 약 0.557541%
                            sell_fee = math.floor(position.current_price * position.buy_quantity * 0.0035)  # 0.35%
                            tax = math.floor(position.current_price * position.buy_quantity * 0.00557541)    # 약 0.557541%
                        else:
                            # 실계좌: 매도 수수료 0.015% (10원미만 절사), 제세금 0.05% + 0.15%
                            sell_fee_base = position.current_price * position.buy_quantity * 0.00015
                            sell_fee = math.floor(sell_fee_base / 10) * 10  # 10원미만 절사
                            
                            tax_005 = math.floor(position.current_price * position.buy_quantity * 0.0005)  # 0.05%, 원미만 절사
                            tax_015 = math.floor(position.current_price * position.buy_quantity * 0.0015)  # 0.15%, 원미만 절사
                            tax = tax_005 + tax_015
                        
                        # 평가금액 = 현재가 × 수량 - 매도 수수료 - 제세금
                        evaluation_amount = position.current_price * position.buy_quantity - sell_fee - tax
                        
                        # 손익 = 평가금액 - 매입금액
                        calculated_profit_loss = evaluation_amount - actual_buy_amount
                        
                        # 수익률 = 손익 / 매입금액 × 100
                        calculated_profit_rate = (calculated_profit_loss / actual_buy_amount) * 100 if actual_buy_amount > 0 else 0
                        
                        if old_profit != int(calculated_profit_loss) or abs(old_rate - calculated_profit_rate) > 0.01:
                            position.current_profit_loss = int(calculated_profit_loss)
                            position.current_profit_loss_rate = calculated_profit_rate
                            updated = True
                    
                    if updated:
                        updated_count += 1
                        print(f"✅ {position.stock_name} ({stock_code})")
                        if old_amount != actual_buy_amount:
                            print(f"   매입금액: {old_amount:,}원 → {actual_buy_amount:,}원" if old_amount else f"   매입금액: 없음 → {actual_buy_amount:,}원")
                        if old_profit != kiwoom_profit_loss or old_rate != kiwoom_profit_rate:
                            print(f"   평가손익: {old_profit:+,}원 ({old_rate:+.2f}%) → {kiwoom_profit_loss:+,}원 ({kiwoom_profit_rate:+.2f}%)")
                        print()
                    else:
                        print(f"⏭️  {position.stock_name} ({stock_code}): 변경 없음")
                else:
                    print(f"⚠️  {position.stock_name} ({stock_code}): 키움 계좌에 없음")
            
            session.commit()
            break
        
        print("=" * 60)
        print(f"✅ 완료: {updated_count}개 포지션이 업데이트되었습니다.")
        print("=" * 60)
        print()
        print("💡 브라우저를 새로고침하면 업데이트된 수익률이 표시됩니다.")
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(sync_actual_buy_amount())

