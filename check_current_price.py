"""현재 Position의 현재가 확인"""
import sys, io
from models import get_db, Position

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

db = next(get_db())
positions = db.query(Position).all()

print("=" * 60)
print("📊 DB에 저장된 현재가")
print("=" * 60)
print()

for p in positions:
    print(f"[{p.stock_name}]")
    print(f"  매수가: {p.buy_price:,}원")
    print(f"  DB 현재가: {p.current_price:,}원")
    
    if p.current_price == p.buy_price:
        print(f"  ⚠️  현재가가 매수가와 동일 (업데이트 안됨)")
    else:
        diff = p.current_price - p.buy_price
        diff_pct = (diff / p.buy_price) * 100
        print(f"  {'✅' if diff >= 0 else '❌'} 손익: {diff:+,}원 ({diff_pct:+.2f}%)")
    print()

print("=" * 60)
print("💡 손절 모니터링이 정상 작동해야 현재가가 업데이트됩니다.")
print("=" * 60)

