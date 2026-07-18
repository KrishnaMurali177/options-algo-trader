#!/bin/bash
# Alert (Discord) if any agent didn't scan near the market open. Runs once, shortly
# after the open, via cron. Reads each agent group's verdicts JSONL (paper/live/shadow)
# and flags symbols whose first scan today is missing or late. See check_missed_open.py.

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

PROJ_DIR="/Users/sirius/projects/options-algo-trader"
DC="/opt/homebrew/bin/docker-compose"
LOG="$PROJ_DIR/options_agent/logs/cron_agent.log"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
cd "$PROJ_DIR"

if ! docker info &>/dev/null; then
    echo "$(ts) check_missed_open: docker unreachable — skipping" >> "$LOG"
    exit 0
fi

# agent-spy already mounts sweet_spot_journal + logs; add live/shadow dirs read-only
# so the one check covers all deployed profiles.
"$DC" --profile live run --rm --no-deps \
    -v "$PROJ_DIR/options_agent/sweet_spot_journal_live:/app/sweet_spot_journal_live" \
    -v "$PROJ_DIR/options_agent/sweet_spot_journal_shadow:/app/sweet_spot_journal_shadow" \
    agent-spy python scripts/check_missed_open.py "$@" >> "$LOG" 2>&1
