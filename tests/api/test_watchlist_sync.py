"""
관심종목 동기화 테스트 스크립트

목적:
- 키움 관심종목 동기화 기능 검증
- 관심종목 그룹 조회 및 종목 리스트 확인

예시:
  python test_watchlist_sync.py
  python test_watchlist_sync.py --group-id 1
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

from api.kiwoom_api import KiwoomAPI
from watchlist_sync_manager import watchlist_sync_manager


def _pp(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


async def run(args: argparse.Namespace) -> int:
    api = KiwoomAPI()
    
    print("=" * 70)
    print("Watchlist Sync Test")
    print(f"- group_id: {args.group_id or 'ALL'}")
    print(f"- current_time: {datetime.now().isoformat()}")
    print("=" * 70)
    
    # 1) 토큰 인증
    print("\n[1] 토큰 인증")
    ok = api.authenticate()
    if not ok:
        print("❌ 인증 실패")
        return 1
    print("✅ 인증 성공")
    
    # 2) 관심종목 그룹 조회
    print("\n[2] 관심종목 그룹 조회")
    try:
        groups = await api.get_favorite_groups()
        
        if groups:
            print(f"✅ 그룹 조회 성공 - {len(groups)}개 그룹")
            print("\n📊 관심종목 그룹 목록:")
            for group in groups:
                group_id = group.get('group_id', 'N/A')
                group_name = group.get('group_name', 'N/A')
                print(f"   - 그룹 ID: {group_id}, 이름: {group_name}")
        else:
            print("⚠️ 관심종목 그룹 없음")
            return 0
        
    except Exception as e:
        print(f"❌ 그룹 조회 실패: {e}")
        return 1
    
    # 3) 특정 그룹의 종목 조회
    if args.group_id:
        print(f"\n[3] 그룹 {args.group_id} 종목 조회")
        try:
            stocks = await api.get_favorite_stocks(args.group_id)
            
            if stocks:
                print(f"✅ 종목 조회 성공 - {len(stocks)}개 종목")
                print("\n📈 관심종목 목록:")
                for i, stock in enumerate(stocks, 1):
                    stock_code = stock.get('stock_code', 'N/A')
                    stock_name = stock.get('stock_name', 'N/A')
                    print(f"   [{i}] {stock_name} ({stock_code})")
            else:
                print("⚠️ 관심종목 없음")
            
        except Exception as e:
            print(f"❌ 종목 조회 실패: {e}")
            return 2
    
    # 4) 동기화 매니저 테스트
    print("\n[4] 관심종목 동기화 테스트")
    try:
        print("⏳ 동기화 시작...")
        success = await watchlist_sync_manager.sync_watchlist()
        
        if success:
            print("✅ 동기화 성공")
            print("   - DB에서 관심종목 데이터를 확인하세요")
        else:
            print("⚠️ 동기화 실패 또는 변경사항 없음")
        
        return 0
        
    except Exception as e:
        print(f"❌ 동기화 실패: {e}")
        import traceback
        traceback.print_exc()
        return 3


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--group-id", type=int, help="조회할 관심종목 그룹 ID")
    args = p.parse_args()
    
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

