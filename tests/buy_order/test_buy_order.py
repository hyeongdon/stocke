"""
매수 주문 실행기 테스트 스크립트

목적:
- 매수 주문 실행 프로세스 검증
- PendingBuySignal -> Position 변환 테스트

예시:
  python test_buy_order.py
  python test_buy_order.py --signal-id 123
  python test_buy_order.py --signal-id 123 --execute  # 실제 주문 실행
"""

# Windows 콘솔 UTF-8 인코딩 설정
import sys
import io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import argparse
import asyncio
import json
from datetime import datetime
from typing import Any

from buy_order_executor import buy_order_executor
from models import get_db, PendingBuySignal
from signal_manager import SignalStatus


def _pp(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


async def run(args: argparse.Namespace) -> int:
    print("=" * 70)
    print("Buy Order Executor Test")
    print(f"- signal_id: {args.signal_id or 'AUTO (첫 PENDING 신호)'}")
    print(f"- execute: {args.execute}")
    print(f"- current_time: {datetime.now().isoformat()}")
    print("=" * 70)
    
    # 1) PENDING 신호 조회
    print("\n[1] PENDING 매수 신호 조회")
    try:
        db = next(get_db())
        
        if args.signal_id:
            # 특정 신호 조회
            signal = db.query(PendingBuySignal).filter(
                PendingBuySignal.id == args.signal_id
            ).first()
            
            if not signal:
                print(f"❌ 신호 ID {args.signal_id} 없음")
                db.close()
                return 1
            
            signals = [signal]
        else:
            # 모든 PENDING 신호 조회
            signals = db.query(PendingBuySignal).filter(
                PendingBuySignal.status == SignalStatus.PENDING.value
            ).order_by(PendingBuySignal.detected_at).all()
        
        if signals:
            print(f"📊 PENDING 신호 {len(signals)}개 발견:")
            for sig in signals:
                print(f"   - ID: {sig.id}, 종목: {sig.stock_name}({sig.stock_code})")
                print(f"     생성시간: {sig.detected_at}, 타입: {sig.signal_type}")
                print(f"     조건ID: {sig.condition_id}")
        else:
            print("⚠️ PENDING 신호 없음")
            print("   - test_signal_manager.py로 신호를 먼저 생성하세요")
            db.close()
            return 0
        
        db.close()
    except Exception as e:
        print(f"❌ 신호 조회 실패: {e}")
        return 2
    
    # 2) 매수 주문 실행 테스트
    if not args.execute:
        print("\n[2] DRY-RUN 모드")
        print("   - 실제 주문을 실행하려면 --execute 옵션을 추가하세요")
        return 0
    
    print("\n[2] 매수 주문 실행")
    try:
        # 첫 번째 신호로 주문 실행
        target_signal = signals[0]
        print(f"   - 대상 신호: ID {target_signal.id}, {target_signal.stock_name}({target_signal.stock_code})")
        
        # 주문 실행 (실제로는 buy_order_executor.process_signals() 사용)
        # 여기서는 단일 신호만 처리
        print("⏳ 주문 실행 중...")
        
        # 자동매매 설정 로드 (필수!)
        await buy_order_executor._load_auto_trade_settings()
        
        # buy_order_executor의 실제 로직 호출
        # 주의: 이것은 실제 주문을 발생시킵니다!
        result = await buy_order_executor._process_single_signal(target_signal)
        
        if result:
            print("✅ 매수 주문 성공")
            print(f"   - 주문 결과: {_pp(result)}")
        else:
            print("❌ 매수 주문 실패")
            print("   - 로그를 확인하여 실패 원인을 파악하세요")
        
        return 0
        
    except Exception as e:
        print(f"❌ 주문 실행 실패: {e}")
        import traceback
        traceback.print_exc()
        return 3


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--signal-id", type=int, help="처리할 신호 ID (미지정시 첫 PENDING 신호)")
    p.add_argument("--execute", action="store_true", help="실제 주문 실행 (주의!)")
    args = p.parse_args()
    
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

