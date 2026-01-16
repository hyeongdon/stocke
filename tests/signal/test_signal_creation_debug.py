"""
신호 생성 프로세스 디버깅용 스크립트
조건식 검색 → 신호 생성까지의 전체 흐름을 추적합니다
"""
import asyncio
import sys
from datetime import datetime

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, '.')

from condition_monitor import condition_monitor
from signal_manager import signal_manager, SignalType
from kiwoom_api import KiwoomAPI
from models import get_db, PendingBuySignal, AutoTradeCondition
from sqlalchemy.orm import Session

async def test_signal_creation():
    """신호 생성 프로세스 디버깅 - 브레이크포인트 여기에 찍으세요!"""
    print("=" * 60)
    print("🔍 신호 생성 프로세스 디버깅 시작")
    print("=" * 60)
    
    # 1. 등록된 조건식 확인
    print("\n[1단계] 등록된 조건식 확인")
    conditions = []
    for db in get_db():
        session: Session = db
        conditions = session.query(AutoTradeCondition).filter(
            AutoTradeCondition.is_enabled == True
        ).all()
        break
    
    # 브레이크포인트 1: 조건식 목록 확인
    print(f"   - 활성화된 조건식 개수: {len(conditions)}")
    for idx, cond in enumerate(conditions, 1):
        print(f"   [{idx}] {cond.condition_name} (API ID: {cond.api_condition_id}, DB ID: {cond.id})")
    
    if not conditions:
        print("\n⚠️ 활성화된 조건식이 없습니다!")
        print("   해결 방법:")
        print("   1. 웹 페이지에서 조건식을 등록하세요")
        print("   2. 또는 DB에서 is_enabled=True로 설정하세요")
        return
    
    # 2. 첫 번째 조건식으로 테스트
    test_condition = conditions[0]
    print(f"\n[2단계] 테스트 조건식: {test_condition.condition_name}")
    print(f"   - API 조건식 ID: {test_condition.api_condition_id}")
    print(f"   - DB ID: {test_condition.id}")
    
    # 브레이크포인트 2: 조건식 정보 확인
    
    # 3. 키움 API로 조건 검색 실행
    print("\n[3단계] 키움 API 조건 검색 실행")
    kiwoom_api = KiwoomAPI()
    
    # 토큰 확인
    if not kiwoom_api.token_manager.get_valid_token():
        print("   - 토큰이 없습니다. 인증 시도...")
        auth_result = kiwoom_api.authenticate()
        if not auth_result:
            print("   ❌ 인증 실패!")
            return
        print("   ✅ 인증 성공")
    else:
        print("   ✅ 유효한 토큰 있음")
    
    # 브레이크포인트 3: 토큰 확인 후
    
    # 조건 검색 실행
    print(f"\n   - 조건 검색 시작: {test_condition.condition_name}")
    try:
        # search_condition_stocks 메서드 사용
        stocks = await kiwoom_api.search_condition_stocks(
            condition_id=str(test_condition.api_condition_id),
            condition_name=test_condition.condition_name
        )
        
        # 브레이크포인트 4: 검색 결과 확인
        if stocks and len(stocks) > 0:
            print(f"   ✅ 검색 성공: {len(stocks)}개 종목 발견")
            
            # 종목 코드 추출 (stocks는 Dict 리스트)
            stock_codes = []
            for idx, stock in enumerate(stocks[:5], 1):  # 처음 5개만 표시
                stock_code = stock.get('stock_code', stock.get('stk_cd', ''))
                stock_name = stock.get('stock_name', stock.get('stk_nm', ''))
                print(f"      [{idx}] {stock_code} - {stock_name}")
                stock_codes.append(stock_code)
            
            if len(stocks) > 5:
                print(f"      ... 외 {len(stocks)-5}개")
                # 나머지 종목 코드도 추가
                for stock in stocks[5:]:
                    stock_code = stock.get('stock_code', stock.get('stk_cd', ''))
                    stock_codes.append(stock_code)
        else:
            print("   ⚠️ 검색 결과 없음 (조건 미충족)")
            print("\n💡 팁: 조건식을 더 느슨하게 설정하거나 다른 조건식으로 시도하세요")
            stock_codes = []
            return
            
    except Exception as e:
        print(f"   ❌ 조건 검색 실패: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 4. 발견된 종목으로 신호 생성 테스트
    print("\n[4단계] 신호 생성 테스트")
    
    if not stock_codes:
        print("   ⚠️ 발견된 종목이 없어서 신호 생성 불가")
        return
    
    # 첫 번째 종목으로 테스트
    test_stock_code = stock_codes[0]
    print(f"   - 테스트 종목 코드: {test_stock_code}")
    
    # 브레이크포인트 5: 신호 생성 전
    
    # 종목 정보 조회 (선택적)
    print(f"   - 현재가 조회 중...")
    try:
        current_price = await kiwoom_api.get_current_price(test_stock_code)
        if current_price:
            print(f"   - 현재가: {current_price:,}원")
        else:
            print(f"   - 현재가 조회 실패 (기본값 사용)")
            current_price = 0
    except Exception as e:
        print(f"   - 현재가 조회 오류: {e}")
        current_price = 0
    
    # 브레이크포인트 6: 현재가 조회 후
    
    # 5. signal_manager로 신호 생성
    print("\n[5단계] signal_manager로 신호 생성")
    
    try:
        # 신호 생성 (DRY-RUN)
        print("   ⚠️ 실제 신호는 생성하지 않습니다 (DRY-RUN)")
        print(f"\n   - 생성될 신호 정보:")
        print(f"     종목 코드: {test_stock_code}")
        print(f"     조건식 API ID: {test_condition.api_condition_id}")
        print(f"     조건식 이름: {test_condition.condition_name}")
        print(f"     현재가: {current_price:,}원" if current_price else "     현재가: 미조회")
        print(f"     신호 타입: CONDITION")
        print(f"     상태: PENDING")
        
        # 브레이크포인트 7: 신호 생성 정보 확인
        
        # 실제 신호 생성을 원하면 주석 해제:
        # await signal_manager.create_signal(
        #     signal_type=SignalType.CONDITION,
        #     stock_code=test_stock_code,
        #     stock_name="테스트종목",  # 실제로는 API에서 조회
        #     condition_id=test_condition.api_condition_id,  # api_condition_id 사용
        #     condition_name=test_condition.condition_name,
        #     target_price=current_price
        # )
        # print("   ✅ 신호 생성 완료!")
        
    except Exception as e:
        print(f"   ❌ 신호 생성 오류: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 6. 생성된 신호 확인
    print("\n[6단계] PENDING 신호 확인")
    
    for db in get_db():
        session: Session = db
        pending_signals = session.query(PendingBuySignal).filter(
            PendingBuySignal.status == "PENDING"
        ).order_by(PendingBuySignal.detected_at.desc()).limit(5).all()
        
        # 브레이크포인트 8: 최종 확인
        print(f"   - 현재 PENDING 신호 개수: {len(pending_signals)}")
        for idx, signal in enumerate(pending_signals, 1):
            print(f"   [{idx}] {signal.stock_name}({signal.stock_code})")
            print(f"       상태: {signal.status}, 시간: {signal.detected_at}")
        
        break
    
    print("\n✅ 디버깅 완료!")
    print("\n💡 실제 신호 생성:")
    print("   - 위의 실제 신호 생성 부분 주석을 해제하세요")
    print("   - 또는 condition_monitor를 실행하세요:")
    print("     await condition_monitor.start_periodic_monitoring()")

async def test_condition_monitor_full():
    """조건식 모니터링 전체 프로세스 테스트"""
    print("=" * 60)
    print("🔍 조건식 모니터링 전체 프로세스 테스트")
    print("=" * 60)
    
    print("\n⚠️ 이 테스트는 실제로 신호를 생성합니다!")
    print("   계속하려면 아래 주석을 해제하세요")
    
    # 주석 해제하여 실제 모니터링 실행:
    # print("\n[1단계] 조건식 모니터링 시작...")
    # await condition_monitor.start_periodic_monitoring()
    # 
    # print("\n[2단계] 10초 대기 (신호 생성 대기)...")
    # await asyncio.sleep(10)
    # 
    # print("\n[3단계] 모니터링 중지...")
    # await condition_monitor.stop_all_monitoring()
    # 
    # print("\n[4단계] 생성된 신호 확인...")
    # for db in get_db():
    #     session: Session = db
    #     signals = session.query(PendingBuySignal).filter(
    #         PendingBuySignal.status == "PENDING"
    #     ).all()
    #     print(f"   - 생성된 신호 개수: {len(signals)}")
    #     for signal in signals:
    #         print(f"     - {signal.stock_name}({signal.stock_code})")
    #     break

def main():
    """메인 함수"""
    try:
        print("\n📌 브레이크포인트 추천 위치:")
        print("   - 30줄: 조건식 목록 확인 후")
        print("   - 48줄: 테스트 조건식 선택 후")
        print("   - 64줄: 토큰 확인 후")
        print("   - 77줄: 조건 검색 결과 확인")
        print("   - 99줄: 신호 생성 전 종목 확인")
        print("   - 113줄: 현재가 조회 후")
        print("   - 127줄: 신호 생성 정보 확인")
        print("   - 151줄: 최종 PENDING 신호 확인")
        print("\n" + "=" * 60)
        
        # 기본 테스트 (신호 생성 흐름만 추적, 실제 생성 X)
        asyncio.run(test_signal_creation())
        
        # 전체 프로세스 테스트 (실제 신호 생성)
        # asyncio.run(test_condition_monitor_full())
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 사용자가 중단했습니다")
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

