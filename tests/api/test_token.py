"""
키움 토큰 발급/갱신 테스트 스크립트

목적:
- 토큰 발급 및 갱신이 정상 작동하는지 검증
- 토큰 유효성 확인

예시:
  python test_token.py
  python test_token.py --renew  # 강제 토큰 갱신
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
from datetime import datetime, timezone, timedelta
from typing import Any

from core.config import Config
from api.kiwoom_api import KiwoomAPI


def _pp(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


async def run(args: argparse.Namespace) -> int:
    api = KiwoomAPI()
    
    print("=" * 70)
    print("Kiwoom Token Test")
    print(f"- use_mock_account: {Config.KIWOOM_USE_MOCK_ACCOUNT}")
    print(f"- force_renew: {args.renew}")
    print(f"- current_time: {datetime.now().isoformat()}")
    print("=" * 70)
    
    # 1) 현재 토큰 상태 확인
    print("\n[1] 현재 토큰 상태 확인")
    current_token = api.token_manager.access_token
    token_expiry = api.token_manager.token_expiry
    is_valid = api.token_manager.is_token_valid()
    
    if current_token:
        print(f"✅ 기존 토큰 있음")
        print(f"   - access_token: {current_token[:20]}...")
        print(f"   - expires_at: {token_expiry}")
        print(f"   - is_valid: {is_valid}")
    else:
        print("❌ 기존 토큰 없음 또는 만료됨")
    
    # 2) 토큰 갱신 (강제 갱신 모드 또는 토큰 없을 때)
    if args.renew or not is_valid:
        print("\n[2] 토큰 발급/갱신 시작")
        success = api.authenticate()
        
        if success:
            print("✅ 토큰 발급/갱신 성공")
            new_token = api.token_manager.access_token
            new_expiry = api.token_manager.token_expiry
            
            if new_token:
                print(f"   - new_access_token: {new_token[:20]}...")
                print(f"   - expires_at: {new_expiry}")
                print(f"   - valid_for: {(new_expiry - datetime.now()).total_seconds() / 60:.1f} 분")
        else:
            print("❌ 토큰 발급/갱신 실패")
            print("   - APP_KEY, APP_SECRET 설정을 확인하세요")
            return 1
    else:
        print("\n[2] 기존 토큰 유효 (--renew 옵션으로 강제 갱신 가능)")
        remaining_time = (token_expiry - datetime.now()).total_seconds() / 60
        print(f"   - 남은 유효시간: {remaining_time:.1f} 분")
    
    # 3) 최종 토큰 유효성 확인
    print("\n[3] 최종 토큰 유효성 확인")
    final_token = api.token_manager.get_valid_token()
    if final_token:
        print("✅ 유효한 토큰 사용 가능")
        return 0
    else:
        print("❌ 유효한 토큰 없음")
        return 2


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--renew", action="store_true", help="강제로 토큰 갱신")
    args = p.parse_args()
    
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

