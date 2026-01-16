"""여러 Signal 삭제 스크립트"""
import sys, io
from models import get_db, PendingBuySignal, Position

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

signal_ids = [14, 15, 16, 17, 18, 19, 20]

db = next(get_db())

print("=" * 60)
print(f"🗑️  Signal 삭제")
print("=" * 60)
print()

deleted_count = 0
for signal_id in signal_ids:
    # Signal 조회
    signal = db.query(PendingBuySignal).filter(PendingBuySignal.id == signal_id).first()
    
    if not signal:
        print(f"⚠️  Signal ID {signal_id}를 찾을 수 없습니다.")
        print()
        continue
    
    print(f"[Signal ID: {signal_id}]")
    print(f"  종목: {signal.stock_name} ({signal.stock_code})")
    print(f"  상태: {signal.status}")
    print(f"  생성일: {signal.detected_at}")
    
    # 관련 Position 확인
    position = db.query(Position).filter(Position.signal_id == signal_id).first()
    if position:
        print(f"  ⚠️  관련 Position 있음 (Position은 유지)")
    
    # Signal 삭제
    db.delete(signal)
    deleted_count += 1
    print(f"  ✅ 삭제 완료")
    print()

# 커밋
db.commit()

print("=" * 60)
print(f"✅ {deleted_count}개 Signal 삭제 완료!")
print("=" * 60)

