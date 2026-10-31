#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$ROOT_DIR/shell/run_batch.sh"
CRON_BEGIN="# stocke-batches: begin"
CRON_END="# stocke-batches: end"
ANALYSIS_CRON_BEGIN="# stocke-analysis-marts: begin"
ANALYSIS_CRON_END="# stocke-analysis-marts: end"

if [[ ! -x "$RUNNER" ]]; then
    echo "Runner is not executable: $RUNNER" >&2
    echo "Run: chmod +x shell/run_batch.sh shell/setup_batch_cron.sh" >&2
    exit 1
fi

CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
FILTERED_CRON="$(printf '%s\n' "$CURRENT_CRON" | awk \
    -v begin="$CRON_BEGIN" -v end="$CRON_END" \
    -v analysis_begin="$ANALYSIS_CRON_BEGIN" -v analysis_end="$ANALYSIS_CRON_END" \
    '$0 == begin || $0 == analysis_begin {skip=1; next} $0 == end || $0 == analysis_end {skip=0; next} !skip')"

# 서버 TZ=UTC 기준. 괄호는 의도한 KST(UTC+9).
NEW_CRON=$(cat <<EOF
$FILTERED_CRON
$CRON_BEGIN
# weekday post-market batches (UTC = KST-9)
42 6 * * 1-5 $RUNNER failed-buy-signals
50 10 * * 1-5 $RUNNER kiwoom-pnl-sync
52 10 * * 1-5 $RUNNER daily-trade-journal
# 애프터장(NXT 야간장) 종료 후 실시간 체결 구독 해제 (KST 20:00 = UTC 11:00)
0 11 * * 1-5 $RUNNER realtime-candle-cleanup
# monthly data batch (UTC = KST-9)
0 11 16 * * $RUNNER trade-industry
$CRON_END
EOF
)

ANALYSIS_CRON=$(cat <<EOF
$ANALYSIS_CRON_BEGIN
# fundamental: daily at 22:00 KST
0 13 * * * $RUNNER fundamental
# technical: weekdays at 22:30 KST; run theme-mart after it completes
30 13 * * 1-5 $RUNNER technical-mart; technical_status=\$?; $RUNNER theme-mart; theme_status=\$?; test "\$technical_status" -eq 0 && test "\$theme_status" -eq 0
# theme-mart: weekends at 23:00 KST
0 14 * * 6,0 $RUNNER theme-mart
$ANALYSIS_CRON_END
EOF
)

printf '%s\n%s\n' "$NEW_CRON" "$ANALYSIS_CRON" | crontab -
echo "Installed Stocke batch cron jobs for $ROOT_DIR"
crontab -l | sed -n "/$CRON_BEGIN/,/$CRON_END/p; /$ANALYSIS_CRON_BEGIN/,/$ANALYSIS_CRON_END/p"