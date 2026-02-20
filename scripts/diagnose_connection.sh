#!/bin/bash

# PostgreSQL 외부 접속 문제 진단 스크립트
# 사용법: ./diagnose_connection.sh

set -e

echo "=========================================="
echo "🔍 PostgreSQL 외부 접속 문제 진단"
echo "=========================================="
echo ""

# 1. 컨테이너 상태
echo "1. Docker 컨테이너 상태:"
if docker ps | grep -q "postgres-stocke"; then
    echo "✅ 컨테이너 실행 중"
    docker ps | grep postgres-stocke
else
    echo "❌ 컨테이너가 실행되지 않음"
    echo "   docker compose start 실행 필요"
    exit 1
fi
echo ""

# 2. 포트 매핑
echo "2. Docker 포트 매핑:"
PORT_MAPPING=$(docker port postgres-stocke 2>/dev/null | grep 5432 || echo "")
if [ -n "$PORT_MAPPING" ]; then
    echo "✅ $PORT_MAPPING"
    if echo "$PORT_MAPPING" | grep -q "0.0.0.0"; then
        echo "   ✅ 모든 IP에서 접속 가능하도록 설정됨"
    else
        echo "   ⚠️  특정 IP만 허용"
    fi
else
    echo "❌ 포트 매핑을 찾을 수 없음"
fi
echo ""

# 3. 포트 리스닝 확인
echo "3. 포트 리스닝 상태:"
if command -v netstat &> /dev/null; then
    LISTENING=$(sudo netstat -tlnp 2>/dev/null | grep 5432 || echo "")
elif command -v ss &> /dev/null; then
    LISTENING=$(sudo ss -tlnp 2>/dev/null | grep 5432 || echo "")
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
        echo "   💡 docker-compose.yml에서 포트 매핑 확인 필요"
    fi
else
    echo "❌ 포트 5432가 리스닝되지 않음"
fi
echo ""

# 4. iptables INPUT 규칙 확인
echo "4. iptables INPUT 규칙 (외부 접속 허용):"
INPUT_RULE=$(sudo iptables -L INPUT -n 2>/dev/null | grep "5432" || echo "")
if [ -n "$INPUT_RULE" ]; then
    echo "✅ INPUT 규칙 존재:"
    echo "$INPUT_RULE" | while read line; do
        echo "   $line"
    done
else
    echo "❌ INPUT 체인에 포트 5432 허용 규칙이 없음"
    echo "   💡 다음 명령으로 추가:"
    echo "      sudo iptables -I INPUT -p tcp --dport 5432 -j ACCEPT"
fi
echo ""

# 5. iptables FORWARD 규칙 확인 (Docker)
echo "5. iptables FORWARD 규칙 (Docker):"
FORWARD_RULE=$(sudo iptables -L FORWARD -n 2>/dev/null | grep "5432\|172.18" | head -3 || echo "")
if [ -n "$FORWARD_RULE" ]; then
    echo "✅ FORWARD 규칙 존재:"
    echo "$FORWARD_RULE" | while read line; do
        echo "   $line"
    done
else
    echo "ℹ️  FORWARD 규칙 없음 (정상일 수 있음)"
fi
echo ""

# 6. 서버 IP 확인
echo "6. 서버 IP 주소:"
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || curl -s ipinfo.io/ip 2>/dev/null || echo "확인 불가")
LOCAL_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "확인 불가")

if [ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "확인 불가" ]; then
    echo "   공인 IP: $PUBLIC_IP"
fi
if [ -n "$LOCAL_IP" ] && [ "$LOCAL_IP" != "확인 불가" ]; then
    echo "   로컬 IP: $LOCAL_IP"
fi
echo ""

# 7. 로컬 접속 테스트
echo "7. 로컬 접속 테스트:"
if docker exec postgres-stocke pg_isready -U stocke_user -d stocke_db > /dev/null 2>&1; then
    echo "✅ 로컬 접속 성공"
else
    echo "❌ 로컬 접속 실패"
