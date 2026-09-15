#!/usr/bin/env python3
"""Align live and shadow on EXECUTION time and fill price, not signal time.

Matching the two books by the signal time stamped in the journal is wrong
whenever an order does not go out on its own bar. On 2026-09-14 live's "13:10"
trade did not reach the broker until 13:15:10, so it filled at the 13:15 price
and lined up with shadow's 13:15 entry, not its 13:10 one. Keyed on signal
time that looks like "live missed the 13:15 signal"; keyed on execution it is
the truth: live missed the 13:10 fill and everything after it shifted a bar.

So this keys on what the broker actually did — contract, side, fill time, fill
price — and reports what each book got, which fills have no counterpart, and
what the price difference cost.

Read-only.

Usage:
  fill_timeline.py --date 2026-09-14 --symbol SPY \
    --account live:/app/.env.live --account shadow:/app/.env.shadow
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = os.environ.get(
    "OPTIONS_AGENT_BASE",
    "/home/rohith/projects/options_algo/options-algo-trader/options_agent",
)
RULE = "=" * 100


def _client(env_file: str):
    from dotenv import dotenv_values
    from alpaca.trading.client import TradingClient
    v = dotenv_values(env_file)
    paper = str(v.get("ALPACA_PAPER", "true")).strip().lower() not in ("false", "0", "no")
    return TradingClient(v.get("ALPACA_API_KEY", ""), v.get("ALPACA_SECRET_KEY", ""), paper=paper)


def fills(client, day: date) -> list[dict]:
    """Every filled order on `day`, in execution order."""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    lo = datetime.combine(day, datetime.min.time(), timezone.utc)
    hi = lo + timedelta(days=2)
    out = []
    for o in client.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500,
                                                after=lo, until=hi, direction="desc")):
        if o.filled_avg_price is None or o.filled_at is None:
            continue
        t = o.filled_at.astimezone()
        if t.strftime("%Y-%m-%d") != day.isoformat():
            continue
        out.append({"sym": o.symbol, "side": o.side.value, "qty": int(float(o.filled_qty or o.qty)),
                    "px": float(o.filled_avg_price), "at": t,
                    "hhmmss": t.strftime("%H:%M:%S")})
    return sorted(out, key=lambda r: r["at"])


def signal_times(book: str, day: str) -> dict:
    """order_id -> journal signal time, so we can show slip vs execution."""
    out = {}
    d = Path(BASE) / f"sweet_spot_journal_{book}"
    for f in sorted(d.glob(f"{day}_*.json")):
        if "verdict" in f.name:
            continue
        try:
            rows = json.load(open(f))
        except Exception:
            continue
        for t in rows:
            if t.get("order_id"):
                out[t["order_id"]] = (t.get("time"), t.get("exit_reason"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True)
    ap.add_argument("--symbol", default=None, help="Underlying, e.g. SPY")
    ap.add_argument("--account", action="append", required=True, metavar="LABEL:ENVFILE")
    ap.add_argument("--tolerance", type=int, default=90,
                    help="Seconds within which two fills count as the same event (default: 90)")
    args = ap.parse_args()

    day = date.fromisoformat(args.date)
    books = {}
    for spec in args.account:
        label, _, env = spec.partition(":")
        rows = fills(_client(env), day)
        if args.symbol:
            rows = [r for r in rows if r["sym"].startswith(args.symbol)]
        books[label] = rows

    labels = list(books)
    print(f"\n{RULE}")
    print(f"FILL TIMELINE (execution time, broker truth) — {args.date}"
          f"{'  ·  ' + args.symbol if args.symbol else ''}")
    print(RULE)

    for label in labels:
        print(f"\n▌ {label.upper()}\n")
        print(f"  {'filled at':10} {'side':5} {'qty':>3} {'contract':22} {'price':>7}")
        print("  " + "-" * 54)
        for r in books[label]:
            print(f"  {r['hhmmss']:10} {r['side']:5} {r['qty']:>3} {r['sym'][:22]:22} {r['px']:>7.2f}")

    # ── Pair fills across books on (contract, side) within a time tolerance ──
    print(f"\n▌ PAIRED ON CONTRACT + SIDE + EXECUTION TIME (±{args.tolerance}s)\n")
    a, b = labels[0], labels[1]
    left, right = list(books[a]), list(books[b])
    used = set()
    pairs = []
    for i, l in enumerate(left):
        best, bestd = None, None
        for j, r in enumerate(right):
            if j in used or r["sym"] != l["sym"] or r["side"] != l["side"]:
                continue
            d = abs((r["at"] - l["at"]).total_seconds())
            if d <= args.tolerance and (bestd is None or d < bestd):
                best, bestd = j, d
        if best is not None:
            used.add(best)
            pairs.append((l, right[best], bestd))
        else:
            pairs.append((l, None, None))
    for j, r in enumerate(right):
        if j not in used:
            pairs.append((None, r, None))
    pairs.sort(key=lambda p: (p[0] or p[1])["at"])

    print(f"  {'contract':22} {'side':5} | {a[:8]:>8} {'px':>6} | {b[:8]:>8} {'px':>6} | {'Δt':>6} {'Δpx':>6}")
    print("  " + "-" * 88)
    unmatched = defaultdict(list)
    for l, r, d in pairs:
        c = (l or r)["sym"][:22]
        side = (l or r)["side"]
        lt = f"{l['hhmmss']}" if l else "—"
        lp = f"{l['px']:.2f}" if l else "—"
        rt = f"{r['hhmmss']}" if r else "—"
        rp = f"{r['px']:.2f}" if r else "—"
        dt = f"{d:.0f}s" if d is not None else "—"
        dpx = f"{l['px'] - r['px']:+.2f}" if (l and r) else "—"
        flag = ""
        if not r:
            flag = f"   << {a.upper()} ONLY"; unmatched[a].append(l)
        elif not l:
            flag = f"   << {b.upper()} ONLY"; unmatched[b].append(r)
        print(f"  {c:22} {side:5} | {lt:>8} {lp:>6} | {rt:>8} {rp:>6} | {dt:>6} {dpx:>6}{flag}")

    print(f"\n▌ WHAT THE UNMATCHED FILLS WERE\n")
    if not unmatched:
        print("  (every fill has a counterpart)")
    for label, rows in unmatched.items():
        for r in rows:
            print(f"  {label:8} {r['hhmmss']} {r['side']:5} {r['qty']:>3} {r['sym']} @ {r['px']:.2f}")

    print(f"\n{RULE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
