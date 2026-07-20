#!/bin/bash
# Linux/Ubuntu wrapper for compare_perf.py — reads BOTH perf DBs (real
# performance.db + shadow performance_shadow.db) and prints the real-vs-shadow
# daily P&L table. Read-only: no Discord, no DB writes. Runs via agent-spy
# (feature-branch scripts, same as the shadow report wrapper). Extra args pass
# through, e.g. --metric total_realized.
#   compare_perf.linux.sh
#   compare_perf.linux.sh --metric total_realized

set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DC="$(command -v docker-compose || echo /usr/bin/docker-compose)"
PERF="$PROJ_DIR/options_agent/perf"

cd "$PROJ_DIR"

if ! docker info &>/dev/null; then
    echo "compare_perf: docker not up — skipping" >&2
    exit 0
fi

# Mount perf/ so both DBs are visible at /app/perf. Output goes to stdout (this
# is an interactive/query tool, not a cron poster).
"$DC" --profile live run --rm --no-deps \
    -v "$PERF:/app/perf" \
    agent-spy python scripts/compare_perf.py "$@"
