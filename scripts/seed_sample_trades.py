"""성과 통계 미리보기용 샘플 매도내역(SellOrder) 시드 스크립트.

성과 통계(/performance/stats)는 sell_orders 테이블의 COMPLETED 내역에서 계산됩니다.
실제 매매 데이터가 아직 없을 때, 화면이 참고 디자인처럼 채워져 나오는지 확인하기 위한 용도입니다.

사용법:
  python scripts/seed_sample_trades.py          # 샘플 10건 삽입
  python scripts/seed_sample_trades.py --clear  # 샘플(stock_code가 SAMPLE_* 인 행)만 삭제

주의: 삽입되는 행은 stock_code 가 "SAMPLE_" 로 시작하므로 나중에 --clear 로 정리할 수 있습니다.
"""
import os
import sys
import argparse
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.models import SessionLocal, SellOrder, Position, init_db  # noqa: E402

# (종목명, 매도가, 수량, 손익(원), 사유)  — 3승 7패 / 트레일링 4건·손절 6건 구성
SAMPLE = [
    ("샘플 레버리지A", 71000, 57, 98829, "TRAILING"),
    ("샘플 반도체B", 25400, 40, 51483, "TRAILING"),
    ("샘플 코스닥C", 18900, 62, 32005, "TRAILING"),
    ("샘플 인버스D", 9800, 100, -50000, "TRAILING"),
    ("샘플 바이오E", 14200, 70, -71629, "STOP_LOSS"),
    ("샘플 2차전지F", 33500, 30, -33026, "STOP_LOSS"),
    ("샘플 IT G", 12500, 80, -30000, "STOP_LOSS"),
    ("샘플 화학H", 28000, 35, -28000, "STOP_LOSS"),
    ("샘플 금융I", 21000, 48, -25394, "STOP_LOSS"),
    ("샘플 게임J", 19000, 55, -21000, "STOP_LOSS"),
]

REASON_DETAIL = {
    "TRAILING": "트레일링 스탑 청산",
    "STOP_LOSS": "손절 라인 도달",
    "TAKE_PROFIT": "익절 목표 도달",
}


def clear_samples():
    db = SessionLocal()
    try:
        n = db.query(SellOrder).filter(SellOrder.stock_code.like("SAMPLE_%")).delete(synchronize_session=False)
        db.query(Position).filter(Position.stock_code.like("SAMPLE_%")).delete(synchronize_session=False)
        db.commit()
        print(f"샘플 매도내역 {n}건 삭제 완료")
    finally:
        db.close()


def seed():
    db = SessionLocal()
    try:
        today = datetime.now().replace(hour=9, minute=30, second=0, microsecond=0)
        for i, (name, price, qty, pl, reason) in enumerate(SAMPLE):
            code = f"SAMPLE_{i+1:02d}"
            sell_amount = price * qty
            buy_amount = sell_amount - pl
            buy_price = max(int(buy_amount / qty), 1)
            ts = today + timedelta(minutes=20 * (i + 1))

            pos = Position(
                stock_code=code, stock_name=name,
                buy_price=buy_price, buy_quantity=qty, buy_amount=buy_amount,
                stop_loss_rate=5.0, take_profit_rate=10.0,
                status="STOP_LOSS" if reason == "STOP_LOSS" else "TAKE_PROFIT",
                buy_time=today, sell_time=ts,
            )
            db.add(pos)
            db.flush()

            order = SellOrder(
                position_id=pos.id, stock_code=code, stock_name=name,
                sell_price=price, sell_quantity=qty, sell_amount=sell_amount,
                sell_reason=reason, sell_reason_detail=REASON_DETAIL.get(reason, ""),
                profit_loss=pl,
                profit_loss_rate=round(pl / buy_amount * 100, 2) if buy_amount else 0,
                status="COMPLETED",
                created_at=ts, ordered_at=ts, completed_at=ts,
            )
            db.add(order)
        db.commit()
        print(f"샘플 매도내역 {len(SAMPLE)}건 삽입 완료 (stock_code SAMPLE_*)")
        print("→ 대시보드 '자동매매' 탭에서 성과 통계를 확인하세요.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clear", action="store_true", help="샘플 데이터만 삭제")
    args = parser.parse_args()

    init_db()
    if args.clear:
        clear_samples()
    else:
        seed()
