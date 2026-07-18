"""Missed-open health check — alert if an agent didn't scan near the market open.

Agents normally first-scan ~10:00 ET. If one is down/asleep at the open (as the
shadow agents were on 2026-07-15, first scan 10:58 — see
docs/2026-07-16_new_golden_validation_and_parity.md §3b), it silently skips the
morning and takes a different, non-reproducible subset of trades. Container
health checks miss this (the container is "up", just not scanning yet).

This reads each agent group's per-day verdicts JSONL and flags any expected
symbol whose FIRST scan today is missing or later than --threshold (ET). Meant to
run once shortly after the open via cron; dedups on a state file so a re-run
doesn't re-alert.

Run (Docker; the wrapper mounts the live/shadow journal dirs read-only):
  docker-compose --profile live run --rm --no-deps \
    -v "$PWD/options_agent/sweet_spot_journal_live:/app/sweet_spot_journal_live" \
    -v "$PWD/options_agent/sweet_spot_journal_shadow:/app/sweet_spot_journal_shadow" \
    agent-spy python scripts/check_missed_open.py
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, time, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - fallback if tzdata missing
    _ET = None

# (label, journal dir, expected symbols). Only groups whose dir exists are checked,
# so this is safe to run whether or not the live/shadow profiles are up.
DEFAULT_GROUPS = [
    ("PAPER",  "/app/sweet_spot_journal",        ["SPY", "QQQ", "MSFT", "AAPL"]),
    ("LIVE",   "/app/sweet_spot_journal_live",   ["SPY", "QQQ"]),
    ("SHADOW", "/app/sweet_spot_journal_shadow", ["SPY", "QQQ"]),
]


def _et_now() -> datetime:
    return datetime.now(_ET) if _ET else datetime.now()


def _scan_bounds_by_symbol(journal_dir: str, day: str) -> dict[str, tuple[str, str]]:
    """symbol -> (first, last) scan 'HH:MM:SS' ET from today's verdicts; {} if none."""
    f = Path(journal_dir) / f"{day}_verdicts.jsonl"
    bounds: dict[str, tuple[str, str]] = {}
    if not f.exists():
        return bounds
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        sym, ts = r.get("symbol"), r.get("ts")
        if not sym or not ts:
            continue
        clock = str(ts)[11:19]  # 'HH:MM:SS' from ISO ts (ts is already ET)
        if sym not in bounds:
            bounds[sym] = (clock, clock)
        else:
            lo, hi = bounds[sym]
            bounds[sym] = (min(lo, clock), max(hi, clock))
    return bounds


def _first_scan_by_symbol(journal_dir: str, day: str) -> dict[str, str]:
    """symbol -> earliest scan 'HH:MM:SS' (ET) from today's verdicts, or {} if none."""
    return {s: b[0] for s, b in _scan_bounds_by_symbol(journal_dir, day).items()}


def _post_discord(webhook: str, title: str, header: str, offenders: list[str]) -> None:
    desc = header
    for o in offenders:
        desc += f"\n🟠 {o}"
    payload = {
        "embeds": [{
            "title": title,
            "color": 15105570,  # orange
            "description": desc,
            "footer": {"text": _et_now().strftime("%Y-%m-%d %I:%M %p ET")},
        }]
    }
    req = urllib.request.Request(
        webhook, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "OptionsAgent/1.0"},
        method="POST")
    urllib.request.urlopen(req, timeout=10)


def _hhmmss(t: datetime) -> str:
    return t.strftime("%H:%M:%S")


