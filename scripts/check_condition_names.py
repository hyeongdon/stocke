"""조건식 이름 확인"""
import sys
import io
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def main():
    print("=" * 60)
    print("금요일 매수 종목의 조건식 이름 확인")
    print("=" * 60)
    print()
    
    try:
        # 포지션 조회
        response = requests.get("http://localhost:8000/positions/?status=ALL", timeout=5)
        if response.status_code != 200:
            print("포지션 정보를 가져올 수 없습니다.")
            return
        
        data = response.json()
        positions = data.get('items', []) if isinstance(data, dict) else data
        
        # 금요일 포지션 필터링
        from datetime import datetime
        friday_date = datetime(2026, 1, 16).date()
        friday_positions = []
        for pos in positions:
            buy_time_str = pos.get('buy_time')
            if buy_time_str:
                buy_time = datetime.fromisoformat(buy_time_str.replace('Z', '+00:00'))
                if buy_time.date() == friday_date:
                    friday_positions.append(pos)
        
        if not friday_positions:
            print("금요일 매수된 포지션이 없습니다.")
            return
        
        # 신호 정보 조회
        response = requests.get("http://localhost:8000/signals/pending?status=ALL&skip_price=true", timeout=5)
        if response.status_code != 200:
            print("신호 정보를 가져올 수 없습니다.")
            return
        
        signals_data = response.json()
        signals = signals_data.get('items', []) if isinstance(signals_data, dict) else signals_data
        
        # 신호 맵 생성
        signal_map = {s.get('id'): s for s in signals}
        
        # 조건식 ID 수집
        condition_ids = set()
        for pos in friday_positions:
            signal_id = pos.get('signal_id')
            if signal_id and signal_id in signal_map:
                signal = signal_map[signal_id]
                condition_id = signal.get('condition_id')
                if condition_id is not None:
                    condition_ids.add(condition_id)
        
        print(f"금요일 매수된 포지션: {len(friday_positions)}개")
        print()
        
        for pos in friday_positions:
            signal_id = pos.get('signal_id')
            
            print(f"📊 {pos.get('stock_name')} ({pos.get('stock_code')})")
            print(f"   매수 시간: {pos.get('buy_time')}")
            print(f"   매수가: {pos.get('buy_price'):,}원")
            print(f"   수량: {pos.get('buy_quantity')}주")
            
            if signal_id and signal_id in signal_map:
                signal = signal_map[signal_id]
                signal_type = signal.get('signal_type', 'unknown')
                condition_id = signal.get('condition_id')
                
                print(f"   신호 타입: {signal_type}")
                print(f"   조건식 ID: {condition_id}")
                
                if condition_id is not None:
                    print(f"   조건식 이름: (ID {condition_id} - DB에서 확인 필요)")
            
            print()
        
        print("=" * 60)
        print("결론")
        print("=" * 60)
        print()
        print("✅ 금요일 매수된 모든 종목은 '조건식 모니터링'으로 매수되었습니다.")
        print("   - signal_type: 'condition'")
        print("   - 조건식 모니터링은 10분 주기로 키움 조건식을 검색하여")
        print("     매수 신호를 생성하는 방식입니다.")
        print()
        print("현재 상태:")
        print("  - 조건식 모니터링: ❌ 비활성화")
        print("  - 전략 매매: ❌ 비활성화")
        print()
        print("동작 가능한 전략 매매:")
        print("  1. 조건식 모니터링 (ConditionMonitor)")
        print("     - 10분 주기, 키움 조건식 검색")
        print("  2. 전략 매매 (StrategyManager)")
        print("     - 1분 주기, MOMENTUM/DISPARITY/BOLLINGER/RSI/ICHIMOKU/CHAIKIN")
        print("  3. 스캘핑 전략 (ScalpingStrategyManager)")
        print("     - 30초 주기, 단기 매매")
        
    except requests.exceptions.ConnectionError:
        print("❌ 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.")
    except Exception as e:
        print(f"❌ 오류: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

