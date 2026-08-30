#!/bin/bash
# Linux/Ubuntu variant of ensure_agents.sh — Docker runs natively (no Colima).
# Ensures the Docker daemon is up, then starts the agent containers.
# Called by cron every 30 min during market hours.

set -euo pipefail

# cron runs with a minimal PATH; include the usual Linux docker locations.
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Project root, derived from this script's location (scripts/ -> ../.. = repo root).
PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DC="$(command -v docker-compose || echo /usr/bin/docker-compose)"
LOG="$PROJ_DIR/options_agent/logs/cron_agent.log"

# Which compose profiles to keep alive. Until 2026-08-30 this was `live` only,
# so the 2 real-money and 4 shadow containers had NO auto-healing: shadow-qqq
# sat dead ~46h after a compose slip and nothing brought it back.
# check_agents_health.linux.sh already alerts on all three stacks; this makes
# the healing side match the alerting side.
# Override for manual/one-off runs: AGENT_PROFILES="live" ensure_agents.linux.sh
IFS=' ' read -r -a PROFILES <<< "${AGENT_PROFILES:-live realmoney shadow}"

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

# One profile at a time, so a failure in one stack (e.g. a missing .env.live)
# still leaves the others healed rather than aborting the whole run.
rc=0
for p in "${PROFILES[@]}"; do
    # `realmoney` and `shadow` get --no-recreate: they carry real money and the
    # A/B shadow book, and are pinned to the .worktree-live-main code mount. A
    # plain `up -d` would RECREATE them mid-session whenever docker-compose.yml
    # has drifted from what is running — silently repointing the live code mount
    # or killing the process holding an open position. Deploys to those stacks
    # stay deliberate and manual; cron only ever restarts what is already down.
    # The paper `live` profile keeps plain `up -d` — that is how paper config
    # changes are meant to land on the next tick.
    extra=""
    [ "$p" != "live" ] && extra="--no-recreate"
    if "$DC" --profile "$p" up -d $extra >> "$LOG" 2>&1; then
        echo "$(ts) ensure_agents: profile '$p' ok" >> "$LOG"
    else
        echo "$(ts) ensure_agents: profile '$p' FAILED" >> "$LOG"
        rc=1
    fi
done

echo "$(ts) ensure_agents: done (rc=$rc)" >> "$LOG"
exit $rc
