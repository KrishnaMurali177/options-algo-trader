#!/bin/bash
# Daily post-hoc reconcile of the SHADOW journal against the shadow paper account.
# Main's code only fills gainz-exit close prices, so stop/decay/stagnation/theta
# exits are left without exit_price/pnl. This backfills them so the shadow-vs-current
# A/B (scripts/compare_shadow_vs_current.py) has real P&L. Called by cron after close.

set -euo pipefail

# cron runs with a minimal PATH — put Homebrew's bin first so docker/colima resolve.
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

PROJ_DIR="/Users/sirius/projects/options-algo-trader"
DC="/opt/homebrew/bin/docker-compose"
LOG="$PROJ_DIR/options_agent/logs/shadow_reconcile.log"

cd "$PROJ_DIR"

# Skip cleanly if Docker/Colima isn't up (weekend/asleep) — don't error the cron.
if ! docker info &>/dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') reconcile_shadow: Docker not up — skipping" >> "$LOG"
    exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') reconcile_shadow: starting..." >> "$LOG"

# Uses THIS branch's code (agent-spy mounts live src/scripts, which have the full
# reconcile). Mount the host shadow journal + shadow-account creds read-only.
"$DC" --profile live run --rm --no-deps \
    -v "$PROJ_DIR/options_agent/sweet_spot_journal_shadow:/app/sweet_spot_journal_shadow" \
    -v "$PROJ_DIR/options_agent/.env.shadow:/app/.env.shadow:ro" \
    agent-spy python scripts/reconcile_shadow.py >> "$LOG" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') reconcile_shadow: done" >> "$LOG"
