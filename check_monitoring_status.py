"""손절 모니터링 상태 확인"""
import sys, io
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

print("=" * 60)
print("🔍 손절 모니터링 상태 확인")
print("=" * 60)
print()

# 모니터링 상태
r = requests.get('http://localhost:8000/monitoring/status')
data = r.json()

stop_loss = data.get('stop_loss', {})
print(f"손절 모니터링: {'✅ 실행 중' if stop_loss.get('is_running') else '❌ 중지'}")

if stop_loss.get('is_running'):
    print(f"모니터링 주기: 2분 (120초)")
    print(f"실시간 현재가 업데이트: ✅ 활성화")
    print()
    print("💡 2분마다 키움 API에서 현재가를 가져와서 DB에 업데이트합니다.")
else:
    print()
    print("💡 손절 모니터링이 꺼져 있어 현재가가 업데이트되지 않습니다.")
    print("   실시간 현재가를 보려면 손절 모니터링을 시작하세요:")
    print()
    print("   POST http://localhost:8000/stop-loss/start")

print()
print("=" * 60)

