#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x "$ROOT_DIR/venv/bin/python" ]]; then
    PYTHON="$ROOT_DIR/venv/bin/python"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON="$ROOT_DIR/.venv/bin/python"
else
    PYTHON="$(command -v python3 || command -v python)"
fi

mkdir -p "$ROOT_DIR/logs"

usage() {
    echo "Usage: $0 <batch-name>"
    echo "Batches: condition-alert, condition-alert-realtime, daily-trade-journal, failed-buy-signals, fundamental, kiwoom-pnl-sync, theme-mart, trade-industry"
}

if [[ $# -ne 1 ]]; then
    usage >&2
    exit 2
fi

case "$1" in
    condition-alert)
        SCRIPT="scripts/condition_telegram_alert.py"
        ARGS=()
        LOG_FILE="$ROOT_DIR/logs/condition_telegram_alert.log"
        ;;
    condition-alert-realtime)
        SCRIPT="scripts/condition_telegram_alert.py"
        ARGS=(--realtime)
        LOG_FILE="$ROOT_DIR/logs/condition_telegram_alert.log"
        ;;
    daily-trade-journal)
        SCRIPT="scripts/daily_trade_journal_batch.py"
        ARGS=()
        LOG_FILE="$ROOT_DIR/logs/daily_trade_journal_batch.log"
        ;;
    failed-buy-signals)
        SCRIPT="scripts/failed_buy_signals_batch.py"
        ARGS=()
        LOG_FILE="$ROOT_DIR/logs/failed_buy_signals_batch.log"
        ;;
    fundamental)
        SCRIPT="scripts/fundamental_mart_batch.py"
        ARGS=()
        LOG_FILE="$ROOT_DIR/logs/fundamental_mart_batch.log"
        ;;
    kiwoom-pnl-sync)
        SCRIPT="scripts/kiwoom_db_pnl_sync_batch.py"
        ARGS=(--apply)
        LOG_FILE="$ROOT_DIR/logs/kiwoom_db_pnl_sync_batch.log"
        ;;
    theme-mart)
        SCRIPT="scripts/theme_mart_batch.py"
        ARGS=(--top-n 0 --no-news)
        LOG_FILE="$ROOT_DIR/logs/theme_mart_batch.log"
        ;;
    trade-industry)
        SCRIPT="scripts/trade_industry_batch.py"
        ARGS=(--months 24 --sleep 0.15)
        LOG_FILE="$ROOT_DIR/logs/trade_industry_batch.log"
        ;;
    *)
        echo "Unknown batch: $1" >&2
        usage >&2
        exit 2
        ;;
esac

if [[ ! -f "$ROOT_DIR/$SCRIPT" ]]; then
    echo "Batch script not found: $ROOT_DIR/$SCRIPT" >&2
    exit 1
fi

{
    printf '\n===== %s %s start =====\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$1"
    "$PYTHON" "$ROOT_DIR/$SCRIPT" "${ARGS[@]}"
    status=$?
    printf '===== %s %s end (exit=%s) =====\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$1" "$status"
    exit "$status"
} >> "$LOG_FILE" 2>&1