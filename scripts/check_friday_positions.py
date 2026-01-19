"""금요일 매수된 포지션의 전략 확인"""
import sys
import os
import io
from datetime import datetime, timedelta

# 프로젝트 루트를 Python 경로에 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from core.models import get_db, Position, PendingBuySignal, StrategySignal

def get_friday_date():
    """가장 최근 금요일 날짜 반환"""
    today = datetime.now()
    # 금요일 찾기 (월요일=0, 금요일=4)
    days_since_friday = (today.weekday() - 4) % 7
    if days_since_friday == 0 and today.hour < 9:  # 오늘 금요일이지만 아직 장 시작 전
        days_since_friday = 7
    friday = today - timedelta(days=days_since_friday)
    return friday.date()

def main():
    print("=" * 60)
    print("금요일 매수된 포지션의 전략 확인")
    print("=" * 60)
    print()
    
    friday_date = get_friday_date()
    print(f"확인 기간: {friday_date} (금요일)")
    print()
    
    for db in get_db():
        session = db
        
        # 금요일에 매수된 포지션 조회
        friday_start = datetime.combine(friday_date, datetime.min.time())
        friday_end = friday_start + timedelta(days=1)
        
        positions = session.query(Position).filter(
            Position.buy_time >= friday_start,
            Position.buy_time < friday_end
        ).order_by(Position.buy_time).all()
        
        if not positions:
            print("금요일에 매수된 포지션이 없습니다.")
            return
        
        print(f"총 {len(positions)}개 포지션 발견")
        print()
        
        for pos in positions:
            print(f"📊 {pos.stock_name} ({pos.stock_code})")
            print(f"   매수 시간: {pos.buy_time}")
            print(f"   매수가: {pos.buy_price:,}원")
            print(f"   수량: {pos.buy_quantity}주")
            print(f"   상태: {pos.status}")
            
            # 신호 정보 확인
            if pos.signal_id:
                # PendingBuySignal 확인
                pending_signal = session.query(PendingBuySignal).filter(
                    PendingBuySignal.id == pos.signal_id
                ).first()
                
                if pending_signal:
                    print(f"   신호 타입: {pending_signal.signal_type}")
                    print(f"   조건식 ID: {pending_signal.condition_id}")
                    
                    # 조건식 이름 확인
                    if pending_signal.condition_id:
                        from core.models import AutoTradeCondition
                        condition = session.query(AutoTradeCondition).filter(
                            AutoTradeCondition.id == pending_signal.condition_id
                        ).first()
                        if condition:
                            print(f"   조건식 이름: {condition.condition_name}")
                
                # StrategySignal 확인
                strategy_signal = session.query(StrategySignal).filter(
                    StrategySignal.id == pos.signal_id
                ).first()
                
                if strategy_signal:
                    print(f"   전략 신호 ID: {strategy_signal.id}")
                    print(f"   전략 ID: {strategy_signal.strategy_id}")
                    print(f"   신호 타입: {strategy_signal.signal_type}")
            
            print()
        
        # 현재 활성화된 전략 확인
        print("=" * 60)
        print("현재 활성화된 전략 매매")
        print("=" * 60)
        print()
        
        # 조건식 모니터링 확인
        from core.models import AutoTradeCondition
        active_conditions = session.query(AutoTradeCondition).filter(
            AutoTradeCondition.is_enabled == True
        ).all()
        
        print(f"활성화된 조건식: {len(active_conditions)}개")
        for cond in active_conditions:
            print(f"  - {cond.condition_name} (ID: {cond.id})")
        print()
        
        # 전략 매매 확인
        from core.models import TradingStrategy
        active_strategies = session.query(TradingStrategy).filter(
            TradingStrategy.is_enabled == True
        ).all()
        
        print(f"활성화된 전략: {len(active_strategies)}개")
        for strat in active_strategies:
            print(f"  - {strat.strategy_name} ({strat.strategy_type})")
        
        break

if __name__ == "__main__":
    main()

