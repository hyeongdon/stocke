"""
실제 키움 계좌에 체결된 주문에 대해 Position 데이터를 생성하는 스크립트
"""
import sys
import io
from datetime import datetime
from core.models import get_db, PendingBuySignal, Position
from sqlalchemy.orm import Session

# UTF-8 인코딩 설정 (Windows 콘솔 문제 해결)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def create_position_for_signal(db: Session, signal: PendingBuySignal, 
                                buy_price: int, quantity: int, 
                                stop_loss_price: int = None):
    """
    Signal에 대한 Position 생성
    """
    try:
        # 이미 Position이 있는지 확인
        existing_position = db.query(Position).filter(Position.signal_id == signal.id).first()
        if existing_position:
            print(f"⚠️  이미 Position이 존재합니다: Signal ID {signal.id}")
            return existing_position
        
        # 손절가 계산 (없으면 -5% 기본값)
        if stop_loss_price is None:
            stop_loss_price = int(buy_price * 0.95)
        
        # Position 생성
        position = Position(
            signal_id=signal.id,
            stock_code=signal.stock_code,
            stock_name=signal.stock_name,
            buy_price=buy_price,
            quantity=quantity,
            buy_amount=buy_price * quantity,
            current_price=buy_price,  # 초기값은 매수가로 설정
            stop_loss_price=stop_loss_price,
            status='ACTIVE',
            created_at=datetime.now()
        )
        
        db.add(position)
        
        # Signal 상태를 COMPLETED로 변경
        signal.status = 'COMPLETED'
        signal.updated_at = datetime.now()
        
        db.commit()
        
        print(f"✅ Position 생성 완료:")
        print(f"   - Signal ID: {signal.id}")
        print(f"   - 종목: {signal.stock_name} ({signal.stock_code})")
        print(f"   - 매수가: {buy_price:,}원")
        print(f"   - 수량: {quantity:,}주")
        print(f"   - 매수금액: {buy_price * quantity:,}원")
        print(f"   - 손절가: {stop_loss_price:,}원")
        print()
        
        return position
        
    except Exception as e:
        db.rollback()
        print(f"❌ Position 생성 실패: {e}")
        raise


def main():
    print("=" * 60)
    print("📊 Position 데이터 생성 스크립트")
    print("=" * 60)
    print()
    
    db = next(get_db())
    
    # Signal 정보 확인
    signals = db.query(PendingBuySignal).filter(
        PendingBuySignal.id.in_([22, 24, 25])
    ).order_by(PendingBuySignal.id).all()
    
    if not signals:
        print("❌ 대상 Signal을 찾을 수 없습니다.")
        return
    
    print("📋 현재 Signal 정보:")
    for signal in signals:
        print(f"   ID={signal.id}, 종목={signal.stock_name}({signal.stock_code}), 상태={signal.status}")
    print()
    
    # 실제 키움 계좌 체결 정보
    # 주의: 아래 매수가와 수량은 예시입니다. 실제 키움 계좌의 체결 정보로 수정해주세요!
    positions_data = [
        {
            'signal_id': 22,
            'stock_name': '현대모비스',
            'stock_code': '012330',
            'buy_price': 200000,  # 실제 체결가로 수정 필요
            'quantity': 5,        # 실제 체결 수량으로 수정 필요
            'stop_loss_price': 190000  # 실제 손절가로 수정 필요 (없으면 None)
        },
        {
            'signal_id': 24,
            'stock_name': '한국단자',
            'stock_code': '000700',
            'buy_price': 50000,   # 실제 체결가로 수정 필요
            'quantity': 20,       # 실제 체결 수량으로 수정 필요
            'stop_loss_price': 47500  # 실제 손절가로 수정 필요 (없으면 None)
        },
        {
            'signal_id': 25,
            'stock_name': '대한항공',
            'stock_code': '003490',
            'buy_price': 18000,   # 실제 체결가로 수정 필요
            'quantity': 50,       # 실제 체결 수량으로 수정 필요
            'stop_loss_price': 17100  # 실제 손절가로 수정 필요 (없으면 None)
        }
    ]
    
    print("⚠️  주의: 아래 정보가 실제 키움 계좌 체결 정보와 일치하는지 확인하세요!")
    print()
    for data in positions_data:
        print(f"   [{data['stock_name']}]")
        print(f"   - 매수가: {data['buy_price']:,}원")
        print(f"   - 수량: {data['quantity']:,}주")
        print(f"   - 매수금액: {data['buy_price'] * data['quantity']:,}원")
        print(f"   - 손절가: {data['stop_loss_price']:,}원")
        print()
    
    response = input("위 정보로 Position을 생성하시겠습니까? (yes/no): ").strip().lower()
    if response != 'yes':
        print("❌ 작업이 취소되었습니다.")
        return
    
    print()
    print("=" * 60)
    print("🚀 Position 생성 시작...")
    print("=" * 60)
    print()
    
    # Position 생성
    created_count = 0
    for data in positions_data:
        signal = db.query(PendingBuySignal).filter(
            PendingBuySignal.id == data['signal_id']
        ).first()
        
        if not signal:
            print(f"⚠️  Signal ID {data['signal_id']}를 찾을 수 없습니다.")
            continue
        
        position = create_position_for_signal(
            db=db,
            signal=signal,
            buy_price=data['buy_price'],
            quantity=data['quantity'],
            stop_loss_price=data['stop_loss_price']
        )
        
        if position:
            created_count += 1
    
    print("=" * 60)
    print(f"✅ 완료: {created_count}개의 Position이 생성되었습니다.")
    print("=" * 60)
    print()
    print("💡 다음 단계:")
    print("   1. 웹 브라우저에서 Ctrl+Shift+R로 새로고침")
    print("   2. 시그널 라이프사이클 페이지에서 현재가/손절가/목표가 확인")
    print("   3. 손절 모니터링이 자동으로 시작됩니다")


if __name__ == '__main__':
    main()

