#!/bin/bash

# Git merge 충돌 해결 스크립트
# 사용법: ./resolve_merge_conflict.sh [옵션]
# 옵션:
#   --ours: 로컬 변경사항 유지 (서버 설정 우선)
#   --theirs: 원격 변경사항 사용 (GitHub 우선)
#   --manual: 수동 해결

set -e

PROJECT_DIR="/home/ubuntu/project/stocke"
cd "$PROJECT_DIR"

# 옵션 확인
MODE="${1:---ours}"

echo "=========================================="
echo "🔧 Git Merge 충돌 해결"
echo "=========================================="
echo ""

# 1. 현재 상태 확인
echo "1. 현재 Git 상태 확인 중..."
git status

echo ""

# 2. 충돌 파일 확인
echo "2. 충돌 파일 확인 중..."
CONFLICT_FILES=$(git diff --name-only --diff-filter=U 2>/dev/null || echo "")

if [ -z "$CONFLICT_FILES" ]; then
    echo "⚠️  충돌 파일을 찾을 수 없습니다. 다른 문제일 수 있습니다."
    echo ""
    echo "현재 상태:"
    git status
    exit 1
fi

echo "충돌 파일:"
echo "$CONFLICT_FILES" | while read file; do
    echo "  - $file"
done
echo ""

# 3. 데이터베이스 백업 (중요!)
echo "3. 데이터베이스 백업 중..."
if [ -f "stock_pipeline.db" ]; then
    BACKUP_DIR="$PROJECT_DIR/backup"
    mkdir -p "$BACKUP_DIR"
    BACKUP_FILE="$BACKUP_DIR/stock_pipeline_$(date +%Y%m%d_%H%M%S).db"
    cp stock_pipeline.db "$BACKUP_FILE"
    echo "✅ 데이터베이스 백업 완료: $BACKUP_FILE"
else
    echo "⚠️  stock_pipeline.db 파일이 없습니다"
fi
echo ""

# 4. 충돌 해결
case "$MODE" in
    --ours)
        echo "4. 로컬 변경사항 유지 (서버 설정 우선)..."
        echo "$CONFLICT_FILES" | while read file; do
            if [ -f "$file" ]; then
                echo "  → $file: 로컬 버전 사용"
                git checkout --ours "$file"
                git add "$file"
            fi
        done
        ;;
    
    --theirs)
        echo "4. 원격 변경사항 사용 (GitHub 우선)..."
        echo "$CONFLICT_FILES" | while read file; do
            if [ -f "$file" ]; then
                echo "  → $file: 원격 버전 사용"
                git checkout --theirs "$file"
                git add "$file"
            fi
        done
        ;;
    
    --manual)
        echo "4. 수동 해결 모드..."
        echo ""
        echo "충돌 파일을 수동으로 편집하세요:"
        echo "$CONFLICT_FILES" | while read file; do
            echo "  - $file"
        done
        echo ""
        echo "편집 후 다음 명령어를 실행하세요:"
        echo "  git add <충돌해결한파일>"
        echo "  git commit"
        exit 0
        ;;
    
    *)
        echo "❌ 잘못된 옵션: $MODE"
        echo ""
        echo "사용법: ./resolve_merge_conflict.sh [--ours|--theirs|--manual]"
        exit 1
        ;;
esac

# 5. Git 사용자 정보 확인 및 설정
echo ""
echo "5. Git 사용자 정보 확인 중..."
GIT_USER_NAME=$(git config user.name 2>/dev/null || echo "")
GIT_USER_EMAIL=$(git config user.email 2>/dev/null || echo "")

if [ -z "$GIT_USER_NAME" ] || [ -z "$GIT_USER_EMAIL" ]; then
    echo "⚠️  Git 사용자 정보가 설정되지 않았습니다. 자동 설정 중..."
    
    # 서버 환경에 맞는 기본값 설정
    git config user.name "Stocke Server"
    git config user.email "server@stocke.local"
    
    echo "✅ Git 사용자 정보 설정 완료"
    echo "   이름: $(git config user.name)"
    echo "   이메일: $(git config user.email)"
else
    echo "✅ Git 사용자 정보 확인됨"
    echo "   이름: $GIT_USER_NAME"
    echo "   이메일: $GIT_USER_EMAIL"
fi

# 6. Merge 완료
echo ""
echo "6. Merge 완료 중..."
if git diff --cached --quiet; then
    echo "⚠️  스테이징된 변경사항이 없습니다"
else
    git commit -m "Resolve merge conflict - $(date +%Y%m%d_%H%M%S)"
    echo "✅ Merge 완료"
fi

echo ""
echo "7. 최종 상태 확인..."
git status

echo ""
echo "=========================================="
echo "✅ Git Merge 충돌 해결 완료"
echo "=========================================="
echo ""

# 7. 서버 재시작 안내
echo "💡 다음 단계:"
echo "   - 서버 재시작: ./restart_server.sh"
echo "   - 상태 확인: ./check_deployment.sh"

