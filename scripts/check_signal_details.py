"""신호 상세 정보 확인"""
import sys
import io
import requests
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def main():
    print("=" * 60)
    print("금요일 매수 종목의 신호 상세 정보")
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
        
        print(f"금요일 매수된 포지션: {len(friday_positions)}개")
        print()
        
        # 신호 정보 조회
        response = requests.get("http://localhost:8000/signals/pending?status=ALL&skip_price=true", timeout=5)
        if response.status_code != 200:
            print("신호 정보를 가져올 수 없습니다.")
            return
        
        signals_data = response.json()
        signals = signals_data.get('items', []) if isinstance(signals_data, dict) else signals_data
        
        # 신호 맵 생성
        signal_map = {s.get('id'): s for s in signals}
        
        for pos in friday_positions:
            signal_id = pos.get('signal_id')
            condition_id = pos.get('condition_id')
            
            print(f"📊 {pos.get('stock_name')} ({pos.get('stock_code')})")
            print(f"   매수 시간: {pos.get('buy_time')}")
            print(f"   매수가: {pos.get('buy_price'):,}원")
            print(f"   수량: {pos.get('buy_quantity')}주")
            
            if signal_id and signal_id in signal_map:
                signal = signal_map[signal_id]
                signal_type = signal.get('signal_type', 'unknown')
                condition_id_from_signal = signal.get('condition_id')
                
                print(f"   신호 ID: {signal_id}")
                print(f"   신호 타입: {signal_type}")
                
                if signal_type == 'condition' or signal_type == 'reference':
                    print(f"   조건식 ID: {condition_id_from_signal}")
                    if condition_id_from_signal:
                        # 조건식 이름 조회
                        cond_response = requests.get(f"http://localhost:8000/monitoring/conditions/", timeout=5)
                        if cond_response.status_code == 200:
                            cond_data = cond_response.json()
                            conditions = cond_data.get('items', []) if isinstance(cond_data, dict) else cond_data
                            for cond in conditions:
                                if cond.get('id') == condition_id_from_signal:
                                    print(f"   조건식 이름: {cond.get('condition_name')}")
                                    break
                
                elif signal_type == 'strategy':
                    print(f"   전략 신호 (전략 ID 확인 필요)")
                
                if signal.get('target_price'):
                    print(f"   목표가: {signal.get('target_price'):,}원")
            else:
                print(f"   신호 ID: {signal_id} (신호 정보 없음)")
            
            print()
        
        # 현재 활성화된 전략 확인
        print("=" * 60)
        print("현재 시스템 상태")
        print("=" * 60)
        print()
        
        # 모니터링 상태
        try:
            response = requests.get("http://localhost:8000/monitoring/status", timeout=5)
            if response.status_code == 200:
                data = response.json()
                print(f"조건식 모니터링: {'✅ 활성화' if data.get('is_running') else '❌ 비활성화'}")
        except:
            pass
        
        try:
            response = requests.get("http://localhost:8000/strategy/status", timeout=5)
            if response.status_code == 200:
                data = response.json()
                print(f"전략 매매: {'✅ 활성화' if data.get('is_running') else '❌ 비활성화'}")
        except:
            pass
        
        print()
        print("=" * 60)
        print("전략 매매 종류")
        print("=" * 60)
        print()
        print("1. 조건식 모니터링 (ConditionMonitor)")
        print("   - signal_type: 'condition' 또는 'reference'")
        print("   - 10분 주기로 조건식 종목 검색")
        print("   - 기준봉 전략 적용 가능")
        print()
        print("2. 전략 매매 (StrategyManager)")
        print("   - signal_type: 'strategy'")
        print("   - 전략 타입: MOMENTUM, DISPARITY, BOLLINGER, RSI, ICHIMOKU, CHAIKIN")
        print("   - 1분 주기로 관심종목 모니터링")
        print()
        print("3. 스캘핑 전략 (ScalpingStrategyManager)")
        print("   - 30초 주기로 활성 종목 모니터링")
        print()
        
    except requests.exceptions.ConnectionError:
        print("❌ 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.")
    except Exception as e:
        print(f"❌ 오류: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

