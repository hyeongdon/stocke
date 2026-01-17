"""
포지션 체결가와 수량을 수동으로 업데이트하는 스크립트
"""
import sys
import os
import io
from datetime import datetime

# 프로젝트 루트를 Python 경로에 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.models import get_db, Position

# UTF-8 인코딩 설정 (Windows 콘솔 문제 해결)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 업데이트할 포지션 정보
POSITIONS_TO_UPDATE = [
    {"stock_name": "삼성화재", "buy_price": 487500, "buy_quantity": 1},
    {"stock_name": "현대건설", "buy_price": 102750, "buy_quantity": 4},
    {"stock_name": "대한항공", "buy_price": 24850, "buy_quantity": 20},
    {"stock_name": "한국단자", "buy_price": 74600, "buy_quantity": 6},
    {"stock_name": "Mobis", "buy_price": 443000, "buy_quantity": 1},  # DB에 "Mobis"로 저장됨
]


def update_position(db, stock_name, buy_price, buy_quantity):
    """포지션 업데이트"""
    try:
        # 종목명으로 포지션 찾기 (보유 중인 것만)
        position = db.query(Position).filter(
            Position.stock_name == stock_name,
            Position.status == "HOLDING"
        ).first()
        
        if not position:
            # 부분 일치로 찾기 시도
            print(f"⚠️  {stock_name}: 정확한 일치 없음, 부분 일치 검색 중...")
            all_positions = db.query(Position).filter(
                Position.status == "HOLDING"
            ).all()
            
            # 종목명에 "현대건설"이 포함된 것 찾기
            matching_positions = [p for p in all_positions if stock_name in p.stock_name or p.stock_name in stock_name]
            
            if matching_positions:
                print(f"   발견된 유사 종목:")
                for p in matching_positions:
                    print(f"   - {p.stock_name} ({p.stock_code})")
                position = matching_positions[0]
                print(f"   → {position.stock_name} 사용")
            else:
                print(f"❌ {stock_name}: 보유 중인 포지션을 찾을 수 없습니다")
                print(f"   현재 보유 중인 종목:")
                for p in all_positions:
                    print(f"   - {p.stock_name} ({p.stock_code})")
                return False
        
        # 기존 정보 출력
        print(f"\n📌 {stock_name} ({position.stock_code})")
        print(f"   기존 매수가: {position.buy_price:,}원")
        print(f"   기존 수량: {position.buy_quantity}주")
        print(f"   기존 매수금액: {position.buy_amount:,}원")
        
        # 업데이트
        old_price = position.buy_price
        old_quantity = position.buy_quantity
        old_amount = position.buy_amount
        
        position.buy_price = buy_price
        position.buy_quantity = buy_quantity
        position.buy_amount = buy_price * buy_quantity
        
        db.commit()
        
        print(f"   ✅ 업데이트 완료!")
        print(f"   새 매수가: {position.buy_price:,}원")
        print(f"   새 수량: {position.buy_quantity}주")
        print(f"   새 매수금액: {position.buy_amount:,}원")
        
        return True
        
    except Exception as e:
        db.rollback()
        print(f"❌ {stock_name} 업데이트 실패: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("=" * 60)
    print("🔄 포지션 체결가/수량 수동 업데이트")
    print("=" * 60)
    print()
    
    # DB 연결
    db = next(get_db())
    
    print(f"📋 업데이트할 포지션: {len(POSITIONS_TO_UPDATE)}개")
    print()
    
    # 각 포지션 업데이트
    updated_count = 0
    for pos_info in POSITIONS_TO_UPDATE:
        stock_name = pos_info["stock_name"]
        buy_price = pos_info["buy_price"]
        buy_quantity = pos_info["buy_quantity"]
        
        if update_position(db, stock_name, buy_price, buy_quantity):
            updated_count += 1
    
    print()
    print("=" * 60)
    print(f"✅ 완료: {updated_count}개 포지션이 업데이트되었습니다.")
    print("=" * 60)
    print()
    print("💡 브라우저를 새로고침하면 업데이트된 정보가 표시됩니다.")


if __name__ == "__main__":
    main()

