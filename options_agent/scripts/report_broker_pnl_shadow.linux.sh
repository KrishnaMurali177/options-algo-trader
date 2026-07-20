#!/bin/bash
# Linux/Ubuntu cron wrapper — records the SHADOW paper account's cash P&L to a
# SEPARATE DB/CSV (perf/performance_shadow.db + shadow_daily_pnl.csv), account-truth
# and parallel to the real-money DB. Runs via agent-spy (feature-branch scripts —
# the shadow's own agent-shadow-spy mounts main's code which lacks this script) with
# the shadow account creds loaded via --env-file. Pass --period day / --backfill.
#   report_broker_pnl_shadow.linux.sh --period day
#   report_broker_pnl_shadow.linux.sh --backfill

set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DC="$(command -v docker-compose || echo /usr/bin/docker-compose)"
LOG="$PROJ_DIR/options_agent/logs/broker_pnl.log"
PERF="$PROJ_DIR/options_agent/perf"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
cd "$PROJ_DIR"
mkdir -p "$PERF"

if ! docker info &>/dev/null; then
    echo "$(ts) report_broker_pnl_shadow: docker not up — skipping" >> "$LOG"
    exit 0
fi

# agent-spy = feature-branch scripts + paper env; --env-file swaps to the shadow
# account. Mount .env.shadow (as a file) and the perf dir.
"$DC" --profile live run --rm --no-deps \
    -v "$PERF:/app/perf" \
    -v "$PROJ_DIR/options_agent/.env.shadow:/app/.env.shadow:ro" \
    agent-spy python scripts/report_broker_pnl.py "$@" \
    --env-file /app/.env.shadow \
    --db /app/perf/performance_shadow.db --csv /app/perf/shadow_daily_pnl.csv \
    --label "🧪 SHADOW · NEW-GOLDEN" >> "$LOG" 2>&1
