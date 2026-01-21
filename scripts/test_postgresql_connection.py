"""
PostgreSQL 연결 테스트 스크립트
"""
import sys
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import create_engine, text
from core.config import Config

def test_connection():
    """PostgreSQL 연결 테스트"""
    print("=" * 60)
    print("🔌 PostgreSQL 연결 테스트")
    print("=" * 60)
    print()
    
    database_url = Config.DATABASE_URL
    print(f"📋 DATABASE_URL: {database_url.split('@')[0].split('//')[1].split(':')[0]}@***")
    print()
    
    if not database_url.startswith('postgresql'):
        print("❌ DATABASE_URL이 PostgreSQL 형식이 아닙니다!")
        print(f"   현재: {database_url}")
        print()
        print("💡 .env 파일에서 다음 형식으로 설정하세요:")
        print("   DATABASE_URL=postgresql://user:password@localhost:5432/dbname")
        return False
    
    try:
        print("🔗 연결 시도 중...")
        engine = create_engine(database_url, pool_pre_ping=True)
        
        with engine.connect() as conn:
            # 버전 확인
            result = conn.execute(text("SELECT version();"))
            version = result.fetchone()[0]
            print(f"✅ PostgreSQL 연결 성공!")
            print()
            print(f"📊 PostgreSQL 버전:")
            print(f"   {version}")
            print()
            
            # 데이터베이스 정보
            result = conn.execute(text("SELECT current_database(), current_user;"))
            db_info = result.fetchone()
            print(f"📁 데이터베이스: {db_info[0]}")
            print(f"👤 사용자: {db_info[1]}")
            print()
            
            # 테이블 목록 확인
            result = conn.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """))
            tables = [row[0] for row in result]
            
            if tables:
                print(f"📋 테이블 목록 ({len(tables)}개):")
                for table in tables:
                    print(f"   - {table}")
            else:
                print("📋 테이블 없음 (마이그레이션이 필요할 수 있습니다)")
            print()
            
            # 연결 풀 정보
            pool = engine.pool
            print(f"🔧 연결 풀 정보:")
            print(f"   크기: {pool.size()}")
            print(f"   최대 오버플로우: {pool._max_overflow}")
            print()
            
        print("=" * 60)
        print("✅ 모든 테스트 통과!")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"❌ 연결 실패: {e}")
        print()
        print("💡 문제 해결 방법:")
        print("   1. PostgreSQL 서비스가 실행 중인지 확인")
        print("   2. 데이터베이스가 생성되었는지 확인")
        print("   3. 사용자 권한이 올바른지 확인")
        print("   4. DATABASE_URL 형식이 올바른지 확인")
        print()
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)

