#!/bin/bash

# 방화벽 및 포트 상태 확인 스크립트 (UFW 없이도 작동)
# 사용법: ./check_firewall.sh

set -e

echo "=========================================="
echo "🔥 방화벽 및 포트 상태 확인"
echo "=========================================="
echo ""

# 1. 방화벽 시스템 확인
echo "1. 방화벽 시스템 확인..."
if command -v ufw &> /dev/null; then
    echo "✅ UFW 설치됨"
    echo "   상태: $(sudo ufw status 2>/dev/null | head -1 || echo '확인 불가')"
elif command -v firewall-cmd &> /dev/null; then
    echo "✅ firewalld 설치됨"
    echo "   상태: $(sudo firewall-cmd --state 2>/dev/null || echo '확인 불가')"
elif command -v iptables &> /dev/null; then
    echo "✅ iptables 사용 중"
else
    echo "ℹ️  로컬 방화벽이 없거나 클라우드 보안 그룹 사용 중"
fi
echo ""

# 2. 포트 리스닝 확인
echo "2. 포트 5432 리스닝 상태 확인..."
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
            echo "      ✅ 외부 접속 가능 (0.0.0.0에 바인딩됨)"
        elif echo "$line" | grep -q "127.0.0.1:5432"; then
            echo "      ⚠️  로컬에서만 접속 가능 (127.0.0.1에만 바인딩됨)"
            echo "      💡 docker-compose.yml에서 포트 매핑 확인 필요"
        fi
    done
else
    echo "❌ 포트 5432가 리스닝되지 않음"
    echo "   컨테이너가 실행 중인지 확인하세요"
fi
echo ""

# 3. Docker 컨테이너 포트 매핑 확인
echo "3. Docker 컨테이너 포트 매핑 확인..."
if docker ps | grep -q "postgres-stocke"; then
    PORT_MAPPING=$(docker port postgres-stocke 2>/dev/null | grep 5432 || echo "")
    if [ -n "$PORT_MAPPING" ]; then
        echo "✅ 포트 매핑: $PORT_MAPPING"
        if echo "$PORT_MAPPING" | grep -q "0.0.0.0"; then
            echo "   ✅ 모든 IP에서 접속 가능"
        else
            echo "   ⚠️  특정 IP만 접속 가능"
        fi
    else
        echo "❌ 포트 매핑을 찾을 수 없음"
    fi
else
    echo "❌ PostgreSQL 컨테이너가 실행되지 않음"
fi
echo ""

# 4. iptables 규칙 확인 (있는 경우)
echo "4. iptables 규칙 확인..."
if command -v iptables &> /dev/null; then
    IPTABLES_RULES=$(sudo iptables -L -n 2>/dev/null | grep -E "5432|ACCEPT|REJECT" | head -10 || echo "")
    if [ -n "$IPTABLES_RULES" ]; then
        echo "iptables 규칙 (일부):"
        echo "$IPTABLES_RULES" | while read line; do
            echo "   $line"
        done
    else
        echo "ℹ️  iptables 규칙이 없거나 확인 불가"
    fi
else
    echo "ℹ️  iptables가 설치되지 않음"
fi
echo ""

# 5. firewalld 상태 확인 (있는 경우)
echo "5. firewalld 상태 확인..."
if command -v firewall-cmd &> /dev/null; then
    FIREWALLD_STATUS=$(sudo firewall-cmd --state 2>/dev/null || echo "비활성")
    echo "   상태: $FIREWALLD_STATUS"
    if [ "$FIREWALLD_STATUS" = "running" ]; then
        OPEN_PORTS=$(sudo firewall-cmd --list-ports 2>/dev/null || echo "")
        if echo "$OPEN_PORTS" | grep -q "5432"; then
            echo "   ✅ 포트 5432가 열려있음"
        else
            echo "   ⚠️  포트 5432가 열려있지 않음"
            echo "   💡 다음 명령으로 열기: sudo firewall-cmd --permanent --add-port=5432/tcp && sudo firewall-cmd --reload"
        fi
    fi
else
    echo "ℹ️  firewalld가 설치되지 않음"
fi
echo ""

# 6. 클라우드 서비스 확인
echo "6. 클라우드 서비스 확인..."
if [ -f /sys/class/dmi/id/product_name ]; then
    PRODUCT=$(cat /sys/class/dmi/id/product_name 2>/dev/null || echo "")
    if echo "$PRODUCT" | grep -qi "amazon\|aws"; then
        echo "   ☁️  AWS EC2 인스턴스로 보임"
        echo "   💡 Security Group에서 인바운드 규칙 확인 필요"
    elif echo "$PRODUCT" | grep -qi "google\|gcp"; then
        echo "   ☁️  GCP 인스턴스로 보임"
        echo "   💡 Firewall Rules에서 규칙 확인 필요"
    elif echo "$PRODUCT" | grep -qi "microsoft\|azure"; then
        echo "   ☁️  Azure 인스턴스로 보임"
        echo "   💡 Network Security Group에서 규칙 확인 필요"
    fi
fi

# 메타데이터 서비스 확인
if curl -s --max-time 2 http://169.254.169.254/latest/meta-data/ &>/dev/null; then
    echo "   ☁️  AWS EC2 인스턴스 확인됨"
    echo "   💡 AWS Console → EC2 → Security Groups에서 포트 5432 허용 필요"
elif curl -s --max-time 2 -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/ &>/dev/null; then
    echo "   ☁️  GCP 인스턴스 확인됨"
    echo "   💡 GCP Console → VPC Network → Firewall에서 규칙 추가 필요"
fi
echo ""

# 7. 요약 및 권장사항
echo "=========================================="
echo "📋 요약 및 권장사항"
echo "=========================================="
echo ""

if [ -n "$LISTENING" ] && echo "$LISTENING" | grep -q "0.0.0.0"; then
    echo "✅ 포트가 외부 접속을 허용하도록 설정됨"
else
    echo "⚠️  포트 설정 확인 필요"
    echo "   docker-compose.yml에서 포트 매핑 확인:"
    echo "   ports:"
    echo "     - \"0.0.0.0:5432:5432\"  또는 \"5432:5432\""
fi
echo ""

echo "💡 다음 단계:"
echo "   1. 클라우드 보안 그룹에서 포트 5432 허용"
echo "   2. 외부에서 포트 테스트: telnet [서버IP] 5432"
echo "   3. DBeaver에서 연결 재시도"
echo ""







