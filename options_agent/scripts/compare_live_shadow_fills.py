#!/usr/bin/env python3
"""Compare what live and shadow PAID and MADE on the same signal.

`compare_live_shadow.py` answers "did both books decide the same thing?".
This answers the next question: given that they decided the same thing, did
they get the same trade? Live and shadow run identical code from
.worktree-live-main, so once a signal matches, every remaining difference is
execution — fill price, exit timing, and the P&L that falls out of them.

Live is real money at 1 contract; shadow is a second paper account at 3 (SPY)
or cascade-sized (QQQ). Everything here is reported PER CONTRACT so the two
are comparable, and dollars are per-contract premium x 100.

Usage:
  compare_live_shadow_fills.py --since 2026-08-19
  compare_live_shadow_fills.py --since 2026-08-19 --symbol SPY --detail
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict

BASE = os.environ.get(
    "OPTIONS_AGENT_BASE",
    "/home/rohith/projects/options_algo/options-algo-trader/options_agent",
)

RULE = "=" * 96


def load(book: str, since: str, until: str | None) -> dict:
    """Trades keyed by (date, symbol, signal time) — the identity of a signal."""
    out: dict[tuple, list] = defaultdict(list)
    for f in sorted(glob.glob(os.path.join(BASE, f"sweet_spot_journal_{book}", "2026-*_*.json"))):
        name = os.path.basename(f)
        if "verdict" in name:
            continue
        day = name[:10]
        if day < since or (until and day > until):
            continue
        try:
            rows = json.load(open(f))
        except Exception:
            continue
        for t in rows:
            out[(day, t.get("symbol"), t.get("time"))].append(t)
    return out


def per_contract_pnl(t: dict) -> float | None:
    """Premium P&L per contract, in dollars.

    `pnl` is already per-contract premium when the agent recorded it; fall
    back to the fill prices when it did not, which is most of live.
    """
    if t.get("pnl") is not None:
        return float(t["pnl"]) * 100.0
    entry = t.get("actual_entry") if t.get("actual_entry") is not None else t.get("option_premium")
    exit_ = t.get("exit_price")
    if entry is None or exit_ is None:
        return None
    return (float(exit_) - float(entry)) * 100.0


def fmt(v, nd=2, width=7):
    if v is None:
        return "—".rjust(width)
    return f"{v:,.{nd}f}".rjust(width)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", required=True, help="First session date, YYYY-MM-DD")
    ap.add_argument("--until", default=None, help="Last session date, YYYY-MM-DD")
    ap.add_argument("--symbol", default=None, help="Restrict to one symbol")
    ap.add_argument("--detail", action="store_true", help="Print every matched trade")
    args = ap.parse_args()

    live = load("live", args.since, args.until)
    shad = load("shadow", args.since, args.until)
    if args.symbol:
        live = {k: v for k, v in live.items() if k[1] == args.symbol}
        shad = {k: v for k, v in shad.items() if k[1] == args.symbol}

    matched = sorted(set(live) & set(shad))
    live_only = sorted(set(live) - set(shad))
    shad_only = sorted(set(shad) - set(live))

    print(f"\n{RULE}")
    print(f"LIVE vs SHADOW FILLS — {args.since} → {args.until or 'latest'}"
          f"{' · ' + args.symbol if args.symbol else ''}")
    print(RULE)
    print(f"\n  matched signals {len(matched)}   live-only {len(live_only)}   shadow-only {len(shad_only)}")
    if live_only:
        print(f"    live-only:   {', '.join(f'{d} {s} {t}' for d, s, t in live_only[:8])}"
              + (" …" if len(live_only) > 8 else ""))
    if shad_only:
        print(f"    shadow-only: {', '.join(f'{d} {s} {t}' for d, s, t in shad_only[:8])}"
              + (" …" if len(shad_only) > 8 else ""))

    if args.detail:
        print(f"\n  {'date':11} {'sym':4} {'time':5} {'L entry':>8} {'S entry':>8} {'slip':>7} "
              f"{'L exit':>8} {'S exit':>8} {'L $/ct':>8} {'S $/ct':>8}  {'L reason':14} {'S reason':14}")
        print("  " + "-" * 118)

    agg = defaultdict(lambda: {"n": 0, "l": 0.0, "s": 0.0, "ln": 0, "sn": 0,
                               "entry_gap": [], "exit_gap": 0, "lw": 0, "sw": 0})
    for k in matched:
        l, s = live[k][0], shad[k][0]
        lp, sp = per_contract_pnl(l), per_contract_pnl(s)
        le, se = l.get("actual_entry"), s.get("actual_entry")
        a = agg[k[1]]
        a["n"] += 1
        if le is not None and se is not None:
            a["entry_gap"].append(float(le) - float(se))
        if l.get("exit_reason") != s.get("exit_reason") or l.get("exit_time", "")[:16] != s.get("exit_time", "")[:16]:
            a["exit_gap"] += 1
        if lp is not None:
            a["l"] += lp; a["ln"] += 1; a["lw"] += 1 if lp > 0 else 0
        if sp is not None:
            a["s"] += sp; a["sn"] += 1; a["sw"] += 1 if sp > 0 else 0

        if args.detail:
            slip = (float(le) - float(se)) if (le is not None and se is not None) else None
            print(f"  {k[0]:11} {k[1]:4} {k[2]:5} {fmt(le,2,8)} {fmt(se,2,8)} {fmt(slip,2,7)} "
                  f"{fmt(l.get('exit_price'),2,8)} {fmt(s.get('exit_price'),2,8)} "
                  f"{fmt(lp,0,8)} {fmt(sp,0,8)}  "
                  f"{str(l.get('exit_reason'))[:14]:14} {str(s.get('exit_reason'))[:14]:14}")

    print(f"\n  {'sym':5} {'matched':>8} {'L $/ct':>10} {'S $/ct':>10} {'gap':>10} "
          f"{'L WR':>7} {'S WR':>7} {'avg entry slip':>15} {'exit differs':>13}")
    print("  " + "-" * 96)
    tl = ts = 0.0
    for sym, a in sorted(agg.items()):
        gap = a["l"] - a["s"] if (a["ln"] and a["sn"]) else None
        eg = sum(a["entry_gap"]) / len(a["entry_gap"]) if a["entry_gap"] else None
        lwr = a["lw"] / a["ln"] * 100 if a["ln"] else None
        swr = a["sw"] / a["sn"] * 100 if a["sn"] else None
        tl += a["l"]; ts += a["s"]
        print(f"  {sym:5} {a['n']:>8} {fmt(a['l'],0,10)} {fmt(a['s'],0,10)} {fmt(gap,0,10)} "
              f"{fmt(lwr,0,7)} {fmt(swr,0,7)} {fmt(eg,3,15)} {a['exit_gap']:>10}/{a['n']}")
    print("  " + "-" * 96)
    print(f"  {'TOTAL':5} {len(matched):>8} {fmt(tl,0,10)} {fmt(ts,0,10)} {fmt(tl-ts,0,10)}")
    print("\n  Note: P&L is per contract and premium-only — it excludes fees and any")
    print("  exercised share legs. Use dashboard/_ledger.py for broker-verified dollars.")
    print(f"\n{RULE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
