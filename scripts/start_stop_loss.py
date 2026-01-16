"""손절 모니터링 시작"""
import sys, io
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

r = requests.post('http://localhost:8000/stop-loss/start')
print('✅ 손절 모니터링 시작됨!' if r.status_code == 200 else f'❌ 실패: HTTP {r.status_code}')
print(r.json())

