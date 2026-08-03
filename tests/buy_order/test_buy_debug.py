"""
매수 주문 디버깅 스크립트
"""
import asyncio
import sys
import io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from managers.buy_order_executor import buy_order_executor
from core.models import get_db, PendingBuySignal
from managers.signal_manager import SignalStatus

async def main():
    print("=== 매수 주문 디버깅 ===\n")
    
    # 1. 자동매매 설정 로드
    print("[1] 자동매매 설정 로드")
    await buy_order_executor._load_auto_trade_settings()
    if buy_order_executor.auto_trade_settings:
        print(f"   ✅ 설정 로드 성공")
        print(f"   - 활성화: {buy_order_executor.auto_trade_settings.is_enabled}")
        print(f"   - 최대 투자금액: {buy_order_executor.auto_trade_settings.max_invest_amount:,}원")
    else:
        print(f"   ❌ 설정 없음")
        return
    
    # 2. PENDING 신호 조회
    print("\n[2] PENDING 신호 조회")
    db = next(get_db())
    signals = db.query(PendingBuySignal).filter(
        PendingBuySignal.status == SignalStatus.PENDING.value
    ).all()
    
    if not signals:
        print("   ❌ PENDING 신호 없음")
        return
    
    signal = signals[0]
    print(f"   ✅ 신호 발견: ID={signal.id}, {signal.stock_name}({signal.stock_code})")
    
    # 3. 매수 조건 검증
    print("\n[3] 매수 조건 검증")
    validation = await buy_order_executor._validate_buy_conditions(signal)
    print(f"   - 검증 결과: {validation}")
    
    if not validation["valid"]:
        print(f"   ❌ 검증 실패: {validation['reason']}")
        return
    print(f"   ✅ 검증 통과")
    
    # 4. 현재가 조회
    print("\n[4] 현재가 조회")
    current_price = await buy_order_executor._get_current_price(signal.stock_code)
    if current_price:
        print(f"   ✅ 현재가: {current_price:,}원")
    else:
        print(f"   ❌ 현재가 조회 실패")
        return
    
    # 5. 매수 수량 계산
    print("\n[5] 매수 수량 계산")
    quantity, buy_amount = await buy_order_executor._calculate_buy_quantity(signal.stock_code, current_price)
    print(f"   - 수량: {quantity}주 (예산 {buy_amount:,}원)")
    print(f"   - 총액: {current_price * quantity:,}원")
    
    if quantity < 1:
        if buy_amount > 0 and current_price > 0:
            print(f"   ❌ 고가주라 1주도 매수 불가 (예산 {buy_amount:,}원 < 1주 {current_price:,}원)")
        else:
            print(f"   ❌ 매수 가능 금액/현재가 없음")
        return
    print(f"   ✅ 매수 가능")
    
    # 6. 실제 주문 실행은 건너뜀 (테스트이므로)
    print("\n[6] 주문 실행 (DRY-RUN)")
    print(f"   - 종목: {signal.stock_name}({signal.stock_code})")
    print(f"   - 가격: {current_price:,}원")
    print(f"   - 수량: {quantity}주")
    print(f"   - 총액: {current_price * quantity:,}원")
    print(f"   💡 실제 주문은 실행하지 않았습니다 (테스트 모드)")

if __name__ == "__main__":
    asyncio.run(main())

