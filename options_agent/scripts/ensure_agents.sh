#!/bin/bash
# Ensure Colima + Docker are running, then start agent containers.
# Called by cron every 30 min during market hours.

set -euo pipefail

# cron runs with a minimal PATH (/usr/bin:/bin), so colima can't find limactl
# and bare `docker` is unresolved. Put Homebrew's bin first.
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

PROJ_DIR="/Users/sirius/projects/options-algo-trader"
COLIMA="/opt/homebrew/bin/colima"
DC="/opt/homebrew/bin/docker-compose"
LOG="$PROJ_DIR/options_agent/logs/cron_agent.log"

echo "$(date '+%Y-%m-%d %H:%M:%S') ensure_agents: checking..." >> "$LOG"

if ! "$COLIMA" status &>/dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ensure_agents: Colima down — starting..." >> "$LOG"
    "$COLIMA" start >> "$LOG" 2>&1
    sleep 10
fi

if ! docker info &>/dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ensure_agents: Docker daemon not responding after Colima start" >> "$LOG"
    exit 1
fi

cd "$PROJ_DIR"
"$DC" --profile live up -d >> "$LOG" 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') ensure_agents: done" >> "$LOG"
