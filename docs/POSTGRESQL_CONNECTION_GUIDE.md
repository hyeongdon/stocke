# PostgreSQL 접속 및 확인 가이드

## 🔍 설치 확인 방법

### 1. Docker 컨테이너 상태 확인

```bash
# 컨테이너 실행 상태 확인
docker ps | grep postgres

# 또는 상세 정보
docker ps -a | grep postgres

# 컨테이너 로그 확인
docker logs postgres-stocke --tail 50
```

**정상 상태:**
```
CONTAINER ID   IMAGE         STATUS         PORTS                    NAMES
a4f806d0406e   postgres:15   Up 2 minutes   0.0.0.0:5432->5432/tcp  postgres-stocke
```

### 2. PostgreSQL 서비스 상태 확인

```bash
# 컨테이너 내부에서 PostgreSQL 프로세스 확인
docker exec postgres-stocke pg_isready -U stocke_user -d stocke_db

# 또는 간단히
docker exec postgres-stocke pg_isready
```

**정상 응답:**
```
/var/run/postgresql:5432 - accepting connections
```

## 🔌 접속 방법

### 방법 1: Docker exec를 통한 접속 (가장 간단)

```bash
# PostgreSQL에 직접 접속
docker exec -it postgres-stocke psql -U stocke_user -d stocke_db

# 또는 postgres 사용자로 접속
docker exec -it postgres-stocke psql -U postgres
```

**접속 후 사용 가능한 명령어:**
```sql
-- 데이터베이스 목록
\l

-- 현재 데이터베이스의 테이블 목록
\dt

-- 테이블 구조 확인
\d table_name

-- SQL 쿼리 실행
SELECT version();

-- 연결 정보 확인
\conninfo

-- 종료
\q
```

### 방법 2: 호스트에서 직접 접속 (psql 설치 필요)

#### psql 설치
```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y postgresql-client

# 설치 확인
psql --version
```

#### 접속
```bash
# 기본 접속
psql -h localhost -p 5432 -U stocke_user -d stocke_db

# 비밀번호 입력 프롬프트가 나타나면 docker-compose.yml에 설정한 비밀번호 입력
```

### 방법 3: Python 스크립트로 접속

```bash
# 연결 테스트 스크립트 실행
cd ~/project/stocke
python3 scripts/test_postgresql_connection.py
```

## 📊 기본 확인 명령어

### 1. 데이터베이스 목록 확인

```bash
docker exec -it postgres-stocke psql -U stocke_user -c "\l"
```

### 2. 테이블 목록 확인

```bash
docker exec -it postgres-stocke psql -U stocke_user -d stocke_db -c "\dt"
```

### 3. PostgreSQL 버전 확인

```bash
docker exec -it postgres-stocke psql -U stocke_user -d stocke_db -c "SELECT version();"
```

### 4. 사용자 목록 확인

```bash
docker exec -it postgres-stocke psql -U stocke_user -d stocke_db -c "\du"
```

### 5. 연결 정보 확인

```bash
docker exec -it postgres-stocke psql -U stocke_user -d stocke_db -c "\conninfo"
```

## 🛠️ 문제 해결

### 문제 1: "psql: error: connection to server failed"

**원인:** 컨테이너가 실행되지 않음

**해결:**
```bash
# 컨테이너 시작
docker compose start
# 또는
docker start postgres-stocke

# 상태 확인
docker ps | grep postgres
```

### 문제 2: "password authentication failed"

**원인:** 잘못된 비밀번호

**해결:**
```bash
# 비밀번호 확인
cat docker-compose.yml | grep POSTGRES_PASSWORD
# 또는
cat .env | grep POSTGRES_PASSWORD

# 비밀번호 재설정 (필요시)
docker exec -it postgres-stocke psql -U postgres -c "ALTER USER stocke_user WITH PASSWORD 'new_password';"
```

### 문제 3: "database does not exist"

**원인:** 데이터베이스가 생성되지 않음

**해결:**
```bash
# 데이터베이스 생성
docker exec -it postgres-stocke psql -U postgres -c "CREATE DATABASE stocke_db;"

# 권한 부여
docker exec -it postgres-stocke psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE stocke_db TO stocke_user;"
```

### 문제 4: "permission denied"

**원인:** 권한 부족

**해결:**
```bash
# 권한 부여
docker exec -it postgres-stocke psql -U postgres -d stocke_db -c "GRANT ALL ON SCHEMA public TO stocke_user;"
docker exec -it postgres-stocke psql -U postgres -d stocke_db -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO stocke_user;"
```

## 📝 빠른 참조 명령어

```bash
# ============================================
# PostgreSQL Docker 컨테이너 관리
# ============================================

# 컨테이너 상태 확인
docker ps | grep postgres

# 컨테이너 시작
docker compose start

# 컨테이너 중지
docker compose stop

# 컨테이너 재시작
docker compose restart

# 로그 확인
docker logs postgres-stocke

# 실시간 로그 확인
docker logs -f postgres-stocke

# ============================================
# PostgreSQL 접속
# ============================================

# 기본 접속
docker exec -it postgres-stocke psql -U stocke_user -d stocke_db

# postgres 사용자로 접속
docker exec -it postgres-stocke psql -U postgres

# ============================================
# 데이터베이스 확인
# ============================================

# 데이터베이스 목록
docker exec postgres-stocke psql -U stocke_user -c "\l"

# 테이블 목록
docker exec postgres-stocke psql -U stocke_user -d stocke_db -c "\dt"

# 버전 확인
docker exec postgres-stocke psql -U stocke_user -d stocke_db -c "SELECT version();"

# 연결 테스트
docker exec postgres-stocke pg_isready -U stocke_user -d stocke_db

# ============================================
# 데이터 관리
# ============================================

# 백업
docker exec postgres-stocke pg_dump -U stocke_user stocke_db > backup.sql

# 복원
docker exec -i postgres-stocke psql -U stocke_user stocke_db < backup.sql
```

## ✅ 설치 확인 체크리스트

다음 명령어들을 순서대로 실행하여 정상 설치 여부를 확인하세요:

```bash
# 1. 컨테이너 실행 확인
docker ps | grep postgres-stocke
# ✅ 결과: 컨테이너가 "Up" 상태로 표시되어야 함

# 2. PostgreSQL 서비스 확인
docker exec postgres-stocke pg_isready
# ✅ 결과: "accepting connections" 메시지

# 3. 데이터베이스 접속 테스트
docker exec -it postgres-stocke psql -U stocke_user -d stocke_db -c "SELECT 1;"
# ✅ 결과: "1" 출력

# 4. 버전 확인
docker exec postgres-stocke psql -U stocke_user -d stocke_db -c "SELECT version();"
# ✅ 결과: PostgreSQL 버전 정보 출력

# 5. Python 스크립트 테스트
cd ~/project/stocke
python3 scripts/test_postgresql_connection.py
# ✅ 결과: "PostgreSQL 연결 성공!" 메시지
```

모든 체크리스트가 통과하면 정상적으로 설치된 것입니다!







