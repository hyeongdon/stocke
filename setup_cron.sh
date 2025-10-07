#!/bin/bash

# Cron Job 설정 스크립트
# 사용법: ./setup_cron.sh

set -e

PROJECT_DIR="/home/ubuntu/project/stocke"
CRON_LOG="$PROJECT_DIR/logs/cron.log"

# 로그 디렉토리 생성
mkdir -p "$PROJECT_DIR/logs"

echo "=== Cron Job 설정 시작 ==="

# 현재 cron 작업 백업
crontab -l > /tmp/crontab_backup_$(date +%Y%m%d_%H%M%S) 2>/dev/null || true

# 새로운 cron 작업 설정
cat > /tmp/new_crontab << EOF
# Stocke 서버 자동 관리
# 매일 오전 2시에 서버 재시작 (최신 코드 반영)
0 2 * * * cd $PROJECT_DIR && ./restart_server.sh >> $CRON_LOG 2>&1

# 매 30분마다 서버 상태 확인
*/30 * * * * cd $PROJECT_DIR && ./health_check.sh >> $CRON_LOG 2>&1

# 매일 오전 1시에 만료된 관심종목 정리
0 1 * * * curl -X POST http://localhost:8001/watchlist/sync/cleanup >> $CRON_LOG 2>&1

# 매주 일요일 오전 3시에 로그 파일 정리 (7일 이상 된 로그 삭제)
0 3 * * 0 find $PROJECT_DIR/logs -name "*.log" -mtime +7 -delete >> $CRON_LOG 2>&1

# 매월 1일 오전 4시에 데이터베이스 백업
0 4 1 * * cd $PROJECT_DIR && cp stock_pipeline.db "backup/stock_pipeline_$(date +%Y%m%d).db" >> $CRON_LOG 2>&1
EOF

# 백업 디렉토리 생성
mkdir -p "$PROJECT_DIR/backup"

# 새로운 cron 작업 적용
crontab /tmp/new_crontab

echo "Cron Job 설정 완료:"
echo ""
echo "설정된 작업:"
echo "- 매일 오전 2시: 서버 재시작"
echo "- 매 30분: 서버 상태 확인"
echo "- 매일 오전 1시: 만료된 관심종목 정리"
echo "- 매주 일요일 오전 3시: 로그 파일 정리"
echo "- 매월 1일 오전 4시: 데이터베이스 백업"
echo ""
echo "로그 파일: $CRON_LOG"
echo ""

# 현재 cron 작업 확인
echo "현재 설정된 cron 작업:"
crontab -l

echo ""
echo "=== Cron Job 설정 완료 ==="
