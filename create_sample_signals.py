"""
시그널 라이프사이클 테스트용 샘플 데이터 생성 스크립트

목적:
- 다양한 상태의 시그널을 DB에 생성하여 라이프사이클 화면 테스트

사용법:
  python create_sample_signals.py
"""

# Windows 콘솔 UTF-8 인코딩 설정
import sys
import io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from datetime import datetime, timedelta
import random
from models import get_db, PendingBuySignal, Position
from sqlalchemy.orm import Session


def create_sample_signals():
    """다양한 상태의 샘플 시그널 생성"""
    
    db = next(get_db())
    
    print("=" * 70)
    print("시그널 라이프사이클 테스트 데이터 생성")
    print("=" * 70)
    
    # 샘플 종목 데이터
    stocks = [
        {"code": "005930", "name": "삼성전자"},
        {"code": "000660", "name": "SK하이닉스"},
        {"code": "035420", "name": "NAVER"},
        {"code": "005380", "name": "현대차"},
        {"code": "051910", "name": "LG화학"},
        {"code": "006400", "name": "삼성SDI"},
        {"code": "035720", "name": "카카오"},
        {"code": "068270", "name": "셀트리온"},
        {"code": "207940", "name": "삼성바이오로직스"},
        {"code": "003670", "name": "포스코퓨처엠"}
    ]
    
    # 상태별 샘플 데이터 생성
    created_signals = []
    
    print("\n[1] PENDING 상태 시그널 생성 (대기중)")
    for i in range(3):
        stock = stocks[i]
        signal = PendingBuySignal(
            condition_id=1,
            stock_code=stock["code"],
            stock_name=stock["name"],
            detected_at=datetime.now() - timedelta(minutes=random.randint(1, 30)),
            detected_date=datetime.now().date(),
            status="PENDING",
            signal_type="condition",
            target_price=random.randint(50000, 100000)
        )
        db.add(signal)
        created_signals.append((stock["name"], "PENDING"))
    
    print("   ✅ 3개 생성 완료")
    
    print("\n[2] PROCESSING 상태 시그널 생성 (처리중)")
    for i in range(3, 5):
        stock = stocks[i]
        signal = PendingBuySignal(
            condition_id=1,
            stock_code=stock["code"],
            stock_name=stock["name"],
            detected_at=datetime.now() - timedelta(minutes=random.randint(5, 15)),
            detected_date=datetime.now().date(),
            status="PROCESSING",
            signal_type="condition",
            target_price=random.randint(50000, 100000)
        )
        db.add(signal)
        created_signals.append((stock["name"], "PROCESSING"))
    
    print("   ✅ 2개 생성 완료")
    
    print("\n[3] ORDERED 상태 시그널 생성 (주문완료)")
    db.commit()  # 먼저 커밋하여 ID 생성
    
    for i in range(5, 7):
        stock = stocks[i]
        buy_price = random.randint(50000, 100000)
        quantity = random.randint(1, 10)
        
        # 시그널 생성
        signal = PendingBuySignal(
            condition_id=1,
            stock_code=stock["code"],
            stock_name=stock["name"],
            detected_at=datetime.now() - timedelta(minutes=random.randint(10, 30)),
            detected_date=datetime.now().date(),
            status="ORDERED",
            signal_type="condition",
            target_price=buy_price
        )
        db.add(signal)
        db.flush()  # ID 생성
        
        # 포지션 생성
        current_price = buy_price + random.randint(-5000, 10000)
        position = Position(
            stock_code=stock["code"],
            stock_name=stock["name"],
            buy_price=buy_price,
            buy_quantity=quantity,
            buy_amount=buy_price * quantity,
            buy_order_id=f"TEST{random.randint(10000, 99999)}",
            current_price=current_price,
            stop_loss_rate=5.0,
            take_profit_rate=10.0,
            condition_id=1,
            signal_id=signal.id,
            status="HOLDING",
            buy_time=datetime.now() - timedelta(minutes=random.randint(5, 20))
        )
        db.add(position)
        created_signals.append((stock["name"], "ORDERED + POSITION"))
    
    print("   ✅ 2개 생성 완료 (포지션 포함)")
    
    print("\n[4] FAILED 상태 시그널 생성 (실패)")
    failure_reasons = [
        "현재가 조회 실패: API 제한 초과",
        "예수금 부족 (필요: 1,000,000원, 보유: 50,000원)",
        "주문 실행 실패: 장 마감 시간입니다",
        "주문 실행 실패: API 오류 발생"
    ]
    
    for i in range(7, 10):
        stock = stocks[i]
        signal = PendingBuySignal(
            condition_id=1,
            stock_code=stock["code"],
            stock_name=stock["name"],
            detected_at=datetime.now() - timedelta(minutes=random.randint(15, 60)),
            detected_date=datetime.now().date(),
            status="FAILED",
            signal_type="condition",
            target_price=random.randint(50000, 100000),
            failure_reason=random.choice(failure_reasons)
        )
        db.add(signal)
        created_signals.append((stock["name"], "FAILED"))
    
    print("   ✅ 3개 생성 완료")
    
    # 커밋
    db.commit()
    
    print("\n" + "=" * 70)
    print("✅ 샘플 데이터 생성 완료!")
    print("\n생성된 시그널:")
    for i, (name, status) in enumerate(created_signals, 1):
        print(f"   [{i:2d}] {name:<15} - {status}")
    
    print("\n다음 명령으로 확인:")
    print("   python main.py")
    print("   http://localhost:8000/static/signal-lifecycle.html")
    print("=" * 70)
    
    db.close()


def clean_old_signals():
    """기존 테스트 데이터 삭제"""
    db = next(get_db())
    
    response = input("\n기존 시그널을 모두 삭제하시겠습니까? (y/N): ")
    if response.lower() == 'y':
        deleted_signals = db.query(PendingBuySignal).delete()
        deleted_positions = db.query(Position).delete()
        db.commit()
        print(f"✅ {deleted_signals}개 시그널, {deleted_positions}개 포지션 삭제 완료")
    
    db.close()


def main():
    print("\n시그널 라이프사이클 테스트 데이터 생성기\n")
    
    # 기존 데이터 삭제 옵션
    clean_old_signals()
    
    # 샘플 데이터 생성
    create_sample_signals()
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

