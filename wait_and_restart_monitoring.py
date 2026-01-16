"""API 제한이 풀릴 때까지 대기 후 손절 모니터링 재시작"""
import sys, io
import time
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

print("=" * 60)
print("⏳ API 제한 해제 대기 중...")
print("=" * 60)
print()

# 손절 모니터링 중지
print("1. 손절 모니터링 중지...")
try:
    r = requests.post('http://localhost:8000/stop-loss/stop', timeout=5)
    print("   ✅ 중지 완료")
except Exception as e:
    print(f"   ⚠️ 중지 실패 (계속 진행): {e}")

print()
print("2. API 제한 해제 대기 중 (90초)...")
print("   키움 API 제한: 1분당 20회")
print("   90초 후 제한이 풀립니다...")

# 90초 대기 (진행률 표시)
for i in range(90, 0, -10):
    print(f"   남은 시간: {i}초...")
    time.sleep(10)

print()
print("3. 손절 모니터링 재시작...")
try:
    r = requests.post('http://localhost:8000/stop-loss/start', timeout=5)
    if r.status_code == 200:
        print("   ✅ 재시작 성공!")
        print()
        print("=" * 60)
        print("✅ 완료: 손절 모니터링이 정상적으로 시작되었습니다.")
        print("=" * 60)
    else:
        print(f"   ❌ 재시작 실패: HTTP {r.status_code}")
        print(f"   응답: {r.text}")
except Exception as e:
    print(f"   ❌ 예외 발생: {e}")

print()
print("💡 launcher.py 로그를 확인하여 정상 작동 여부를 확인하세요.")

