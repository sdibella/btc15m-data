#!/usr/bin/env bash
# External watchdog: restart datacollector if data file is stale (>120s old)

set -e

DATA_DIR="/home/stefan/KalshiBTC15min-data/data"
PREFIX="kxbtc15m"
STALE_SECONDS=120
SERVICE="datacollector.service"

TODAY=$(date -u +%Y-%m-%d)
DATA_FILE="$DATA_DIR/${PREFIX}-${TODAY}.jsonl"

# If file doesn't exist, collector may be starting up — skip
if [ ! -f "$DATA_FILE" ]; then
    exit 0
fi

# Check file age
FILE_AGE=$(( $(date +%s) - $(stat -c %Y "$DATA_FILE") ))

if [ "$FILE_AGE" -gt "$STALE_SECONDS" ]; then
    echo "WATCHDOG: data file stale for ${FILE_AGE}s (threshold: ${STALE_SECONDS}s), restarting collector"
    systemctl --user restart "$SERVICE"
    echo "WATCHDOG: collector restarted"
else
    echo "WATCHDOG: ok (file age: ${FILE_AGE}s)"
fi
