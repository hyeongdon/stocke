"""자동매매 설정 확인"""
import sys, io
from models import get_db, AutoTradeSettings

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

db = next(get_db())
settings = db.query(AutoTradeSettings).first()

print("=" * 60)
print("⚙️  자동매매 설정")
print("=" * 60)
print()

if settings:
    print(f"활성화: {'✅ 예' if settings.is_enabled else '❌ 아니오'}")
    print(f"최대 투자금액: {settings.max_invest_amount:,}원")
    print(f"손절 비율: {settings.stop_loss_rate}%")
    print(f"익절 비율: {settings.take_profit_rate}%")
    print()
    
    if not settings.is_enabled:
        print("⚠️  문제 발견: 자동매매가 비활성화되어 있습니다!")
        print()
        print("💡 손절 모니터링은 자동매매가 활성화된 경우에만 작동합니다.")
        print("   자동매매를 활성화하거나, 손절 모니터링 로직을 수정해야 합니다.")
else:
    print("❌ 자동매매 설정이 없습니다!")

print()
print("=" * 60)

