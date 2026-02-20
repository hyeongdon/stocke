# PostgreSQL Docker 설치 가이드

## 📋 개요
이 가이드는 Docker를 사용하여 PostgreSQL을 설치하고 관리하는 방법을 설명합니다. 직접 설치 방식과의 차이점도 함께 설명합니다.

## 🔄 직접 설치 vs Docker 방식 비교

### 직접 설치 방식 (apt install)

#### ✅ 장점
- **시스템 통합**: 시스템 서비스로 직접 관리
- **성능**: 네이티브 설치로 약간 더 빠를 수 있음
- **리소스**: 컨테이너 오버헤드 없음
- **디버깅**: 시스템 로그와 통합되어 관리 용이
- **백업**: 표준 PostgreSQL 백업 도구 사용 가능

#### ❌ 단점
- **설치 복잡도**: 패키지 관리, 의존성 해결 필요
- **버전 관리**: 시스템 패키지와 버전 충돌 가능
- **정리 어려움**: 완전 제거가 복잡할 수 있음
- **다중 버전**: 여러 버전 동시 설치 어려움
- **시스템 영향**: 시스템 레벨 설정 변경 필요

### Docker 방식

#### ✅ 장점
- **간편한 설치**: `docker run` 한 줄로 설치 완료
- **격리**: 시스템과 완전히 분리, 다른 서비스에 영향 없음
- **버전 관리**: 원하는 버전 쉽게 선택 및 변경
- **이식성**: 설정 파일로 어디서나 동일 환경 재현
- **정리 용이**: `docker rm` 한 번에 완전 제거
- **다중 인스턴스**: 여러 PostgreSQL 인스턴스 쉽게 실행
- **백업/복원**: 볼륨 마운트로 데이터 관리 간편

#### ❌ 단점
- **Docker 필요**: Docker 설치 필요
- **오버헤드**: 약간의 메모리/CPU 오버헤드 (보통 무시 가능)
- **네트워크**: 포트 매핑 설정 필요
- **학습 곡선**: Docker 기본 개념 이해 필요

## 🐳 Docker 설치 (Ubuntu)

### 1. Docker 설치

```bash
# 기존 Docker 제거 (있는 경우)
sudo apt-get remove docker docker-engine docker.io containerd runc

# Docker 저장소 추가
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release

# Docker 공식 GPG 키 추가
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# Docker 저장소 추가
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Docker 설치
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Docker 서비스 시작
sudo systemctl start docker
sudo systemctl enable docker

# 현재 사용자를 docker 그룹에 추가 (sudo 없이 사용)
sudo usermod -aG docker $USER

# 재로그인 또는 다음 명령 실행
newgrp docker

# 설치 확인
docker --version
docker compose version
```

### 2. Docker Compose 설치 (선택사항, 이미 포함됨)

Docker Compose는 위 단계에서 이미 설치되었습니다. 확인:

```bash
docker compose version
```

## 🚀 PostgreSQL Docker 설치 방법

### 방법 1: docker run 명령어 (간단한 방법)

```bash
# PostgreSQL 컨테이너 실행
docker run -d \
  --name postgres-stocke \
  --restart unless-stopped \
  -e POSTGRES_USER=stocke_user \
  -e POSTGRES_PASSWORD=your_secure_password_here \
  -e POSTGRES_DB=stocke_db \
  -p 5432:5432 \
  -v postgres-stocke-data:/var/lib/postgresql/data \
  postgres:15

# 실행 확인
docker ps

# 로그 확인
docker logs postgres-stocke
```

**명령어 설명:**
- `-d`: 백그라운드 실행
- `--name`: 컨테이너 이름
- `--restart unless-stopped`: 자동 재시작
- `-e POSTGRES_USER`: 데이터베이스 사용자
- `-e POSTGRES_PASSWORD`: 비밀번호
- `-e POSTGRES_DB`: 데이터베이스 이름
- `-p 5432:5432`: 포트 매핑 (호스트:컨테이너)
- `-v postgres-stocke-data`: 데이터 영구 저장 볼륨

### 방법 2: Docker Compose 사용 (권장)

#### docker-compose.yml 생성

