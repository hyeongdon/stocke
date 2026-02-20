#!/bin/bash

# PostgreSQL 외부 접속 완전 수정 스크립트
# 사용법: ./fix_all_postgresql_access.sh

set -e

echo "=========================================="
echo "🔧 PostgreSQL 외부 접속 완전 수정"
echo "=========================================="
echo ""

# 1. 컨테이너 상태 확인
echo "1. Docker 컨테이너 상태 확인..."
if ! docker ps | grep -q "postgres-stocke"; then
    echo "❌ 컨테이너가 실행되지 않음"
    echo "   컨테이너 시작 중..."
    docker compose up -d
    sleep 5
fi
echo "✅ 컨테이너 실행 중"
echo ""

# 2. docker-compose.yml 포트 매핑 확인
echo "2. docker-compose.yml 포트 매핑 확인..."
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if grep -q "5432:5432" docker-compose.yml || grep -q "0.0.0.0:5432:5432" docker-compose.yml; then
    echo "✅ 포트 매핑 설정 확인됨"
else
    echo "⚠️  포트 매핑 확인 필요"
    echo "   docker-compose.yml에서 ports: \"5432:5432\" 확인"
fi
echo ""

# 3. 컨테이너 재시작 (포트 바인딩 확인)
echo "3. 컨테이너 재시작 (포트 바인딩 확인)..."
docker compose restart postgres
sleep 5
echo "✅ 컨테이너 재시작 완료"
echo ""

# 4. 포트 리스닝 확인
echo "4. 포트 리스닝 상태 확인..."
if command -v ss &> /dev/null; then
    LISTENING=$(sudo ss -tlnp 2>/dev/null | grep 5432 || echo "")
elif command -v netstat &> /dev/null; then
    LISTENING=$(sudo netstat -tlnp 2>/dev/null | grep 5432 || echo "")
else
    LISTENING=""
fi

if [ -n "$LISTENING" ]; then
    echo "✅ 포트 5432 리스닝 중:"
    echo "$LISTENING"
    if echo "$LISTENING" | grep -q "0.0.0.0:5432\|:::5432"; then
        echo "   ✅ 외부 접속 가능 (0.0.0.0에 바인딩됨)"
    elif echo "$LISTENING" | grep -q "127.0.0.1:5432"; then
        echo "   ❌ 로컬에서만 접속 가능 (127.0.0.1에만 바인딩됨)"
        echo "   💡 docker-compose.yml 확인 필요"
    fi
else
    echo "❌ 포트 5432가 리스닝되지 않음"
    echo "   컨테이너 로그 확인:"
    docker logs postgres-stocke --tail 20
    exit 1
fi
echo ""

# 5. iptables INPUT 규칙 추가
echo "5. iptables INPUT 규칙 추가..."
if sudo iptables -C INPUT -p tcp --dport 5432 -j ACCEPT 2>/dev/null; then
    echo "✅ 포트 5432 허용 규칙이 이미 존재함"
else
    echo "   규칙 추가 중..."
    sudo iptables -I INPUT -p tcp --dport 5432 -j ACCEPT
    echo "✅ 규칙 추가 완료"
fi

# 규칙 확인
echo "   현재 INPUT 규칙:"
sudo iptables -L INPUT -n | grep "5432" | head -3
echo ""

# 6. iptables 규칙 저장
echo "6. iptables 규칙 저장..."
if command -v netfilter-persistent &> /dev/null; then
    sudo netfilter-persistent save 2>/dev/null && echo "✅ 규칙 저장 완료" || echo "⚠️  규칙 저장 실패 (수동 저장 필요)"
elif [ -d /etc/iptables ]; then
    sudo iptables-save | sudo tee /etc/iptables/rules.v4 > /dev/null 2>&1 && echo "✅ 규칙 저장 완료" || echo "⚠️  규칙 저장 실패"
else
    echo "⚠️  iptables-persistent가 설치되지 않음"
    echo "   다음 명령으로 설치: sudo apt-get install iptables-persistent"
fi
echo ""

# 7. 로컬 접속 테스트
echo "7. 로컬 접속 테스트..."
if docker exec postgres-stocke pg_isready -U stocke_user -d stocke_db > /dev/null 2>&1; then
    echo "✅ 로컬 접속 성공"
else
    echo "❌ 로컬 접속 실패"
    echo "   PostgreSQL 서비스에 문제가 있을 수 있음"
fi
echo ""

# 8. 서버 IP 확인
echo "8. 서버 IP 주소:"
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || curl -s ipinfo.io/ip 2>/dev/null || echo "확인 불가")
if [ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "확인 불가" ]; then
    echo "   공인 IP: $PUBLIC_IP"
else
    echo "   공인 IP: 확인 불가"
fi
echo ""

# 9. 종합 확인
echo "=========================================="
echo "📋 종합 확인"
echo "=========================================="
echo ""

ISSUES=0

# 포트 리스닝 확인
if [ -z "$LISTENING" ]; then
    echo "❌ 포트가 리스닝되지 않음"
    ISSUES=$((ISSUES + 1))
elif echo "$LISTENING" | grep -q "127.0.0.1"; then
    echo "❌ 포트가 로컬에만 바인딩됨"
    ISSUES=$((ISSUES + 1))
else
    echo "✅ 포트가 외부 접속 가능하도록 바인딩됨"
fi

# iptables 규칙 확인
if sudo iptables -L INPUT -n | grep -q "5432"; then
    echo "✅ iptables INPUT 규칙 존재"
else
    echo "❌ iptables INPUT 규칙 없음"
    ISSUES=$((ISSUES + 1))
fi

echo ""
if [ $ISSUES -eq 0 ]; then
    echo "✅ 서버 설정 완료!"
    echo ""
    echo "⚠️  클라우드 보안 그룹 확인:"
    echo "   OCI Console → Security Lists → Ingress Rules"
    echo "   포트 5432 규칙이 추가되어 있는지 확인"
    echo ""
    echo "🧪 테스트:"
    if [ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "확인 불가" ]; then
        echo "   telnet $PUBLIC_IP 5432"
        echo "   또는"
        echo "   https://www.yougetsignal.com/tools/open-ports/"
        echo "   포트: 5432, IP: $PUBLIC_IP"
    fi
else
    echo "❌ $ISSUES 개의 문제가 발견되었습니다"
    echo "   위의 오류를 확인하고 수정하세요"
fi
echo ""







