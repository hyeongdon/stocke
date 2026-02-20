#!/bin/bash

# PostgreSQL Docker 컨테이너 재시작 스크립트 (locale 오류 수정)
# 사용법: ./fix_postgresql_docker.sh

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=========================================="
echo "🔧 PostgreSQL Docker 컨테이너 수정"
echo "=========================================="
echo ""

# 1. 기존 컨테이너 중지 및 제거
echo "1. 기존 컨테이너 중지 및 제거 중..."
if docker ps -a | grep -q "postgres-stocke"; then
    echo "   컨테이너 중지 중..."
    docker compose down 2>/dev/null || docker-compose down 2>/dev/null || docker stop postgres-stocke 2>/dev/null || true
    echo "   컨테이너 제거 중..."
    docker rm -f postgres-stocke 2>/dev/null || true
    echo "✅ 기존 컨테이너 제거 완료"
else
    echo "ℹ️  기존 컨테이너가 없습니다"
fi
echo ""

# 2. 기존 볼륨 제거 (선택사항)
echo "2. 기존 볼륨 제거 여부 확인"
read -p "   기존 데이터를 삭제하고 새로 시작하시겠습니까? (y/n): " remove_volume
if [ "$remove_volume" == "y" ]; then
    echo "   볼륨 제거 중..."
    docker volume rm postgres-stocke-data 2>/dev/null || true
    echo "✅ 볼륨 제거 완료"
else
    echo "ℹ️  기존 볼륨 유지 (데이터 보존)"
fi
echo ""

# 3. docker-compose.yml 확인
echo "3. docker-compose.yml 확인 중..."
if [ ! -f "docker-compose.yml" ]; then
    echo "❌ docker-compose.yml 파일이 없습니다."
    exit 1
fi

# locale 설정이 있는지 확인
if grep -q "locale=ko_KR.UTF-8" docker-compose.yml; then
    echo "⚠️  docker-compose.yml에 locale 설정이 있습니다."
    echo "   locale 설정을 제거해야 합니다."
    echo ""
    read -p "   자동으로 수정하시겠습니까? (y/n): " fix_compose
    if [ "$fix_compose" == "y" ]; then
        # locale 설정 제거
        sed -i 's/--locale=ko_KR.UTF-8//g' docker-compose.yml
        sed -i 's/  *--/ --/g' docker-compose.yml  # 공백 정리
        sed -i 's/--encoding=UTF8  *"/--encoding=UTF8"/g' docker-compose.yml
        echo "✅ docker-compose.yml 수정 완료"
    fi
fi
echo ""

# 4. PostgreSQL 컨테이너 시작
echo "4. PostgreSQL 컨테이너 시작 중..."
if command -v docker compose &> /dev/null; then
    docker compose up -d
else
    docker-compose up -d
fi

# 컨테이너 시작 대기
echo "   컨테이너 시작 대기 중..."
sleep 10

# 컨테이너 상태 확인
echo "5. 컨테이너 상태 확인 중..."
if docker ps | grep -q "postgres-stocke"; then
    echo "✅ PostgreSQL 컨테이너 실행 중"
    
    # 로그 확인
    echo ""
    echo "📋 최근 로그 (오류 확인):"
    docker logs postgres-stocke --tail 20
    
    # 연결 테스트
    echo ""
    echo "6. PostgreSQL 연결 테스트 중..."
    sleep 5
    
    for i in {1..10}; do
        if docker exec postgres-stocke pg_isready -U stocke_user -d stocke_db > /dev/null 2>&1; then
            echo "✅ PostgreSQL 연결 성공!"
            break
        else
            if [ $i -eq 10 ]; then
                echo "❌ 연결 실패 (10회 시도 후 실패)"
                echo ""
                echo "📋 최근 로그:"
                docker logs postgres-stocke --tail 30
            else
                echo "   시도 $i/10... 대기 중..."
                sleep 3
            fi
        fi
    done
else
    echo "❌ 컨테이너 시작 실패"
    echo ""
    echo "📋 로그 확인:"
    docker logs postgres-stocke --tail 50
    exit 1
fi
echo ""

echo "=========================================="
echo "✅ PostgreSQL Docker 수정 완료!"
echo "=========================================="
echo ""
echo "💡 다음 단계:"
echo "   # .env 파일에 DATABASE_URL 추가"
echo "   DATABASE_URL=postgresql://stocke_user:your_password@localhost:5432/stocke_db"
echo ""
echo "   # 연결 테스트"
echo "   python scripts/test_postgresql_connection.py"
echo ""







