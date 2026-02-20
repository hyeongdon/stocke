#!/bin/bash

# PostgreSQL 외부 접속 정보 출력 스크립트
# 사용법: ./get_postgresql_connection_info.sh

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=========================================="
echo "📋 PostgreSQL 외부 접속 정보"
echo "=========================================="
echo ""

# 1. 서버 IP 주소
echo "1. 서버 IP 주소:"
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || curl -s ipinfo.io/ip 2>/dev/null || echo "확인 불가")
LOCAL_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || ip addr show | grep "inet " | grep -v 127.0.0.1 | head -1 | awk '{print $2}' | cut -d/ -f1)

if [ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "확인 불가" ]; then
    echo "   공인 IP: $PUBLIC_IP"
fi
if [ -n "$LOCAL_IP" ]; then
    echo "   로컬 IP: $LOCAL_IP"
fi
echo ""

# 2. 포트 확인
echo "2. 포트 정보:"
if docker ps | grep -q "postgres-stocke"; then
    PORT_MAPPING=$(docker port postgres-stocke 2>/dev/null | grep 5432 || echo "확인 불가")
    if [ "$PORT_MAPPING" != "확인 불가" ]; then
        echo "   $PORT_MAPPING"
    else
        echo "   5432 (기본값)"
    fi
else
    echo "   ⚠️  PostgreSQL 컨테이너가 실행되지 않음"
fi
echo ""

# 3. 데이터베이스 정보
echo "3. 데이터베이스 정보:"
if [ -f "docker-compose.yml" ]; then
    DB_NAME=$(grep "POSTGRES_DB" docker-compose.yml | awk -F'=' '{print $2}' | tr -d ' ' | tr -d '"' || echo "stocke_db")
    DB_USER=$(grep "POSTGRES_USER" docker-compose.yml | awk -F'=' '{print $2}' | tr -d ' ' | tr -d '"' || echo "stocke_user")
    
    echo "   데이터베이스: $DB_NAME"
    echo "   사용자: $DB_USER"
else
    echo "   ⚠️  docker-compose.yml 파일을 찾을 수 없음"
fi
echo ""

# 4. 비밀번호 정보
echo "4. 비밀번호:"
if [ -f "docker-compose.yml" ]; then
    PASSWORD=$(grep "POSTGRES_PASSWORD" docker-compose.yml | grep -v "^#" | awk -F'=' '{print $2}' | tr -d ' ' | tr -d '"' | tr -d "'" || echo "")
    if [ -n "$PASSWORD" ] && [ "$PASSWORD" != "change_me_secure_password" ]; then
        echo "   docker-compose.yml에서 확인: POSTGRES_PASSWORD"
    elif [ -f ".env" ]; then
        ENV_PASSWORD=$(grep "POSTGRES_PASSWORD" .env | grep -v "^#" | awk -F'=' '{print $2}' | tr -d ' ' || echo "")
        if [ -n "$ENV_PASSWORD" ]; then
            echo "   .env 파일에서 확인: POSTGRES_PASSWORD"
        else
            echo "   ⚠️  비밀번호를 찾을 수 없음"
        fi
    else
        echo "   ⚠️  비밀번호를 찾을 수 없음"
    fi
else
    echo "   ⚠️  docker-compose.yml 파일을 찾을 수 없음"
fi
echo ""

# 5. DBeaver 연결 정보
echo "=========================================="
echo "🔌 DBeaver 연결 정보"
echo "=========================================="
echo ""
echo "새 연결 생성 시 다음 정보를 입력하세요:"
echo ""
echo "Host: $PUBLIC_IP"
echo "Port: 5432"
echo "Database: $DB_NAME"
echo "Username: $DB_USER"
echo "Password: [docker-compose.yml 또는 .env에서 확인]"
echo ""

# 6. 연결 문자열
echo "=========================================="
echo "📝 연결 문자열"
echo "=========================================="
echo ""
echo "JDBC URL:"
echo "jdbc:postgresql://$PUBLIC_IP:5432/$DB_NAME"
echo ""
echo "PostgreSQL URL:"
echo "postgresql://$DB_USER:[비밀번호]@$PUBLIC_IP:5432/$DB_NAME"
echo ""

# 7. 방화벽 확인
echo "=========================================="
echo "🔥 방화벽 상태"
echo "=========================================="
echo ""
if command -v ufw &> /dev/null; then
    UFW_STATUS=$(sudo ufw status | grep "5432" || echo "포트 5432 규칙 없음")
    echo "UFW 상태:"
    echo "$UFW_STATUS"
    if echo "$UFW_STATUS" | grep -q "5432"; then
        echo "✅ 포트 5432가 열려있습니다"
    else
        echo "⚠️  포트 5432가 열려있지 않을 수 있습니다"
        echo "   다음 명령으로 열기: sudo ufw allow 5432/tcp"
    fi
else
    echo "ℹ️  UFW가 설치되지 않았거나 다른 방화벽을 사용 중입니다"
fi
echo ""

# 8. 접속 테스트
echo "=========================================="
echo "🧪 접속 테스트"
echo "=========================================="
echo ""
if docker ps | grep -q "postgres-stocke"; then
    if docker exec postgres-stocke pg_isready -U $DB_USER -d $DB_NAME > /dev/null 2>&1; then
        echo "✅ PostgreSQL 서비스 정상"
    else
        echo "❌ PostgreSQL 서비스 응답 없음"
    fi
else
    echo "⚠️  PostgreSQL 컨테이너가 실행되지 않음"
fi
echo ""

echo "=========================================="
echo "✅ 정보 출력 완료"
echo "=========================================="
echo ""
echo "💡 참고:"
echo "   - 클라우드 서버인 경우 보안 그룹에서 포트 5432를 열어야 합니다"
echo "   - 더 안전한 접속을 위해 SSH 터널링을 사용하는 것을 권장합니다"
echo ""







