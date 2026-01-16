"""Signal 삭제 스크립트"""
import sys, io
from core.models import get_db, PendingBuySignal, Position

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

signal_id = 26

db = next(get_db())

# Signal 조회
signal = db.query(PendingBuySignal).filter(PendingBuySignal.id == signal_id).first()

if not signal:
    print(f"❌ Signal ID {signal_id}를 찾을 수 없습니다.")
    exit(1)

print("=" * 60)
print(f"🗑️  Signal 삭제")
print("=" * 60)
print()
print(f"Signal ID: {signal.id}")
print(f"종목: {signal.stock_name} ({signal.stock_code})")
print(f"상태: {signal.status}")
print(f"생성일: {signal.detected_at}")
print()

# 관련 Position 확인
position = db.query(Position).filter(Position.signal_id == signal_id).first()
if position:
    print(f"⚠️  경고: 이 Signal과 연결된 Position이 있습니다!")
    print(f"   Position ID: {position.id}")
    print(f"   종목: {position.stock_name}")
    print(f"   상태: {position.status}")
    print()
    print("   ⏭️  Position은 유지하고 Signal만 삭제합니다.")
    print()

# Signal 삭제
print(f"Signal 삭제 중...")
db.delete(signal)
db.commit()

print()
print("=" * 60)
print(f"✅ Signal ID {signal_id} (고려아연) 삭제 완료!")
print("=" * 60)

