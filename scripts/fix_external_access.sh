#!/bin/bash

# PostgreSQL 외부 접속 허용 스크립트
# 사용법: ./fix_external_access.sh

set -e

echo "=========================================="
echo "🔧 PostgreSQL 외부 접속 설정"
echo "=========================================="
echo ""

# 1. 현재 iptables 규칙 확인
echo "1. 현재 iptables 규칙 확인..."
EXISTING_RULE=$(sudo iptables -L INPUT -n | grep "5432" || echo "")
if [ -n "$EXISTING_RULE" ]; then
    echo "   기존 규칙:"
    echo "   $EXISTING_RULE"
else
    echo "   ℹ️  외부 접속을 허용하는 규칙이 없음"
fi
echo ""

# 2. 포트 리스닝 확인
echo "2. 포트 리스닝 상태 확인..."
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
        echo "   ✅ 외부 접속 가능하도록 바인딩됨"
    else
        echo "   ⚠️  로컬에서만 접속 가능"
    fi
else
    echo "❌ 포트 5432가 리스닝되지 않음"
    echo "   컨테이너가 실행 중인지 확인하세요"
    exit 1
fi
echo ""

# 3. 외부 접속 허용 규칙 추가
echo "3. iptables 규칙 추가..."
if sudo iptables -C INPUT -p tcp --dport 5432 -j ACCEPT 2>/dev/null; then
    echo "✅ 포트 5432 허용 규칙이 이미 존재함"
else
    echo "   규칙 추가 중..."
    sudo iptables -I INPUT -p tcp --dport 5432 -j ACCEPT
    echo "✅ 규칙 추가 완료"
fi
echo ""

# 4. 규칙 확인
echo "4. 추가된 규칙 확인..."
sudo iptables -L INPUT -n | grep "5432" | head -5
echo ""

# 5. 규칙 저장 (영구적으로 유지)
echo "5. 규칙 저장..."
if command -v iptables-save &> /dev/null; then
    # iptables-persistent가 설치되어 있으면 자동 저장
    if command -v netfilter-persistent &> /dev/null; then
        echo "   netfilter-persistent로 저장 중..."
        sudo netfilter-persistent save 2>/dev/null || echo "   ⚠️  저장 실패 (수동 저장 필요)"
    elif [ -f /etc/iptables/rules.v4 ]; then
        echo "   /etc/iptables/rules.v4에 저장 중..."
        sudo iptables-save | sudo tee /etc/iptables/rules.v4 > /dev/null
    else
        echo "   ⚠️  규칙이 재부팅 후 사라질 수 있습니다"
        echo "   💡 다음 명령으로 수동 저장:"
        echo "      sudo iptables-save > /etc/iptables/rules.v4"
        echo "      또는"
        echo "      sudo apt-get install iptables-persistent"
    fi
else
    echo "   ⚠️  iptables-save를 찾을 수 없음"
fi
echo ""

# 6. 클라우드 보안 그룹 안내
echo "=========================================="
echo "☁️  클라우드 보안 그룹 확인"
echo "=========================================="
echo ""
echo "⚠️  중요: 클라우드 서버를 사용하는 경우"
echo "   클라우드 보안 그룹에서도 포트를 열어야 합니다!"
echo ""
echo "AWS EC2:"
echo "   1. AWS Console → EC2 → Security Groups"
echo "   2. 인스턴스에 연결된 보안 그룹 선택"
echo "   3. 인바운드 규칙 편집 → 규칙 추가"
echo "      - Type: PostgreSQL 또는 Custom TCP"
echo "      - Port: 5432"
echo "      - Source: Your IP (또는 0.0.0.0/0 - 모든 IP)"
echo ""
echo "GCP:"
echo "   1. GCP Console → VPC Network → Firewall"
echo "   2. 방화벽 규칙 만들기"
echo "      - 프로토콜: TCP"
echo "      - 포트: 5432"
echo "      - 소스 IP 범위: Your IP"
echo ""
echo "Azure:"
echo "   1. Azure Portal → Virtual Machines"
echo "   2. 네트워킹 → 인바운드 포트 규칙 추가"
echo "      - 포트: 5432"
echo "      - 프로토콜: TCP"
echo ""

# 7. 테스트 안내
echo "=========================================="
echo "🧪 테스트 방법"
echo "=========================================="
echo ""
echo "다른 컴퓨터에서 테스트:"
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || curl -s ipinfo.io/ip 2>/dev/null || echo "[서버IP]")
echo "   telnet $PUBLIC_IP 5432"
echo "   또는"
echo "   nc -zv $PUBLIC_IP 5432"
echo ""
echo "온라인 도구:"
echo "   https://www.yougetsignal.com/tools/open-ports/"
echo "   포트: 5432, IP: $PUBLIC_IP"
echo ""

echo "=========================================="
echo "✅ 설정 완료"
echo "=========================================="
echo ""
echo "💡 다음 단계:"
echo "   1. 클라우드 보안 그룹에서 포트 5432 허용 (중요!)"
echo "   2. 외부에서 포트 테스트"
echo "   3. DBeaver에서 연결 재시도"
echo ""







