import requests
import re
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Dict, Optional

class NaverStockDiscussionCrawler:
    """
    네이버 금융 종목토론방 크롤링 클래스
    """
    
    def __init__(self):
        self.base_url = "https://finance.naver.com"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
    
    def crawl_discussion_posts(self, stock_code: str, page: int = 1, max_pages: int = 1, today_only: bool = False) -> List[Dict]:
        """
        네이버 금융 종목토론방 글 크롤링
        
        Args:
            stock_code (str): 종목코드 (예: '005930')
            page (int): 시작 페이지 번호
            max_pages (int): 크롤링할 최대 페이지 수
            today_only (bool): 당일 글만 필터링 여부
        
        Returns:
            List[Dict]: 토론방 글 정보 리스트 (제목, 작성자, 날짜)
        """
        all_posts = []
        today_str = datetime.now().strftime('%m.%d')  # 오늘 날짜 (MM.DD 형식)
        print(f"오늘 날짜 형식: {today_str}")
        
        for current_page in range(page, page + max_pages):
            try:
                url = f'{self.base_url}/item/board.nhn?code={stock_code}&page={current_page}'
                response = requests.get(url, headers=self.headers, verify=False, timeout=10)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # 글 제목과 링크 추출
                title_elements = soup.select('td.title a')
                
                # 작성자 정보 추출
                author_elements = soup.select('td.p11')
                
                # HTML 구조 디버깅 제거됨
                
                # 토론 게시글이 있는 테이블 찾기 (행이 많은 테이블)
                tables = soup.select('table')
                discussion_table = None
                for table in tables:
                    rows = table.select('tr')
                    if len(rows) > 10:  # 게시글이 많은 테이블
                        discussion_table = table
                        break
                
                page_posts = []
                post_count = 0
                
                if discussion_table:
                    table_rows = discussion_table.select('tr')
                    
                    for row in table_rows:
                        cells = row.select('td')
                        if len(cells) >= 6:  # 날짜, 제목, 작성자, 조회수 등이 있는 행
                            # 첫 번째 셀이 날짜 (yyyy.mm.dd hh:mm 형식)
                            date_cell = cells[0]
                            date_text = date_cell.get_text().strip()
                            
                            # 두 번째 셀이 제목
                            title_cell = cells[1]
                            title_link = title_cell.select_one('a')
                            if title_link:
                                title_text = title_link.get_text().strip()
                                # 댓글 개수 제거 (예: "제목 [5]" -> "제목")
                                title_text = re.sub(r'\s*\[\d+\]\s*$', '', title_text)
                                
                                if not title_text:  # 빈 제목 제외
                                    continue
                                
                                # 날짜 형식 확인 (yyyy.mm.dd 형식)
                                if re.match(r'\d{4}\.\d{2}\.\d{2}', date_text):
                                     # 당일 글 필터링 (yyyy.mm.dd 형식으로 비교)
                                     if today_only:
                                         today_full = datetime.now().strftime('%Y.%m.%d')
                                         if today_full not in date_text:
                                             continue
                                    
                                post_info = {
                                        'title': title_text
                                }
                                    
                                page_posts.append(post_info)
                                post_count += 1
                
                all_posts.extend(page_posts)
                print(f"페이지 {current_page}: {len(page_posts)}개 글 수집")
                
            except Exception as e:
                print(f"페이지 {current_page} 크롤링 오류: {e}")
                continue
        
        return all_posts
 

# 사용 예시
if __name__ == "__main__":
    # 사용 예제
    crawler = NaverStockDiscussionCrawler()
    
    # 삼성전자(005930) 종목토론방 크롤링 - 당일 글의 제목과 작성자만
    stock_code = "005930"
    
    # 1. 먼저 당일 글만 수집 테스트
    print("당일 글만 수집 중 (제목, 작성자, 날짜)...")
    today_posts = crawler.crawl_discussion_posts(
        stock_code, 
        page=1, 
        max_pages=10, 
        today_only=True
    )
    
    print(f"\n총 {len(today_posts)}개의 당일 글을 수집했습니다.")
    
 
    posts = today_posts
    
    print(f"\n최종적으로 {len(posts)}개의 글을 수집했습니다.")
    
    # 수집된 글 제목 출력
    for post in posts:
        print(f"{post['title']}")
 