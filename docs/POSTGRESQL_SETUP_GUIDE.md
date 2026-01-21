# PostgreSQL 설치 및 설정 가이드

## 📋 개요
이 가이드는 Stocke 프로젝트에서 SQLite에서 PostgreSQL로 마이그레이션하거나 새로 PostgreSQL을 설정하는 방법을 설명합니다.

> **💡 Docker 방식도 고려해보세요!**
> 
> Ubuntu 서버에 설치할 경우, Docker 방식이 더 간편하고 관리하기 쉽습니다.
> - **직접 설치**: 시스템에 직접 설치, 더 나은 성능, 시스템 통합
> - **Docker**: 간편한 설치/제거, 격리된 환경, 버전 관리 용이
> 
> 자세한 내용은 [PostgreSQL Docker 설치 가이드](./POSTGRESQL_DOCKER_GUIDE.md)를 참고하세요.

## 🗄️ PostgreSQL 설치

### Windows 설치

#### 방법 1: 공식 설치 프로그램 (권장)
1. **PostgreSQL 다운로드**
   - https://www.postgresql.org/download/windows/ 접속
   - "Download the installer" 클릭
   - 최신 버전 다운로드 (예: PostgreSQL 15.x)

2. **설치 실행**
   ```bash
   # 설치 프로그램 실행 후:
   # - 설치 경로: 기본값 사용 (C:\Program Files\PostgreSQL\15)
   # - 포트: 5432 (기본값)
   # - Superuser 비밀번호: 안전한 비밀번호 설정 (기억해두세요!)
   # - Locale: Korean, Korea (또는 기본값)
   ```

3. **설치 확인**
   ```bash
   # 명령 프롬프트에서 확인
   psql --version
   ```

#### 방법 2: Chocolatey 사용
```bash
# Chocolatey가 설치되어 있다면
choco install postgresql15
```

### Linux 설치 (Ubuntu/Debian)

#### Ubuntu/Debian
```bash
# 패키지 목록 업데이트
sudo apt update

# PostgreSQL 설치
sudo apt install postgresql postgresql-contrib

# PostgreSQL 서비스 시작
sudo systemctl start postgresql
sudo systemctl enable postgresql

# 설치 확인
psql --version
```

#### CentOS/RHEL
```bash
# PostgreSQL 저장소 추가
sudo yum install -y postgresql-server postgresql-contrib

# 데이터베이스 초기화
sudo postgresql-setup --initdb

# 서비스 시작
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

### macOS 설치

#### Homebrew 사용 (권장)
```bash
# Homebrew로 설치
brew install postgresql@15

# 서비스 시작
brew services start postgresql@15
```

## 🔧 PostgreSQL 초기 설정

### 1. PostgreSQL 접속

#### Windows
```bash
# PostgreSQL 설치 시 자동으로 생성된 사용자로 접속
psql -U postgres
```

#### Linux
```bash
# postgres 사용자로 전환 후 접속
sudo -u postgres psql
```

### 2. 데이터베이스 생성

```sql
-- 데이터베이스 생성
CREATE DATABASE stocke_db;

-- 인코딩 확인 (UTF-8 권장)
-- 생성 시 자동으로 UTF-8로 설정됨

-- 데이터베이스 목록 확인
\l
```

### 3. 사용자 생성 및 권한 부여

```sql
-- 사용자 생성
CREATE USER stocke_user WITH PASSWORD 'your_secure_password_here';

-- 데이터베이스 권한 부여
GRANT ALL PRIVILEGES ON DATABASE stocke_db TO stocke_user;

-- 스키마 권한 부여 (PostgreSQL 15+)
\c stocke_db
GRANT ALL ON SCHEMA public TO stocke_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO stocke_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO stocke_user;

-- 연결 확인
\q
```

### 4. 연결 테스트

```bash
# 새로 생성한 사용자로 접속 테스트
psql -U stocke_user -d stocke_db -h localhost

# 접속 성공 시 다음 명령어로 확인
\conninfo
\q
```

## 🔌 프로젝트 설정 변경

### 1. Python 패키지 설치

```bash
# 가상환경 활성화
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

# PostgreSQL 드라이버 설치
pip install psycopg2-binary

# 또는 소스에서 빌드 (더 안정적)
pip install psycopg2
```

### 2. requirements.txt 업데이트

`requirements.txt`에 다음을 추가:
```
psycopg2-binary>=2.9.0
```

### 3. 환경 변수 설정

`.env` 파일에 PostgreSQL 연결 정보 추가:

```env
# PostgreSQL 데이터베이스 설정
# 형식: postgresql://[user]:[password]@[host]:[port]/[database]
DATABASE_URL=postgresql://stocke_user:your_secure_password_here@localhost:5432/stocke_db

