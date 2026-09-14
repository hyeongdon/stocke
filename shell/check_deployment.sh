#!/bin/bash

# 서버 배포 상태 종합 확인 스크립트
# 사용법: ./check_deployment.sh

set -e

# 프로젝트 디렉토리 설정
PROJECT_DIR="/home/ubuntu/project/stocke"
LOG_DIR="$PROJECT_DIR/logs"
STATUS_LOG="$LOG_DIR/deployment_status.log"

# 로그 디렉토리 생성
mkdir -p "$LOG_DIR"

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 로그 함수
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$STATUS_LOG"
}

# 상태 출력 함수
print_status() {
    if [ "$1" = "OK" ]; then
        echo -e "${GREEN}✅ $2${NC}"
    elif [ "$1" = "WARN" ]; then
        echo -e "${YELLOW}⚠️  $2${NC}"
    else
        echo -e "${RED}❌ $2${NC}"
    fi
}

echo ""
echo "=========================================="
echo "🚀 서버 배포 상태 종합 확인"
echo "=========================================="
echo ""

log "=== 서버 배포 상태 확인 시작 ==="

# 1. 서버 프로세스 확인
echo -e "${BLUE}1. 서버 프로세스 확인${NC}"
SERVER_PID=$(ps aux | grep "uvicorn core.main:app" | grep -v grep | awk '{print $2}')

if [ -z "$SERVER_PID" ]; then
    print_status "ERROR" "서버 프로세스가 실행되지 않음"
else
    print_status "OK" "서버 프로세스 실행 중 (PID: $SERVER_PID)"
    
    # 프로세스 상세 정보
    CPU_USAGE=$(ps -p "$SERVER_PID" -o %cpu --no-headers | tr -d ' ')
    MEM_USAGE=$(ps -p "$SERVER_PID" -o %mem --no-headers | tr -d ' ')
    echo "   CPU 사용률: ${CPU_USAGE}%"
    echo "   메모리 사용률: ${MEM_USAGE}%"
    
    if (( $(echo "$MEM_USAGE > 80" | bc -l 2>/dev/null || echo "0") )); then
        print_status "WARN" "메모리 사용량이 높음 (${MEM_USAGE}%)"
    fi
fi
echo ""

# 2. 서버 응답 확인
echo -e "${BLUE}2. 서버 응답 확인${NC}"
if curl -f -s http://localhost:8001/docs > /dev/null 2>&1; then
    print_status "OK" "서버 응답 정상 (http://localhost:8001)"
else
    print_status "ERROR" "서버 응답 실패"
fi
echo ""

# 3. API 엔드포인트 확인
echo -e "${BLUE}3. 주요 API 엔드포인트 확인${NC}"
API_ENDPOINTS=(
    "/watchlist/sync/status:관심종목 동기화"
    "/monitoring/status:모니터링 상태"
    "/positions/:포지션 목록"
    "/signals/pending:매수대기 신호"
    "/stop-loss/status:손절익절 상태"
)

for endpoint_info in "${API_ENDPOINTS[@]}"; do
    IFS=':' read -r endpoint name <<< "$endpoint_info"
    if curl -f -s "http://localhost:8001$endpoint" > /dev/null 2>&1; then
        print_status "OK" "$name"
    else
        print_status "ERROR" "$name (응답 실패)"
    fi
done
echo ""

# 4. 데이터베이스 확인
echo -e "${BLUE}4. 데이터베이스 확인${NC}"
if [ -f "$PROJECT_DIR/stocke.db" ]; then
    DB_SIZE=$(du -h "$PROJECT_DIR/stocke.db" | cut -f1)
    print_status "OK" "데이터베이스 파일 존재 (크기: $DB_SIZE)"
else
    print_status "WARN" "데이터베이스 파일을 찾을 수 없음"
fi
echo ""

# 5. 로그 파일 확인
echo -e "${BLUE}5. 로그 파일 확인${NC}"
if [ -d "$LOG_DIR" ]; then
    LOG_COUNT=$(find "$LOG_DIR" -name "*.log" -type f | wc -l)
    print_status "OK" "로그 디렉토리 존재 (로그 파일: $LOG_COUNT개)"
    
    # 최근 로그 확인
    if [ -f "$LOG_DIR/health_check.log" ]; then
        LAST_CHECK=$(tail -1 "$LOG_DIR/health_check.log" 2>/dev/null | cut -d']' -f1 | tr -d '[')
        echo "   마지막 상태 확인: $LAST_CHECK"
    fi
else
    print_status "WARN" "로그 디렉토리가 없음"
fi
echo ""

