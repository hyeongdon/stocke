"""조건식 종목 선택 기준 설명"""
import sys
import io
import requests
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def main():
    print("=" * 60)
    print("조건식 모니터링 종목 선택 기준 설명")
    print("=" * 60)
    print()
    
    print("📋 조건식 모니터링 동작 방식:")
    print()
    print("1️⃣  조건식 검색 (10분 주기)")
    print("   - 키움 API에서 활성화된 조건식 목록 조회")
    print("   - 각 조건식에 대해 종목 검색 실행")
    print("   - 조건식 검색 결과: 여러 종목이 나올 수 있음")
    print()
    print("2️⃣  종목 선택 기준")
    print("   - Config.MAX_SIGNALS_PER_CONDITION_SCAN 설정값 확인")
    print("   - 기본값: 1개 (조건식당 최대 1개 종목만 신호 생성)")
    print("   - 검색 결과 중 앞에서부터 최대 N개만 선택")
    print("   - 코드: results[:max_signals]")
    print()
    print("3️⃣  신호 생성")
    print("   - 선택된 종목에 대해 PENDING 신호 생성")
    print("   - signal_type: 'condition'")
    print("   - 조건식 ID와 종목 정보 저장")
    print()
    print("4️⃣  매수 주문")
    print("   - BuyOrderExecutor가 PENDING 신호를 처리")
    print("   - 자동매매가 활성화되어 있어야 함")
    print()
    print("=" * 60)
    print("금요일 매수 종목 분석")
    print("=" * 60)
    print()
    
    try:
        # 금요일 포지션 확인
        friday_date = datetime(2026, 1, 16).date()
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
                
                # 조건식별 그룹화
                condition_groups = {}
                for pos in friday_positions:
                    signal_id = pos.get('signal_id')
                    if signal_id and signal_id in signal_map:
                        signal = signal_map[signal_id]
                        condition_id = signal.get('condition_id')
                        if condition_id not in condition_groups:
                            condition_groups[condition_id] = []
                        condition_groups[condition_id].append(pos)
                
                print("조건식별 매수 종목:")
                for condition_id, pos_list in condition_groups.items():
                    print(f"  조건식 ID {condition_id}: {len(pos_list)}개 종목")
                    for pos in pos_list:
                        print(f"    - {pos.get('stock_name')} ({pos.get('stock_code')})")
                print()
                
                # 조건식 이름 확인
                response = requests.get("http://localhost:8000/conditions/", timeout=5)
                if response.status_code == 200:
                    conditions = response.json()
                    if isinstance(conditions, list):
                        condition_map = {}
                        for cond in conditions:
                            cond_id = cond.get('id')
                            condition_map[cond_id] = cond.get('condition_name')
                        
                        print("조건식 이름:")
                        for condition_id, pos_list in condition_groups.items():
                            cond_name = condition_map.get(condition_id, f"조건식 ID {condition_id}")
                            print(f"  조건식 ID {condition_id}: {cond_name} → {len(pos_list)}개 종목 매수")
                        print()
        
        print("=" * 60)
        print("종목 선택 기준 요약")
        print("=" * 60)
        print()
        print("✅ 현재 로직:")
        print("   1. 조건식 검색 결과에서 최대 1개 종목만 선택 (기본값)")
        print("   2. 검색 결과의 첫 번째 종목부터 순서대로 선택")
        print("   3. 키움 API가 반환하는 종목 순서에 따라 결정됨")
        print()
        print("💡 5개 종목이 매수된 이유:")
        print("   - 5개의 조건식이 각각 1개씩 종목을 선택")
        print("   - 또는 MAX_SIGNALS_PER_CONDITION_SCAN 설정값이 변경되었을 수 있음")
        print()
        print("🔧 설정 변경 방법:")
        print("   - core/config.py에서 MAX_SIGNALS_PER_CONDITION_SCAN 값 수정")
        print("   - 값이 1이면 조건식당 1개, 5면 조건식당 5개까지 선택 가능")
        print()
        
    except requests.exceptions.ConnectionError:
        print("❌ 서버에 연결할 수 없습니다.")
    except Exception as e:
        print(f"❌ 오류: {e}")

if __name__ == "__main__":
    main()

