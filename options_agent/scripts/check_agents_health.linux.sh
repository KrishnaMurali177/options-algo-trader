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

# ── Ops incidents raised by the agents themselves ──
# Live agents run with DISCORD_WEBHOOK_URL deliberately empty so trade activity
# never pings (it tempts money-losing manual closes). Infrastructure failures are
# a different class and MUST reach a human: the agent appends them to
# logs/ops_alerts.log and we escalate here, on the ops channel. Added 2026-09-11
# after Alpaca backend timeouts left two open real-money 0DTE positions with no
# stop/target evaluation for 40 minutes, and nothing told anyone.
OPS_LOG="$PROJ_DIR/options_agent/logs/ops_alerts.log"
OPS_STATE="$PROJ_DIR/options_agent/logs/.ops_alerts_pos"
if [ -f "$OPS_LOG" ]; then
    prev_lines=0
    [ -f "$OPS_STATE" ] && prev_lines=$(cat "$OPS_STATE" 2>/dev/null)
    case "$prev_lines" in ''|*[!0-9]*) prev_lines=0 ;; esac
    cur_lines=$(wc -l < "$OPS_LOG")
    # Rotated/truncated log: replay from the top rather than skipping everything.
    [ "$cur_lines" -lt "$prev_lines" ] && prev_lines=0
    if [ "$cur_lines" -gt "$prev_lines" ]; then
        desc=""
        n=0
        # Cap at 10 lines so a stuck agent can't post a wall of text every 10 min.
        while IFS= read -r line; do
            [ -z "$line" ] && continue
            _ts=$(printf '%s' "$line" | cut -f1 | cut -dT -f2 | cut -d. -f1)
            _sym=$(printf '%s' "$line" | cut -f2)
            _msg=$(printf '%s' "$line" | cut -f3- | tr -d '"\\' | cut -c1-180)
            desc="${desc}\n🛑 [${_ts}] ${_sym}: ${_msg}"
            n=$((n + 1))
        done <<< "$(tail -n +$((prev_lines + 1)) "$OPS_LOG" | head -10)"
        if [ -n "$desc" ]; then
            total=$((cur_lines - prev_lines))
            [ "$total" -gt 10 ] && desc="${desc}\n… and $((total - 10)) more"
            payload="{\"embeds\":[{\"title\":\"🛑 Agent OPS incident\",\"color\":15158332,\"description\":\"${desc}\",\"footer\":{\"text\":\"$(date '+%Y-%m-%d %I:%M %p %Z')\"}}]}"
            post "$payload"
            echo "$(ts) check_agents_health: OPS ALERT — $total new line(s)" >> "$LOG"
        fi
    fi
    echo "$cur_lines" > "$OPS_STATE"
fi
