"""Signal 상태를 ORDERED로 되돌리는 스크립트"""
import sys
import io
from datetime import datetime
from models import get_db, PendingBuySignal

# UTF-8 인코딩 설정
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

db = next(get_db())

# Signal 조회
signals = db.query(PendingBuySignal).filter(
    PendingBuySignal.id.in_([22, 24, 25])
).all()

print("=" * 60)
print("📝 Signal 상태 변경")
print("=" * 60)
print()

for signal in signals:
    print(f"ID={signal.id}, {signal.stock_name}")
    print(f"   변경 전: {signal.status} → 변경 후: ORDERED")
    signal.status = 'ORDERED'
    signal.updated_at = datetime.now()

db.commit()

print()
print("=" * 60)
print("✅ 완료! Signal 상태를 ORDERED로 변경했습니다.")
print("=" * 60)
print()
print("💡 브라우저를 새로고침하세요 (Ctrl+Shift+R)")

