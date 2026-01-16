"""
손절/익절 관리 테스트 스크립트

목적:
- 손절/익절 모니터링 기능 검증
- 보유 포지션 확인
- 손절/익절 조건 체크

예시:
  python test_stop_loss.py
  python test_stop_loss.py --monitor  # 실시간 모니터링 1회 실행
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
from managers.stop_loss_manager import StopLossManager
from core.models import get_db, Position, AutoTradeSettings


def _pp(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


async def run(args: argparse.Namespace) -> int:
    manager = StopLossManager()
    
    print("=" * 70)
    print("Stop Loss Manager Test")
    print(f"- monitor_mode: {args.monitor}")
    print(f"- current_time: {datetime.now().isoformat()}")
    print("=" * 70)
    
    # 1) 자동매매 설정 확인
    print("\n[1] 자동매매 설정 확인")
    try:
        db = next(get_db())
        settings = db.query(AutoTradeSettings).first()
        
        if settings:
            print("✅ 자동매매 설정 존재")
            print(f"   - 활성화: {settings.is_enabled}")
            print(f"   - 손절률: {settings.stop_loss_percent}%")
            print(f"   - 익절률: {settings.take_profit_percent}%")
        else:
            print("⚠️ 자동매매 설정 없음")
            print("   - 웹 인터페이스에서 설정을 생성하세요")
        
        db.close()
    except Exception as e:
        print(f"❌ 설정 조회 실패: {e}")
    
    # 2) 보유 포지션 확인
    print("\n[2] 보유 포지션 확인")
    try:
        db = next(get_db())
        positions = db.query(Position).filter(Position.status == "HOLDING").all()
        
        if positions:
            print(f"📊 보유 포지션 {len(positions)}개:")
            for pos in positions:
                print(f"   - {pos.stock_name}({pos.stock_code})")
                print(f"     매수가: {pos.buy_price}, 수량: {pos.quantity}")
                print(f"     현재가: {pos.current_price or 'N/A'}")
                if pos.current_price:
                    pnl_pct = ((pos.current_price - pos.buy_price) / pos.buy_price) * 100
                    print(f"     수익률: {pnl_pct:.2f}%")
        else:
            print("✅ 보유 포지션 없음")
        
        db.close()
    except Exception as e:
        print(f"❌ 포지션 조회 실패: {e}")
    
    # 3) 손절/익절 모니터링 테스트
    if args.monitor:
        print("\n[3] 손절/익절 모니터링 1회 실행")
        try:
            # 설정 로드
            await manager._load_auto_trade_settings()
            
            if manager.auto_trade_settings and manager.auto_trade_settings.is_enabled:
                print("✅ 자동매매 활성화 - 모니터링 시작")
                
                # 1회 모니터링 실행
                await manager._monitor_positions()
                
                print("✅ 모니터링 완료")
                print("   - 로그를 확인하여 손절/익절 체크 결과를 확인하세요")
            else:
                print("⚠️ 자동매매 비활성화 - 모니터링 건너뜀")
            
            return 0
            
        except Exception as e:
            print(f"❌ 모니터링 실패: {e}")
            import traceback
            traceback.print_exc()
            return 1
    else:
        print("\n[3] 모니터링 모드 아님 (--monitor 옵션 추가)")
        return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--monitor", action="store_true", help="손절/익절 모니터링 1회 실행")
    args = p.parse_args()
    
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

