#!/usr/bin/env python
"""Time-of-day analysis for sweet spot replay trades.

Slices the 1-year SPY golden-param replay by entry-time bucket
(30-min and hourly) and reports per-bucket WR / PF / P&L /
outcome mix. Used to identify whether the 33% time_stop bucket
clusters in a particular window we could prune.

Usage:
    cd options_agent
    python scripts/analyze_time_of_day.py --symbol SPY --days 365
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.replay_sweet_spot import replay_day

logging.basicConfig(level=logging.WARNING)


def collect_trades(df: pd.DataFrame, trading_days: list, symbol: str,
                   gainz_on: bool = True) -> list[dict]:
    """Run the full replay and return every trigger dict."""
    all_triggers = []
    for day in trading_days:
        day_bars = df[df.index.date == day]
        if len(day_bars) < 24:
            continue
        triggers = replay_day(
            day_bars, day,
            max_chop=5, min_quality=4, max_quality=7, min_cascade=4,
            breakout_pct=0.25, cooldown_bars=3,
            scan_end="13:59", scan_start="11:30",
            target_mult_low=1.0, target_mult_mid=1.25, target_mult_high=1.5,
            regime_guard=True, symbol=symbol,
            max_trades_per_day=3, max_stops_per_day=1,
            gainz_exit=gainz_on, gainz_body_ratio=0.5,
            gainz_rsi_overbought=65.0, gainz_rsi_oversold=35.0,
        )
        all_triggers.extend(triggers)
    return all_triggers


def bucket_30min(t: str) -> str:
    """Map HH:MM to the 30-min bucket label (e.g. 11:42 → '11:30')."""
    h, m = int(t[:2]), int(t[3:5])
    bucket_m = 0 if m < 30 else 30
    return f"{h:02d}:{bucket_m:02d}"


def bucket_hour(t: str) -> str:
    return f"{t[:2]}:00"


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "pnl": 0.0, "avg": 0.0,
                "outcomes": {}}
    wins = [t for t in trades if t["is_winner"]]
    losses = [t for t in trades if not t["is_winner"]]
    gross_p = sum(t["pnl"] for t in wins)
    gross_l = abs(sum(t["pnl"] for t in losses))
    pf = gross_p / gross_l if gross_l > 0 else float("inf")
    outcomes = defaultdict(int)
    for t in trades:
        outcomes[t["outcome"]] += 1
    return {
        "n": len(trades),
        "wr": len(wins) / len(trades) * 100,
        "pf": pf,
        "pnl": sum(t["pnl"] for t in trades),
        "avg": sum(t["pnl"] for t in trades) / len(trades),
        "outcomes": dict(outcomes),
    }


def print_table(title: str, buckets: dict, total_n: int):
    print(f"\n{'═' * 92}")
    print(f"  {title}")
    print(f"{'═' * 92}")
    print(f"  {'Bucket':<10} {'N':>4} {'%':>5} {'WR%':>6} {'PF':>5} "
          f"{'P&L':>9} {'Avg':>8} {'time_stop':>10} {'gainz':>7} {'target':>7} {'stop':>5}")
    print(f"  {'-' * 10} {'-' * 4} {'-' * 5} {'-' * 6} {'-' * 5} "
          f"{'-' * 9} {'-' * 8} {'-' * 10} {'-' * 7} {'-' * 7} {'-' * 5}")
    for bucket, s in sorted(buckets.items()):
        share = s["n"] / total_n * 100
        ts = s["outcomes"].get("time_stop", 0)
        gz = s["outcomes"].get("gainz_exit", 0)
        tg = s["outcomes"].get("target", 0)
        sp = s["outcomes"].get("stop", 0)
        print(f"  {bucket:<10} {s['n']:>4} {share:>4.1f}% {s['wr']:>5.1f}% "
              f"{s['pf']:>5.2f} ${s['pnl']:>+7.2f} ${s['avg']:>+6.3f} "
              f"{ts:>10} {gz:>7} {tg:>7} {sp:>5}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--no-gainz", action="store_true",
                        help="Run without Gainz exit (baseline)")
    args = parser.parse_args()

    from src.utils.alpaca_data import fetch_bars
    print(f"Loading {args.days} days of {args.symbol} 5-min bars...")
    df = fetch_bars(args.symbol, days_back=args.days, interval="5min")
    trading_days = sorted(set(df.index.date))
    print(f"Loaded {len(trading_days)} trading days. Running replay...")

    trades = collect_trades(df, trading_days, args.symbol, gainz_on=not args.no_gainz)
    overall = summarize(trades)
    print(f"Total: {overall['n']} trades, WR {overall['wr']:.1f}%, PF {overall['pf']:.2f}, "
          f"P&L ${overall['pnl']:+.2f}")

    # ── Bucket by 30-min ──
    by_30 = defaultdict(list)
    for t in trades:
        by_30[bucket_30min(t["time"])].append(t)
    by_30_summary = {b: summarize(ts) for b, ts in by_30.items()}
    print_table(f"30-MIN ENTRY BUCKETS — {args.symbol}, {args.days}d "
                f"({'gainz on' if not args.no_gainz else 'baseline'})",
                by_30_summary, overall["n"])

    # ── Bucket by hour ──
    by_hr = defaultdict(list)
    for t in trades:
        by_hr[bucket_hour(t["time"])].append(t)
    by_hr_summary = {b: summarize(ts) for b, ts in by_hr.items()}
    print_table(f"HOURLY ENTRY BUCKETS — {args.symbol}, {args.days}d",
                by_hr_summary, overall["n"])

    # ── Direction × time mix (call vs put per bucket) ──
    by_dir = defaultdict(lambda: defaultdict(list))
    for t in trades:
        d = "CALL" if "call" in t["direction"] else "PUT"
        by_dir[bucket_30min(t["time"])][d].append(t)
    print(f"\n{'═' * 92}")
    print(f"  CALL vs PUT MIX BY 30-MIN BUCKET")
    print(f"{'═' * 92}")
    print(f"  {'Bucket':<10} {'CALL N':>7} {'CALL WR':>8} {'CALL PnL':>10} "
          f"{'PUT N':>7} {'PUT WR':>8} {'PUT PnL':>10}")
    print(f"  {'-' * 10} {'-' * 7} {'-' * 8} {'-' * 10} "
          f"{'-' * 7} {'-' * 8} {'-' * 10}")
    for bucket in sorted(by_dir.keys()):
        c_s = summarize(by_dir[bucket]["CALL"])
        p_s = summarize(by_dir[bucket]["PUT"])
        print(f"  {bucket:<10} {c_s['n']:>7} {c_s['wr']:>7.1f}% ${c_s['pnl']:>+8.2f} "
              f"{p_s['n']:>7} {p_s['wr']:>7.1f}% ${p_s['pnl']:>+8.2f}")


if __name__ == "__main__":
    main()
