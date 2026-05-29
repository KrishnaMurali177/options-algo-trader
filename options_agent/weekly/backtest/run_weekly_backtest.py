#!/usr/bin/env python3
"""CLI runner for weekly options backtester.

Usage:
  python -m weekly.backtest.run_weekly_backtest --days 365 --symbol SPY
  python -m weekly.backtest.run_weekly_backtest --days 730 --symbols SPY,QQQ --real-options
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import yfinance as yf

from src.utils.alpaca_data import fetch_daily_bars
from weekly.backtest.replay_weekly import (
    WeeklyBacktestConfig,
    compute_metrics,
    print_summary,
    replay_weekly,
)

logger = logging.getLogger(__name__)


def _fetch_vix_history(days: int) -> dict:
    """Fetch daily VIX closes as a date → float map."""
    try:
        vix_df = yf.download("^VIX", period=f"{days}d", interval="1d", progress=False)
        if isinstance(vix_df.columns, pd.MultiIndex):
            vix_df.columns = vix_df.columns.get_level_values(0)
        return {
            d.date() if hasattr(d, 'date') else d: float(row["Close"])
            for d, row in vix_df.iterrows()
        }
    except Exception as e:
        logger.warning("VIX history fetch failed: %s — using 20.0 default", e)
        return {}


def main():
    parser = argparse.ArgumentParser(description="Weekly Options Backtester")
    parser.add_argument("--days", type=int, default=365, help="Days of history to backtest")
    parser.add_argument("--symbol", "-s", default="SPY", help="Single symbol")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols (overrides --symbol)")

    # Config overrides
    parser.add_argument("--quality-min", type=int, default=3)
    parser.add_argument("--quality-max", type=int, default=7)
    parser.add_argument("--chop-min", type=int, default=2)
    parser.add_argument("--chop-max", type=int, default=5)
    parser.add_argument("--explosion-min", type=int, default=2)
    parser.add_argument("--target-delta", type=float, default=0.35)
    parser.add_argument("--min-dte", type=int, default=3)
    parser.add_argument("--max-dte", type=int, default=7)
    parser.add_argument("--stop-atr", type=float, default=1.5)
    parser.add_argument("--target-atr", type=float, default=2.0)
    parser.add_argument("--decay-halflife", type=float, default=2.0)
    parser.add_argument("--no-trailing", action="store_true")
    parser.add_argument("--no-gainz", action="store_true")
    parser.add_argument("--no-real-options", action="store_true")
    parser.add_argument("--slippage", type=float, default=0.0)
    parser.add_argument("--vix-max", type=float, default=30.0)

    # Output
    parser.add_argument("--export-csv", default=None, help="Export triggers to CSV")
    parser.add_argument("--export-triggers", default=None, help="Export triggers as JSON")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
    )

    symbols = args.symbols.split(",") if args.symbols else [args.symbol]

    config = WeeklyBacktestConfig(
        quality_min=args.quality_min,
        quality_max=args.quality_max,
        chop_min=args.chop_min,
        chop_max=args.chop_max,
        explosion_min=args.explosion_min,
        target_delta=args.target_delta,
        min_dte=args.min_dte,
        max_dte=args.max_dte,
        stop_atr_mult=args.stop_atr,
        target_atr_mult=args.target_atr,
        decay_halflife_sessions=args.decay_halflife,
        trailing_enabled=not args.no_trailing,
        gainz_exit=not args.no_gainz,
        real_options=not args.no_real_options,
        slippage=args.slippage,
        vix_max=args.vix_max,
    )

    logger.info("Fetching VIX history (%d days)...", args.days)
    vix_map = _fetch_vix_history(args.days + 30)

    all_triggers = []
    all_trading_days = set()

    for symbol in symbols:
        logger.info("Fetching %s daily bars (%d days)...", symbol, args.days)
        bars = fetch_daily_bars(symbol, days_back=args.days + 60)
        if bars is None or bars.empty:
            logger.error("No data for %s — skipping", symbol)
            continue

        trading_days = sorted(set(
            d.date() if hasattr(d, 'date') else d for d in bars.index
        ))
        all_trading_days.update(trading_days)

        logger.info("Running replay for %s (%d bars)...", symbol, len(bars))
        triggers = replay_weekly(bars, symbol, config, vix_map)
        all_triggers.extend(triggers)

        metrics = compute_metrics(triggers, trading_days)
        print_summary(metrics, symbol)

    if len(symbols) > 1 and all_triggers:
        combined = compute_metrics(all_triggers, sorted(all_trading_days))
        print_summary(combined, "COMBINED")

    if args.export_csv and all_triggers:
        df = pd.DataFrame(all_triggers)
        df.to_csv(args.export_csv, index=False)
        logger.info("Exported %d triggers to %s", len(all_triggers), args.export_csv)

    if args.export_triggers and all_triggers:
        with open(args.export_triggers, "w") as f:
            json.dump(all_triggers, f, indent=2, default=str)
        logger.info("Exported %d triggers to %s", len(all_triggers), args.export_triggers)


if __name__ == "__main__":
    main()