```yaml
# docker-compose.yml
version: '3.8'

services:
  postgres:
    image: postgres:15
    container_name: postgres-stocke
    restart: unless-stopped
    environment:
      POSTGRES_USER: stocke_user
      POSTGRES_PASSWORD: your_secure_password_here
      POSTGRES_DB: stocke_db
      POSTGRES_INITDB_ARGS: "--encoding=UTF8"
    ports:
      - "5432:5432"
    volumes:
      - postgres-stocke-data:/var/lib/postgresql/data
      - ./postgres-init:/docker-entrypoint-initdb.d  # 초기화 스크립트 (선택)
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U stocke_user -d stocke_db"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres-stocke-data:
    driver: local
```

#### 실행

```bash
# 컨테이너 시작
docker compose up -d

# 상태 확인
docker compose ps

# 로그 확인
docker compose logs -f postgres

# 중지
docker compose stop

# 시작
docker compose start

# 완전 제거 (데이터는 유지)
docker compose down

# 완전 제거 (데이터도 삭제)
docker compose down -v
```

## 🔧 초기 설정

### 1. 데이터베이스 접속

```bash
# Docker 컨테이너 내부에서 접속
docker exec -it postgres-stocke psql -U stocke_user -d stocke_db

# 또는 호스트에서 직접 접속 (psql이 설치된 경우)
psql -h localhost -p 5432 -U stocke_user -d stocke_db
```

### 2. 권한 설정

```sql
-- 이미 docker-compose.yml에서 사용자와 DB가 생성되었으므로
-- 추가 권한만 설정하면 됩니다

\c stocke_db

-- 스키마 권한 부여
GRANT ALL ON SCHEMA public TO stocke_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO stocke_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO stocke_user;

-- 확인
\du
\q
```

## 🔌 프로젝트 연결 설정

### .env 파일 설정

```env
# Docker PostgreSQL 연결
DATABASE_URL=postgresql://stocke_user:your_secure_password_here@localhost:5432/stocke_db
```

**중요**: Docker 컨테이너는 `localhost:5432`로 접속 가능합니다.

## 📦 데이터 관리

### 볼륨 확인

```bash
# 볼륨 목록
docker volume ls

# 볼륨 상세 정보
docker volume inspect postgres-stocke-data

# 볼륨 위치 확인 (Linux)
docker volume inspect postgres-stocke-data | grep Mountpoint
```

### 백업

```bash
# 데이터베이스 백업
docker exec postgres-stocke pg_dump -U stocke_user stocke_db > backup_$(date +%Y%m%d_%H%M%S).sql

# 또는 볼륨 직접 백업
docker run --rm \
  -v postgres-stocke-data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/postgres-backup-$(date +%Y%m%d).tar.gz /data
```

### 복원

```bash
# SQL 덤프 파일로 복원
docker exec -i postgres-stocke psql -U stocke_user stocke_db < backup_20240101_120000.sql

# 또는 볼륨 복원
docker run --rm \
  -v postgres-stocke-data:/data \
  -v $(pwd):/backup \
  alpine tar xzf /backup/postgres-backup-20240101.tar.gz -C /
```

## 🛠️ 유용한 명령어

### 컨테이너 관리

```bash
# 컨테이너 상태 확인
docker ps -a | grep postgres

# 컨테이너 로그 확인
docker logs postgres-stocke
docker logs -f postgres-stocke  # 실시간 로그

# 컨테이너 재시작
docker restart postgres-stocke

# 컨테이너 중지
docker stop postgres-stocke

# 컨테이너 시작
docker start postgres-stocke

# 컨테이너 제거 (데이터는 유지)
docker rm postgres-stocke

# 컨테이너 + 볼륨 제거 (데이터도 삭제)
docker rm -v postgres-stocke
```

### 데이터베이스 관리

```bash
# 컨테이너 내부 접속
docker exec -it postgres-stocke bash

# PostgreSQL 접속
docker exec -it postgres-stocke psql -U stocke_user -d stocke_db

# SQL 명령 실행
docker exec postgres-stocke psql -U stocke_user -d stocke_db -c "SELECT version();"
```

## 🔒 보안 설정

### 1. 비밀번호 관리

```bash
# 환경 변수 파일 사용 (.env)
# docker-compose.yml에서 env_file 사용
```

```yaml
# docker-compose.yml
services:
  postgres:
    env_file:
      - .env.postgres
```

