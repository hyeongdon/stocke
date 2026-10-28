#!/usr/bin/env bash

set -u

PROJECT_DIR="${PROJECT_DIR:-/home/ubuntu/project/stocke}"
BRANCH="${DEPLOY_BRANCH:-main}"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/source_sync.log"
# 프로젝트 루트에 두면 git status 에 미추적 파일로 잡혀 동기화가 매번 중단된다
LOCK_FILE="$LOG_DIR/.source_sync.lock"
PYTHON="$PROJECT_DIR/venv/bin/python"
NOTIFY="$PROJECT_DIR/scripts/notify_deployment_sync.py"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

notify() {
    local status="$1"
    local message="$2"
    if [ -x "$PYTHON" ] && [ -f "$NOTIFY" ]; then
        "$PYTHON" "$NOTIFY" --status "$status" --message "$message" >> "$LOG_FILE" 2>&1 || \
            log "WARNING: Telegram notification failed"
    else
        log "WARNING: notification script or venv Python not found"
    fi
}

lock_holders() {
    local fd pid
    for fd in /proc/[0-9]*/fd/*; do
        if [ "$(readlink "$fd" 2>/dev/null)" = "$LOCK_FILE" ]; then
            pid=${fd#/proc/}
            echo "${pid%%/*}"
        fi
    done | sort -u | while read -r pid; do
        [ "$pid" = "$$" ] && continue
        ps -o pid=,lstart=,args= -p "$pid" 2>/dev/null | cut -c1-120
    done
}

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    holders=$(lock_holders | tr '\n' ' ')
    message="중단: 동기화 잠금이 이미 잡혀 있음 (보유 프로세스: ${holders:-확인 불가})"
    log "ERROR: $message"
    notify failure "$message"
    exit 1
fi

cd "$PROJECT_DIR" || {
    log "ERROR: project directory not found: $PROJECT_DIR"
    exit 1
}

log "=== production source sync started (branch=$BRANCH) ==="

# 미추적 파일(.env, *.db, 로그 등)은 fast-forward 를 막지 않으므로 추적 파일 변경만 검사
dirty=$(git status --porcelain --untracked-files=no)
if [ -n "$dirty" ]; then
    message="중단: 서버에 커밋되지 않은 로컬 변경이 있어 덮어쓰지 않음 ($(echo "$dirty" | head -5 | tr '\n' ' '))"
    log "ERROR: $message"
    notify failure "$message"
    exit 1
fi

before=$(git rev-parse HEAD 2>/dev/null || true)
if [ -z "$before" ]; then
    message="중단: 현재 커밋을 확인할 수 없음"
    log "ERROR: $message"
    notify failure "$message"
    exit 1
fi

if ! git fetch origin "$BRANCH" >> "$LOG_FILE" 2>&1; then
    message="실패: origin/$BRANCH fetch 오류"
    log "ERROR: $message"
    notify failure "$message"
    exit 1
fi

remote=$(git rev-parse "origin/$BRANCH" 2>/dev/null || true)
if [ -z "$remote" ]; then
    message="실패: origin/$BRANCH 원격 커밋을 확인할 수 없음"
    log "ERROR: $message"
    notify failure "$message"
    exit 1
fi

if [ "$before" = "$remote" ]; then
    message="완료: 변경 없음 (현재 커밋 ${before:0:7})"
    log "$message"
    notify success "$message"
    exit 0
fi

if ! git merge-base --is-ancestor "$before" "$remote"; then
    message="중단: fast-forward가 아닌 원격 변경이라 자동 반영하지 않음"
    log "ERROR: $message"
    notify failure "$message"
    exit 1
fi

if ! git merge --ff-only "$remote" >> "$LOG_FILE" 2>&1; then
    message="실패: fast-forward 반영 오류"
    log "ERROR: $message"
    notify failure "$message"
    exit 1
fi

after=$(git rev-parse HEAD)
log "source updated: ${before:0:7} -> ${after:0:7}"
# 9>&- : 새로 뜨는 서버가 잠금 fd 를 물려받으면 이후 동기화가 영구히 막힌다
if ! "$PROJECT_DIR/shell/restart_server.sh" 9>&- >> "$LOG_FILE" 2>&1; then
    message="소스 반영 후 서버 재시작 실패 (커밋 ${after:0:7})"
    log "ERROR: $message"
    notify failure "$message"
    exit 1
fi

message="완료: 소스 반영 및 서버 헬스체크 완료 (${before:0:7} -> ${after:0:7})"
log "$message"
notify success "$message"
log "=== production source sync finished ==="