"""
3개 종목 (대한항공, 현대모비스, 한국단자)의 Position을 자동으로 생성하는 스크립트
"""
import sys
import io
from datetime import datetime
from core.models import get_db, PendingBuySignal, Position

# UTF-8 인코딩 설정 (Windows 콘솔 문제 해결)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


def create_position(db, signal_id: int, buy_price: int, quantity: int):
    """Position 생성"""
    try:
        # Signal 조회
        signal = db.query(PendingBuySignal).filter(PendingBuySignal.id == signal_id).first()
        if not signal:
            print(f"❌ Signal ID {signal_id}를 찾을 수 없습니다.")
            return None
        
        # 이미 Position이 있는지 확인
        existing = db.query(Position).filter(Position.signal_id == signal.id).first()
        if existing:
            print(f"⚠️  이미 Position이 존재: {signal.stock_name}")
            return existing
        
        # 손절가 계산 (-5%)
        stop_loss_price = int(buy_price * 0.95)
        
        # Position 생성
        position = Position(
            signal_id=signal.id,
            stock_code=signal.stock_code,
            stock_name=signal.stock_name,
            buy_price=buy_price,
            buy_quantity=quantity,  # 필드명 수정
            buy_amount=buy_price * quantity,
            current_price=buy_price,
            stop_loss_price=stop_loss_price,
            status='HOLDING',  # 기본 상태
            buy_time=datetime.now()  # 필드명 수정
        )
        
        db.add(position)
        
        # Signal 상태 변경
        signal.status = 'COMPLETED'
        signal.updated_at = datetime.now()
        
        db.commit()
        
        print(f"✅ {signal.stock_name}: {buy_price:,}원 x {quantity}주 = {buy_price * quantity:,}원")
        return position
        
    except Exception as e:
        db.rollback()
        print(f"❌ 실패: {e}")
        return None


def main():
    print("=" * 70)
    print("📊 Position 자동 생성")
    print("=" * 70)
    print()
    
    db = next(get_db())
    
    # ORDERED 상태의 Signal 확인
    signals = db.query(PendingBuySignal).filter(
        PendingBuySignal.status == 'ORDERED'
    ).order_by(PendingBuySignal.id).all()
    
    print("📋 ORDERED 상태의 Signal:")
    for signal in signals:
        print(f"   ID={signal.id} | {signal.stock_name} ({signal.stock_code})")
    print()
    
    # 매수 정보 (키움 계좌 실제 체결 정보)
    positions_data = [
        {'signal_id': 25, 'name': '대한항공', 'buy_price': 24850, 'quantity': 20},
        {'signal_id': 22, 'name': '현대모비스', 'buy_price': 443000, 'quantity': 1},
        {'signal_id': 24, 'name': '한국단자', 'buy_price': 74600, 'quantity': 6}
    ]
    
    print("⚠️  생성할 Position 정보:")
    print()
    for data in positions_data:
        print(f"   [{data['name']}]")
        print(f"   - 매수가: {data['buy_price']:,}원")
        print(f"   - 수량: {data['quantity']}주")
        print(f"   - 매수금액: {data['buy_price'] * data['quantity']:,}원")
        print(f"   - 손절가: {int(data['buy_price'] * 0.95):,}원 (-5%)")
        print()
    
    print()
    print("=" * 70)
    print("🚀 Position 생성 중...")
    print("=" * 70)
    print()
    
    created_count = 0
    for data in positions_data:
        position = create_position(
            db=db,
            signal_id=data['signal_id'],
            buy_price=data['buy_price'],
            quantity=data['quantity']
        )
        if position:
            created_count += 1
    
    print()
    print("=" * 70)
    print(f"✅ 완료: {created_count}개의 Position 생성!")
    print("=" * 70)
    print()
    print("💡 브라우저에서 Ctrl+Shift+R로 새로고침하세요!")


if __name__ == '__main__':
    main()

