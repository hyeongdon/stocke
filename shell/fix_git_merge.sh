#!/bin/bash

# Git merge 충돌 해결 스크립트
# 사용법: ./fix_git_merge.sh [옵션]
# 옵션:
#   --stash: 로컬 변경사항을 stash하고 pull (기본값)
#   --commit: 로컬 변경사항을 commit하고 pull
#   --discard: 로컬 변경사항을 버리고 pull (주의!)

set -e

PROJECT_DIR="/home/ubuntu/project/stocke"
cd "$PROJECT_DIR"

# 옵션 확인
MODE="${1:---stash}"

echo "=========================================="
echo "🔧 Git Merge 충돌 해결"
echo "=========================================="
echo ""

# 1. 현재 상태 확인
echo "1. 현재 상태 확인 중..."
git status

echo ""
echo "충돌 파일:"
echo "  - health_check.sh"
echo "  - restart_server.sh"
echo "  - setup_cron.sh"
echo "  - stock_pipeline.db"
echo ""

# 2. 데이터베이스 백업 (중요!)
echo "2. 데이터베이스 백업 중..."
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

# 3. 처리 방법 선택
case "$MODE" in
    --stash)
        echo "3. 로컬 변경사항을 stash하고 pull..."
        git stash push -m "Auto stash before pull $(date +%Y%m%d_%H%M%S)"
        echo "✅ 변경사항 stash 완료"
        echo ""
        echo "4. Git pull 실행..."
        git pull origin main
        echo ""
        echo "5. Stash된 변경사항 확인..."
        if git stash list | grep -q "Auto stash"; then
            echo "⚠️  Stash된 변경사항이 있습니다. 필요시 다음 명령으로 복원하세요:"
            echo "   git stash pop"
        fi
        ;;
    
    --commit)
        echo "3. 로컬 변경사항을 commit하고 pull..."
        git add health_check.sh restart_server.sh setup_cron.sh
        git commit -m "Update server scripts before pull $(date +%Y%m%d_%H%M%S)"
        echo "✅ 변경사항 commit 완료"
        echo ""
        echo "4. Git pull 실행..."
        git pull origin main
        ;;
    
    --discard)
        echo "⚠️  경고: 로컬 변경사항을 모두 버립니다!"
        read -p "계속하시겠습니까? (yes/no): " confirm
        if [ "$confirm" != "yes" ]; then
            echo "취소되었습니다."
            exit 0
        fi
        echo ""
        echo "3. 로컬 변경사항을 버리고 pull..."
        git checkout -- health_check.sh restart_server.sh setup_cron.sh
        # 데이터베이스는 백업했으므로 원격 버전으로 복원하지 않음
        echo "✅ 변경사항 버리기 완료"
        echo ""
        echo "4. Git pull 실행..."
        git pull origin main
        ;;
    
    *)
        echo "❌ 잘못된 옵션: $MODE"
        echo ""
        echo "사용법: ./fix_git_merge.sh [--stash|--commit|--discard]"
        exit 1
        ;;
esac

echo ""
echo "=========================================="
echo "✅ Git Merge 충돌 해결 완료"
echo "=========================================="
echo ""

# 6. 최종 상태 확인
echo "6. 최종 상태 확인..."
git status

echo ""
echo "💡 다음 단계:"
echo "   - 서버 재시작: ./restart_server.sh"
echo "   - 상태 확인: ./check_deployment.sh"

