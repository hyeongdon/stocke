#!/bin/bash

# PostgreSQL Docker 설치 및 설정 스크립트
# 사용법: ./setup_postgresql_docker.sh

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=========================================="
echo "🐳 PostgreSQL Docker 설치 스크립트"
echo "=========================================="
echo ""

# 1. Docker 설치 확인
echo "1. Docker 설치 확인 중..."
if ! command -v docker &> /dev/null; then
    echo "❌ Docker가 설치되어 있지 않습니다."
    echo ""
    echo "Docker 설치 방법:"
    echo "  sudo apt-get update"
    echo "  sudo apt-get install -y docker.io docker-compose"
    echo "  sudo systemctl start docker"
    echo "  sudo systemctl enable docker"
    echo "  sudo usermod -aG docker \$USER"
    echo ""
    echo "설치 후 재로그인하거나 다음 명령 실행:"
    echo "  newgrp docker"
    exit 1
fi

if ! command -v docker compose &> /dev/null && ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose가 설치되어 있지 않습니다."
    exit 1
fi

echo "✅ Docker 설치 확인 완료"
echo "   Docker 버전: $(docker --version)"
echo ""

# 2. docker-compose.yml 확인
echo "2. docker-compose.yml 확인 중..."
if [ ! -f "docker-compose.yml" ]; then
    echo "❌ docker-compose.yml 파일이 없습니다."
    echo "   프로젝트 루트에 docker-compose.yml 파일을 생성하세요."
    exit 1
fi
echo "✅ docker-compose.yml 확인 완료"
echo ""

# 3. 비밀번호 설정
echo "3. PostgreSQL 비밀번호 설정"
if [ -f ".env" ] && grep -q "POSTGRES_PASSWORD" .env; then
    echo "ℹ️  .env 파일에 POSTGRES_PASSWORD가 이미 설정되어 있습니다."
    read -p "   기존 비밀번호를 사용하시겠습니까? (y/n): " use_existing
    if [ "$use_existing" != "y" ]; then
        read -sp "   새 비밀번호를 입력하세요: " POSTGRES_PASSWORD
        echo ""
        if [ -z "$POSTGRES_PASSWORD" ]; then
            echo "❌ 비밀번호가 입력되지 않았습니다."
            exit 1
        fi
        # .env 파일에 POSTGRES_PASSWORD 추가 또는 업데이트
        if grep -q "POSTGRES_PASSWORD" .env; then
            sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$POSTGRES_PASSWORD/" .env
        else
            echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" >> .env
        fi
        export POSTGRES_PASSWORD
    fi
else
    read -sp "   PostgreSQL 비밀번호를 입력하세요: " POSTGRES_PASSWORD
    echo ""
    if [ -z "$POSTGRES_PASSWORD" ]; then
        echo "❌ 비밀번호가 입력되지 않았습니다."
        exit 1
    fi
    # .env 파일 생성 또는 업데이트
    if [ ! -f ".env" ]; then
        touch .env
    fi
    if grep -q "POSTGRES_PASSWORD" .env; then
        sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$POSTGRES_PASSWORD/" .env
    else
        echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" >> .env
    fi
    export POSTGRES_PASSWORD
fi
echo "✅ 비밀번호 설정 완료"
echo ""

# 4. 기존 컨테이너 확인
echo "4. 기존 PostgreSQL 컨테이너 확인 중..."
if docker ps -a | grep -q "postgres-stocke"; then
    echo "⚠️  기존 postgres-stocke 컨테이너가 발견되었습니다."
    read -p "   기존 컨테이너를 제거하고 새로 시작하시겠습니까? (y/n): " remove_existing
    if [ "$remove_existing" == "y" ]; then
        echo "   기존 컨테이너 중지 및 제거 중..."
        docker compose down -v 2>/dev/null || docker-compose down -v 2>/dev/null || true
        docker rm -f postgres-stocke 2>/dev/null || true
        echo "✅ 기존 컨테이너 제거 완료"
    else
        echo "ℹ️  기존 컨테이너를 유지합니다."
        echo "   기존 컨테이너 시작 중..."
        docker compose start 2>/dev/null || docker-compose start 2>/dev/null || docker start postgres-stocke
        echo "✅ 기존 컨테이너 시작 완료"
        echo ""
        echo "=========================================="
        echo "✅ PostgreSQL Docker 설정 완료"
        echo "=========================================="
        exit 0
    fi
fi
echo ""

# 5. PostgreSQL 컨테이너 시작
echo "5. PostgreSQL 컨테이너 시작 중..."
if command -v docker compose &> /dev/null; then
    docker compose up -d
else
    docker-compose up -d
fi

# 컨테이너 시작 대기
echo "   컨테이너 시작 대기 중..."
sleep 5

# 컨테이너 상태 확인
if docker ps | grep -q "postgres-stocke"; then
    echo "✅ PostgreSQL 컨테이너 시작 완료"
else
    echo "❌ 컨테이너 시작 실패"
    echo "   로그 확인: docker logs postgres-stocke"
    exit 1
fi
echo ""

# 6. 연결 테스트
echo "6. PostgreSQL 연결 테스트 중..."
sleep 5  # PostgreSQL 초기화 대기

if docker exec postgres-stocke pg_isready -U stocke_user -d stocke_db > /dev/null 2>&1; then
    echo "✅ PostgreSQL 연결 성공"
else
    echo "⚠️  연결 테스트 실패 (아직 초기화 중일 수 있습니다)"
    echo "   잠시 후 다시 시도하세요: docker exec postgres-stocke pg_isready -U stocke_user"
fi
echo ""

# 7. DATABASE_URL 설정 안내
echo "7. 프로젝트 설정"
echo ""
echo "📝 .env 파일에 다음을 추가하세요:"
echo ""
echo "   DATABASE_URL=postgresql://stocke_user:${POSTGRES_PASSWORD:-your_password}@localhost:5432/stocke_db"
echo ""

# 8. 유용한 명령어 안내
echo "=========================================="
echo "✅ PostgreSQL Docker 설치 완료!"
echo "=========================================="
echo ""
echo "💡 유용한 명령어:"
echo ""
echo "   # 컨테이너 상태 확인"
echo "   docker ps | grep postgres"
echo ""
echo "   # 로그 확인"
echo "   docker logs postgres-stocke"
echo ""
echo "   # PostgreSQL 접속"
echo "   docker exec -it postgres-stocke psql -U stocke_user -d stocke_db"
echo ""
echo "   # 컨테이너 중지"
echo "   docker compose stop"
echo ""
echo "   # 컨테이너 시작"
echo "   docker compose start"
echo ""
echo "   # 컨테이너 제거 (데이터는 유지)"
echo "   docker compose down"
echo ""
echo "   # 컨테이너 + 데이터 제거"
echo "   docker compose down -v"
echo ""

