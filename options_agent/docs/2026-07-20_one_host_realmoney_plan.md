# Consolidate real money to ONE host

**Date:** 2026-07-20 · **Status:** proposed (not yet executed)

## Why

Real money currently runs on **both** the Mac and Ubuntu, hitting the **same** live Alpaca
account (251263287). Running two independent agents on one account causes three recurring
problems — all observed live during 07-16→07-17:

1. **Doubling.** Each host places its own order, so a trade meant to be 1 contract becomes 2
   (e.g. 07-16 SPY 754C, 07-17 746C/696C). `--max-contracts 1` caps *per order*, not the
   account — so intended size is silently 2×, and unpredictably so (some trades single-host).
2. **`max_stops` asymmetry.** The daily-stop circuit breaker counts stops *per host*, and which
   host "owns" a stop depends on which monitor closes the trade first (shared-account race). On
   07-17 Ubuntu SPY halted (1/1) while the Mac kept trading; Ubuntu QQQ ran on while its stop was
   booked by the other host. Participation becomes non-deterministic.
3. **Config sprawl / confusion.** Two hosts, host-labelled Discord tags, divergent trade
   selection, plus the shadow (main's code) and paper — a large surface for misconfiguration on
   the path where mistakes cost real money.

Single-host real money removes **all three** at once. It's higher-leverage than any parameter
tweak (see the SPY cheap-3 analysis in `2026-07-16_new_golden_validation_and_parity.md` §5).

## Which host: **Ubuntu**

- **Always-on** (sleep/suspend masked, lid-ignore) — the right home for unattended real money.
  The Mac sleeps; it caused the 07-15 missed-open gap.
- Native Docker, cron installed, monitoring active (missed-open + stale guards).
- Matches the stated goal of eventually migrating fully to Ubuntu.

The **Mac keeps** paper (`live` profile), shadow, public, and the dashboard — none of which have
real-money stakes. (Paper also doubles across hosts, but that's cosmetic; see optional step 5.)

## Cutover (do at/after market close, account flat)

1. **Confirm the real account is flat** (no open positions) — 0DTE is flat after close:
   ```bash
   docker-compose --profile realmoney run --rm --no-deps -T agent-live-spy python -c \
     "from src.utils.alpaca_paper import AlpacaPaperTrader as T; print(T().get_positions())"
   ```
2. **Stop real money on the Mac** (leave paper/shadow/public running):
   ```bash
   # MAC
   docker-compose --profile realmoney stop agent-live-spy agent-live-qqq
   ```
   Confirm the Mac has no `realmoney` cron that would restart them (it doesn't —
   `ensure_agents` only manages the paper `live` profile).
3. **Confirm Ubuntu is the sole real-money trader** (already running there):
   ```bash
   # UBUNTU
   docker-compose --profile realmoney ps          # both up + healthy
   ```
4. **Simplify the Discord tag** — with one host, drop the host label. On Ubuntu's root `.env`:
   ```bash
   # UBUNTU:  HOST_LABEL back to the plain real-money tag
   echo 'HOST_LABEL=REAL-MONEY' > .env   # (or remove the line; default is REAL-MONEY)
   docker-compose --profile realmoney up -d --force-recreate agent-live-spy agent-live-qqq
   ```
   Now posts read `🔴 ALPACA · REAL-MONEY` again (no MAC/UBUNTU split needed).
5. *(optional)* **Also single-host the paper agents** for a clean paper A/B — pick one host to
   run the `live` (paper) profile; stop it on the other. Lower priority (paper $ is not real).

## Cron migration (Mac → Ubuntu)

Once real money is on Ubuntu, the Mac's paper/live crons must be cleaned up or they
**undo the cutover and false-alarm**:

- **Mac — remove** (paper agents are stopped; `ensure_agents` would restart them every
  30 min, `check_agents_health` would alert them "down", the report crons read a now-stale
  live journal):
  ```bash
  crontab -l | grep -vE 'ensure_agents.sh|check_agents_health.sh|send_daily_report|send_weekly_report' | crontab -
  ```
  (Mac real-money stays down on its own — `ensure_agents` only manages the paper profile.)
- **Ubuntu — the real-money reports now live here.** Installed:
  ```
  20 13 * * 1-5  report_broker_pnl.linux.sh --period day    # EOD cash → Discord + DB/CSV
  5  14 * * 5    report_broker_pnl.linux.sh --period week   # EOW cash → Discord
  ```
  These post the real cash P&L **and** persist a durable daily row (see below).

## Daily performance history (durable, not just Discord)

`report_broker_pnl.py --db/--csv` records each day's real-money row — keyed on
`(date, account)`, upserted so re-runs don't duplicate — to:

- **`options_agent/perf/performance.db`** (SQLite, queryable):
  ```bash
  sqlite3 options_agent/perf/performance.db \
    "SELECT date, total_realized, equity, n_fills, winners, losers FROM daily_pnl ORDER BY date"
  ```
- **`options_agent/perf/daily_pnl.csv`** (human/Excel/grep mirror).

Columns: date, account, paper, equity, buying_power, today_pnl, total_realized,
per_symbol (JSON), n_fills, winners, losers, recorded_at. This is the authoritative
account-truth history for future analysis, independent of the trade journals.

**Seed history (one-shot):** `--backfill` imports every historical day — per-day
realized cash from filled orders + daily equity/P&L from Alpaca portfolio history —
idempotent on `(date, account)`, so it's safe to re-run:
```bash
docker-compose --profile realmoney run --rm --no-deps \
  -v "$PWD/options_agent/perf:/app/perf" agent-live-spy \
  python scripts/report_broker_pnl.py --backfill \
  --db /app/perf/performance.db --csv /app/perf/daily_pnl.csv
```
(Pre-funding zero-equity days are skipped.)

**Shadow account tracked separately.** The shadow (NEW-golden, 2nd paper account)
is recorded to its own `perf/performance_shadow.db` + `shadow_daily_pnl.csv` via
`report_broker_pnl_shadow.linux.sh` (runs `agent-spy` with `--env-file .env.shadow`,
since the shadow's own agent mounts main's code without this script). Same schema —
so the real-money and shadow histories are directly comparable. Cron:
```
25 13 * * 1-5  report_broker_pnl_shadow.linux.sh --period day
```

## Verify (next session)

- One order per trigger on account 251263287 (no same-second duplicate).
- `max_stops` behaves deterministically (one count, one host).
- Discord shows a single real-money post per event.

## Rollback

Re-parallelize is just: start `--profile realmoney` on the Mac again and restore the
`HOST_LABEL=MAC` root `.env`. Nothing is destroyed; this is purely which host runs the profile.

## Note

Once real money is single-host and stable, the SPY **cheap-3** params (§5 of the validation doc,
+39% no-stack P&L, no porting) become a clean, low-confusion change worth revisiting — because at
that point there's only one config to touch, not a two-host parallel matrix.
