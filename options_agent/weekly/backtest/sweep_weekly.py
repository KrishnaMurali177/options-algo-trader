#!/usr/bin/env python3
"""Parameter grid search for weekly options backtester.

Usage:
  python -m weekly.backtest.sweep_weekly --days 365 --symbols SPY,QQQ --quick
  python -m weekly.backtest.sweep_weekly --days 365 --symbol SPY --full
"""

from __future__ import annotations

import argparse
import itertools
import logging
import os
import sys
from copy import deepcopy
from dataclasses import replace
from multiprocessing import Pool, cpu_count
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import yfinance as yf

from src.utils.alpaca_data import fetch_daily_bars
from weekly.backtest.replay_weekly import (
    WeeklyBacktestConfig,
    compute_metrics,
    replay_weekly,
)

logger = logging.getLogger(__name__)

# ── Parameter Grids ──

QUICK_GRID = {
    "quality_band": [(2, 6), (3, 7), (4, 8)],
    "chop_max": [4, 5, 6],
    "target_delta": [0.25, 0.30, 0.35, 0.40],
    "dte_window": [(2, 5), (3, 7), (5, 10)],
    "stop_atr_mult": [1.0, 1.5, 2.0],
    "target_atr_mult": [1.5, 2.0, 3.0],
    "decay_halflife_sessions": [1.5, 2.0, 3.0],
}

FULL_GRID = {
    **QUICK_GRID,
    "chop_min": [1, 2, 3],
    "trailing_enabled": [True, False],
    "gainz_body_ratio": [0.5, 0.6, 0.7],
    "gainz_rsi_overbought": [70, 75, 80],
}


def _build_configs(
    base: WeeklyBacktestConfig,
    grid: dict[str, list],
) -> list[tuple[str, WeeklyBacktestConfig]]:
    """Generate all config combinations from the parameter grid."""
    param_names = list(grid.keys())
    param_values = list(grid.values())
    configs = []

    for combo in itertools.product(*param_values):
        params = dict(zip(param_names, combo))
        label_parts = []
        overrides = {}

        for name, val in params.items():
            if name == "quality_band":
                overrides["quality_min"] = val[0]
                overrides["quality_max"] = val[1]
                label_parts.append(f"Q{val[0]}-{val[1]}")
            elif name == "dte_window":
                overrides["min_dte"] = val[0]
                overrides["max_dte"] = val[1]
                label_parts.append(f"DTE{val[0]}-{val[1]}")
            else:
                overrides[name] = val
                short = name.replace("_sessions", "").replace("_mult", "").replace("_atr", "")
                label_parts.append(f"{short[:4]}={val}")

        label = " ".join(label_parts)
        config = replace(base, **overrides)
        configs.append((label, config))

    return configs


# ── Worker ──

def _run_single(args: tuple) -> dict:
    """Worker for a single config combo. Returns metrics dict with label."""
    label, config, bars_dict, vix_map = args

    all_triggers = []
    all_days = set()

    for symbol, bars in bars_dict.items():
        trading_days = sorted(set(
            d.date() if hasattr(d, 'date') else d for d in bars.index
        ))
        all_days.update(trading_days)
        triggers = replay_weekly(bars, symbol, config, vix_map)
        all_triggers.extend(triggers)

    metrics = compute_metrics(all_triggers, sorted(all_days))
    metrics["label"] = label
    return metrics


# ── Sweep ──

def sweep_weekly(
    bars_dict: dict[str, pd.DataFrame],
    grid: dict[str, list],
    base_config: WeeklyBacktestConfig,
    vix_map: dict | None = None,
    n_jobs: int = 1,
) -> pd.DataFrame:
    """Run replay_weekly for every combination in the grid.

    Returns DataFrame with one row per combination, sorted by Sharpe.
    """
    configs = _build_configs(base_config, grid)
    total = len(configs)
    logger.info("Sweep: %d combinations, %d jobs", total, n_jobs)

    work = [(label, config, bars_dict, vix_map) for label, config in configs]

    if n_jobs > 1:
        with Pool(min(n_jobs, cpu_count())) as pool:
            results = pool.map(_run_single, work)
    else:
        results = []
        for i, w in enumerate(work):
            if (i + 1) % 50 == 0 or i == 0:
                logger.info("  Progress: %d / %d", i + 1, total)
            results.append(_run_single(w))

    df = pd.DataFrame(results)
    df = df.sort_values("sharpe", ascending=False).reset_index(drop=True)
    return df


