#!/bin/bash

# 빠른 Git Pull 스크립트 (충돌 자동 해결)
# 사용법: ./quick_pull.sh

set -e

PROJECT_DIR="/home/ubuntu/project/stocke"
cd "$PROJECT_DIR"

echo "=========================================="
echo "🔄 빠른 Git Pull (자동 충돌 해결)"
echo "=========================================="
echo ""

# 1. 현재 상태 확인
echo "1. 현재 Git 상태 확인 중..."
git status --short

echo ""

# 2. 데이터베이스 백업
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

# 3. 로컬 변경사항 stash
echo "3. 로컬 변경사항 임시 저장 중..."
git stash push -m "Auto stash before pull $(date +%Y%m%d_%H%M%S)" || {
    echo "⚠️  Stash할 변경사항이 없습니다"
}
echo "✅ 변경사항 stash 완료"
echo ""

# 4. Git Pull
echo "4. Git Pull 실행 중..."
if git pull origin main; then
    echo "✅ Git Pull 성공"
else
    echo "❌ Git Pull 실패"
    echo ""
    echo "현재 상태:"
    git status
    exit 1
fi
echo ""

# 5. Stash 복원 시도 (선택사항)
echo "5. Stash된 변경사항 확인 중..."
if git stash list | grep -q "Auto stash"; then
    echo "⚠️  Stash된 변경사항이 있습니다"
    echo ""
    read -p "Stash된 변경사항을 복원하시겠습니까? (y/n): " restore_stash
    if [ "$restore_stash" = "y" ] || [ "$restore_stash" = "Y" ]; then
        if git stash pop; then
            echo "✅ Stash 복원 완료"
        else
            echo "⚠️  Stash 복원 중 충돌 발생 (수동 해결 필요)"
            echo "충돌 파일을 확인하고 수동으로 해결하세요"
        fi
    else
        echo "Stash 복원 건너뜀"
    fi
else
    echo "✅ Stash된 변경사항 없음"
fi
echo ""

# 6. 최종 상태 확인
echo "6. 최종 상태 확인..."
git status --short

echo ""
echo "=========================================="
echo "✅ Git Pull 완료"
echo "=========================================="
echo ""

# 7. 서버 재시작 안내
echo "💡 다음 단계:"
echo "   - 서버 재시작: ./restart_server.sh"
echo "   - 상태 확인: ./check_deployment.sh"


