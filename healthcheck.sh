#!/usr/bin/env bash
# Cron watchdog for datacollector systemd service
# Install: crontab -e → */5 * * * * /home/stefan/KalshiBTC15min-data/healthcheck.sh

set -euo pipefail

SERVICE="datacollector.service"
DATA_DIR="/home/stefan/KalshiBTC15min-data/data"
STALE_THRESHOLD=120  # seconds

log() { logger -t datacollector-healthcheck "$@"; }

# Check 1: Is the systemd service active?
if ! systemctl --user is-active --quiet "$SERVICE"; then
    log "Service not active — resetting and restarting"
    systemctl --user reset-failed "$SERVICE" 2>/dev/null || true
    systemctl --user start "$SERVICE"
    exit 0
fi

# Check 2: Is today's data file being written to?
TODAY=$(date -u +%Y-%m-%d)
DATA_FILE="$DATA_DIR/kxbtc15m-${TODAY}.jsonl"

if [ ! -f "$DATA_FILE" ]; then
    # No file yet today — only restart if it's past 00:05 UTC (give time for first write)
    HOUR_MIN=$(date -u +%H%M)
    if [ "$HOUR_MIN" -gt "0005" ]; then
        log "No data file for today ($TODAY) — restarting"
        systemctl --user restart "$SERVICE"
    fi
    exit 0
fi

# Check file age
FILE_AGE=$(( $(date +%s) - $(stat -c %Y "$DATA_FILE") ))
if [ "$FILE_AGE" -gt "$STALE_THRESHOLD" ]; then
    log "Data file stale (${FILE_AGE}s old) — restarting"
    systemctl --user restart "$SERVICE"
fi
