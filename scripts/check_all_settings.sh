#!/bin/bash

# PostgreSQL 외부 접속 문제 종합 확인 스크립트
# 사용법: ./check_all_settings.sh

set -e

echo "=========================================="
echo "🔍 PostgreSQL 외부 접속 종합 확인"
echo "=========================================="
echo ""

# 1. 포트 리스닝 확인
echo "1. 포트 리스닝 상태:"
if command -v ss &> /dev/null; then
    LISTENING=$(sudo ss -tlnp 2>/dev/null | grep 5432 || echo "")
elif command -v netstat &> /dev/null; then
    LISTENING=$(sudo netstat -tlnp 2>/dev/null | grep 5432 || echo "")
else
    LISTENING=""
fi

if [ -n "$LISTENING" ]; then
    echo "$LISTENING"
    if echo "$LISTENING" | grep -q "0.0.0.0:5432\|:::5432"; then
        echo "✅ 포트가 외부 접속 가능하도록 바인딩됨"
    else
        echo "❌ 포트가 로컬에만 바인딩됨"
    fi
else
    echo "❌ 포트가 리스닝되지 않음"
fi
echo ""

# 2. iptables INPUT 규칙 확인
echo "2. iptables INPUT 규칙:"
INPUT_RULES=$(sudo iptables -L INPUT -n 2>/dev/null | grep "5432" || echo "")
if [ -n "$INPUT_RULES" ]; then
    echo "✅ INPUT 규칙 존재:"
    echo "$INPUT_RULES" | while read line; do
        echo "   $line"
    done
else
    echo "❌ INPUT 체인에 포트 5432 허용 규칙이 없음"
    echo "   💡 다음 명령으로 추가:"
    echo "      sudo iptables -I INPUT -p tcp --dport 5432 -j ACCEPT"
fi
echo ""

# 3. iptables FORWARD 규칙 확인 (Docker)
echo "3. iptables FORWARD 규칙 (Docker):"
FORWARD_RULES=$(sudo iptables -L FORWARD -n 2>/dev/null | grep -E "5432|172.18" | head -5 || echo "")
if [ -n "$FORWARD_RULES" ]; then
    echo "✅ FORWARD 규칙 존재:"
    echo "$FORWARD_RULES" | while read line; do
        echo "   $line"
    done
else
    echo "ℹ️  FORWARD 규칙 없음 (정상일 수 있음)"
fi
echo ""

# 4. iptables 전체 체인 확인
echo "4. iptables 전체 체인 상태:"
echo "   INPUT 체인 기본 정책:"
INPUT_POLICY=$(sudo iptables -L INPUT -n 2>/dev/null | head -1 | grep -o "policy [A-Z]*" || echo "확인 불가")
echo "   $INPUT_POLICY"

# ACCEPT 규칙 개수
ACCEPT_COUNT=$(sudo iptables -L INPUT -n 2>/dev/null | grep -c "ACCEPT" || echo "0")
echo "   ACCEPT 규칙 개수: $ACCEPT_COUNT"

# DROP/REJECT 규칙 개수
DROP_COUNT=$(sudo iptables -L INPUT -n 2>/dev/null | grep -cE "DROP|REJECT" || echo "0")
echo "   DROP/REJECT 규칙 개수: $DROP_COUNT"
echo ""

# 5. Docker 컨테이너 상태
echo "5. Docker 컨테이너 상태:"
if docker ps | grep -q "postgres-stocke"; then
    echo "✅ 컨테이너 실행 중"
    PORT_MAPPING=$(docker port postgres-stocke 2>/dev/null | grep 5432 || echo "")
    if [ -n "$PORT_MAPPING" ]; then
        echo "   포트 매핑: $PORT_MAPPING"
    fi
else
    echo "❌ 컨테이너가 실행되지 않음"
fi
echo ""

# 6. 로컬 접속 테스트
echo "6. 로컬 접속 테스트:"
if docker exec postgres-stocke pg_isready -U stocke_user -d stocke_db > /dev/null 2>&1; then
    echo "✅ 로컬 접속 성공"
else
    echo "❌ 로컬 접속 실패"
fi
echo ""

# 7. 서버 IP 확인
echo "7. 서버 IP 주소:"
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || curl -s ipinfo.io/ip 2>/dev/null || echo "확인 불가")
if [ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "확인 불가" ]; then
    echo "   공인 IP: $PUBLIC_IP"
else
    echo "   공인 IP: 확인 불가"
fi
echo ""

# 8. 종합 진단
echo "=========================================="
echo "📋 종합 진단 결과"
echo "=========================================="
echo ""

ISSUES=0
WARNINGS=0

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

# iptables INPUT 규칙 확인
if [ -z "$INPUT_RULES" ]; then
    echo "❌ iptables INPUT 규칙 없음"
    ISSUES=$((ISSUES + 1))
else
    echo "✅ iptables INPUT 규칙 존재"
fi

# INPUT 정책 확인
if echo "$INPUT_POLICY" | grep -q "DROP\|REJECT"; then
    echo "⚠️  INPUT 체인 기본 정책이 DROP/REJECT"
    WARNINGS=$((WARNINGS + 1))
fi

echo ""

# 9. 해결 방법 제시
if [ $ISSUES -eq 0 ]; then
    echo "✅ 서버 설정은 정상입니다!"
    echo ""
    echo "⚠️  클라우드 보안 그룹 확인 필요:"
    echo "   1. OCI Security Lists에 포트 5432 규칙이 있는지 확인"
    echo "   2. NSG를 사용하는 경우, NSG에도 규칙 추가 확인"
    echo "   3. 규칙이 저장되었는지 확인 (페이지 새로고침)"
    echo ""
    echo "🧪 테스트:"
    if [ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "확인 불가" ]; then
        echo "   온라인 도구: https://www.yougetsignal.com/tools/open-ports/"
        echo "   포트: 5432, IP: $PUBLIC_IP"
        echo ""
        echo "   Windows에서: telnet $PUBLIC_IP 5432"
    fi
else
    echo "❌ $ISSUES 개의 문제가 발견되었습니다"
    echo ""
    echo "🔧 해결 방법:"
    if [ -z "$INPUT_RULES" ]; then
        echo "   1. iptables 규칙 추가:"
        echo "      sudo iptables -I INPUT -p tcp --dport 5432 -j ACCEPT"
        echo "      sudo apt-get install -y iptables-persistent"
        echo "      sudo netfilter-persistent save"
        echo ""
    fi
fi

if [ $WARNINGS -gt 0 ]; then
    echo "⚠️  $WARNINGS 개의 경고가 있습니다"
fi

echo ""







