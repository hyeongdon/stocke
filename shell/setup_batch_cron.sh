#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$ROOT_DIR/shell/run_batch.sh"
CRON_BEGIN="# stocke-batches: begin"
CRON_END="# stocke-batches: end"

if [[ ! -x "$RUNNER" ]]; then
    echo "Runner is not executable: $RUNNER" >&2
    echo "Run: chmod +x shell/run_batch.sh shell/setup_batch_cron.sh" >&2
    exit 1
fi

CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
FILTERED_CRON="$(printf '%s\n' "$CURRENT_CRON" | awk -v begin="$CRON_BEGIN" -v end="$CRON_END" '$0 == begin {skip=1; next} $0 == end {skip=0; next} !skip')"

# 서버 TZ=UTC 기준. 괄호는 의도한 KST(UTC+9).
NEW_CRON=$(cat <<EOF
$FILTERED_CRON
$CRON_BEGIN
# weekday post-market batches (UTC = KST-9)
42 6 * * 1-5 $RUNNER failed-buy-signals
50 10 * * 1-5 $RUNNER kiwoom-pnl-sync
52 10 * * 1-5 $RUNNER daily-trade-journal
# daily and monthly data batches (UTC = KST-9)
# fundamental / theme-mart 동시 실행 시 OOM → 30분 간격
0 9 * * * $RUNNER fundamental
30 9 * * * $RUNNER theme-mart
0 11 16 * * $RUNNER trade-industry
$CRON_END
EOF
)

printf '%s\n' "$NEW_CRON" | crontab -
echo "Installed Stocke batch cron jobs for $ROOT_DIR"
crontab -l | sed -n "/$CRON_BEGIN/,/$CRON_END/p"