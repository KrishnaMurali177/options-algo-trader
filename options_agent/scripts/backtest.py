#!/usr/bin/env python
"""Backtest — replay historical 5-min intraday data through the opening range breakout strategy.

Usage:
    cd options_agent
    python scripts/backtest.py --symbol AAPL
    python scripts/backtest.py --symbol SPY --period 60d --tune
    python scripts/backtest.py --symbol TSLA --period 30d --save
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.backtester import IntradayBacktester
from src.signal_tuner import SignalTuner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def print_header(text: str):
    print(f"\n{'═' * 70}")
    print(f"  {text}")
    print(f"{'═' * 70}")


def main():
    parser = argparse.ArgumentParser(description="Backtest intraday opening range breakout strategy")
    parser.add_argument("--symbol", "-s", default="SPY", help="Symbol to backtest (default: SPY)")
    parser.add_argument("--period", "-p", default="60d", help="Period (e.g., 60d, 6mo, 1y)")
    parser.add_argument("--tune", action="store_true", help="Run signal tuner to find optimal weights")
    parser.add_argument("--save", action="store_true", help="Save trade log to CSV")
    parser.add_argument("--sweet-spot", action="store_true",
                        help="Only take trades matching dashboard sweet spot criteria (Q 4-7 + cascade ≥ 3 + chop ≤ 7)")
    parser.add_argument("--max-chop", type=int, default=7, help="Max choppiness score for sweet spot (default: 7)")
    parser.add_argument("--min-cascade", type=int, default=3, help="Min cascade explosion score for sweet spot (default: 3)")
    args = parser.parse_args()

    # ── Run Backtest ──
    mode_label = " [SWEET SPOT MODE]" if args.sweet_spot else ""
    print_header(f"BACKTEST: {args.symbol} — {args.period} of 5-min data{mode_label}")

    bt = IntradayBacktester(
        sweet_spot_only=args.sweet_spot,
        max_chop_score=args.max_chop,
        min_cascade=args.min_cascade,
    )
    report = bt.run(args.symbol, args.period)

    # ── Summary Stats ──
    print_header("SUMMARY")
    print(f"  Symbol:           {report.symbol}")
    print(f"  Period:           {report.period}")
    print(f"  Trading Days:     {report.total_days}")
    print(f"  Total Signals:    {report.total_trades}")
    print(f"  Trades Taken:     {report.trades_taken}")
    print(f"  Skipped:          {report.total_trades - report.trades_taken}")
    print()
    print(f"  Win Rate:         {report.win_rate:.1f}%")
    print(f"  Wins / Losses:    {report.wins} / {report.losses}")
    print(f"  Avg P&L/Trade:    ${report.avg_pnl_per_trade:.4f}")
    print(f"  Total P&L:        ${report.total_pnl:.4f}")
    print(f"  Max Win:          ${report.max_win:.4f}")
    print(f"  Max Loss:         ${report.max_loss:.4f}")
    print(f"  Avg Winner:       ${report.avg_winner:.4f}")
    print(f"  Avg Loser:        ${report.avg_loser:.4f}")
    print(f"  Profit Factor:    {report.profit_factor:.2f}")
    print()
    print(f"  Call Trades:      {report.call_trades}  (win rate: {report.call_win_rate:.1f}%)")
    print(f"  Put Trades:       {report.put_trades}  (win rate: {report.put_win_rate:.1f}%)")

    # ── Exit Reasons ──
    print_header("EXIT REASONS")
    for reason, count in sorted(report.exit_reasons.items(), key=lambda x: -x[1]):
        pct = count / report.trades_taken * 100 if report.trades_taken > 0 else 0
        bar = "█" * int(pct / 2)
        print(f"  {reason:<12} {count:>4}  ({pct:5.1f}%)  {bar}")

    # ── Signal Accuracy ──
    print_header("SIGNAL ACCURACY (when aligned with trade direction)")
    print(f"  {'Signal':<20} {'Active':>6} {'Wins':>5} {'Win%':>7} {'Avg P&L':>10}")
    print(f"  {'─' * 20} {'─' * 6} {'─' * 5} {'─' * 7} {'─' * 10}")
    for sa in report.signal_accuracy:
        print(f"  {sa.signal_name:<20} {sa.times_active:>6} {sa.wins_when_active:>5} {sa.win_rate:>6.1f}% ${sa.avg_pnl_when_active:>9.4f}")

    # ── Trade Log ──
    print_header("TRADE LOG (last 20)")
    taken = [t for t in report.trades if t.direction != "skip"]
    display = taken[-20:]
    if display:
        rows = []
        for t in display:
            rows.append({
                "Date": str(t.trade_date),
                "Dir": t.direction.upper(),
                "Entry": f"${t.entry_price:.2f}",
                "Exit": f"${t.exit_price:.2f}",
                "P&L": f"${t.pnl_dollars:+.4f}",
                "Exit Reason": t.exit_reason,
                "Momentum": t.momentum_score,
                "Entry@": t.entry_time or "-",
                "Exit@": t.exit_time or "-",
            })
        df = pd.DataFrame(rows)
        print(df.to_string(index=False))

    # ── Signal Tuning ──
    if args.tune:
        print_header("SIGNAL TUNING")
        tuner = SignalTuner()
        result = tuner.tune(report)

        print(f"\n  Current Win Rate:    {result.current_win_rate:.1f}%")
        print(f"  Current Profit Factor: {result.current_profit_factor:.2f}")
        print()
        print(f"  ✅ Optimal Breakout Threshold: {result.optimal_breakout_threshold}")
        print(f"  ✅ Optimal Win Rate:           {result.optimal_win_rate:.1f}%")
        print(f"  ✅ Optimal Profit Factor:       {result.optimal_profit_factor:.2f}")
        print(f"  ✅ Optimal Total P&L:           ${result.optimal_total_pnl:.4f}")
        print(f"  ✅ Trades Taken:                {result.optimal_trades_taken}")
        print(f"  📈 Improvement:                 {result.improvement_pct:+.1f}%")

        print(f"\n  Optimal Signal Weights:")
        for name, weight in sorted(result.optimal_weights.items()):
            default = {"price_vs_range": 30, "intraday_rsi": 20, "intraday_macd": 15,
                       "vwap": 15, "volume": 5, "or_candle": 10, "vix": 5}.get(name, 0)
            change = weight - default
            marker = f" ({change:+d})" if change != 0 else ""
            print(f"    {name:<20} = {weight:>3}{marker}")

        print(f"\n  Signal Importance Ranking:")
        print(f"  {'Signal':<20} {'Importance':>10} {'Win%':>7} {'Avg P&L':>10} {'Count':>6}")
        print(f"  {'─' * 20} {'─' * 10} {'─' * 7} {'─' * 10} {'─' * 6}")
        for s in result.signal_ranking:
            print(f"  {s['name']:<20} {s['importance_score']:>10.1f} {s['win_rate']:>6.1f}% ${s['avg_pnl']:>9.4f} {s['count']:>6}")

    # ── Save CSV ──
    if args.save:
        os.makedirs("backtest_results", exist_ok=True)
        filename = f"backtest_results/{args.symbol}_{date.today().isoformat()}.csv"
        rows = []
        for t in report.trades:
            row = {
                "date": t.trade_date,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "stop_loss": t.stop_loss,
                "target_1": t.target_1,
                "target_2": t.target_2,
                "range_high": t.range_high,
                "range_low": t.range_low,
                "exit_reason": t.exit_reason,
                "pnl": t.pnl_dollars,
                "pnl_pct": t.pnl_pct,
                "momentum": t.momentum_score,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "is_winner": t.is_winner,
            }
            row.update({f"sig_{k}": v for k, v in t.signal_scores.items()})
            rows.append(row)
        pd.DataFrame(rows).to_csv(filename, index=False)
        print(f"\n  💾 Saved to {filename}")


if __name__ == "__main__":
    main()

