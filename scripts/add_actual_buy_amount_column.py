"""
Position 테이블에 actual_buy_amount 컬럼 추가
"""
import sys
import os
import io

# 프로젝트 루트를 Python 경로에 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# UTF-8 인코딩 설정
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from core.models import get_db
import sqlite3

def add_column():
    """actual_buy_amount 컬럼 추가"""
    try:
        # DB 파일 경로
        db_path = os.path.join(project_root, 'stock_pipeline.db')
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 컬럼 존재 여부 확인
        cursor.execute("PRAGMA table_info(positions)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'actual_buy_amount' in columns:
            print("✅ actual_buy_amount 컬럼이 이미 존재합니다.")
            conn.close()
            return
        
        # 컬럼 추가
        print("📝 actual_buy_amount 컬럼 추가 중...")
        cursor.execute("ALTER TABLE positions ADD COLUMN actual_buy_amount INTEGER")
        conn.commit()
        
        print("✅ actual_buy_amount 컬럼이 추가되었습니다.")
        conn.close()
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("=" * 60)
    print("🔄 Position 테이블에 actual_buy_amount 컬럼 추가")
    print("=" * 60)
    print()
    add_column()
    print()
    print("=" * 60)
    print("✅ 완료!")
    print("=" * 60)

