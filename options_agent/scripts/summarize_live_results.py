#!/usr/bin/env python
"""Aggregate live paper-trading results from sweet_spot_journal/*.json.

Reads every reconciled trigger across all journal files and prints
WR / PF / P&L / exit-mix in the same format as the replay output —
so you can compare backtest expectation vs actual live performance.

Usage:
    cd options_agent
    python scripts/summarize_live_results.py                # all-time
    python scripts/summarize_live_results.py --days 7       # last 7 days
    python scripts/summarize_live_results.py --since 2026-04-01
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

JOURNAL_DIR = Path(__file__).resolve().parent.parent / "sweet_spot_journal"


def load_trades(start: date | None = None, end: date | None = None) -> list[dict]:
    """Load every closed trigger across journal files in [start, end]."""
    trades = []
    for jf in sorted(JOURNAL_DIR.glob("*.json")):
        try:
            d = date.fromisoformat(jf.stem)
        except ValueError:
            continue
        if start and d < start:
            continue
        if end and d > end:
            continue
        try:
            entries = json.loads(jf.read_text())
        except Exception:
            continue
        for t in entries:
            t["_journal_date"] = jf.stem
            trades.append(t)
    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=0, help="Last N days (0 = all-time)")
    parser.add_argument("--since", type=str, default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--symbol", type=str, default=None, help="Filter by symbol")
    parser.add_argument("--include-open", action="store_true", help="Include unclosed positions in stats")
    args = parser.parse_args()

    end = date.today()
    if args.since:
        start = date.fromisoformat(args.since)
    elif args.days > 0:
        start = end - timedelta(days=args.days)
    else:
        start = None

    all_trades = load_trades(start=start, end=end)
    if args.symbol:
        all_trades = [t for t in all_trades if t.get("symbol") == args.symbol]

    closed = [t for t in all_trades if t.get("closed") and t.get("pnl") is not None]
    open_t = [t for t in all_trades if not t.get("closed")]
    no_pnl = [t for t in all_trades if t.get("closed") and t.get("pnl") is None]

    stats_pool = closed if not args.include_open else closed + open_t

    label = f"last {args.days}d" if args.days else (f"since {args.since}" if args.since else "all-time")
    sym_label = args.symbol or "ALL"

    print(f"\n{'═' * 70}")
    print(f"  LIVE AGENT RESULTS — {sym_label}, {label}")
    print(f"  Source: {JOURNAL_DIR}")
    print(f"{'═' * 70}")

    if not all_trades:
        print("\n  No journal entries found in this window.")
        return

    print(f"\n  Triggers logged: {len(all_trades)}  "
          f"(closed: {len(closed)}, open: {len(open_t)}, missing P&L: {len(no_pnl)})")

    if not stats_pool:
        print("  No closed trades with P&L yet.")
        if no_pnl:
            print(f"  ⚠ {len(no_pnl)} closed trades have no P&L — run the agent's "
                  f"reconciliation step or check Alpaca connectivity.")
        return

    wins = [t for t in stats_pool if t.get("is_winner")]
    losses = [t for t in stats_pool if t.get("is_winner") is False]
    gross_p = sum(t["pnl"] for t in wins)
    gross_l = abs(sum(t["pnl"] for t in losses))
    pf = gross_p / gross_l if gross_l > 0 else float("inf")
    total_pnl = sum(t["pnl"] for t in stats_pool)
    avg_pnl = total_pnl / len(stats_pool)
    wr = len(wins) / len(stats_pool) * 100

    print(f"\n  Win Rate:        {wr:.1f}% ({len(wins)}/{len(stats_pool)})")
    print(f"  Profit Factor:   {pf:.2f}")
    print(f"  Total P&L:       ${total_pnl:+.2f}")
    print(f"  Avg P&L/Trade:   ${avg_pnl:+.4f}")
    if wins:
        print(f"  Avg Winner:      ${sum(t['pnl'] for t in wins)/len(wins):+.4f}")
    if losses:
        print(f"  Avg Loser:       ${sum(t['pnl'] for t in losses)/len(losses):+.4f}")

    # ── Exit mix ──
    outcomes = {}
    for t in stats_pool:
        r = t.get("exit_reason", "unknown")
        outcomes[r] = outcomes.get(r, 0) + 1
    print(f"\n  Exit Breakdown:")
    for r, c in sorted(outcomes.items(), key=lambda x: -x[1]):
        print(f"    {r:<12} {c:>3} ({c/len(stats_pool)*100:.0f}%)")

    # ── Backtest comparison ──
    print(f"\n  Backtest expectation (SPY 1-yr golden + Gainz): WR 63.6%, PF 1.81")
    delta_wr = wr - 63.6
    delta_pf = pf - 1.81
    print(f"  Live vs backtest:   WR {delta_wr:+.1f}pp · PF {delta_pf:+.2f}")

    # ── Trade log ──
    print(f"\n  {'Date':<12} {'Time':<6} {'Sym':<5} {'Dir':<5} {'Entry':>8} "
          f"{'Exit':>8} {'P&L':>8} {'Reason':<10}")
    print(f"  {'-'*12} {'-'*6} {'-'*5} {'-'*5} {'-'*8} {'-'*8} {'-'*8} {'-'*10}")
    for t in sorted(stats_pool, key=lambda x: (x.get("_journal_date", ""), x.get("time", ""))):
        d = "CALL" if "call" in t.get("direction", "") else "PUT"
        entry = t.get("actual_entry") or t.get("entry") or 0
        exit_p = t.get("exit_price") or 0
        pnl = t.get("pnl", 0)
        reason = t.get("exit_reason", "?")
        marker = "✅" if t.get("is_winner") else "❌"
        print(f"  {t['_journal_date']:<12} {t.get('time', ''):<6} {t.get('symbol', ''):<5} "
              f"{d:<5} ${entry:>7.2f} ${exit_p:>7.2f} ${pnl:>+7.2f} {reason:<10} {marker}")


if __name__ == "__main__":
    main()