def print_sweep_results(df: pd.DataFrame, top_n: int = 20) -> None:
    """Pretty-print the top sweep results."""
    print(f"\n{'=' * 100}")
    print(f"  TOP {min(top_n, len(df))} CONFIGURATIONS (sorted by Sharpe)")
    print(f"{'=' * 100}")
    print(f"{'Config':<40s} {'Trades':>6s} {'WR%':>6s} {'PF':>6s} {'P&L':>10s} {'Sharpe':>7s} {'MaxDD':>10s} {'AvgH':>5s}")
    print("-" * 100)

    for _, row in df.head(top_n).iterrows():
        print(
            f"{row['label']:<40s} "
            f"{row['trades']:>6.0f} "
            f"{row['win_rate']:>6.1f} "
            f"{row['profit_factor']:>6.2f} "
            f"${row['total_pnl']:>9,.2f} "
            f"{row['sharpe']:>7.2f} "
            f"${row['max_dd']:>9,.2f} "
            f"{row['avg_hold_days']:>5.1f}"
        )

    print(f"{'=' * 100}")

    # Bottom 5 for comparison
    if len(df) > top_n:
        print(f"\n  WORST 5:")
        print("-" * 100)
        for _, row in df.tail(5).iterrows():
            print(
                f"{row['label']:<40s} "
                f"{row['trades']:>6.0f} "
                f"{row['win_rate']:>6.1f} "
                f"{row['profit_factor']:>6.2f} "
                f"${row['total_pnl']:>9,.2f} "
                f"{row['sharpe']:>7.2f} "
                f"${row['max_dd']:>9,.2f} "
                f"{row['avg_hold_days']:>5.1f}"
            )
        print(f"{'=' * 100}\n")


def _fetch_vix_history(days: int) -> dict:
    try:
        vix_df = yf.download("^VIX", period=f"{days}d", interval="1d", progress=False)
        if isinstance(vix_df.columns, pd.MultiIndex):
            vix_df.columns = vix_df.columns.get_level_values(0)
        return {
            d.date() if hasattr(d, 'date') else d: float(row["Close"])
            for d, row in vix_df.iterrows()
        }
    except Exception:
        return {}


def main():
    parser = argparse.ArgumentParser(description="Weekly Options Parameter Sweep")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--symbol", "-s", default="SPY")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols")
    parser.add_argument("--quick", action="store_true", help="Use reduced grid (~1K combos)")
    parser.add_argument("--full", action="store_true", help="Use full grid (~10K+ combos)")
    parser.add_argument("--jobs", "-j", type=int, default=1, help="Parallel workers")
    parser.add_argument("--top", type=int, default=20, help="Show top N results")
    parser.add_argument("--export-csv", default=None, help="Export results to CSV")
    parser.add_argument("--no-real-options", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    symbols = args.symbols.split(",") if args.symbols else [args.symbol]
    grid = FULL_GRID if args.full else QUICK_GRID

    base = WeeklyBacktestConfig(real_options=not args.no_real_options)

    logger.info("Fetching data for %s (%d days)...", ", ".join(symbols), args.days)
    vix_map = _fetch_vix_history(args.days + 30)

    bars_dict = {}
    for sym in symbols:
        bars = fetch_daily_bars(sym, days_back=args.days + 60)
        if bars is not None and not bars.empty:
            bars_dict[sym] = bars
            logger.info("  %s: %d bars", sym, len(bars))
        else:
            logger.warning("  %s: no data — skipping", sym)

    if not bars_dict:
        logger.error("No data loaded — aborting")
        return

    results = sweep_weekly(bars_dict, grid, base, vix_map, n_jobs=args.jobs)
    print_sweep_results(results, top_n=args.top)

    if args.export_csv:
        results.to_csv(args.export_csv, index=False)
        logger.info("Exported %d results to %s", len(results), args.export_csv)


if __name__ == "__main__":
    main()