# 또는 환경 변수로 직접 설정
# export DATABASE_URL=postgresql://stocke_user:password@localhost:5432/stocke_db
```

**연결 문자열 형식:**
```
postgresql://[사용자명]:[비밀번호]@[호스트]:[포트]/[데이터베이스명]
```

**예시:**
```
postgresql://stocke_user:mypassword123@localhost:5432/stocke_db
```

### 4. 코드 수정

#### `core/models.py` 수정

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core.config import Config

# DATABASE_URL 가져오기
DATABASE_URL = Config.DATABASE_URL

# PostgreSQL용 엔진 생성
# SQLite와 달리 connect_args가 필요 없음
if DATABASE_URL.startswith('postgresql'):
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,  # 연결 상태 확인
        pool_size=10,        # 연결 풀 크기
        max_overflow=20,     # 최대 오버플로우
        future=True,
    )
else:
    # SQLite용 (기존 코드 유지)
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        future=True,
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
```

## 📦 데이터 마이그레이션 (SQLite → PostgreSQL)

### 방법 1: SQLAlchemy를 통한 자동 마이그레이션 (권장)

```python
# migrate_to_postgresql.py
import sys
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from core.models import Base, PendingBuySignal, AutoTradeCondition, AutoTradeSettings, WatchlistStock, TradingStrategy, StrategySignal, Position
from core.config import Config

def migrate_data():
    # SQLite 연결
    sqlite_url = "sqlite:///./stock_pipeline.db"
    sqlite_engine = create_engine(sqlite_url)
    sqlite_session = sessionmaker(bind=sqlite_engine)()
    
    # PostgreSQL 연결
    postgres_url = Config.DATABASE_URL
    if not postgres_url.startswith('postgresql'):
        print("❌ PostgreSQL URL이 아닙니다!")
        return
    
    postgres_engine = create_engine(postgres_url, pool_pre_ping=True)
    postgres_session = sessionmaker(bind=postgres_engine)()
    
    try:
        # PostgreSQL에 테이블 생성
        print("📦 PostgreSQL에 테이블 생성 중...")
        Base.metadata.create_all(bind=postgres_engine)
        print("✅ 테이블 생성 완료")
        
        # 각 테이블 데이터 마이그레이션
        tables = [
            (PendingBuySignal, sqlite_session.query(PendingBuySignal).all()),
            (AutoTradeCondition, sqlite_session.query(AutoTradeCondition).all()),
            (AutoTradeSettings, sqlite_session.query(AutoTradeSettings).all()),
            (WatchlistStock, sqlite_session.query(WatchlistStock).all()),
            (TradingStrategy, sqlite_session.query(TradingStrategy).all()),
            (StrategySignal, sqlite_session.query(StrategySignal).all()),
            (Position, sqlite_session.query(Position).all()),
        ]
        
        for Model, records in tables:
            if records:
                print(f"📤 {Model.__tablename__}: {len(records)}개 레코드 마이그레이션 중...")
                for record in records:
                    # SQLite에서 가져온 데이터를 PostgreSQL에 삽입
                    postgres_session.merge(record)
                postgres_session.commit()
                print(f"✅ {Model.__tablename__} 마이그레이션 완료")
        
        print("\n🎉 데이터 마이그레이션 완료!")
        
    except Exception as e:
        postgres_session.rollback()
        print(f"❌ 오류 발생: {e}")
        raise
    finally:
        sqlite_session.close()
        postgres_session.close()

if __name__ == "__main__":
    migrate_data()
```

**실행 방법:**
```bash
# .env 파일에 PostgreSQL DATABASE_URL 설정 후
python migrate_to_postgresql.py
```

### 방법 2: pg_dump/pg_restore 사용 (고급)

SQLite 데이터를 CSV로 내보낸 후 PostgreSQL로 가져오기:

```bash
# SQLite 데이터를 CSV로 내보내기
sqlite3 stock_pipeline.db <<EOF
.mode csv
.headers on
.output pending_buy_signals.csv
SELECT * FROM pending_buy_signals;
.quit
EOF

# PostgreSQL로 CSV 가져오기
psql -U stocke_user -d stocke_db -c "\COPY pending_buy_signals FROM 'pending_buy_signals.csv' WITH CSV HEADER;"
```

## ✅ 연결 테스트

### Python 스크립트로 테스트

```python
# test_postgresql_connection.py
from sqlalchemy import create_engine, text
from core.config import Config

def test_connection():
    try:
        engine = create_engine(Config.DATABASE_URL, pool_pre_ping=True)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version();"))
            version = result.fetchone()[0]
            print(f"✅ PostgreSQL 연결 성공!")
            print(f"📊 버전: {version}")
            return True
    except Exception as e:
        print(f"❌ 연결 실패: {e}")
        return False

if __name__ == "__main__":
    test_connection()
```

