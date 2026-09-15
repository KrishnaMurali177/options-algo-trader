#!/usr/bin/env python3
"""Reconstruct missing journal exits from the broker, and diff the two books.

Two uses:

  1. Validate / preview the _reconcile_journal fix. The live lineage only
     looked up an exit fill when exit_reason was "gainz_exit", so every other
     agent-initiated close (stagnation, stop, decay_target, theta_exit,
     time_stop) kept close_order_id but never got exit_price or pnl. This
     re-derives them exactly the way the fixed code would, without writing
     anything, so you can see what the repair recovers before deploying it.

  2. Show, contract by contract, what live and shadow each did on a given
     session — including contracts only one of them traded.

P&L uses the corrected sign convention: options are always BOUGHT, so a long
put profits when its premium rises just like a call. Only a share-mode put is
short the underlying.

Read-only. Never writes a journal.

Usage:
  journal_exit_audit.py --date 2026-09-14 \
    --account live:/app/.env.live --account shadow:/app/.env.shadow
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

BASE = os.environ.get(
    "OPTIONS_AGENT_BASE",
    "/home/rohith/projects/options_algo/options-algo-trader/options_agent",
)
RULE = "=" * 104


def _client(env_file: str):
    from dotenv import dotenv_values
    from alpaca.trading.client import TradingClient
    v = dotenv_values(env_file)
    paper = str(v.get("ALPACA_PAPER", "true")).strip().lower() not in ("false", "0", "no")
    return TradingClient(v.get("ALPACA_API_KEY", ""), v.get("ALPACA_SECRET_KEY", ""), paper=paper)


def _fill(client, order_id: str):
    """Fill price/time for one order id, or None."""
    try:
        o = client.get_order_by_id(order_id)
    except Exception:
        return None
    if not o.filled_avg_price:
        return None
    return {"price": float(o.filled_avg_price),
            "time": str(o.filled_at) if o.filled_at else None,
            "qty": int(float(o.filled_qty or o.qty or 0))}


def pnl_of(trig: dict, entry, exit_p) -> float | None:
    """Corrected sign: long premium for anything with an OCC symbol."""
    if entry is None or exit_p is None:
        return None
    is_option = bool(trig.get("occ_symbol"))
    if is_option or "call" in trig.get("direction", ""):
        return exit_p - entry
    return entry - exit_p


def load_book(book: str, day: str) -> list[dict]:
    out = []
    d = Path(BASE) / f"sweet_spot_journal_{book}"
    for f in sorted(d.glob(f"{day}_*.json")):
        if "verdict" in f.name:
            continue
        try:
            out.extend(json.load(open(f)))
        except Exception:
            pass
    return out


def enrich(book: str, day: str, client) -> list[dict]:
    """Journal rows plus broker-derived exit_price/pnl where the journal lacks them."""
    rows = load_book(book, day)
    for t in rows:
        t["_book"] = book
        t["_recovered"] = False
        entry, exit_p = t.get("actual_entry"), t.get("exit_price")
        if exit_p is None and t.get("close_order_id"):
            f = _fill(client, t["close_order_id"])
            if f:
                exit_p = f["price"]
                t["_recovered"] = True
        if entry is None and t.get("order_id"):
            f = _fill(client, t["order_id"])
            if f:
                entry = f["price"]
        t["_entry"], t["_exit"] = entry, exit_p
        t["_pnl"] = pnl_of(t, entry, exit_p)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True)
    ap.add_argument("--account", action="append", required=True, metavar="LABEL:ENVFILE")
    ap.add_argument("--symbol", default=None)
    args = ap.parse_args()

    books = {}
    for spec in args.account:
        label, _, env = spec.partition(":")
        books[label] = enrich(label, args.date, _client(env))

    if args.symbol:
        books = {k: [t for t in v if t.get("symbol") == args.symbol] for k, v in books.items()}

    print(f"\n{RULE}")
    print(f"JOURNAL EXIT AUDIT — {args.date}"
          f"{'  ·  ' + args.symbol if args.symbol else ''}")
    print(RULE)

    # ── What the fix recovers ──
    print("\n▌ EXITS THE JOURNAL WAS MISSING (recovered from broker close orders)\n")
    print(f"  {'book':8} {'sym':5} {'time':6} {'contract':22} {'reason':14} {'entry':>7} {'exit':>7} {'$/ct':>8}")
    print("  " + "-" * 92)
    any_rec = False
    for label, rows in books.items():
        for t in rows:
            if not t["_recovered"]:
                continue
            any_rec = True
            print(f"  {label:8} {t.get('symbol',''):5} {t.get('time',''):6} "
                  f"{str(t.get('occ_symbol'))[:22]:22} {str(t.get('exit_reason'))[:14]:14} "
                  f"{t['_entry'] if t['_entry'] is not None else float('nan'):>7.2f} "
                  f"{t['_exit']:>7.2f} {(t['_pnl'] or 0)*100:>8,.0f}")
    if not any_rec:
        print("  (nothing missing — journals already complete for this session)")

    # ── Contract-level comparison across books ──
    print(f"\n▌ CONTRACT-BY-CONTRACT, BOTH BOOKS\n")
    keys = sorted({(t.get("symbol"), t.get("time"), t.get("occ_symbol"))
                   for rows in books.values() for t in rows})
    labels = list(books)
    idx = {l: {(t.get("symbol"), t.get("time"), t.get("occ_symbol")): t for t in rows}
           for l, rows in books.items()}
    hdr = f"  {'sym':5} {'time':6} {'contract':22}"
    for l in labels:
        hdr += f" | {l[:6]:>6} {'ct':>3} {'entry':>6} {'exit':>6} {'$':>7}"
    print(hdr)
    print("  " + "-" * (len(hdr) + 4))
    totals = defaultdict(float)
    for k in keys:
        line = f"  {k[0] or '':5} {k[1] or '':6} {str(k[2])[:22]:22}"
        present = []
        for l in labels:
            t = idx[l].get(k)
            if t is None:
                line += f" | {'—':>6} {'—':>3} {'—':>6} {'—':>6} {'—':>7}"
                continue
            present.append(l)
            n = t.get("num_contracts") or 0
            d = (t["_pnl"] or 0) * 100 * n
            totals[l] += d
            line += (f" | {'✓':>6} {n:>3} "
                     f"{t['_entry'] if t['_entry'] is not None else float('nan'):>6.2f} "
                     f"{t['_exit'] if t['_exit'] is not None else float('nan'):>6.2f} "
                     f"{d:>7,.0f}")
        flag = "   << ONLY " + present[0].upper() if len(present) == 1 else ""
        print(line + flag)
    print("  " + "-" * (len(hdr) + 4))
    tl = f"  {'TOTAL':5} {'':6} {'':22}"
    for l in labels:
        tl += f" | {'':>6} {'':>3} {'':>6} {'':>6} {totals[l]:>7,.0f}"
    print(tl)

    print(f"\n  Sized dollars = per-contract premium P&L x 100 x contracts.")
    print(f"  Read-only: no journal was modified.")
    print(f"\n{RULE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
