"""모든 전략 매매 종류 및 상태 확인"""
import sys
import io
import requests
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def main():
    print("=" * 60)
    print("금요일 매수 종목 및 전체 전략 매매 상태")
    print("=" * 60)
    print()
    
    try:
        # 1. 금요일 매수 종목 확인
        friday_date = datetime(2026, 1, 16).date()
        print(f"📅 확인 기간: {friday_date} (금요일)")
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
                print(f"✅ 금요일 매수된 포지션: {len(friday_positions)}개")
                print()
                
                # 신호 정보 조회
                response = requests.get("http://localhost:8000/signals/pending?status=ALL&skip_price=true", timeout=5)
                signals = []
                if response.status_code == 200:
                    signals_data = response.json()
                    signals = signals_data.get('items', []) if isinstance(signals_data, dict) else signals_data
                
                signal_map = {s.get('id'): s for s in signals}
                
                for pos in friday_positions:
                    signal_id = pos.get('signal_id')
                    signal_type = "알 수 없음"
                    condition_id = None
                    
                    if signal_id and signal_id in signal_map:
                        signal = signal_map[signal_id]
                        signal_type = signal.get('signal_type', 'unknown')
                        condition_id = signal.get('condition_id')
                    
                    print(f"📊 {pos.get('stock_name')} ({pos.get('stock_code')})")
                    print(f"   매수 시간: {pos.get('buy_time')}")
                    print(f"   신호 타입: {signal_type}")
                    if condition_id is not None:
                        print(f"   조건식 ID: {condition_id}")
                    print()
            else:
                print("금요일에 매수된 포지션이 없습니다.")
                print()
        
        # 2. 조건식 목록 확인
        print("=" * 60)
        print("키움 조건식 목록")
        print("=" * 60)
        print()
        
        response = requests.get("http://localhost:8000/conditions/", timeout=5)
        if response.status_code == 200:
            conditions = response.json()
            if isinstance(conditions, list):
                print(f"총 {len(conditions)}개 조건식")
                for i, cond in enumerate(conditions, 1):
                    enabled = "✅" if cond.get('is_enabled') else "❌"
                    print(f"  {enabled} {i}. {cond.get('condition_name')} (ID: {cond.get('id')}, API ID: {cond.get('api_id')})")
                print()
            else:
                print("조건식 데이터 형식 오류")
        else:
            print("조건식 목록을 가져올 수 없습니다.")
            print()
        
        # 3. 전략 매매 목록 확인
        print("=" * 60)
        print("전략 매매 목록")
        print("=" * 60)
        print()
        
        response = requests.get("http://localhost:8000/strategies/", timeout=5)
        if response.status_code == 200:
            data = response.json()
            strategies = data.get('items', []) if isinstance(data, dict) else data
            
            print(f"총 {len(strategies)}개 전략")
            for strat in strategies:
                enabled = "✅" if strat.get('is_enabled') else "❌"
                print(f"  {enabled} {strat.get('strategy_name')} ({strat.get('strategy_type')})")
            print()
        else:
            print("전략 목록을 가져올 수 없습니다.")
            print()
        
        # 4. 모니터링 상태 확인
        print("=" * 60)
        print("현재 모니터링 상태")
        print("=" * 60)
        print()
        
        try:
            response = requests.get("http://localhost:8000/monitoring/status", timeout=5)
            if response.status_code == 200:
                data = response.json()
                status = "✅ 활성화" if data.get('is_running') else "❌ 비활성화"
                print(f"조건식 모니터링: {status}")
        except:
            print("조건식 모니터링 상태 확인 실패")
        
        try:
            response = requests.get("http://localhost:8000/strategy/status", timeout=5)
            if response.status_code == 200:
                data = response.json()
                status = "✅ 활성화" if data.get('is_running') else "❌ 비활성화"
                print(f"전략 매매: {status}")
        except:
            print("전략 매매 상태 확인 실패")
        
        print()
        print("=" * 60)
        print("전략 매매 종류 요약")
        print("=" * 60)
        print()
        print("1️⃣  조건식 모니터링 (ConditionMonitor)")
        print("   - 신호 타입: 'condition' 또는 'reference'")
        print("   - 주기: 10분")
        print("   - 방식: 키움 조건식 검색 → 기준봉 전략 적용 → 매수 신호 생성")
        print("   - 금요일 매수: ✅ 이 방식으로 매수됨")
        print()
        print("2️⃣  전략 매매 (StrategyManager)")
        print("   - 신호 타입: 'strategy'")
        print("   - 주기: 1분")
        print("   - 전략 종류:")
        print("     • MOMENTUM (모멘텀)")
        print("     • DISPARITY (이격도)")
        print("     • BOLLINGER (볼린저 밴드)")
        print("     • RSI (상대강도지수)")
        print("     • ICHIMOKU (일목균형표)")
        print("     • CHAIKIN (차이킨 오실레이터)")
        print("   - 방식: 관심종목 모니터링 → 차트 분석 → 전략별 신호 생성")
        print()
        print("3️⃣  스캘핑 전략 (ScalpingStrategyManager)")
        print("   - 주기: 30초")
        print("   - 방식: 활성 종목 단기 매매")
        print()
        
    except requests.exceptions.ConnectionError:
        print("❌ 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.")
    except Exception as e:
        print(f"❌ 오류: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

