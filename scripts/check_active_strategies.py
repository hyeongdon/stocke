"""현재 활성화된 전략 매매 확인"""
import sys
import io
import requests
import json
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def get_friday_date():
    """가장 최근 금요일 날짜 반환"""
    today = datetime.now()
    days_since_friday = (today.weekday() - 4) % 7
    if days_since_friday == 0 and today.hour < 9:
        days_since_friday = 7
    friday = today - timedelta(days=days_since_friday)
    return friday.date()

def main():
    print("=" * 60)
    print("금요일 매수 종목 및 활성 전략 확인")
    print("=" * 60)
    print()
    
    try:
        # 1. 금요일 매수된 포지션 확인
        friday_date = get_friday_date()
        print(f"확인 기간: {friday_date} (금요일)")
        print()
        
        response = requests.get("http://localhost:8000/positions/?status=ALL", timeout=5)
        if response.status_code == 200:
            data = response.json()
            positions = data.get('items', []) if isinstance(data, dict) else data
            
            friday_positions = []
            for pos in positions:
                buy_time_str = pos.get('buy_time')
                if buy_time_str:
                    buy_time = datetime.fromisoformat(buy_time_str.replace('Z', '+00:00'))
                    if buy_time.date() == friday_date:
                        friday_positions.append(pos)
            
            if friday_positions:
                print(f"금요일 매수된 포지션: {len(friday_positions)}개")
                print()
                for pos in friday_positions:
                    print(f"📊 {pos.get('stock_name')} ({pos.get('stock_code')})")
                    print(f"   매수 시간: {pos.get('buy_time')}")
                    print(f"   매수가: {pos.get('buy_price'):,}원")
                    print(f"   수량: {pos.get('buy_quantity')}주")
                    print(f"   신호 ID: {pos.get('signal_id')}")
                    print(f"   조건식 ID: {pos.get('condition_id')}")
                    print()
            else:
                print("금요일에 매수된 포지션이 없습니다.")
                print()
        
        # 2. 활성화된 조건식 확인
        print("=" * 60)
        print("활성화된 조건식 모니터링")
        print("=" * 60)
        print()
        
        response = requests.get("http://localhost:8000/monitoring/conditions/", timeout=5)
        if response.status_code == 200:
            data = response.json()
            conditions = data.get('items', []) if isinstance(data, dict) else data
            
            active_conditions = [c for c in conditions if c.get('is_enabled')]
            print(f"활성화된 조건식: {len(active_conditions)}개")
            for cond in active_conditions:
                print(f"  ✅ {cond.get('condition_name')} (ID: {cond.get('id')})")
            print()
        else:
            print("조건식 정보를 가져올 수 없습니다.")
            print()
        
        # 3. 활성화된 전략 확인
        print("=" * 60)
        print("활성화된 전략 매매")
        print("=" * 60)
        print()
        
        response = requests.get("http://localhost:8000/strategies/", timeout=5)
        if response.status_code == 200:
            data = response.json()
            strategies = data.get('items', []) if isinstance(data, dict) else data
            
            active_strategies = [s for s in strategies if s.get('is_enabled')]
            print(f"활성화된 전략: {len(active_strategies)}개")
            for strat in active_strategies:
                print(f"  ✅ {strat.get('strategy_name')} ({strat.get('strategy_type')})")
                params = strat.get('parameters', {})
                if params:
                    print(f"     파라미터: {json.dumps(params, ensure_ascii=False)}")
            print()
        else:
            print("전략 정보를 가져올 수 없습니다.")
            print()
        
        # 4. 모니터링 상태 확인
        print("=" * 60)
        print("모니터링 상태")
        print("=" * 60)
        print()
        
        response = requests.get("http://localhost:8000/monitoring/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"조건식 모니터링: {'✅ 활성화' if data.get('is_running') else '❌ 비활성화'}")
            print()
        
        response = requests.get("http://localhost:8000/strategy/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"전략 매매: {'✅ 활성화' if data.get('is_running') else '❌ 비활성화'}")
            print()
        
    except requests.exceptions.ConnectionError:
        print("❌ 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.")
    except Exception as e:
        print(f"❌ 오류: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

