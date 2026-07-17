#!/bin/bash
# Linux/Ubuntu variant of check_missed_open.sh — native Docker, path-derived root.
# Alert (Discord) if any agent didn't scan near the market open. See check_missed_open.py.

set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DC="$(command -v docker-compose || echo /usr/bin/docker-compose)"
LOG="$PROJ_DIR/options_agent/logs/cron_agent.log"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
cd "$PROJ_DIR"

if ! docker info &>/dev/null; then
    echo "$(ts) check_missed_open: docker unreachable — skipping" >> "$LOG"
    exit 0
fi

"$DC" --profile live run --rm --no-deps \
    -v "$PROJ_DIR/options_agent/sweet_spot_journal_live:/app/sweet_spot_journal_live" \
    -v "$PROJ_DIR/options_agent/sweet_spot_journal_shadow:/app/sweet_spot_journal_shadow" \
    agent-spy python scripts/check_missed_open.py >> "$LOG" 2>&1
