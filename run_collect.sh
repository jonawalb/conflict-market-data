#!/bin/bash
# Wrapper for scheduled collector runs. Serialises via a lock so an overrunning
# daily job never collides with the hourly book job.
set -uo pipefail

MODE="${1:-book}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${BOW_DATA_DIR:-$HOME/Library/Application Support/BettingOnWar}"
LOCK="$DATA_DIR/.collector.lock"
PYTHON="$(command -v python3)"

mkdir -p "$DATA_DIR"

# Non-blocking lock: if a run is already active, skip this tick rather than queue.
exec 9>"$LOCK"
if ! flock -n 9 2>/dev/null; then
    # macOS has no flock(1) by default; fall back to a PID file.
    if [ -f "$DATA_DIR/.collector.pid" ] && kill -0 "$(cat "$DATA_DIR/.collector.pid")" 2>/dev/null; then
        echo "$(date -u +%FT%TZ) skip: run already active" >> "$DATA_DIR/scheduler.log"
        exit 0
    fi
fi
echo $$ > "$DATA_DIR/.collector.pid"
trap 'rm -f "$DATA_DIR/.collector.pid"' EXIT

echo "$(date -u +%FT%TZ) start mode=$MODE" >> "$DATA_DIR/scheduler.log"
"$PYTHON" "$HERE/bow_collect.py" "$MODE" >> "$DATA_DIR/scheduler.log" 2>&1
STATUS=$?
echo "$(date -u +%FT%TZ) end mode=$MODE status=$STATUS" >> "$DATA_DIR/scheduler.log"
exit $STATUS
