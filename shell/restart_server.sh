#!/bin/bash

# 서버 재시작 스크립트
# 사용법: ./restart_server.sh

set -e  # 오류 발생 시 스크립트 중단

# 프로젝트 디렉토리 설정
PROJECT_DIR="/home/ubuntu/project/stocke"
LOG_DIR="$PROJECT_DIR/logs"
RESTART_LOG="$LOG_DIR/restart.log"

# 로그 디렉토리 생성
mkdir -p "$LOG_DIR"

# 로그 함수
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$RESTART_LOG"
}

log "=== 서버 재시작 시작 ==="

# 프로젝트 디렉토리로 이동
cd "$PROJECT_DIR"

# 1. 현재 실행 중인 서버 프로세스 확인 및 종료
log "현재 실행 중인 서버 프로세스 확인 중..."
SERVER_PID=$(ps aux | grep "uvicorn core.main:app" | grep -v grep | awk '{print $2}')

if [ -n "$SERVER_PID" ]; then
    log "서버 프로세스 발견 (PID: $SERVER_PID) - 종료 중..."
    
    # SIGTERM으로 정상 종료 시도
    kill "$SERVER_PID"
    
    # 10초 대기
    sleep 10
    
    # 여전히 실행 중인지 확인
    if kill -0 "$SERVER_PID" 2>/dev/null; then
        log "정상 종료 실패 - 강제 종료 중..."
        kill -9 "$SERVER_PID"
        sleep 5
    fi
    
    log "서버 프로세스 종료 완료"
else
    log "실행 중인 서버 프로세스가 없습니다"
fi

# 2. Git 최신 코드 가져오기
log "Git 최신 코드 가져오기 중..."
git pull origin main

# 3. 가상환경 활성화 및 의존성 확인
log "가상환경 활성화 중..."
source venv/bin/activate

# 4. 서버 재시작
log "서버 재시작 중..."
# PYTHONPATH 설정 및 core.main 모듈로 실행
export PYTHONPATH="$PROJECT_DIR:$PYTHONPATH"
nohup uvicorn core.main:app --host 0.0.0.0 --port 8001 --reload > server.log 2>&1 &

# 5. 서버 시작 확인
sleep 5
NEW_PID=$(ps aux | grep "uvicorn core.main:app" | grep -v grep | awk '{print $2}')

if [ -n "$NEW_PID" ]; then
    log "서버 재시작 성공 (새 PID: $NEW_PID)"
    
    # 서버 응답 확인
    sleep 10
    if curl -f http://localhost:8001/docs > /dev/null 2>&1; then
        log "서버 응답 확인 완료"
    else
        log "경고: 서버 응답 확인 실패"
    fi
else
    log "오류: 서버 재시작 실패"
    exit 1
fi

log "=== 서버 재시작 완료 ==="
echo ""
