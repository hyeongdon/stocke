#!/bin/bash

# 서버 상태 확인 및 자동 복구 스크립트
# 사용법: ./health_check.sh

set -e

# 프로젝트 디렉토리 설정
PROJECT_DIR="/home/ubuntu/project/stocke"
LOG_DIR="$PROJECT_DIR/logs"
HEALTH_LOG="$LOG_DIR/health_check.log"

# 로그 디렉토리 생성
mkdir -p "$LOG_DIR"

# 로그 함수
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$HEALTH_LOG"
}

log "=== 서버 상태 확인 시작 ==="

# 1. 서버 프로세스 확인
SERVER_PID=$(ps aux | grep "uvicorn core.main:app" | grep -v grep | awk '{print $2}')

if [ -z "$SERVER_PID" ]; then
    log "경고: 서버 프로세스가 실행되지 않음 - 자동 복구 시작"
    cd "$PROJECT_DIR"
    ./restart_server.sh
    exit 0
fi

log "서버 프로세스 확인됨 (PID: $SERVER_PID)"

# 2. 서버 응답 확인
if curl -f http://localhost:8001/docs > /dev/null 2>&1; then
    log "서버 응답 정상"
else
    log "경고: 서버 응답 실패 - 자동 복구 시작"
    cd "$PROJECT_DIR"
    ./restart_server.sh
    exit 0
fi

# 3. API 엔드포인트 확인
API_ENDPOINTS=(
    "/watchlist/sync/status"
    "/strategy/status"
    "/monitoring/status"
)

for endpoint in "${API_ENDPOINTS[@]}"; do
    if curl -f "http://localhost:8001$endpoint" > /dev/null 2>&1; then
        log "API 엔드포인트 확인됨: $endpoint"
    else
        log "경고: API 엔드포인트 응답 실패: $endpoint"
    fi
done

# 4. 메모리 사용량 확인
MEMORY_USAGE=$(ps -p "$SERVER_PID" -o %mem --no-headers | tr -d ' ')
if (( $(echo "$MEMORY_USAGE > 80" | bc -l) )); then
    log "경고: 메모리 사용량 높음 ($MEMORY_USAGE%) - 재시작 권장"
fi

# 5. 디스크 사용량 확인
DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 80 ]; then
    log "경고: 디스크 사용량 높음 ($DISK_USAGE%)"
fi

log "=== 서버 상태 확인 완료 ==="
echo ""
