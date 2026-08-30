#!/bin/bash
# Linux/Ubuntu variant of check_agents_health.sh — native Docker, path-derived root.
# Ping Discord if any expected agent container is down/unhealthy. Dedups via a
# state file so it alerts on transitions only. Does NOT restart anything —
# ensure_agents.linux.sh handles healing.

set -uo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$PROJ_DIR/options_agent/.env"
STATE_FILE="$PROJ_DIR/options_agent/logs/.agent_health_state"
LOG="$PROJ_DIR/options_agent/logs/cron_agent.log"
# Paper agents, LIVE real-money agents, and the shadow (2nd paper acct) stack.
# Live + shadow added 2026-08-24: they were previously unmonitored, so shadow-qqq
# sat dead ~46h unnoticed and a live agent could die silently. NOTE: container
# health proves the process is alive, NOT that orders reach the broker — the
# 08-19..08-21 outage had healthy containers placing zero orders. Use
# check_live_fills.sh for execution truth.
AGENTS=(
    options-agent-spy options-agent-qqq options-agent-msft options-agent-aapl
    options-agent-live-spy options-agent-live-qqq
    options-agent-shadow-spy options-agent-shadow-qqq
    options-agent-shadow-aapl options-agent-shadow-googl
)

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# Load Discord webhook from the agents' env file.
WEBHOOK=$(grep -E '^DISCORD_WEBHOOK_URL=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'")
if [ -z "${WEBHOOK:-}" ]; then
    echo "$(ts) check_agents_health: no DISCORD_WEBHOOK_URL — skipping" >> "$LOG"
    exit 0
fi

# If the Docker daemon is unreachable we can't judge container health; skip
# quietly (ensure_agents.linux.sh owns bringing Docker back up).
if ! docker info &>/dev/null; then
    echo "$(ts) check_agents_health: docker daemon unreachable — skipping" >> "$LOG"
    exit 0
fi

down=()
for a in "${AGENTS[@]}"; do
    status=$(docker inspect -f '{{.State.Status}}:{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$a" 2>/dev/null || echo "missing")
    case "$status" in
        running:healthy|running:none|running:starting) ;;  # treat starting as ok (grace)
        *) down+=("$a ($status)") ;;
    esac
done

cur="${down[*]:-}"
prev=""
[ -f "$STATE_FILE" ] && prev=$(cat "$STATE_FILE")

post() {  # $1=json payload
    curl -s -o /dev/null -H "Content-Type: application/json" -X POST -d "$1" "$WEBHOOK"
}

if [ -n "$cur" ] && [ "$cur" != "$prev" ]; then
    desc="The following agent container(s) are not healthy:"
    for d in "${down[@]}"; do desc="${desc}\n🔴 ${d}"; done
    payload="{\"embeds\":[{\"title\":\"⚠️ Options Agent DOWN\",\"color\":13107200,\"description\":\"${desc}\",\"footer\":{\"text\":\"$(date '+%Y-%m-%d %I:%M %p %Z')\"}}]}"
    post "$payload"
    echo "$(ts) check_agents_health: ALERTED — $cur" >> "$LOG"
elif [ -z "$cur" ] && [ -n "$prev" ]; then
    payload="{\"embeds\":[{\"title\":\"✅ Options Agents recovered\",\"color\":3066993,\"description\":\"All agent containers are healthy again.\",\"footer\":{\"text\":\"$(date '+%Y-%m-%d %I:%M %p %Z')\"}}]}"
    post "$payload"
    echo "$(ts) check_agents_health: RECOVERED" >> "$LOG"
fi

echo "$cur" > "$STATE_FILE"
