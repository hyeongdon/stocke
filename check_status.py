"""현재 상태 확인"""
import sys, io
from models import get_db, PendingBuySignal, Position

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

db = next(get_db())

print("=" * 60)
print("📊 현재 상태 확인")
print("=" * 60)
print()

signals = db.query(PendingBuySignal).filter(PendingBuySignal.id.in_([22,24,25])).all()
positions = db.query(Position).filter(Position.signal_id.in_([22,24,25])).all()

print(f"✅ Position 개수: {len(positions)}개")
print()

for signal in signals:
    position = next((p for p in positions if p.signal_id == signal.id), None)
    print(f"[{signal.stock_name}]")
    print(f"  - Signal 상태: {signal.status}")
    if position:
        print(f"  - Position 상태: {position.status}")
        print(f"  - 매수가: {position.buy_price:,}원")
        print(f"  - 수량: {position.buy_quantity}주")
        print(f"  - ✅ 주문 완료 & 보유 중")
    else:
        print(f"  - Position: 없음")
        print(f"  - ❌ Position 미생성")
    print()

print("=" * 60)
print("💡 결론:")
if len(positions) == 3:
    print("   ✅ 3개 종목 모두 주문 완료되어 포지션 보유 중입니다!")
    print("   📍 Signal 상태를 'HOLDING'으로 업데이트하는 것을 권장합니다.")
else:
    print(f"   ⚠️  Position이 {len(positions)}개만 생성되었습니다.")
print("=" * 60)