```env
# .env.postgres
POSTGRES_USER=stocke_user
POSTGRES_PASSWORD=your_secure_password_here
POSTGRES_DB=stocke_db
```

### 2. 네트워크 격리

```yaml
# docker-compose.yml
services:
  postgres:
    networks:
      - stocke-network

networks:
  stocke-network:
    driver: bridge
```

### 3. 포트 제한

```yaml
# 외부 접속 차단, 같은 Docker 네트워크에서만 접속 가능
services:
  postgres:
    ports: []  # 포트 매핑 제거
    expose:
      - "5432"  # 내부 네트워크에서만 접근 가능
```

## 📊 성능 최적화

### Docker Compose 최적화 설정

```yaml
# docker-compose.yml
services:
  postgres:
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '1.0'
          memory: 1G
    command:
      - "postgres"
      - "-c"
      - "shared_buffers=512MB"
      - "-c"
      - "max_connections=100"
      - "-c"
      - "effective_cache_size=1GB"
```

## 🔄 버전 업그레이드

### PostgreSQL 버전 업그레이드

```bash
# 1. 백업
docker exec postgres-stocke pg_dump -U stocke_user stocke_db > backup.sql

# 2. 새 버전 컨테이너 실행
docker run -d \
  --name postgres-stocke-new \
  -e POSTGRES_USER=stocke_user \
  -e POSTGRES_PASSWORD=your_password \
  -e POSTGRES_DB=stocke_db \
  -p 5433:5432 \
  -v postgres-stocke-data-new:/var/lib/postgresql/data \
  postgres:16

# 3. 데이터 복원
docker exec -i postgres-stocke-new psql -U stocke_user stocke_db < backup.sql

# 4. 테스트 후 교체
docker stop postgres-stocke
docker rm postgres-stocke
docker stop postgres-stocke-new
docker rename postgres-stocke-new postgres-stocke
```

## 🐛 문제 해결

### 문제 1: 포트 충돌

```bash
# 포트 사용 확인
sudo netstat -tlnp | grep 5432

# 다른 포트 사용
docker run -d --name postgres-stocke -p 5433:5432 ...
```

### 문제 2: 볼륨 권한 문제

```bash
# 볼륨 권한 수정
docker exec postgres-stocke chown -R postgres:postgres /var/lib/postgresql/data
```

### 문제 3: 컨테이너가 시작되지 않음

```bash
# 로그 확인
docker logs postgres-stocke

# 컨테이너 재생성
docker rm postgres-stocke
docker compose up -d
```

### 문제 4: 데이터 손실 방지

```bash
# 볼륨 백업 (정기적으로)
docker run --rm \
  -v postgres-stocke-data:/source:ro \
  -v $(pwd)/backups:/backup \
  alpine tar czf /backup/postgres-$(date +%Y%m%d).tar.gz -C /source .
```

## 📝 체크리스트

- [ ] Docker 설치 완료
- [ ] Docker Compose 설치 완료
- [ ] docker-compose.yml 생성
- [ ] PostgreSQL 컨테이너 실행
- [ ] 데이터베이스 접속 테스트
- [ ] .env 파일에 DATABASE_URL 설정
- [ ] 프로젝트 연결 테스트
- [ ] 백업 스크립트 설정 (선택)

## 🎯 언제 어떤 방식을 선택할까?

### 직접 설치를 선택하는 경우
- ✅ 프로덕션 환경에서 최고 성능 필요
- ✅ 시스템 관리자가 PostgreSQL 전문가
- ✅ 장기 운영 및 시스템 통합 중요
- ✅ 표준 PostgreSQL 도구 사용 필요

### Docker를 선택하는 경우
- ✅ 빠른 프로토타이핑 및 개발
- ✅ 여러 환경에서 동일한 설정 필요
- ✅ 쉬운 버전 관리 및 업그레이드
- ✅ 시스템에 최소한의 영향
- ✅ 개발/테스트/프로덕션 환경 일관성

## 📚 참고 자료

- [Docker 공식 문서](https://docs.docker.com/)
- [PostgreSQL Docker 이미지](https://hub.docker.com/_/postgres)
- [Docker Compose 문서](https://docs.docker.com/compose/)

