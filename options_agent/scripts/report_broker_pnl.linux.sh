#!/bin/bash
# Linux/Ubuntu cron wrapper for report_broker_pnl.py — posts the real-money cash
# P&L to Discord AND records the daily row to a durable SQLite DB + CSV mirror
# (options_agent/perf/) for easy future access. Pass --period day|week through.
#   report_broker_pnl.linux.sh --period day
#   report_broker_pnl.linux.sh --period week

set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DC="$(command -v docker-compose || echo /usr/bin/docker-compose)"
LOG="$PROJ_DIR/options_agent/logs/broker_pnl.log"
PERF="$PROJ_DIR/options_agent/perf"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
cd "$PROJ_DIR"
mkdir -p "$PERF"   # host-owned so the container (root) and host both read it

if ! docker info &>/dev/null; then
    echo "$(ts) report_broker_pnl: docker not up — skipping" >> "$LOG"
    exit 0
fi

# Real-money account (.env.live via the realmoney profile). Mount perf so the
# DB/CSV persist on the host. Fixed db/csv/label; --period comes from "$@".
"$DC" --profile realmoney run --rm --no-deps \
    -v "$PERF:/app/perf" \
    agent-live-spy python scripts/report_broker_pnl.py "$@" \
    --db /app/perf/performance.db --csv /app/perf/daily_pnl.csv \
    --label "🔴 ALPACA · REAL-MONEY" >> "$LOG" 2>&1
