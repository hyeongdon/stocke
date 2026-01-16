"""
조건식 모니터링 테스트 스크립트

목적:
- 조건식 검색이 정상 작동하는지 검증
- 조건식으로 검색된 종목 리스트 확인
- 신호 생성 프로세스 테스트

예시:
  python test_condition_monitor.py --condition-id 1 --condition-name "상승종목"
  python test_condition_monitor.py --condition-id 1 --condition-name "상승종목" --create-signal
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

from core.config import Config
from api.kiwoom_api import KiwoomAPI
from condition_monitor import ConditionMonitor
from managers.signal_manager import SignalType


def _pp(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


async def run(args: argparse.Namespace) -> int:
    api = KiwoomAPI()
    monitor = ConditionMonitor()
    
    print("=" * 70)
    print("Condition Monitor Test")
    print(f"- condition_id: {args.condition_id}")
    print(f"- condition_name: {args.condition_name}")
    print(f"- create_signal: {args.create_signal}")
    print(f"- current_time: {datetime.now().isoformat()}")
    print("=" * 70)
    
    # 1) 토큰 인증
    print("\n[1] 토큰 인증")
    ok = api.authenticate()
    if not ok:
        print("❌ 인증 실패")
        return 1
    print("✅ 인증 성공")
    
    # 2) 조건식 검색
    print(f"\n[2] 조건식 검색 - ID: {args.condition_id}, 이름: {args.condition_name}")
    try:
        results = await api.search_condition_stocks(str(args.condition_id), args.condition_name)
        
        if results:
            print(f"✅ 검색 성공 - {len(results)}개 종목 발견")
            print("\n📊 검색된 종목 목록:")
            for i, stock in enumerate(results, 1):
                stock_code = stock.get('stock_code', 'N/A')
                stock_name = stock.get('stock_name', 'N/A')
                print(f"   [{i}] {stock_name} ({stock_code})")
            
            # 3) 신호 생성 테스트 (옵션)
            if args.create_signal and results:
                print(f"\n[3] 신호 생성 테스트 - 첫 번째 종목만")
                first_stock = results[0]
                stock_code = first_stock.get('stock_code')
                stock_name = first_stock.get('stock_name')
                
                print(f"   - 종목: {stock_name} ({stock_code})")
                
                # ConditionMonitor의 start_monitoring 호출
                success = await monitor.start_monitoring(args.condition_id, args.condition_name)
                
                if success:
                    print("✅ 조건식 모니터링 시작 성공")
                    print("   - DB에서 PendingBuySignal 테이블을 확인하세요")
                else:
                    print("⚠️ 조건식 모니터링 시작 실패 (API 제한 또는 중복)")
            
            return 0
        else:
            print("⚠️ 검색 결과 없음")
            return 0
            
    except Exception as e:
        print(f"❌ 조건식 검색 실패: {e}")
        import traceback
        traceback.print_exc()
        return 2


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--condition-id", type=int, required=True, help="조건식 ID")
    p.add_argument("--condition-name", required=True, help="조건식 이름")
    p.add_argument("--create-signal", action="store_true", help="신호 생성 테스트 실행")
    args = p.parse_args()
    
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

