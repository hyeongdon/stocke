"""
키움 계좌의 실제 잔고를 조회하여 DB의 Position 데이터와 동기화하는 스크립트
"""
import sys
import io
import asyncio
from datetime import datetime
from models import get_db, PendingBuySignal, Position
from kiwoom_api import KiwoomAPI
from config import Config

# UTF-8 인코딩 설정 (Windows 콘솔 문제 해결)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


async def get_account_balance(api: KiwoomAPI):
    """
    키움 계좌의 보유 종목 조회
    """
    try:
        balance = await api.get_account_balance(Config.KIWOOM_MOCK_ACCOUNT_NUMBER)
        return balance
    except Exception as e:
        print(f"❌ 계좌 잔고 조회 실패: {e}")
        return None


def find_signal_by_stock_code(db, stock_code: str):
    """
    종목코드로 ORDERED 상태의 Signal 찾기
    """
    signal = db.query(PendingBuySignal).filter(
        PendingBuySignal.stock_code == stock_code,
        PendingBuySignal.status == 'ORDERED'
    ).order_by(PendingBuySignal.created_at.desc()).first()
    
    return signal


def create_position_from_balance(db, signal: PendingBuySignal, holding: dict):
    """
    키움 잔고 정보로 Position 생성
    """
    try:
        # 이미 Position이 있는지 확인
        existing_position = db.query(Position).filter(
            Position.signal_id == signal.id
        ).first()
        
        if existing_position:
            print(f"⚠️  이미 Position이 존재합니다: Signal ID {signal.id}")
            return existing_position
        
        # 키움 API에서 받은 정보 파싱
        buy_price = int(holding.get('pchs_avg_pric', 0))  # 매입평균가격
        quantity = int(holding.get('hldg_qty', 0))        # 보유수량
        current_price = int(holding.get('prpr', 0))       # 현재가
        
        if buy_price == 0 or quantity == 0:
            print(f"❌ 유효하지 않은 데이터: 매수가={buy_price}, 수량={quantity}")
            return None
        
        # 손절가 계산 (-5% 기본값)
        stop_loss_price = int(buy_price * 0.95)
        
        # Position 생성
        position = Position(
            signal_id=signal.id,
            stock_code=signal.stock_code,
            stock_name=signal.stock_name,
            buy_price=buy_price,
            quantity=quantity,
            buy_amount=buy_price * quantity,
            current_price=current_price if current_price > 0 else buy_price,
            stop_loss_price=stop_loss_price,
            status='ACTIVE',
            created_at=datetime.now()
        )
        
        db.add(position)
        
        # Signal 상태를 COMPLETED로 변경
        signal.status = 'COMPLETED'
        signal.updated_at = datetime.now()
        
        db.commit()
        
        print(f"✅ Position 생성 완료:")
        print(f"   - Signal ID: {signal.id}")
        print(f"   - 종목: {signal.stock_name} ({signal.stock_code})")
        print(f"   - 매수가: {buy_price:,}원")
        print(f"   - 수량: {quantity:,}주")
        print(f"   - 매수금액: {buy_price * quantity:,}원")
        print(f"   - 현재가: {current_price:,}원")
        print(f"   - 손절가: {stop_loss_price:,}원")
        print()
        
        return position
        
    except Exception as e:
        db.rollback()
        print(f"❌ Position 생성 실패: {e}")
        import traceback
        traceback.print_exc()
        return None


async def main():
    print("=" * 60)
    print("🔄 키움 계좌 → DB Position 동기화")
    print("=" * 60)
    print()
    
    # DB 연결
    db = next(get_db())
    
    # 키움 API 초기화
    api = KiwoomAPI()
    
    # TokenManager 인증 (KiwoomAPI가 내부적으로 TokenManager 사용)
    print("🔐 키움 API 인증 중...")
    try:
        # TokenManager는 자동으로 인증됨
        print("✅ 인증 준비 완료")
        print()
    except Exception as e:
        print(f"❌ 인증 실패: {e}")
        return
    
    # 계좌 잔고 조회
    print("📊 계좌 잔고 조회 중...")
    balance = await get_account_balance(api)
    
    if not balance or 'output1' not in balance:
        print("❌ 계좌 잔고를 가져올 수 없습니다.")
        return
    
    holdings = balance['output1']  # 보유 종목 리스트
    
    if not holdings:
        print("❌ 보유 종목이 없습니다.")
        return
    
    print(f"✅ 보유 종목 {len(holdings)}개 발견")
    print()
    
    # 대상 종목 필터링 (대한항공, 현대모비스, 한국단자)
    target_stocks = {
        '003490': '대한항공',
        '012330': '현대모비스',
        '000700': '한국단자'
    }
    
    print("=" * 60)
    print("📋 보유 종목 정보:")
    print("=" * 60)
    
    target_holdings = []
    for holding in holdings:
        stock_code = holding.get('pdno', '')  # 상품번호 (종목코드)
        stock_name = holding.get('prdt_name', '')  # 상품명
        
        if stock_code in target_stocks:
            buy_price = int(holding.get('pchs_avg_pric', 0))
            quantity = int(holding.get('hldg_qty', 0))
            current_price = int(holding.get('prpr', 0))
            
            print(f"\n📌 {stock_name} ({stock_code})")
            print(f"   - 매수가: {buy_price:,}원")
            print(f"   - 수량: {quantity:,}주")
            print(f"   - 매수금액: {buy_price * quantity:,}원")
            print(f"   - 현재가: {current_price:,}원")
            
            target_holdings.append((stock_code, holding))
    
    if not target_holdings:
        print("\n❌ 대상 종목이 계좌에 없습니다.")
        return
    
    print()
    print("=" * 60)
    response = input(f"\n위 {len(target_holdings)}개 종목에 대해 Position을 생성하시겠습니까? (yes/no): ").strip().lower()
    if response != 'yes':
        print("❌ 작업이 취소되었습니다.")
        return
    
    print()
    print("=" * 60)
    print("🚀 Position 생성 시작...")
    print("=" * 60)
    print()
    
    # Position 생성
    created_count = 0
    for stock_code, holding in target_holdings:
        # Signal 찾기
        signal = find_signal_by_stock_code(db, stock_code)
        
        if not signal:
            print(f"⚠️  종목 {stock_code}에 대한 ORDERED 상태의 Signal을 찾을 수 없습니다.")
            continue
        
        # Position 생성
        position = create_position_from_balance(db, signal, holding)
        
        if position:
            created_count += 1
    
    print("=" * 60)
    print(f"✅ 완료: {created_count}개의 Position이 생성되었습니다.")
    print("=" * 60)
    print()
    print("💡 다음 단계:")
    print("   1. 웹 브라우저에서 Ctrl+Shift+R로 새로고침")
    print("   2. 시그널 라이프사이클 페이지에서 현재가/손절가/목표가 확인")
    print("   3. 손절 모니터링이 자동으로 해당 포지션들을 모니터링합니다")


if __name__ == '__main__':
    asyncio.run(main())