def _alert(args, day: str, offenders: list[str], title: str, header: str,
           tag: str) -> None:
    """Dedup on a state file (once per day+offender-set+tag), then post + log."""
    key = f"{day}|{tag}|" + ";".join(sorted(offenders))
    state = Path(args.state_file)
    if state.exists() and state.read_text().strip() == key:
        print("already alerted for this set — skipping"); return
    print(f"{title}\n  " + "\n  ".join(offenders))
    if args.webhook:
        try:
            _post_discord(args.webhook, title, header, offenders)
            print("Discord alert sent.")
        except Exception as e:  # don't let a webhook hiccup crash the cron
            print(f"Discord post failed: {e}")
    else:
        print("(no DISCORD_WEBHOOK_URL — logged only)")
    state.write_text(key)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["open", "stale"], default="open",
                    help="open = alert if an agent didn't scan by the open threshold; "
                         "stale = alert if an agent stopped scanning mid-session")
    ap.add_argument("--threshold", default="10:07",
                    help="[open] Latest acceptable first-scan time, ET HH:MM (default 10:07)")
    ap.add_argument("--max-staleness", type=int, default=15,
                    help="[stale] Alert if latest scan is older than N minutes (default 15)")
    ap.add_argument("--state-file", default=None,
                    help="Dedup state file (default: per-mode under /app/logs)")
    ap.add_argument("--webhook", default=os.environ.get("DISCORD_WEBHOOK_URL", ""))
    args = ap.parse_args()

    if args.state_file is None:  # keep open/stale dedup state separate
        args.state_file = f"/app/logs/.missed_{args.mode}_state"

    now = _et_now()
    day = now.strftime("%Y-%m-%d")
    if now.weekday() >= 5:
        print("weekend — skipping"); return

    if args.mode == "open":
        thr_h, thr_m = (int(x) for x in args.threshold.split(":"))
        if now.timetz().replace(tzinfo=None) < time(thr_h, thr_m):
            print(f"before threshold {args.threshold} ET — skipping"); return
        thr = f"{thr_h:02d}:{thr_m:02d}:00"
        offenders: list[str] = []
        for label, jdir, symbols in DEFAULT_GROUPS:
            if not Path(jdir).is_dir():
                continue
            first = _first_scan_by_symbol(jdir, day)
            for sym in symbols:
                fs = first.get(sym)
                if fs is None:
                    offenders.append(f"{label} {sym}: no scan yet")
                elif fs > thr:
                    offenders.append(f"{label} {sym}: first scan {fs} (> {args.threshold})")
        if not offenders:
            print(f"{day}: all agents scanned by {args.threshold} ET — OK"); return
        _alert(args, day, offenders, "⚠️ Options Agent MISSED THE OPEN",
               f"Agents that did NOT scan by the open threshold ({args.threshold} ET):", "open")
        return

    # ── mode == "stale": catch a mid-session stall (agent up but not scanning) ──
    # Only meaningful once agents are scanning and before the close (ET 10:10–15:55).
    hm = now.timetz().replace(tzinfo=None)
    if hm < time(10, 10) or hm > time(15, 55):
        print("outside mid-session window (10:10–15:55 ET) — skipping"); return
    now_clock = _hhmmss(now)
    cutoff_dt = now - timedelta(minutes=args.max_staleness)
    cutoff = _hhmmss(cutoff_dt)
    offenders = []
    for label, jdir, symbols in DEFAULT_GROUPS:
        if not Path(jdir).is_dir():
            continue
        bounds = _scan_bounds_by_symbol(jdir, day)
        for sym in symbols:
            b = bounds.get(sym)
            if b is None:
                continue  # never scanned today → the 'open' check owns that case
            last = b[1]
            if last < cutoff:
                offenders.append(f"{label} {sym}: last scan {last} ET ({args.max_staleness}m+ stale)")
    if not offenders:
        print(f"{day} {now_clock}: all agents scanning fresh (< {args.max_staleness}m) — OK"); return
    # 'last scan' is fixed while stalled, so the offender set is stable → dedup on
    # it alone alerts once per stall episode (no 15-min spam).
    _alert(args, day, offenders, "🛑 Options Agent STALLED (mid-session)",
           f"Agents whose last scan is > {args.max_staleness} min old (stalled, not scanning):",
           "stale")


if __name__ == "__main__":
    main()
