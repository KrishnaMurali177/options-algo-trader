#!/usr/bin/env python
"""Sweep GainzAlgoV2 thresholds against the 1-year SPY replay.

Loads cached 5-min bars, iterates over (RSI overbought/oversold, body-ratio)
combinations, runs the full sweet-spot replay for each, and prints a sorted
comparison table.

Usage:
    cd options_agent
    python scripts/sweep_gainz_thresholds.py --days 365
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.replay_sweet_spot import replay_day

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def run_combo(df: pd.DataFrame, trading_days: list, rsi_ob: float, rsi_os: float,
              body_ratio: float, gainz_on: bool) -> dict:
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
            target_mult_low=1.0, target_mult_mid=1.5, target_mult_high=1.5,
            regime_guard=True, symbol="SPY",
            max_trades_per_day=3, max_stops_per_day=1,
            gainz_exit=gainz_on,
            gainz_body_ratio=body_ratio,
            gainz_rsi_overbought=rsi_ob,
            gainz_rsi_oversold=rsi_os,
        )
        all_triggers.extend(triggers)

    if not all_triggers:
        return {"trades": 0, "wr": 0.0, "pf": 0.0, "pnl": 0.0, "gainz_exits": 0}

    wins = [t for t in all_triggers if t["is_winner"]]
    losses = [t for t in all_triggers if not t["is_winner"]]
    gross_p = sum(t["pnl"] for t in wins)
    gross_l = abs(sum(t["pnl"] for t in losses))
    pf = gross_p / gross_l if gross_l > 0 else float("inf")
    return {
        "trades": len(all_triggers),
        "wr": len(wins) / len(all_triggers) * 100,
        "pf": pf,
        "pnl": sum(t["pnl"] for t in all_triggers),
        "avg_w": np.mean([t["pnl"] for t in wins]) if wins else 0.0,
        "avg_l": np.mean([t["pnl"] for t in losses]) if losses else 0.0,
        "gainz_exits": sum(1 for t in all_triggers if t["outcome"] == "gainz_exit"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()

    from src.utils.alpaca_data import fetch_bars
    print(f"Loading {args.days} days of {args.symbol} 5-min bars...")
    df = fetch_bars(args.symbol, days_back=args.days, interval="5min")
    trading_days = sorted(set(df.index.date))
    print(f"Loaded {len(trading_days)} trading days.\n")

    rsi_pairs = [(60, 40), (65, 35), (70, 30), (75, 25), (80, 20)]
    body_ratios = [0.3, 0.4, 0.5, 0.6, 0.7]

    results = []

    print("Running baseline (no Gainz exit)...")
    base = run_combo(df, trading_days, 70, 30, 0.5, gainz_on=False)
    results.append({"label": "BASELINE (no gainz)", "rsi_ob": "-", "rsi_os": "-",
                    "body": "-", **base})

    for rsi_ob, rsi_os in rsi_pairs:
        for body in body_ratios:
            label = f"RSI {rsi_ob}/{rsi_os}, body {body}"
            print(f"Running {label}...")
            r = run_combo(df, trading_days, rsi_ob, rsi_os, body, gainz_on=True)
            results.append({"label": label, "rsi_ob": rsi_ob, "rsi_os": rsi_os,
                            "body": body, **r})

    # ── Sort by P&L descending ──
    results_sorted = sorted(results, key=lambda r: -r["pnl"])

    print(f"\n{'═' * 92}")
    print(f"  GAINZ THRESHOLD SWEEP — {args.symbol}, {len(trading_days)} days, sorted by P&L")
    print(f"{'═' * 92}")
    print(f"  {'Combo':<26} {'Trades':>7} {'WR%':>6} {'PF':>5} {'P&L':>9} "
          f"{'AvgW':>7} {'AvgL':>7} {'GzExits':>8}")
    print(f"  {'-' * 26} {'-' * 7} {'-' * 6} {'-' * 5} {'-' * 9} "
          f"{'-' * 7} {'-' * 7} {'-' * 8}")
    for r in results_sorted:
        marker = "★" if "BASELINE" in r["label"] else " "
        print(f"  {marker}{r['label']:<25} {r['trades']:>7} {r['wr']:>5.1f}% "
              f"{r['pf']:>5.2f} ${r['pnl']:>+7.2f} ${r['avg_w']:>+5.2f} "
              f"${r['avg_l']:>+5.2f} {r['gainz_exits']:>8}")

    # ── Highlight best combo ──
    best = results_sorted[0]
    base_row = next(r for r in results if "BASELINE" in r["label"])
    if "BASELINE" not in best["label"]:
        delta_pnl = best["pnl"] - base_row["pnl"]
        delta_pf = best["pf"] - base_row["pf"]
        delta_wr = best["wr"] - base_row["wr"]
        print(f"\n  Best combo lift vs baseline: P&L ${delta_pnl:+.2f}, "
              f"PF {delta_pf:+.2f}, WR {delta_wr:+.1f}pp")


if __name__ == "__main__":
    main()
