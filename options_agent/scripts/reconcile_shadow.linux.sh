#!/bin/bash
# Linux/Ubuntu variant of reconcile_shadow.sh — native Docker, path-derived root.
# Daily post-hoc reconcile of the SHADOW journal against the shadow paper account
# so the shadow-vs-current A/B always has fresh P&L. Called by cron after close.

set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DC="$(command -v docker-compose || echo /usr/bin/docker-compose)"
LOG="$PROJ_DIR/options_agent/logs/shadow_reconcile.log"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
cd "$PROJ_DIR"

# Skip cleanly if Docker isn't up (weekend/off) — don't error the cron.
if ! docker info &>/dev/null; then
    echo "$(ts) reconcile_shadow: Docker not up — skipping" >> "$LOG"
    exit 0
fi

echo "$(ts) reconcile_shadow: starting..." >> "$LOG"

# This-branch code (agent-spy mounts live src/scripts) + host shadow journal and
# shadow-account creds, mounted read-only.
"$DC" --profile live run --rm --no-deps \
    -v "$PROJ_DIR/options_agent/sweet_spot_journal_shadow:/app/sweet_spot_journal_shadow" \
    -v "$PROJ_DIR/options_agent/.env.shadow:/app/.env.shadow:ro" \
    agent-spy python scripts/reconcile_shadow.py >> "$LOG" 2>&1

echo "$(ts) reconcile_shadow: done" >> "$LOG"
