"""
손절/익절 모니터링 디버깅용 스크립트
브레이크포인트를 찍고 단계별로 실행을 추적할 수 있습니다
"""
import asyncio
import sys
from datetime import datetime

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, '.')

from managers.stop_loss_manager import StopLossManager
from core.models import get_db, Position, AutoTradeSettings
from sqlalchemy.orm import Session

async def test_stop_loss_manager():
    """손절/익절 모니터링 디버깅 - 브레이크포인트 여기에 찍으세요!"""
    print("=" * 60)
    print("🔍 손절/익절 모니터링 디버깅 시작")
    print("=" * 60)
    
    # 1. StopLossManager 인스턴스 생성
    print("\n[1단계] StopLossManager 인스턴스 생성")
    manager = StopLossManager()
    
    # 브레이크포인트 1: 여기서 멈추고 manager 객체 확인
    print(f"   - 인스턴스 생성 완료: {manager}")
    print(f"   - 모니터링 간격: {manager.monitoring_interval}초")
    
    # 2. 자동매매 설정 로드
    print("\n[2단계] 자동매매 설정 로드")
    await manager._load_auto_trade_settings()
    
    # 브레이크포인트 2: 설정 확인
    if manager.auto_trade_settings:
        print(f"   - 자동매매 활성화: {manager.auto_trade_settings.is_enabled}")
        print(f"   - 손절률: {manager.auto_trade_settings.stop_loss_rate}%")
        print(f"   - 익절률: {manager.auto_trade_settings.take_profit_rate}%")
    else:
        print("   - ⚠️ 자동매매 설정이 없습니다")
    
    # 3. 활성 포지션 조회
    print("\n[3단계] 활성 포지션 조회 (HOLDING 상태)")
    positions = await manager._get_active_positions()
    
    # 브레이크포인트 3: 포지션 목록 확인
    print(f"   - 발견된 포지션 개수: {len(positions)}")
    for idx, pos in enumerate(positions, 1):
        print(f"   [{idx}] {pos.stock_name}({pos.stock_code})")
        print(f"       매수가: {pos.buy_price:,}원 × {pos.buy_quantity}주")
        print(f"       상태: {pos.status}")
    
    if not positions:
        print("\n⚠️ 모니터링할 포지션이 없습니다. 테스트 종료")
        return
    
    # 4. 첫 번째 포지션만 확인 (테스트용)
    print("\n[4단계] 첫 번째 포지션 손절/익절 확인")
    test_position = positions[0]
    print(f"   - 테스트 대상: {test_position.stock_name}({test_position.stock_code})")
    
    # 4-1. 현재가 조회
    print("\n[4-1단계] 현재가 조회")
    current_price = await manager._get_current_price(test_position.stock_code)
    
    # 브레이크포인트 4: 현재가 확인
    print(f"   - 현재가: {current_price:,}원" if current_price else "   - ❌ 현재가 조회 실패")
    if not current_price:
        return
    
    # 4-2. 손익 계산
    print("\n[4-2단계] 손익 계산")
    profit_loss = (current_price - test_position.buy_price) * test_position.buy_quantity
    profit_loss_rate = (current_price - test_position.buy_price) / test_position.buy_price * 100
    
    # 브레이크포인트 5: 손익 확인
    print(f"   - 매수가: {test_position.buy_price:,}원")
    print(f"   - 현재가: {current_price:,}원")
    print(f"   - 손익금액: {profit_loss:+,}원")
    print(f"   - 손익률: {profit_loss_rate:+.2f}%")
    
    # 4-3. 손절/익절 판단
    print("\n[4-3단계] 손절/익절 판단")
    
    if not manager.auto_trade_settings:
        print("   - ⚠️ 자동매매 설정이 없어 판단 불가")
        return
    
    stop_loss_rate = manager.auto_trade_settings.stop_loss_rate
    take_profit_rate = manager.auto_trade_settings.take_profit_rate
    
    print(f"   - 손절 기준: -{stop_loss_rate}%")
    print(f"   - 익절 기준: +{take_profit_rate}%")
    
    # 브레이크포인트 6: 판단 결과 확인
    if profit_loss_rate <= -stop_loss_rate:
        print(f"   - 🔴 손절 조건 충족! (현재: {profit_loss_rate:.2f}% <= -{stop_loss_rate}%)")
        sell_reason = "손절"
    elif profit_loss_rate >= take_profit_rate:
        print(f"   - 🟢 익절 조건 충족! (현재: {profit_loss_rate:.2f}% >= +{take_profit_rate}%)")
        sell_reason = "익절"
    else:
        print(f"   - ⚪ 보유 유지 ({profit_loss_rate:+.2f}%)")
        sell_reason = None
    
    # 4-4. 매도 주문 실행 여부
    if sell_reason:
        print(f"\n[4-4단계] 매도 주문 ({sell_reason})")
        print("   ⚠️ 실제 주문은 실행하지 않습니다 (DRY-RUN)")
        print(f"   - 주문 정보:")
        print(f"     종목: {test_position.stock_name}({test_position.stock_code})")
        print(f"     가격: {current_price:,}원")
        print(f"     수량: {test_position.buy_quantity}주")
        print(f"     예상 손익: {profit_loss:+,}원 ({profit_loss_rate:+.2f}%)")
        print(f"     사유: {sell_reason}")
    else:
        print("\n   ✅ 매도 조건 미충족 - 보유 유지")
    
    # 브레이크포인트 7: 여기서 확인
    print("\n✅ 디버깅 완료!")
    print("\n💡 실제 매도를 실행하려면 manager._execute_sell_order()를 호출하세요")

def main():
    """메인 함수"""
    try:
        print("\n📌 브레이크포인트 추천 위치:")
        print("   - 27줄: manager 인스턴스 생성 후")
        print("   - 36줄: 자동매매 설정 로드 후")
        print("   - 46줄: 활성 포지션 조회 후")
        print("   - 65줄: 현재가 조회 결과 확인")
        print("   - 76줄: 손익 계산 결과 확인")
        print("   - 91줄: 손절/익절 판단 결과 확인")
        print("   - 106줄: 매도 주문 정보 확인")
        print("\n" + "=" * 60)
        
        # 비동기 실행
        asyncio.run(test_stop_loss_manager())
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 사용자가 중단했습니다")
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

