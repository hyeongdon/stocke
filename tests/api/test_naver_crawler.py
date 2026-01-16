"""
네이버 토론방 크롤링 테스트 스크립트

목적:
- 네이버 금융 종목토론방 크롤링 기능 검증
- 게시글 수집 확인

예시:
  python test_naver_crawler.py --stock-code 005930
  python test_naver_crawler.py --stock-code 005930 --pages 3
  python test_naver_crawler.py --stock-code 005930 --today-only
"""

# Windows 콘솔 UTF-8 인코딩 설정
import sys
import io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import argparse
from datetime import datetime

from naver_discussion_crawler import NaverStockDiscussionCrawler


def run(args: argparse.Namespace) -> int:
    crawler = NaverStockDiscussionCrawler()
    
    print("=" * 70)
    print("Naver Discussion Crawler Test")
    print(f"- stock_code: {args.stock_code}")
    print(f"- max_pages: {args.pages}")
    print(f"- today_only: {args.today_only}")
    print(f"- current_time: {datetime.now().isoformat()}")
    print("=" * 70)
    
    # 네이버 토론방 크롤링
    print(f"\n[1] 네이버 금융 종목토론방 크롤링 시작")
    try:
        posts = crawler.crawl_discussion_posts(
            stock_code=args.stock_code,
            page=1,
            max_pages=args.pages,
            today_only=args.today_only
        )
        
        if posts:
            print(f"✅ 크롤링 성공 - {len(posts)}개 게시글 수집")
            
            print("\n📊 수집된 게시글:")
            for i, post in enumerate(posts[:10], 1):  # 최대 10개만 출력
                print(f"   [{i}] {post.get('title', 'N/A')}")
                print(f"       작성자: {post.get('author', 'N/A')}")
                print(f"       날짜: {post.get('date', 'N/A')}")
                print(f"       조회: {post.get('views', 'N/A')}, 공감: {post.get('likes', 'N/A')}")
            
            if len(posts) > 10:
                print(f"   ... 외 {len(posts) - 10}개 게시글")
            
            # 통계
            print(f"\n📈 통계:")
            print(f"   - 총 게시글 수: {len(posts)}")
            
            if args.today_only:
                today_str = datetime.now().strftime('%m.%d')
                today_posts = [p for p in posts if today_str in p.get('date', '')]
                print(f"   - 오늘 게시글: {len(today_posts)}")
            
            return 0
        else:
            print("⚠️ 수집된 게시글 없음")
            return 0
            
    except Exception as e:
        print(f"❌ 크롤링 실패: {e}")
        import traceback
        traceback.print_exc()
        return 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--stock-code", required=True, help="종목코드 (예: 005930)")
    p.add_argument("--pages", type=int, default=1, help="크롤링할 페이지 수")
    p.add_argument("--today-only", action="store_true", help="오늘 게시글만 필터링")
    args = p.parse_args()
    
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

