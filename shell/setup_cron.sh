#!/bin/bash

# Cron Job 설정 스크립트
# 사용법: ./setup_cron.sh

set -e

PROJECT_DIR="/home/ubuntu/project/stocke"
CRON_LOG="$PROJECT_DIR/logs/cron.log"

# 로그 디렉토리 생성
mkdir -p "$PROJECT_DIR/logs"
chmod +x "$PROJECT_DIR"/shell/*.sh

echo "=== Cron Job 설정 시작 ==="

# 현재 cron 작업 백업
crontab -l > /tmp/crontab_backup_$(date +%Y%m%d_%H%M%S) 2>/dev/null || true

# 새로운 cron 작업 설정
cat > /tmp/new_crontab << EOF
# Stocke 서버 자동 관리 — 서버 시계는 UTC (KST = UTC+9). 시각은 모두 UTC 로 적는다.
# 매일 02:00 KST (UTC 17:00) 소스 동기화 후 변경 시에만 서버 재시작 + 결과 텔레그램
# (UTC 02:00 으로 두면 KST 11:00 장중 재시작이 된다)
0 17 * * * cd $PROJECT_DIR && ./shell/sync_production_source.sh >> $CRON_LOG 2>&1

# 매 30분마다 서버 상태 확인
*/30 * * * * cd $PROJECT_DIR && ./shell/health_check.sh >> $CRON_LOG 2>&1

# 매일 01:00 KST (서버 UTC 16:00) 만료된 관심종목 정리 — API 는 로그인 필요라 DB 직접 정리
0 16 * * * cd $PROJECT_DIR && ./venv/bin/python scripts/watchlist_cleanup_batch.py >> $CRON_LOG 2>&1

# 매일 23:00 KST (서버 UTC 14:00) 로그 7일 보관 정리
# stock_pipeline.log / cron.log 처럼 계속 커지는 파일은 본문을 자른다.
0 14 * * * cd $PROJECT_DIR && ./venv/bin/python scripts/log_cleanup_batch.py >> $CRON_LOG 2>&1

# 매월 2일 04:00 KST (1일 UTC 19:00) 데이터베이스 백업
# (\$ 로 설치 시점이 아닌 실행 시점에 날짜 계산, crontab 에서 % 는 \% 로 이스케이프)
0 19 1 * * cd $PROJECT_DIR && cp stock_pipeline.db "backup/stock_pipeline_\$(date +\\%Y\\%m\\%d).db" >> $CRON_LOG 2>&1
EOF

# 백업 디렉토리 생성
mkdir -p "$PROJECT_DIR/backup"

# 새로운 cron 작업 적용
crontab /tmp/new_crontab

echo "Cron Job 설정 완료:"
echo ""
echo "설정된 작업:"
echo "- 매일 02:00 KST: 소스 동기화, 변경 시 서버 재시작, 결과 텔레그램"
echo "- 매 30분: 서버 상태 확인"
echo "- 매일 01:00 KST: 만료된 관심종목 정리 (DB 직접)"
echo "- 매일 23:00 KST: 로그 7일 보관 정리"
echo "- 매월 2일 04:00 KST: 데이터베이스 백업"
echo ""
echo "로그 파일: $CRON_LOG"
echo ""

# 현재 cron 작업 확인
echo "현재 설정된 cron 작업:"
crontab -l

echo ""
echo "=== Cron Job 설정 완료 ==="
