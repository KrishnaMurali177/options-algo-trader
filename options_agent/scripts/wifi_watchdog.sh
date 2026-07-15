#!/bin/zsh
# wifi_watchdog.sh — bounce macOS Wi-Fi if the host loses internet or DNS.
#
# Why: the always-on trading agents crash-loop when the Mac drops Wi-Fi and
# macOS doesn't auto-reconnect (observed 2026-07-15: DNS failed inside Docker,
# all agents restarted ~98x, host load spiked to 175). This runs on an interval
# via launchd and power-cycles the Wi-Fi interface so connectivity returns
# without manual intervention — the agents then self-heal on their next restart.
#
# Install: see com.optionsagent.wifiwatchdog.plist (same dir).
# Runs as the logged-in user; `networksetup -setairportpower` needs no sudo.

set -u

LOG="${HOME}/Library/Logs/wifi_watchdog.log"
PING_HOST="1.1.1.1"                    # raw reachability (no DNS needed)
DNS_HOST="paper-api.alpaca.markets"    # the name the agents actually resolve

log() { print -r -- "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

# Detect the Wi-Fi hardware device (usually en0, but don't assume).
wifi_dev="$(networksetup -listallhardwareports \
  | awk '/Wi-Fi|AirPort/{getline; print $2; exit}')"
if [[ -z "$wifi_dev" ]]; then
  log "ERROR: could not determine Wi-Fi device; aborting"
  exit 1
fi

# Healthy only if we can BOTH reach the internet (ping) AND resolve DNS —
# the real failure we hit was DNS, which a plain ping would miss.
if ping -c1 -t3 "$PING_HOST" >/dev/null 2>&1 \
   && dscacheutil -q host -a name "$DNS_HOST" 2>/dev/null | grep -q ip_address; then
  exit 0   # all good — stay quiet
fi

log "network DOWN (ping/DNS failed) — power-cycling Wi-Fi ($wifi_dev)"
networksetup -setairportpower "$wifi_dev" off
sleep 3
networksetup -setairportpower "$wifi_dev" on
sleep 8

# Best-effort DNS cache nudge after reconnect (full flush would need sudo).
dscacheutil -flushcache 2>/dev/null

if ping -c1 -t5 "$PING_HOST" >/dev/null 2>&1; then
  log "Wi-Fi bounced OK — connectivity restored on $wifi_dev"
else
  log "Wi-Fi bounced but STILL no connectivity on $wifi_dev (will retry next run)"
fi
