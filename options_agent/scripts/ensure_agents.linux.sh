#!/bin/bash
# Linux/Ubuntu variant of ensure_agents.sh — Docker runs natively (no Colima).
# Ensures the Docker daemon is up, then starts the live agent containers.
# Called by cron every 30 min during market hours.

set -euo pipefail

# cron runs with a minimal PATH; include the usual Linux docker locations.
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Project root, derived from this script's location (scripts/ -> ../.. = repo root).
PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DC="$(command -v docker-compose || echo /usr/bin/docker-compose)"
LOG="$PROJ_DIR/options_agent/logs/cron_agent.log"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
echo "$(ts) ensure_agents: checking..." >> "$LOG"

# On Ubuntu the Docker daemon is a systemd service (usually already running at
# boot). Best-effort start if it's down; non-fatal if we lack privileges.
if ! docker info &>/dev/null; then
    echo "$(ts) ensure_agents: Docker daemon down — attempting start" >> "$LOG"
    systemctl start docker >> "$LOG" 2>&1 || sudo -n systemctl start docker >> "$LOG" 2>&1 || true
    sleep 5
fi
if ! docker info &>/dev/null; then
    echo "$(ts) ensure_agents: Docker daemon still not responding — aborting" >> "$LOG"
    exit 1
fi

cd "$PROJ_DIR"
"$DC" --profile live up -d >> "$LOG" 2>&1
echo "$(ts) ensure_agents: done" >> "$LOG"
