#!/bin/bash

# PostgreSQL 설치 및 상태 확인 스크립트
# 사용법: ./check_postgresql.sh

set -e

echo "=========================================="
echo "🔍 PostgreSQL 설치 확인"
echo "=========================================="
echo ""

# 1. Docker 컨테이너 확인
echo "1. Docker 컨테이너 상태 확인..."
if docker ps | grep -q "postgres-stocke"; then
    echo "✅ PostgreSQL 컨테이너 실행 중"
    docker ps | grep postgres-stocke
else
    echo "❌ PostgreSQL 컨테이너가 실행되지 않음"
    echo ""
    echo "💡 컨테이너 시작:"
    echo "   docker compose start"
    echo "   또는"
    echo "   docker start postgres-stocke"
    exit 1
fi
echo ""

# 2. PostgreSQL 서비스 확인
echo "2. PostgreSQL 서비스 상태 확인..."
if docker exec postgres-stocke pg_isready > /dev/null 2>&1; then
    echo "✅ PostgreSQL 서비스 정상"
    docker exec postgres-stocke pg_isready
else
    echo "❌ PostgreSQL 서비스 응답 없음"
    echo ""
    echo "📋 최근 로그:"
    docker logs postgres-stocke --tail 20
    exit 1
fi
echo ""

# 3. 데이터베이스 접속 테스트
echo "3. 데이터베이스 접속 테스트..."
if docker exec postgres-stocke psql -U stocke_user -d stocke_db -c "SELECT 1;" > /dev/null 2>&1; then
    echo "✅ 데이터베이스 접속 성공"
else
    echo "❌ 데이터베이스 접속 실패"
    echo ""
    echo "💡 데이터베이스 생성이 필요할 수 있습니다:"
    echo "   docker exec -it postgres-stocke psql -U postgres -c \"CREATE DATABASE stocke_db;\""
    exit 1
fi
echo ""

# 4. 버전 확인
echo "4. PostgreSQL 버전 확인..."
VERSION=$(docker exec postgres-stocke psql -U stocke_user -d stocke_db -t -c "SELECT version();" | head -1)
echo "   $VERSION"
echo ""

# 5. 데이터베이스 목록
echo "5. 데이터베이스 목록:"
docker exec postgres-stocke psql -U stocke_user -c "\l" | grep -E "Name|stocke"
echo ""

# 6. 테이블 목록 (있는 경우)
echo "6. 테이블 목록:"
TABLE_COUNT=$(docker exec postgres-stocke psql -U stocke_user -d stocke_db -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';" | tr -d ' ')
if [ "$TABLE_COUNT" -gt 0 ]; then
    echo "   테이블 개수: $TABLE_COUNT"
    docker exec postgres-stocke psql -U stocke_user -d stocke_db -c "\dt" | head -20
else
    echo "   ℹ️  테이블이 없습니다 (정상 - 아직 마이그레이션하지 않았을 수 있음)"
fi
echo ""

# 7. 연결 정보
echo "7. 연결 정보:"
docker exec postgres-stocke psql -U stocke_user -d stocke_db -c "\conninfo"
echo ""

# 8. .env 파일 확인
echo "8. .env 파일 DATABASE_URL 확인..."
if [ -f ".env" ]; then
    if grep -q "DATABASE_URL=postgresql://" .env; then
        echo "✅ .env 파일에 PostgreSQL URL이 설정되어 있습니다"
        grep "DATABASE_URL=" .env | sed 's/\(password=\)[^@]*/\1***/'
    else
        echo "⚠️  .env 파일에 PostgreSQL URL이 설정되지 않았습니다"
        echo ""
        echo "💡 .env 파일에 다음을 추가하세요:"
        echo "   DATABASE_URL=postgresql://stocke_user:비밀번호@localhost:5432/stocke_db"
    fi
else
    echo "⚠️  .env 파일이 없습니다"
fi
echo ""

echo "=========================================="
echo "✅ PostgreSQL 설치 확인 완료!"
echo "=========================================="
echo ""
echo "💡 접속 방법:"
echo "   docker exec -it postgres-stocke psql -U stocke_user -d stocke_db"
echo ""







