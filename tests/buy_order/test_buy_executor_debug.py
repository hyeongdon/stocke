"""
매수 주문 실행기 디버깅용 스크립트
브레이크포인트를 찍고 단계별로 실행을 추적할 수 있습니다
"""
import asyncio
import sys
from datetime import datetime

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, '.')

from managers.buy_order_executor import BuyOrderExecutor
from core.models import get_db, PendingBuySignal, AutoTradeSettings
from sqlalchemy.orm import Session

async def test_buy_executor():
    """매수 주문 실행기 테스트 - 브레이크포인트 여기에 찍으세요!"""
    print("=" * 60)
    print("🔍 매수 주문 실행기 디버깅 시작")
    print("=" * 60)
    
    # 1. BuyOrderExecutor 인스턴스 생성
    print("\n[1단계] BuyOrderExecutor 인스턴스 생성")
    executor = BuyOrderExecutor()
    
    # 브레이크포인트 1: 여기서 멈추고 executor 객체 확인
    print(f"   - 인스턴스 생성 완료: {executor}")
    
    # 2. 자동매매 설정 로드
    print("\n[2단계] 자동매매 설정 로드")
    await executor._load_auto_trade_settings()
    
    # 브레이크포인트 2: 설정 확인
    if executor.auto_trade_settings:
        print(f"   - 자동매매 활성화: {executor.auto_trade_settings.is_enabled}")
        print(f"   - 최대 투자금액: {executor.auto_trade_settings.max_invest_amount:,}원")
        print(f"   - 손절률: {executor.auto_trade_settings.stop_loss_rate}%")
        print(f"   - 익절률: {executor.auto_trade_settings.take_profit_rate}%")
    else:
        print("   - ⚠️ 자동매매 설정이 없습니다")
    
    # 3. PENDING 신호 조회
    print("\n[3단계] PENDING 신호 조회")
    pending_signals = await executor._get_pending_signals()
    
    # 브레이크포인트 3: 신호 목록 확인
    print(f"   - 발견된 신호 개수: {len(pending_signals)}")
    for idx, signal in enumerate(pending_signals, 1):
        print(f"   [{idx}] {signal.stock_name}({signal.stock_code}) - 상태: {signal.status}")
    
    if not pending_signals:
        print("\n⚠️ 처리할 신호가 없습니다. 테스트 종료")
        return
    
    # 4. 첫 번째 신호만 처리 (테스트용)
    print("\n[4단계] 첫 번째 신호 처리")
    test_signal = pending_signals[0]
    print(f"   - 테스트 대상: {test_signal.stock_name}({test_signal.stock_code})")
    
    # 4-1. 매수 전 검증
    print("\n[4-1단계] 매수 전 검증")
    validation_result = await executor._validate_buy_conditions(test_signal)
    
    # 브레이크포인트 4: 검증 결과 확인
    print(f"   - 검증 결과: {validation_result}")
    if not validation_result["valid"]:
        print(f"   - ❌ 검증 실패: {validation_result['reason']}")
        return
    print("   - ✅ 검증 통과")
    
    # 4-2. 현재가 조회
    print("\n[4-2단계] 현재가 조회")
    current_price = await executor._get_current_price(test_signal.stock_code)
    
    # 브레이크포인트 5: 현재가 확인
    print(f"   - 현재가: {current_price:,}원" if current_price else "   - ❌ 현재가 조회 실패")
    if not current_price:
        return
    
    # 4-3. 매수 수량 계산
    print("\n[4-3단계] 매수 수량 계산")
    quantity = await executor._calculate_buy_quantity(test_signal.stock_code, current_price)
    
    # 브레이크포인트 6: 수량 확인
    print(f"   - 매수 수량: {quantity}주")
    print(f"   - 총 매수금액: {current_price * quantity:,}원")
    if quantity < 1:
        print("   - ❌ 매수 수량 부족")
        return
    
    # 4-4. 매수 주문 실행 여부 확인
    print("\n[4-4단계] 매수 주문 실행")
    print("   ⚠️ 실제 주문은 실행하지 않습니다 (DRY-RUN)")
    print(f"   - 주문 정보:")
    print(f"     종목: {test_signal.stock_name}({test_signal.stock_code})")
    print(f"     가격: {current_price:,}원")
    print(f"     수량: {quantity}주")
    print(f"     금액: {current_price * quantity:,}원")
    
    # 브레이크포인트 7: 여기서 확인
    print("\n✅ 디버깅 완료!")
    print("\n💡 실제 주문을 실행하려면 executor._execute_buy_order()를 호출하세요")

def main():
    """메인 함수"""
    try:
        print("\n📌 브레이크포인트 추천 위치:")
        print("   - 51줄: executor 인스턴스 생성 후")
        print("   - 60줄: 자동매매 설정 로드 후")
        print("   - 70줄: PENDING 신호 조회 후")
        print("   - 84줄: 매수 전 검증 결과 확인")
        print("   - 94줄: 현재가 조회 결과 확인")
        print("   - 104줄: 매수 수량 계산 결과 확인")
        print("   - 118줄: 최종 주문 정보 확인")
        print("\n" + "=" * 60)
        
        # 비동기 실행
        asyncio.run(test_buy_executor())
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 사용자가 중단했습니다")
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

