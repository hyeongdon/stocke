"""
시그널 관리 테스트 스크립트

목적:
- 시그널 생성, 조회, 중복 방지 기능 검증
- DB에 신호가 정상적으로 저장되는지 확인

예시:
  python test_signal_manager.py --stock-code 005930 --stock-name "삼성전자"
  python test_signal_manager.py --stock-code 005930 --stock-name "삼성전자" --condition-id 1
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

from managers.signal_manager import signal_manager, SignalType, SignalStatus
from core.models import get_db, PendingBuySignal


def _pp(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


async def run(args: argparse.Namespace) -> int:
    print("=" * 70)
    print("Signal Manager Test")
    print(f"- stock_code: {args.stock_code}")
    print(f"- stock_name: {args.stock_name}")
    print(f"- condition_id: {args.condition_id}")
    print(f"- signal_type: {args.signal_type}")
    print(f"- current_time: {datetime.now().isoformat()}")
    print("=" * 70)
    
    # 1) 기존 신호 조회
    print("\n[1] 기존 신호 조회")
    try:
        db = next(get_db())
        existing_signals = db.query(PendingBuySignal).filter(
            PendingBuySignal.stock_code == args.stock_code,
            PendingBuySignal.status == SignalStatus.PENDING.value
        ).all()
        
        if existing_signals:
            print(f"⚠️ 기존 PENDING 신호 {len(existing_signals)}개 발견:")
            for sig in existing_signals:
                print(f"   - ID: {sig.id}, 종목: {sig.stock_name}({sig.stock_code})")
                print(f"     생성시간: {sig.detected_at}, 타입: {sig.signal_type}")
        else:
            print("✅ 기존 PENDING 신호 없음")
        
        db.close()
    except Exception as e:
        print(f"❌ 기존 신호 조회 실패: {e}")
    
    # 2) 신호 생성 테스트
    print(f"\n[2] 신호 생성 테스트 - {args.stock_name}({args.stock_code})")
    try:
        # 신호 타입 매핑
        type_map = {
            "condition": SignalType.CONDITION_SIGNAL,
            "reference": SignalType.REFERENCE_CANDLE,
            "strategy": SignalType.STRATEGY
        }
        signal_type_enum = type_map[args.signal_type]
        
        success = await signal_manager.create_signal(
            condition_id=args.condition_id,
            stock_code=args.stock_code,
            stock_name=args.stock_name,
            signal_type=signal_type_enum,
            additional_data={
                "test_mode": True,
                "created_by": "test_signal_manager.py"
            }
        )
        
        if success:
            print("✅ 신호 생성 성공")
        else:
            print("⚠️ 신호 생성 실패 (중복 또는 제약조건)")
        
    except Exception as e:
        print(f"❌ 신호 생성 실패: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # 3) 생성된 신호 확인
    print("\n[3] 생성된 신호 확인")
    try:
        db = next(get_db())
        new_signals = db.query(PendingBuySignal).filter(
            PendingBuySignal.stock_code == args.stock_code
        ).order_by(PendingBuySignal.detected_at.desc()).limit(3).all()
        
        if new_signals:
            print(f"📊 최근 신호 {len(new_signals)}개:")
            for sig in new_signals:
                print(f"   - ID: {sig.id}, 상태: {sig.status}")
                print(f"     종목: {sig.stock_name}({sig.stock_code})")
                print(f"     생성: {sig.detected_at}, 타입: {sig.signal_type}")
                print(f"     조건ID: {sig.condition_id}")
        else:
            print("⚠️ 신호 없음")
        
        db.close()
        return 0
        
    except Exception as e:
        print(f"❌ 신호 확인 실패: {e}")
        return 2


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--stock-code", required=True, help="종목코드")
    p.add_argument("--stock-name", required=True, help="종목명")
    p.add_argument("--condition-id", type=int, default=999, help="조건식 ID (테스트용 기본값: 999)")
    p.add_argument("--signal-type", choices=["condition", "reference", "strategy"], 
                   default="condition", help="신호 타입")
    args = p.parse_args()
    
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