fi
echo ""

# 8. 클라우드 서비스 확인
echo "8. 클라우드 서비스 확인:"
if curl -s --max-time 2 http://169.254.169.254/latest/meta-data/ &>/dev/null 2>&1; then
    echo "☁️  AWS EC2 인스턴스 확인됨"
    echo "   ⚠️  Security Group에서 인바운드 규칙 확인 필요!"
    echo "   AWS Console → EC2 → Security Groups → 인바운드 규칙"
    echo "   Type: PostgreSQL 또는 Custom TCP, Port: 5432"
elif curl -s --max-time 2 -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/ &>/dev/null 2>&1; then
    echo "☁️  GCP 인스턴스 확인됨"
    echo "   ⚠️  Firewall Rules에서 규칙 확인 필요!"
    echo "   GCP Console → VPC Network → Firewall"
elif curl -s --max-time 2 -H "Metadata:true" http://169.254.169.254/metadata/instance &>/dev/null 2>&1; then
    echo "☁️  Azure 인스턴스 확인됨"
    echo "   ⚠️  Network Security Group에서 규칙 확인 필요!"
elif curl -s --max-time 2 http://169.254.169.254/opc/v1/instance/ &>/dev/null 2>&1; then
    echo "☁️  Oracle Cloud Infrastructure (OCI) 인스턴스 확인됨"
    echo "   ⚠️  Security Lists 또는 Network Security Groups 확인 필요!"
    echo "   OCI Console → Networking → Virtual Cloud Networks"
    echo "   → Security Lists → Ingress Rules → 포트 5432 추가"
else
    echo "ℹ️  클라우드 서비스 자동 감지 실패"
    echo "   수동으로 클라우드 보안 그룹 확인 필요"
fi
echo ""

# 9. 종합 진단
echo "=========================================="
echo "📋 종합 진단 결과"
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
fi

# iptables 규칙 확인
if [ -z "$INPUT_RULE" ]; then
    echo "❌ iptables INPUT 규칙 없음"
    ISSUES=$((ISSUES + 1))
fi

# 클라우드 보안 그룹 안내
echo "⚠️  클라우드 보안 그룹 확인 필요 (수동 확인)"

if [ $ISSUES -eq 0 ]; then
    echo ""
    echo "✅ 서버 설정은 정상으로 보입니다"
    echo "   클라우드 보안 그룹 설정을 확인하세요!"
else
    echo ""
    echo "❌ $ISSUES 개의 문제가 발견되었습니다"
    echo "   위의 해결 방법을 참고하여 수정하세요"
fi
echo ""

# 10. 해결 방법 제시
echo "=========================================="
echo "🔧 해결 방법"
echo "=========================================="
echo ""

if [ -z "$INPUT_RULE" ]; then
    echo "1. iptables 규칙 추가:"
    echo "   sudo iptables -I INPUT -p tcp --dport 5432 -j ACCEPT"
    echo "   sudo apt-get install iptables-persistent"
    echo "   sudo netfilter-persistent save"
    echo ""
fi

echo "2. 클라우드 보안 그룹 설정 (가장 중요!):"
echo "   - AWS: Security Groups → 인바운드 규칙 → PostgreSQL (5432) 추가"
echo "   - GCP: Firewall Rules → TCP 5432 허용"
echo "   - Azure: Network Security Group → 인바운드 규칙 추가"
echo "   - OCI: Security Lists 또는 Network Security Groups → Ingress Rules → 포트 5432 추가"
echo "      OCI Console → Networking → Virtual Cloud Networks"
echo "      → Security Lists → Ingress Rules → Add Ingress Rules"
echo ""

echo "3. 포트 테스트:"
if [ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "확인 불가" ]; then
    echo "   telnet $PUBLIC_IP 5432"
    echo "   또는"
    echo "   https://www.yougetsignal.com/tools/open-ports/"
    echo "   포트: 5432, IP: $PUBLIC_IP"
else
    echo "   telnet [서버IP] 5432"
fi
echo ""

