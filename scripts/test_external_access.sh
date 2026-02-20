#!/bin/bash

# PostgreSQL 외부 접속 가능 여부 테스트 스크립트
# 사용법: ./test_external_access.sh

set -e

echo "=========================================="
echo "🔍 PostgreSQL 외부 접속 테스트"
echo "=========================================="
echo ""

# 1. 컨테이너 상태 확인
echo "1. Docker 컨테이너 상태 확인..."
if docker ps | grep -q "postgres-stocke"; then
    echo "✅ PostgreSQL 컨테이너 실행 중"
    docker ps | grep postgres-stocke
else
    echo "❌ PostgreSQL 컨테이너가 실행되지 않음"
    echo "   docker compose start 실행 필요"
    exit 1
fi
echo ""

# 2. 포트 매핑 확인
echo "2. 포트 매핑 확인..."
PORT_MAPPING=$(docker port postgres-stocke 2>/dev/null | grep 5432 || echo "")
if [ -n "$PORT_MAPPING" ]; then
    echo "✅ 포트 매핑: $PORT_MAPPING"
    if echo "$PORT_MAPPING" | grep -q "0.0.0.0"; then
        echo "   ✅ 모든 IP에서 접속 가능"
    else
        echo "   ⚠️  특정 IP만 접속 가능할 수 있음"
    fi
else
    echo "❌ 포트 매핑을 찾을 수 없음"
fi
echo ""

# 3. 포트 리스닝 확인
echo "3. 포트 리스닝 확인..."
if command -v netstat &> /dev/null; then
    LISTENING=$(sudo netstat -tlnp 2>/dev/null | grep 5432 || echo "")
elif command -v ss &> /dev/null; then
    LISTENING=$(sudo ss -tlnp 2>/dev/null | grep 5432 || echo "")
else
    LISTENING=""
fi

if [ -n "$LISTENING" ]; then
    echo "✅ 포트 5432가 리스닝 중:"
    echo "$LISTENING" | while read line; do
        echo "   $line"
        if echo "$line" | grep -q "0.0.0.0:5432\|:::5432"; then
            echo "      ✅ 외부 접속 가능"
        elif echo "$line" | grep -q "127.0.0.1:5432"; then
            echo "      ⚠️  로컬에서만 접속 가능"
        fi
    done
else
    echo "❌ 포트 5432가 리스닝되지 않음"
fi
echo ""

# 4. 방화벽 확인
echo "4. 방화벽 상태 확인..."
if command -v ufw &> /dev/null; then
    UFW_STATUS=$(sudo ufw status 2>/dev/null | grep -E "5432|Status" || echo "")
    if [ -n "$UFW_STATUS" ]; then
        echo "UFW 상태:"
        echo "$UFW_STATUS" | while read line; do
            echo "   $line"
        done
        if echo "$UFW_STATUS" | grep -q "5432"; then
            echo "   ✅ 포트 5432 규칙이 있음"
        else
            echo "   ⚠️  포트 5432 규칙이 없음"
            echo "   💡 다음 명령으로 열기: sudo ufw allow 5432/tcp"
        fi
    else
        echo "   ℹ️  UFW가 비활성화되어 있음"
    fi
else
    echo "   ℹ️  UFW가 설치되지 않음 (다른 방화벽 사용 중일 수 있음)"
fi
echo ""

# 5. 로컬 접속 테스트
echo "5. 로컬 접속 테스트..."
if docker exec postgres-stocke pg_isready -U stocke_user -d stocke_db > /dev/null 2>&1; then
    echo "✅ 로컬 접속 성공"
else
    echo "❌ 로컬 접속 실패"
    echo "   PostgreSQL 서비스에 문제가 있을 수 있음"
fi
echo ""

# 6. 서버 IP 확인
echo "6. 서버 IP 주소:"
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || curl -s ipinfo.io/ip 2>/dev/null || echo "확인 불가")
LOCAL_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || ip addr show | grep "inet " | grep -v 127.0.0.1 | head -1 | awk '{print $2}' | cut -d/ -f1)

if [ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "확인 불가" ]; then
    echo "   공인 IP: $PUBLIC_IP"
fi
if [ -n "$LOCAL_IP" ]; then
    echo "   로컬 IP: $LOCAL_IP"
fi
echo ""

# 7. 외부 접속 테스트 안내
echo "=========================================="
echo "🧪 외부 접속 테스트 방법"
echo "=========================================="
echo ""
echo "다른 컴퓨터에서 다음 명령으로 테스트하세요:"
echo ""
if [ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "확인 불가" ]; then
    echo "   telnet $PUBLIC_IP 5432"
    echo "   또는"
    echo "   nc -zv $PUBLIC_IP 5432"
    echo ""
    echo "온라인 도구 사용:"
    echo "   https://www.yougetsignal.com/tools/open-ports/"
    echo "   포트: 5432, IP: $PUBLIC_IP"
else
    echo "   telnet [서버IP] 5432"
    echo "   또는"
    echo "   nc -zv [서버IP] 5432"
fi
echo ""

# 8. 클라우드 보안 그룹 안내
echo "=========================================="
echo "☁️  클라우드 보안 그룹 확인"
echo "=========================================="
echo ""
echo "클라우드 서버(AWS, GCP, Azure)를 사용하는 경우:"
echo "   보안 그룹/방화벽 규칙에서 인바운드 규칙 추가:"
echo "   - Type: PostgreSQL 또는 Custom TCP"
echo "   - Port: 5432"
echo "   - Source: Your IP (또는 특정 IP)"
echo ""

# 9. 요약
echo "=========================================="
echo "📋 요약"
echo "=========================================="
echo ""
echo "DBeaver 연결 정보:"
echo "   Host: $PUBLIC_IP"
echo "   Port: 5432"
echo "   Database: stocke_db"
echo "   Username: stocke_user"
echo ""

if [ -n "$LISTENING" ] && echo "$LISTENING" | grep -q "0.0.0.0"; then
    echo "✅ 포트가 외부 접속을 허용하도록 설정됨"
else
    echo "⚠️  포트 설정을 확인하세요"
fi

if command -v ufw &> /dev/null && sudo ufw status | grep -q "5432"; then
    echo "✅ 방화벽 규칙이 설정됨"
else
    echo "⚠️  방화벽 규칙 확인 필요"
fi
echo ""







