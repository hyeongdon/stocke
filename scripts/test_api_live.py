"""실시간으로 API 응답 확인"""
import sys, io
import requests
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

url = "http://localhost:8000/signals/pending?status=ALL&skip_price=true"
print("📡 API 호출 중...")

try:
    response = requests.get(url, timeout=5)
    
    if response.status_code == 200:
        data = response.json()
        
        # Signal 22, 24, 25 확인
        target_signals = [s for s in data['items'] if s['id'] in [22, 24, 25]]
        
        print(f"\n✅ API 응답 성공 (상태 코드: {response.status_code})")
        print(f"총 Signal 개수: {len(data['items'])}")
        print(f"대상 Signal (22,24,25): {len(target_signals)}개\n")
        
        for signal in target_signals:
            print(f"[{signal['stock_name']}] ID={signal['id']}")
            print(f"  status: {signal['status']}")
            print(f"  position 존재: {'예' if 'position' in signal else '아니오'}")
            
            if 'position' in signal and signal['position']:
                pos = signal['position']
                print(f"  ✅ Position 데이터:")
                print(f"     - buy_price: {pos.get('buy_price'):,}원")
                print(f"     - buy_quantity: {pos.get('buy_quantity')}주")
                print(f"     - current_price: {pos.get('current_price'):,}원")
            else:
                print(f"  ❌ Position 데이터 없음!")
            print()
        
    else:
        print(f"❌ API 오류 (상태 코드: {response.status_code})")
        print(response.text)
        
except Exception as e:
    print(f"❌ 예외 발생: {e}")
    import traceback
    traceback.print_exc()

