"""
테스트 데이터 정리 스크립트
오늘(2026-01-16) 이전의 모든 테스트 데이터를 삭제합니다.
"""
import sys
import io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from datetime import date
from core.models import get_db, PendingBuySignal, Position, SellOrder

def cleanup_test_data():
    today = date.today()
    print(f"=== 테스트 데이터 정리 시작 ===")
    print(f"기준일: {today}")
    print(f"삭제 대상: {today} 이전 데이터\n")
    
    for db in get_db():
        try:
            # 1. PendingBuySignal 정리
            old_signals = db.query(PendingBuySignal).filter(
                PendingBuySignal.detected_date < today
            ).all()
            
            print(f"[1] PendingBuySignal")
            print(f"   - 삭제 대상: {len(old_signals)}개")
            
            if old_signals:
                for signal in old_signals:
                    print(f"      삭제: ID={signal.id}, {signal.stock_name}({signal.stock_code}), 날짜={signal.detected_date}")
                    db.delete(signal)
                
                db.commit()
                print(f"   [OK] {len(old_signals)}개 삭제 완료\n")
            else:
                print(f"   [INFO] 삭제할 데이터 없음\n")
            
            # 2. Position 정리 (created_at이 없으므로 id 기반으로 판단 - 조심스럽게)
            # 오래된 포지션들 확인
            positions = db.query(Position).all()
            print(f"[2] Position")
            print(f"   - 총 포지션: {len(positions)}개")
            
            # 사용자에게 확인 받기 - 모두 테스트 데이터인지
            if positions:
                print(f"   [WARNING] Position 데이터는 수동으로 확인이 필요합니다.")
                print(f"   현재 Position 목록:")
                for pos in positions:
                    print(f"      ID={pos.id}, {pos.stock_name}({pos.stock_code}), 상태={pos.status}")
                
                response = input("\n   모든 Position을 삭제하시겠습니까? (yes/no): ")
                if response.lower() == 'yes':
                    for pos in positions:
                        db.delete(pos)
                    db.commit()
                    print(f"   [OK] {len(positions)}개 삭제 완료\n")
                else:
                    print(f"   [INFO] Position 삭제 건너뜀\n")
            else:
                print(f"   [INFO] Position 데이터 없음\n")
            
            # 3. SellOrder 정리
            sell_orders = db.query(SellOrder).all()
            print(f"[3] SellOrder")
            print(f"   - 총 매도 주문: {len(sell_orders)}개")
            
            if sell_orders:
                for order in sell_orders:
                    print(f"      삭제: ID={order.id}, {order.stock_name}, 사유={order.sell_reason}")
                    db.delete(order)
                
                db.commit()
                print(f"   [OK] {len(sell_orders)}개 삭제 완료\n")
            else:
                print(f"   [INFO] 매도 주문 데이터 없음\n")
            
            break
            
        except Exception as e:
            print(f"[ERROR] 오류 발생: {e}")
            db.rollback()
            return False
    
    print("=" * 50)
    print("[OK] 테스트 데이터 정리 완료!")
    return True

if __name__ == "__main__":
    cleanup_test_data()

