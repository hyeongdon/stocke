"""
SQLite에서 PostgreSQL로 데이터 마이그레이션 스크립트
"""
import sys
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core.models import (
    Base,
    PendingBuySignal,
    AutoTradeCondition,
    AutoTradeSettings,
    WatchlistStock,
    TradingStrategy,
    StrategySignal,
    Position
)
from core.config import Config
from datetime import datetime

def migrate_data():
    """SQLite 데이터를 PostgreSQL로 마이그레이션"""
    
    # SQLite 연결
    sqlite_url = "sqlite:///./stock_pipeline.db"
    sqlite_engine = create_engine(sqlite_url)
    sqlite_session = sessionmaker(bind=sqlite_engine)()
    
    # PostgreSQL 연결
    postgres_url = Config.DATABASE_URL
    if not postgres_url.startswith('postgresql'):
        print("❌ 오류: DATABASE_URL이 PostgreSQL 형식이 아닙니다!")
        print(f"   현재 DATABASE_URL: {postgres_url}")
        print("   .env 파일에서 DATABASE_URL을 PostgreSQL 형식으로 설정하세요:")
        print("   DATABASE_URL=postgresql://user:password@localhost:5432/dbname")
        return False
    
    print(f"📦 PostgreSQL 연결 중: {postgres_url.split('@')[1] if '@' in postgres_url else 'localhost'}")
    postgres_engine = create_engine(postgres_url, pool_pre_ping=True)
    postgres_session = sessionmaker(bind=postgres_engine)()
    
    try:
        # PostgreSQL에 테이블 생성
        print("\n📦 PostgreSQL에 테이블 생성 중...")
        Base.metadata.create_all(bind=postgres_engine)
        print("✅ 테이블 생성 완료")
        
        # 각 테이블 데이터 마이그레이션
        tables = [
            (PendingBuySignal, "pending_buy_signals"),
            (AutoTradeCondition, "auto_trade_conditions"),
            (AutoTradeSettings, "auto_trade_settings"),
            (WatchlistStock, "watchlist_stocks"),
            (TradingStrategy, "trading_strategies"),
            (StrategySignal, "strategy_signals"),
            (Position, "positions"),
        ]
        
        total_migrated = 0
        
        for Model, table_name in tables:
            try:
                records = sqlite_session.query(Model).all()
                if records:
                    print(f"\n📤 {table_name}: {len(records)}개 레코드 마이그레이션 중...")
                    migrated_count = 0
                    for record in records:
                        try:
                            # SQLite에서 가져온 데이터를 PostgreSQL에 삽입
                            postgres_session.merge(record)
                            migrated_count += 1
                        except Exception as e:
                            print(f"   ⚠️  레코드 마이그레이션 실패 (ID: {getattr(record, 'id', 'N/A')}): {e}")
                    
                    postgres_session.commit()
                    print(f"✅ {table_name}: {migrated_count}개 레코드 마이그레이션 완료")
                    total_migrated += migrated_count
                else:
                    print(f"ℹ️  {table_name}: 마이그레이션할 데이터 없음")
            except Exception as e:
                print(f"❌ {table_name} 마이그레이션 중 오류: {e}")
                postgres_session.rollback()
        
        print(f"\n🎉 데이터 마이그레이션 완료! (총 {total_migrated}개 레코드)")
        return True
        
    except Exception as e:
        postgres_session.rollback()
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        sqlite_session.close()
        postgres_session.close()

def verify_migration():
    """마이그레이션 결과 검증"""
    postgres_url = Config.DATABASE_URL
    if not postgres_url.startswith('postgresql'):
        print("❌ PostgreSQL 연결 정보가 없습니다.")
        return False
    
    postgres_engine = create_engine(postgres_url, pool_pre_ping=True)
    postgres_session = sessionmaker(bind=postgres_engine)()
    
    try:
        print("\n🔍 마이그레이션 결과 검증 중...")
        
        tables = [
            (PendingBuySignal, "pending_buy_signals"),
            (AutoTradeCondition, "auto_trade_conditions"),
            (AutoTradeSettings, "auto_trade_settings"),
            (WatchlistStock, "watchlist_stocks"),
            (TradingStrategy, "trading_strategies"),
            (StrategySignal, "strategy_signals"),
            (Position, "positions"),
        ]
        
        for Model, table_name in tables:
            count = postgres_session.query(Model).count()
            print(f"   {table_name}: {count}개 레코드")
        
        print("✅ 검증 완료")
        return True
    except Exception as e:
        print(f"❌ 검증 중 오류: {e}")
        return False
    finally:
        postgres_session.close()

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 SQLite → PostgreSQL 데이터 마이그레이션")
    print("=" * 60)
    print(f"⏰ 시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 마이그레이션 실행
    success = migrate_data()
    
    if success:
        # 검증
        verify_migration()
        print("\n" + "=" * 60)
        print("✅ 마이그레이션 완료!")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("❌ 마이그레이션 실패")
        print("=" * 60)
        sys.exit(1)

