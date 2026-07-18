# Running the options-algo-trader on Ubuntu

Steps to stand up the full stack on a fresh Ubuntu box. The repo is portable;
only **secrets** and **machine-local state** need to be copied over by hand
(they're gitignored). macOS-specific cron wrappers have Linux twins (`*.linux.sh`).

---

## 1. Prerequisites

```bash
# Docker Engine + the V1 docker-compose binary (this project uses the hyphenated
# `docker-compose`, NOT the `docker compose` V2 plugin — see CLAUDE.md).
sudo apt-get update
sudo apt-get install -y docker.io
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
     -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Run docker without sudo (log out/in afterwards for it to take effect).
sudo usermod -aG docker "$USER"

# Docker starts on boot as a systemd service.
sudo systemctl enable --now docker
```

Verify: `docker info` and `docker-compose version` both succeed as your user.

---

## 2. Clone the repo

```bash
cd ~/projects   # or wherever you keep code
git clone git@github.com:KrishnaMurali177/options-algo-trader.git
cd options-algo-trader
git checkout feature/mag7-single-name-agents
```

---

## 3. Copy the secrets (NOT in git)

These hold live API keys / Discord webhooks and are deliberately gitignored.
Copy them from the Mac using the migration bundle (see `ubuntu_migration.zip`,
produced by `scripts/make_migration_zip.sh`), then unzip into place:

```
options_agent/.env            # shared config + Alpaca paper keys (data)
options_agent/.env.live       # LIVE Alpaca keys (ALPACA_PAPER=false)
options_agent/.env.public     # BROKER=public + Public.com creds
options_agent/.env.shadow     # 2nd Alpaca PAPER account (shadow A/B)
```

```bash
unzip ~/ubuntu_migration.zip -d options_agent/
```

Templates (`.env.example`, `.env.live.example`, …) are committed if you'd rather
fill them in fresh. **Never commit the real `.env*` files.**

> Transfer the zip over a trusted channel (scp/USB), and delete it from both
> machines once imported — it contains live-money credentials.

---

## 4. Recreate the shadow worktree (for the shadow A/B agents)

The shadow agents run `main`'s NEW-golden code from a sibling git worktree that
is gitignored. Recreate it:

```bash
git worktree add .worktree-main main
```

(Skip if you don't run the `shadow` profile.)

---

## 5. Build the image and start the agents

```bash
docker-compose build                       # builds the `options-algo-trader` image
docker-compose --profile live up -d        # SPY/QQQ/MSFT/AAPL paper agents + dashboard
```

Optional profiles (start only what you need):

```bash
docker-compose --profile realmoney up -d agent-live-spy agent-live-qqq   # LIVE money
docker-compose --profile shadow    up -d agent-shadow-spy agent-shadow-qqq
docker-compose --profile public    up -d agent-public-spy
```

Dashboard: http://localhost:8501

Journals (`sweet_spot_journal*/`) and `data_cache/` regenerate themselves at
runtime. Only copy `sweet_spot_journal_live/` from the Mac if you want to keep
real-money trade history continuous.

---

## 6. Cron (use the Linux wrappers)

The `*.linux.sh` wrappers derive the project dir from their own path, use the
Linux docker locations, and drop the macOS Colima logic. Make them executable
and install the schedule (times are the host's local timezone — adjust if the
Ubuntu box isn't on US/Pacific like the Mac):

```bash
chmod +x options_agent/scripts/*.linux.sh

( crontab -l 2>/dev/null | grep -v '\.linux\.sh'
  P="$HOME/projects/options-algo-trader/options_agent/scripts"
  echo "*/30 6-13 * * 1-5 $P/ensure_agents.linux.sh"
  echo "*/10 6-13 * * 1-5 $P/check_agents_health.linux.sh"
  echo "12 7 * * 1-5 $P/check_missed_open.linux.sh"
  echo "*/15 7-12 * * 1-5 $P/check_missed_open.linux.sh --mode stale"
  echo "12 13 * * 1-5 $P/reconcile_shadow.linux.sh"
) | crontab -

crontab -l | grep linux   # verify
```

(Adjust `$P` if you cloned somewhere other than `~/projects`.)

`check_missed_open.linux.sh` runs once at **7:12 local (10:12 ET)** and alerts
Discord if any agent (paper/live/shadow) failed to first-scan by ~10:07 ET — i.e.
it was down/asleep at the open and silently skipped the morning. The
`--mode stale` line runs it **every 15 min through the session** and alerts if an
agent *stopped* scanning mid-day (last scan > 15 min old) — catching a mid-session
stall, e.g. the 07-17 Ubuntu drop at 11:00 ET that made a live trade un-doubled.
Both are distinct from `check_agents_health` (which only checks the container is
*up*, not that it actually *scanned*). See
`docs/2026-07-16_new_golden_validation_and_parity.md` §3b for the motivating incident.

The daily/weekly Discord reports run via `docker-compose ... send_daily_report.py`
— copy those two lines from the Mac's crontab if you want them here too; they're
already cross-platform (no macOS paths inside).

---

## 7. Keep it always-on (Docker up 24/7, no sleep)

Since this box is meant to run the agents unattended, make sure neither Docker
nor the OS goes to sleep on you.

**Docker survives reboots + crashes**

```bash
sudo systemctl enable --now docker     # daemon starts on boot
```

The agent containers already declare `restart: unless-stopped` in
`docker-compose.yml`, so they come back automatically after a daemon restart or
reboot — nothing extra needed. `ensure_agents.linux.sh` (cron, every 30 min) is
the belt-and-suspenders re-start if anything is still down during market hours.

**Stop the machine from suspending**

```bash
# Block all sleep/suspend/hibernate system-wide.
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

**Laptop lid — keep running with the lid closed**

Edit `/etc/systemd/logind.conf` and set:

```
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
```

then apply: `sudo systemctl restart systemd-logind`
(note: restarting logind may end your desktop session — do it over SSH or expect
to log back in).

**If it's running a GNOME desktop, also disable GUI auto-suspend**

```bash
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing'
```

**Verify nothing will suspend:**

```bash
systemctl status sleep.target        # should show "masked"
cat /sys/power/state                 # informational
```

> Headless/server route (optional): if you won't use the desktop, boot to the
> multi-user target (`sudo systemctl set-default multi-user.target`) — no GUI
> power manager to fight, lower idle load.

---

## 8. Sanity checks

```bash
docker-compose ps                                   # agents up
docker-compose logs -f --tail=50 agent-spy          # scanning?
docker-compose run --rm --no-deps dashboard \
    python scripts/compare_shadow_vs_current.py --symbols SPY,QQQ   # A/B works
```

---

## Notes / gotchas

- **`docker-compose` (V1) vs `docker compose` (V2):** this project standardizes
  on the hyphenated V1 binary. If you only have the V2 plugin, either install the
  V1 binary (step 1) or alias `docker-compose='docker compose'` — but the cron
  wrappers call the binary by name via `command -v docker-compose`.
- **Timezone:** cron times above assume the Mac's US/Pacific. `13:12` local =
  after the 4pm ET close only if the box is on Pacific. Either set the box's TZ to
  match, or shift the cron minutes/hours to land just after market close locally.
- **Two machines, one live account:** do NOT run the `realmoney` or `public`
  profiles on both the Mac and Ubuntu at once — they'd double-trade the same real
  account. Run live on exactly one host; use the other for paper/shadow only.