**실행:**
```bash
python test_postgresql_connection.py
```

## 🔒 보안 설정

### 1. 방화벽 설정

```bash
# PostgreSQL 포트(5432)만 허용
# Windows Firewall
netsh advfirewall firewall add rule name="PostgreSQL" dir=in action=allow protocol=TCP localport=5432

# Linux (ufw)
sudo ufw allow 5432/tcp
```

### 2. pg_hba.conf 설정 (원격 접속 제한)

```bash
# PostgreSQL 설정 파일 위치
# Windows: C:\Program Files\PostgreSQL\15\data\pg_hba.conf
# Linux: /etc/postgresql/15/main/pg_hba.conf

# 로컬 접속만 허용 (기본값)
# host    all             all             127.0.0.1/32            md5
```

### 3. postgresql.conf 설정

```bash
# 최대 연결 수 설정
max_connections = 100

# 공유 메모리 설정
shared_buffers = 256MB

# 로그 설정
logging_collector = on
log_directory = 'log'
log_filename = 'postgresql-%Y-%m-%d.log'
```

## 🛠️ 유용한 명령어

### PostgreSQL 관리 명령어

```sql
-- 데이터베이스 목록
\l

-- 현재 데이터베이스 연결
\c stocke_db

-- 테이블 목록
\dt

-- 테이블 구조 확인
\d pending_buy_signals

-- 사용자 목록
\du

-- 연결 정보 확인
\conninfo

-- 종료
\q
```

### Python에서 사용

```python
from sqlalchemy import create_engine, text
from core.config import Config

engine = create_engine(Config.DATABASE_URL)

# 테이블 목록 확인
with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
    """))
    tables = [row[0] for row in result]
    print(f"테이블 목록: {tables}")
```

## 🐛 문제 해결

### 문제 1: "psycopg2" 모듈을 찾을 수 없음

```bash
# 해결 방법
pip install psycopg2-binary

# 또는 Windows에서 Visual C++ 빌드 도구 필요
pip install psycopg2
```

### 문제 2: "password authentication failed"

```sql
-- PostgreSQL에서 비밀번호 재설정
ALTER USER stocke_user WITH PASSWORD 'new_password';

-- .env 파일의 DATABASE_URL도 업데이트
```

### 문제 3: "could not connect to server"

```bash
# PostgreSQL 서비스 상태 확인
# Windows
sc query postgresql-x64-15

# Linux
sudo systemctl status postgresql

# 서비스 시작
# Windows
net start postgresql-x64-15

# Linux
sudo systemctl start postgresql
```

### 문제 4: "database does not exist"

```sql
-- 데이터베이스 생성
CREATE DATABASE stocke_db;
```

### 문제 5: "permission denied"

```sql
-- 권한 부여
GRANT ALL PRIVILEGES ON DATABASE stocke_db TO stocke_user;
\c stocke_db
GRANT ALL ON SCHEMA public TO stocke_user;
```

## 📊 성능 최적화

### 인덱스 확인

```sql
-- 인덱스 목록 확인
SELECT 
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename, indexname;
```

### 통계 정보 업데이트

```sql
-- 통계 정보 업데이트
ANALYZE;

-- 특정 테이블만
ANALYZE pending_buy_signals;
```

### 연결 풀 설정

```python
# core/models.py에서
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,        # 기본 연결 수
    max_overflow=20,     # 최대 추가 연결 수
    pool_recycle=3600,   # 1시간마다 연결 재생성
    future=True,
)
```

## 🔄 롤백 (PostgreSQL → SQLite)

필요시 다시 SQLite로 돌아가기:

```env
# .env 파일에서
DATABASE_URL=sqlite:///./stock_pipeline.db
```

코드는 자동으로 SQLite 모드로 전환됩니다.

## 📝 체크리스트

- [ ] PostgreSQL 설치 완료
- [ ] 데이터베이스 생성 (`stocke_db`)
- [ ] 사용자 생성 및 권한 부여 (`stocke_user`)
- [ ] `psycopg2-binary` 패키지 설치
- [ ] `.env` 파일에 `DATABASE_URL` 설정
- [ ] `core/models.py` 코드 수정 (PostgreSQL 지원)
- [ ] 연결 테스트 성공
- [ ] 데이터 마이그레이션 완료 (기존 SQLite 데이터가 있는 경우)
- [ ] 애플리케이션 실행 및 테스트

## 📚 참고 자료

- [PostgreSQL 공식 문서](https://www.postgresql.org/docs/)
- [SQLAlchemy PostgreSQL 문서](https://docs.sqlalchemy.org/en/14/dialects/postgresql.html)
- [psycopg2 문서](https://www.psycopg.org/docs/)

