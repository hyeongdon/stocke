"""수동으로 현재가를 업데이트하는 테스트 스크립트"""
import sys, io
import asyncio
from models import get_db, Position
from kiwoom_api import KiwoomAPI

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

async def update_current_prices():
    """모든 Position의 현재가를 수동으로 업데이트"""
    print("=" * 60)
    print("🔄 현재가 수동 업데이트 테스트")
    print("=" * 60)
    print()
    
    # DB에서 Position 조회
    db = next(get_db())
    positions = db.query(Position).filter(Position.status == 'HOLDING').all()
    
    if not positions:
        print("❌ 업데이트할 Position이 없습니다.")
        return
    
    print(f"📊 {len(positions)}개 Position 발견")
    print()
    
    # 키움 API 초기화
    api = KiwoomAPI()
    
    # 각 Position의 현재가 조회 및 업데이트
    for idx, position in enumerate(positions, 1):
        print(f"[{idx}/{len(positions)}] {position.stock_name} ({position.stock_code})")
        print(f"  매수가: {position.buy_price:,}원")
        print(f"  기존 현재가: {position.current_price:,}원" if position.current_price else "  기존 현재가: 없음")
        
        try:
            # 현재가 조회
            print(f"  🔍 키움 API에서 현재가 조회 중...")
            current_price = await api.get_current_price(position.stock_code)
            
            if current_price and current_price > 0:
                # 손익 계산
                profit_loss = (current_price - position.buy_price) * position.buy_quantity
                profit_loss_rate = (current_price - position.buy_price) / position.buy_price * 100
                
                # DB 업데이트
                position.current_price = current_price
                position.current_profit_loss = profit_loss
                position.current_profit_loss_rate = profit_loss_rate
                
                db.commit()
                
                print(f"  ✅ 업데이트 완료!")
                print(f"     현재가: {current_price:,}원")
                print(f"     손익: {profit_loss:+,}원 ({profit_loss_rate:+.2f}%)")
            else:
                print(f"  ❌ 현재가 조회 실패 (반환값: {current_price})")
                
        except Exception as e:
            print(f"  ❌ 오류 발생: {e}")
            import traceback
            traceback.print_exc()
        
        print()
        
        # API 제한 고려 (5초 대기)
        if idx < len(positions):
            print(f"  ⏳ API 제한 고려 5초 대기...")
            await asyncio.sleep(5)
            print()
    
    print("=" * 60)
    print("✅ 완료!")
    print("=" * 60)
    print()
    print("💡 브라우저를 새로고침하면 업데이트된 현재가가 표시됩니다.")

if __name__ == '__main__':
    asyncio.run(update_current_prices())

