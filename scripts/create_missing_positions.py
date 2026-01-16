"""
수동으로 Position 데이터를 생성하는 스크립트
키움 계좌에 체결된 주문 정보를 입력받아 Position을 생성합니다.
"""
import sys
import io
from datetime import datetime
from core.models import get_db, PendingBuySignal, Position
from sqlalchemy.orm import Session

# UTF-8 인코딩 설정 (Windows 콘솔 문제 해결)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


def create_position(db: Session, signal_id: int, buy_price: int, quantity: int, stop_loss_price: int = None):
    """
    Position 생성
    """
    try:
        # Signal 조회
        signal = db.query(PendingBuySignal).filter(PendingBuySignal.id == signal_id).first()
        if not signal:
            print(f"❌ Signal ID {signal_id}를 찾을 수 없습니다.")
            return None
        
        # 이미 Position이 있는지 확인
        existing_position = db.query(Position).filter(Position.signal_id == signal.id).first()
        if existing_position:
            print(f"⚠️  이미 Position이 존재합니다: Signal ID {signal.id} - {signal.stock_name}")
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
        print(f"   종목: {signal.stock_name} ({signal.stock_code})")
        print(f"   매수가: {buy_price:,}원")
        print(f"   수량: {quantity:,}주")
        print(f"   매수금액: {buy_price * quantity:,}원")
        print(f"   손절가: {stop_loss_price:,}원")
        print()
        
        return position
        
    except Exception as e:
        db.rollback()
        print(f"❌ Position 생성 실패: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    print("=" * 70)
    print("📊 Position 수동 생성 스크립트")
    print("=" * 70)
    print()
    
    db = next(get_db())
    
    # 현재 ORDERED 상태의 Signal 조회
    signals = db.query(PendingBuySignal).filter(
        PendingBuySignal.status == 'ORDERED'
    ).order_by(PendingBuySignal.id).all()
    
    if not signals:
        print("❌ ORDERED 상태의 Signal이 없습니다.")
        return
    
    print("📋 ORDERED 상태의 Signal:")
    print()
    for signal in signals:
        print(f"   ID={signal.id:2d} | {signal.stock_name:10s} ({signal.stock_code}) | {signal.detected_at}")
    
    print()
    print("=" * 70)
    print("💡 키움 계좌에서 확인한 실제 체결 정보를 입력하세요")
    print("=" * 70)
    print()
    
    # Position 데이터 수동 입력
    positions_to_create = []
    
    for signal in signals:
        print(f"\n📌 [{signal.stock_name} ({signal.stock_code})] - Signal ID: {signal.id}")
        print("   키움 계좌에서 이 종목이 체결되었나요? (y/n): ", end='')
        
        response = input().strip().lower()
        if response != 'y':
            print("   ⏭️  건너뜀")
            continue
        
        # 매수가 입력
        while True:
            try:
                buy_price_str = input("   매수가 (원): ").strip().replace(',', '')
                buy_price = int(buy_price_str)
                if buy_price <= 0:
                    print("   ❌ 매수가는 0보다 커야 합니다.")
                    continue
                break
            except ValueError:
                print("   ❌ 숫자를 입력하세요.")
        
        # 수량 입력
        while True:
            try:
                quantity_str = input("   수량 (주): ").strip().replace(',', '')
                quantity = int(quantity_str)
                if quantity <= 0:
                    print("   ❌ 수량은 0보다 커야 합니다.")
                    continue
                break
            except ValueError:
                print("   ❌ 숫자를 입력하세요.")
        
        # 손절가 입력 (선택사항)
        print(f"   손절가 (원) [기본값: {int(buy_price * 0.95):,}원 (-5%)]: ", end='')
        stop_loss_str = input().strip().replace(',', '')
        if stop_loss_str:
            try:
                stop_loss_price = int(stop_loss_str)
            except ValueError:
                print("   ⚠️  잘못된 입력. 기본값 사용")
                stop_loss_price = int(buy_price * 0.95)
        else:
            stop_loss_price = int(buy_price * 0.95)
        
        positions_to_create.append({
            'signal_id': signal.id,
            'stock_name': signal.stock_name,
            'buy_price': buy_price,
            'quantity': quantity,
            'stop_loss_price': stop_loss_price
        })
        
        print(f"   ✅ 입력 완료: {buy_price:,}원 x {quantity:,}주 = {buy_price * quantity:,}원")
    
    if not positions_to_create:
        print("\n❌ 생성할 Position이 없습니다.")
        return
    
    # 확인
    print()
    print("=" * 70)
    print(f"📋 생성할 Position 요약 ({len(positions_to_create)}개):")
    print("=" * 70)
    for data in positions_to_create:
        print(f"\n   [{data['stock_name']}]")
        print(f"   - 매수가: {data['buy_price']:,}원")
        print(f"   - 수량: {data['quantity']:,}주")
        print(f"   - 매수금액: {data['buy_price'] * data['quantity']:,}원")
        print(f"   - 손절가: {data['stop_loss_price']:,}원")
    
    print()
    print("=" * 70)
    response = input("\n위 정보로 Position을 생성하시겠습니까? (yes/no): ").strip().lower()
    if response != 'yes':
        print("❌ 작업이 취소되었습니다.")
        return
    
    # Position 생성
    print()
    print("=" * 70)
    print("🚀 Position 생성 중...")
    print("=" * 70)
    print()
    
    created_count = 0
    for data in positions_to_create:
        position = create_position(
            db=db,
            signal_id=data['signal_id'],
            buy_price=data['buy_price'],
            quantity=data['quantity'],
            stop_loss_price=data['stop_loss_price']
        )
        if position:
            created_count += 1
    
    print("=" * 70)
    print(f"✅ 완료: {created_count}개의 Position이 생성되었습니다!")
    print("=" * 70)
    print()
    print("💡 다음 단계:")
    print("   1. 웹 브라우저에서 Ctrl+Shift+R로 강제 새로고침")
    print("   2. 시그널 라이프사이클 페이지에서 현재가/손절가/목표가 확인")
    print("   3. 손절 모니터링이 자동으로 해당 포지션들을 모니터링합니다")
    print()


if __name__ == '__main__':
    main()

