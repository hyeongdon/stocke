"""API 응답 테스트"""
import sys
import io
import requests
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# API 호출
url = "http://localhost:8000/signals/pending?status=ALL&skip_price=true"
print(f"📡 API 호출: {url}")
print()

try:
    response = requests.get(url, timeout=5)
    print(f"상태 코드: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"응답 데이터 타입: {type(data)}")
        
        if isinstance(data, list):
            print(f"✅ Signal 개수: {len(data)}")
            print()
            for signal in data[:5]:  # 처음 5개만 출력
                print(f"ID={signal.get('id')}, {signal.get('stock_name')}, status={signal.get('status')}")
                if signal.get('position'):
                    pos = signal['position']
                    print(f"  → Position: buy_price={pos.get('buy_price')}, current_price={pos.get('current_price')}")
        else:
            print("응답 데이터:")
            print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(f"❌ 오류: {response.text}")
        
except Exception as e:
    print(f"❌ 예외 발생: {e}")

