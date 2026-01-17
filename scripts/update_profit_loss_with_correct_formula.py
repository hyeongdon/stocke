"""정확한 모의투자 공식으로 평가손익 재계산 및 DB 업데이트"""
import sys
import os
import io
import math
import asyncio

# 프로젝트 루트를 Python 경로에 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from core.models import get_db, Position
from core.config import Config

async def update_all_positions():
    """모든 HOLDING 포지션의 평가손익을 정확한 공식으로 재계산"""
    print("=" * 60)
    print("정확한 모의투자 공식으로 평가손익 재계산")
    print("=" * 60)
    print()
    
    is_mock_account = Config.KIWOOM_USE_MOCK_ACCOUNT
    print(f"계좌 타입: {'모의투자' if is_mock_account else '실계좌'}")
    print()
    
    updated_count = 0
    for db in get_db():
        session = db
        positions = session.query(Position).filter(Position.status == "HOLDING").all()
        
        if not positions:
            print("업데이트할 포지션이 없습니다.")
            return
        
        print(f"총 {len(positions)}개 포지션 업데이트 중...")
        print()
        
        for position in positions:
            if not position.current_price or position.current_price <= 0:
                print(f"⏭️  {position.stock_name}: 현재가 없음")
                continue
            
            actual_buy_amount = position.actual_buy_amount if position.actual_buy_amount else position.buy_amount
            
            if is_mock_account:
                # 모의투자 계좌: 매도 수수료 0.35%, 제세금 총 0.557%
                sell_fee = math.floor(position.current_price * position.buy_quantity * 0.0035)  # 0.35%
                tax = math.floor(position.current_price * position.buy_quantity * 0.00557)  # 0.557%
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
            profit_loss = evaluation_amount - actual_buy_amount
            
            # 수익률 = 손익 / 매입금액 × 100
            profit_loss_rate = (profit_loss / actual_buy_amount) * 100 if actual_buy_amount > 0 else 0
            
            old_profit = position.current_profit_loss
            old_rate = position.current_profit_loss_rate
            
            position.current_profit_loss = int(profit_loss)
            position.current_profit_loss_rate = profit_loss_rate
            
            updated_count += 1
            print(f"✅ {position.stock_name}")
            print(f"   현재가: {position.current_price:,}원 × {position.buy_quantity}주 = {position.current_price * position.buy_quantity:,}원")
            print(f"   매도 수수료 (0.35%): {sell_fee:,}원")
            print(f"   제세금 (0.557%): {tax:,}원")
            print(f"   평가금액: {evaluation_amount:,}원")
            print(f"   실제매입금액: {actual_buy_amount:,}원")
            print(f"   평가손익: {old_profit:+,}원 ({old_rate:+.2f}%) → {profit_loss:+,}원 ({profit_loss_rate:+.2f}%)")
            print()
        
        session.commit()
        break
    
    print("=" * 60)
    print(f"✅ 완료: {updated_count}개 포지션이 업데이트되었습니다.")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(update_all_positions())