# 6. 디스크 사용량 확인
echo -e "${BLUE}6. 디스크 사용량 확인${NC}"
DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -lt 80 ]; then
    print_status "OK" "디스크 사용량: ${DISK_USAGE}%"
else
    print_status "WARN" "디스크 사용량이 높음: ${DISK_USAGE}%"
fi
echo ""

# 7. 네트워크 포트 확인
echo -e "${BLUE}7. 네트워크 포트 확인${NC}"
if netstat -tuln 2>/dev/null | grep -q ":8001"; then
    print_status "OK" "포트 8001 리스닝 중"
else
    print_status "WARN" "포트 8001이 리스닝되지 않음"
fi
echo ""

# 8. Git 상태 확인
echo -e "${BLUE}8. Git 상태 확인${NC}"
cd "$PROJECT_DIR"
if [ -d ".git" ]; then
    CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
    LAST_COMMIT=$(git log -1 --format="%h - %s" 2>/dev/null || echo "unknown")
    print_status "OK" "현재 브랜치: $CURRENT_BRANCH"
    echo "   최근 커밋: $LAST_COMMIT"
    
    # 최신 코드 확인
    git fetch origin > /dev/null 2>&1
    LOCAL=$(git rev-parse HEAD 2>/dev/null)
    REMOTE=$(git rev-parse origin/main 2>/dev/null)
    if [ "$LOCAL" = "$REMOTE" ]; then
        print_status "OK" "최신 코드 상태"
    else
        print_status "WARN" "업데이트 가능한 코드가 있음"
    fi
else
    print_status "WARN" "Git 저장소가 아님"
fi
echo ""

# 9. 가상환경 확인
echo -e "${BLUE}9. 가상환경 확인${NC}"
if [ -d "$PROJECT_DIR/venv" ]; then
    print_status "OK" "가상환경 디렉토리 존재"
    if [ -f "$PROJECT_DIR/venv/bin/activate" ]; then
        print_status "OK" "가상환경 활성화 스크립트 존재"
    else
        print_status "WARN" "가상환경 활성화 스크립트 없음"
    fi
else
    print_status "WARN" "가상환경 디렉토리가 없음"
fi
echo ""

# 10. 최근 서버 로그 확인
echo -e "${BLUE}10. 최근 서버 로그 확인${NC}"
if [ -f "$PROJECT_DIR/server.log" ]; then
    ERROR_COUNT=$(tail -100 "$PROJECT_DIR/server.log" 2>/dev/null | grep -i "error" | wc -l)
    if [ "$ERROR_COUNT" -eq 0 ]; then
        print_status "OK" "최근 100줄에 오류 없음"
    else
        print_status "WARN" "최근 100줄에 오류 $ERROR_COUNT개 발견"
    fi
else
    print_status "WARN" "서버 로그 파일이 없음"
fi
echo ""

# 종합 상태
echo "=========================================="
echo "📊 종합 상태"
echo "=========================================="

# 오류 카운트
ERROR_COUNT=0
WARN_COUNT=0

# 서버 프로세스
if [ -z "$SERVER_PID" ]; then
    ERROR_COUNT=$((ERROR_COUNT + 1))
fi

# 서버 응답
if ! curl -f -s http://localhost:8001/docs > /dev/null 2>&1; then
    ERROR_COUNT=$((ERROR_COUNT + 1))
fi

# 메모리 사용량
if [ -n "$SERVER_PID" ]; then
    MEM_USAGE=$(ps -p "$SERVER_PID" -o %mem --no-headers | tr -d ' ')
    if (( $(echo "$MEM_USAGE > 80" | bc -l 2>/dev/null || echo "0") )); then
        WARN_COUNT=$((WARN_COUNT + 1))
    fi
fi

# 디스크 사용량
if [ "$DISK_USAGE" -gt 80 ]; then
    WARN_COUNT=$((WARN_COUNT + 1))
fi

# 결과 출력
if [ "$ERROR_COUNT" -eq 0 ] && [ "$WARN_COUNT" -eq 0 ]; then
    print_status "OK" "모든 상태 정상"
    echo ""
    echo "💡 서버가 정상적으로 실행 중입니다."
elif [ "$ERROR_COUNT" -eq 0 ]; then
    print_status "WARN" "경고 $WARN_COUNT개 발견 (정상 동작 중)"
    echo ""
    echo "💡 서버는 정상 동작 중이지만 일부 경고가 있습니다."
else
    print_status "ERROR" "오류 $ERROR_COUNT개, 경고 $WARN_COUNT개 발견"
    echo ""
    echo "💡 서버에 문제가 있을 수 있습니다. ./restart_server.sh 실행을 권장합니다."
fi

echo ""
log "=== 서버 배포 상태 확인 완료 ==="
echo ""

