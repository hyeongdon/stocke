#!/bin/bash

# Git 사용자 정보 설정 스크립트
# 사용법: ./setup_git_config.sh [이름] [이메일]

set -e

PROJECT_DIR="/home/ubuntu/project/stocke"
cd "$PROJECT_DIR"

# 파라미터 확인
USER_NAME="${1:-Stocke Server}"
USER_EMAIL="${2:-server@stocke.local}"

echo "=========================================="
echo "⚙️  Git 사용자 정보 설정"
echo "=========================================="
echo ""

# 현재 설정 확인
echo "현재 Git 설정:"
CURRENT_NAME=$(git config user.name 2>/dev/null || echo "설정 안됨")
CURRENT_EMAIL=$(git config user.email 2>/dev/null || echo "설정 안됨")
echo "  이름: $CURRENT_NAME"
echo "  이메일: $CURRENT_EMAIL"
echo ""

# 새 설정 적용
echo "새 Git 설정 적용 중..."
git config user.name "$USER_NAME"
git config user.email "$USER_EMAIL"

# 전역 설정 (선택사항)
read -p "전역 설정도 변경하시겠습니까? (y/n): " set_global
if [ "$set_global" = "y" ] || [ "$set_global" = "Y" ]; then
    git config --global user.name "$USER_NAME"
    git config --global user.email "$USER_EMAIL"
    echo "✅ 전역 설정 완료"
else
    echo "✅ 로컬 설정만 완료 (이 저장소에만 적용)"
fi

echo ""
echo "설정된 값:"
echo "  이름: $(git config user.name)"
echo "  이메일: $(git config user.email)"
echo ""
echo "=========================================="
echo "✅ Git 사용자 정보 설정 완료"
echo "=========================================="


