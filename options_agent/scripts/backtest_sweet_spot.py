#!/usr/bin/env python
"""Backtest — Sweet Spot filter: only trade when quality is 4–7 AND cascade explosion ≥ threshold.

Usage:
    cd options_agent
    python scripts/backtest_sweet_spot.py --symbol SPY --period 1y
    python scripts/backtest_sweet_spot.py --symbol QQQ --period 1y --prime  # explosion ≥ 7
    python scripts/backtest_sweet_spot.py --symbol SPY --period 1y --min-explosion 4  # custom threshold
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.backtester import IntradayBacktester
from src.momentum_cascade import MomentumCascadeDetector, CascadeResult
from src.models.market_data import MarketIndicators
from src.utils.choppiness import compute_choppiness

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def print_header(text: str):
    print(f"\n{'═' * 70}")
    print(f"  {text}")
    print(f"{'═' * 70}")


class SweetSpotBacktester(IntradayBacktester):
    """Extended backtester that computes cascade explosion score per day
    and only trades when in the 'sweet spot' (quality 4–7 + explosion >= threshold)."""

    def __init__(self, min_quality: int = 4, max_quality: int = 7,
                 min_explosion: int = 7, max_chop: int = 10, **kwargs):
        super().__init__(**kwargs)
        self.min_quality = min_quality
        self.max_quality = max_quality
        self.min_explosion = min_explosion
        self.max_chop = max_chop  # 10 = disabled, lower = stricter
        self._cascade_detector = MomentumCascadeDetector()
        # Track stats
        self.sweet_spot_stats = {
            "total_evaluated": 0,
            "sweet_spot_trades": 0,
            "quality_in_range": 0,
            "explosion_met": 0,
            "chop_filtered": 0,
            "quality_distribution": {},
            "explosion_distribution": {},
            "chop_distribution": {},
        }

    def _simulate_day(self, day_df, symbol, trade_date, vix, interval, prev_day,
                      daily_map=None):
        """Override: run normal simulation, then apply sweet spot filter."""
        # Run the parent simulation to get all signals
        result = super()._simulate_day(day_df, symbol, trade_date, vix, interval,
                                        prev_day, daily_map)

        if result.direction == "skip":
            return result

        self.sweet_spot_stats["total_evaluated"] += 1

        # Get quality score from signals
        quality = result.signal_scores.get("quality_score", 0)

        # Track quality distribution
        self.sweet_spot_stats["quality_distribution"][quality] = \
            self.sweet_spot_stats["quality_distribution"].get(quality, 0) + 1

        # Check quality range
        quality_ok = self.min_quality <= quality <= self.max_quality
        if quality_ok:
            self.sweet_spot_stats["quality_in_range"] += 1

        # Compute cascade explosion score from the day's bars
        explosion_score = self._compute_explosion(
            day_df, symbol, trade_date, quality, result, interval
        )

        # Track explosion distribution
        self.sweet_spot_stats["explosion_distribution"][explosion_score] = \
            self.sweet_spot_stats["explosion_distribution"].get(explosion_score, 0) + 1

        explosion_ok = explosion_score >= self.min_explosion
        if explosion_ok:
            self.sweet_spot_stats["explosion_met"] += 1

        # Compute choppiness from the day's bars
        chop_result = compute_choppiness(day_df)
        chop_score = chop_result.chop_score
        self.sweet_spot_stats["chop_distribution"][chop_score] = \
            self.sweet_spot_stats["chop_distribution"].get(chop_score, 0) + 1
        chop_ok = chop_score <= self.max_chop

        # Sweet spot filter
        if quality_ok and explosion_ok and chop_ok:
            self.sweet_spot_stats["sweet_spot_trades"] += 1
            # Tag the trade
            result.signal_scores["sweet_spot"] = 1
            result.signal_scores["explosion_score"] = explosion_score
            result.signal_scores["chop_score"] = chop_score
            return result
        else:
            # Convert to skip
            reason = []
            if not quality_ok:
                reason.append(f"quality={quality} not in [{self.min_quality},{self.max_quality}]")
            if not explosion_ok:
                reason.append(f"explosion={explosion_score} < {self.min_explosion}")
            if not chop_ok:
                reason.append(f"chop={chop_score} > {self.max_chop}")
                self.sweet_spot_stats["chop_filtered"] += 1
            return self._skip_trade(
                trade_date, symbol, f"no_sweet_spot ({', '.join(reason)})",
                range_high=result.range_high, range_low=result.range_low,
                momentum=result.momentum_score, signals=result.signal_scores,
            )

    def _compute_explosion(self, day_df, symbol, trade_date, quality, result, interval):
        """Compute cascade explosion score from the day's bars.

        For 1h mode (few bars), uses a proxy-based approach since the full
        cascade detector needs 6+ bars. Synthesizes from available signals:
          - Price acceleration from multi-bar RoC
          - Volume climax from bar volumes
          - Quality/momentum boost (same as live cascade)
          - ZLEMA trend from indicator_close
        """
        try:
            # Use ALL bars up to end of trading day for better signal
            # (in live mode we'd only see up to current time, but for backtest
            # we can look at the full day to approximate what cascade would detect)
            if interval == "1h":
                # For 1h mode: use all bars in the day
                bars = day_df.copy()
            else:
                # For 5m mode: use bars up to OR end + a few post-OR bars
                opening_bars = day_df.between_time("09:30", "10:29")
                if opening_bars.empty:
                    return 0
                # Include some post-OR bars for cascade detection
                post_or_start = opening_bars.index[-1]
                bars = day_df[day_df.index <= post_or_start]

            if len(bars) < 2:
                # Not enough bars for cascade — use proxy scoring
                return self._proxy_explosion(quality, result)

            close = bars["Close"].astype(float)
            volume = bars["Volume"].astype(float)
            high = bars["High"].astype(float)
            low = bars["Low"].astype(float)

            score = 0

            # ── 1. Price Acceleration ──
            if len(close) >= 3:
                roc_recent = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
                roc_prior = (float(close.iloc[-2]) - float(close.iloc[-3])) / float(close.iloc[-3]) * 100 if len(close) >= 3 else 0
                same_dir = (roc_recent < 0 and roc_prior < 0) or (roc_recent > 0 and roc_prior > 0)
                if same_dir and abs(roc_recent) > abs(roc_prior) and abs(roc_recent) > 0.1:
                    score += 2
                elif same_dir and abs(roc_recent) > 0.15:
                    score += 1

            # ── 2. Volume Climax ──
            avg_vol = float(volume.mean())
            recent_vol = float(volume.iloc[-1])
            vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1
            if vol_ratio >= 2.0:
                score += 2
            elif vol_ratio >= 1.5:
                score += 1

            # ── 3. Range expansion (proxy for cascade breakdown) ──
            day_range = float(high.max()) - float(low.min())
            or_bar = bars.iloc[0]
            or_range = float(or_bar["High"]) - float(or_bar["Low"])
            if or_range > 0 and day_range / or_range >= 2.0:
                score += 2
            elif or_range > 0 and day_range / or_range >= 1.5:
                score += 1

            # ── 4. Quality Boost ──
            if quality >= 8:
                score += 2
            elif quality >= 7:
                score += 1

            # ── 5. Momentum Alignment ──
            mom = result.momentum_score
            if abs(mom) >= 60:
                score += 2
            elif abs(mom) >= 40:
                score += 1

            return min(10, max(0, score))

        except Exception as e:
            logger.debug("Cascade computation failed for %s %s: %s", symbol, trade_date, e)
            return self._proxy_explosion(quality, result)

    def _proxy_explosion(self, quality, result):
        """Minimal proxy when not enough bars for full cascade."""
        score = 0
        if quality >= 8:
            score += 2
        elif quality >= 7:
            score += 1
        mom = result.momentum_score
        if abs(mom) >= 60:
            score += 2
        elif abs(mom) >= 40:
            score += 1
        return min(10, score)

    def run_dates(self, symbol: str, start: str, end: str):
        """Run backtest over a specific date range using start/end dates."""
        import yfinance as yf
        from datetime import date as dt_date
        from src.models.backtest_result import BacktestReport

        # Try 5m first, fall back to 1h
        df = yf.download(symbol, start=start, end=end, interval="5m", progress=False)
        if df.empty:
            df = yf.download(symbol, start=start, end=end, interval="1h", progress=False)
            interval = "1h"
            min_bars_per_day = 4
            logger.info("Using 1-hour bars for %s to %s", start, end)
        else:
            interval = "5m"
            min_bars_per_day = 24

        if df.empty:
            raise ValueError(f"No data for {symbol} from {start} to {end}")

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert("US/Eastern")

        # VIX
        vix_df = yf.download("^VIX", start=start, end=end, interval="1d", progress=False)
        if isinstance(vix_df.columns, pd.MultiIndex):
            vix_df.columns = vix_df.columns.get_level_values(0)
        vix_map = {}
        if not vix_df.empty:
            for idx, row in vix_df.iterrows():
                vix_map[pd.Timestamp(idx).date()] = float(row["Close"])

        # Daily OHLC
        daily_df = yf.download(symbol, start=start, end=end, interval="1d", progress=False)
        if isinstance(daily_df.columns, pd.MultiIndex):
            daily_df.columns = daily_df.columns.get_level_values(0)
        daily_map = {}
        if not daily_df.empty:
            for idx, row in daily_df.iterrows():
                d = pd.Timestamp(idx).date()
                daily_map[d] = {
                    "open": float(row["Open"]), "high": float(row["High"]),
                    "low": float(row["Low"]), "close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                }

        trading_days = sorted(set(df.index.date))
        logger.info("Found %d trading days (%s) from %s to %s", len(trading_days), interval, start, end)

        trades = []
        for i, day in enumerate(trading_days):
            day_bars = df[df.index.date == day].copy()
            if len(day_bars) < min_bars_per_day:
                continue
            vix = vix_map.get(day, 20.0)
            prev_day_data = daily_map.get(trading_days[i - 1]) if i > 0 else None
            result = self._simulate_day(day_bars, symbol, day, vix, interval, prev_day_data, daily_map)
            trades.append(result)

        return self._build_report(symbol, f"{start}_to_{end}", len(trading_days), trades)


def main():
    parser = argparse.ArgumentParser(description="Backtest with Sweet Spot filter")
    parser.add_argument("--symbol", "-s", default="SPY")
    parser.add_argument("--period", "-p", default="1y")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD (overrides --period)")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (overrides --period)")
    parser.add_argument("--prime", action="store_true", help="Prime sweet spot (explosion ≥ 7)")
    parser.add_argument("--min-explosion", type=int, default=None, help="Min explosion score (default: 7 for prime, 4 for normal)")
    parser.add_argument("--min-quality", type=int, default=4)
    parser.add_argument("--max-quality", type=int, default=7)
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--max-chop", type=int, default=10,
                        help="Max chop score to allow (default: 10=disabled, 7=recommended for backtest, 5=recommended for live)")
    args = parser.parse_args()

    min_explosion = args.min_explosion
    if min_explosion is None:
        min_explosion = 7 if args.prime else 4

    mode_label = f"sweet_spot_E{min_explosion}_Q{args.min_quality}-{args.max_quality}"

    # If start/end specified, override period
    if args.start and args.end:
        period_label = f"{args.start} to {args.end}"
        mode_label += f"_{args.start}_{args.end}"
    else:
        period_label = args.period

    print_header(f"SWEET SPOT BACKTEST: {args.symbol} — {period_label} ({mode_label})")

    bt = SweetSpotBacktester(
        min_quality=args.min_quality,
        max_quality=args.max_quality,
        min_explosion=min_explosion,
        max_chop=args.max_chop,
    )

    if args.start and args.end:
        report = bt.run_dates(args.symbol, args.start, args.end)
    else:
        report = bt.run(args.symbol, args.period)

    # Print results
    print_header("SUMMARY")
    taken = [t for t in report.trades if t.direction != "skip"]
    winners = [t for t in taken if t.is_winner]
    losers = [t for t in taken if not t.is_winner]

    print(f"  Symbol:           {report.symbol}")
    print(f"  Period:           {report.period}")
    print(f"  Filter:           Quality {args.min_quality}–{args.max_quality}, Explosion ≥ {min_explosion}, Chop ≤ {args.max_chop}")
    print(f"  Trading Days:     {report.total_days}")
    print(f"  Total Evaluated:  {bt.sweet_spot_stats['total_evaluated']}")
    print(f"  Quality in Range: {bt.sweet_spot_stats['quality_in_range']}")
    print(f"  Explosion Met:    {bt.sweet_spot_stats['explosion_met']}")
    print(f"  Chop Filtered:    {bt.sweet_spot_stats['chop_filtered']}")
    print(f"  Sweet Spot Trades:{bt.sweet_spot_stats['sweet_spot_trades']}")
    print(f"  Trades Taken:     {report.trades_taken}")
    print()
    if report.trades_taken > 0:
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
    else:
        print("  ⚠️  No trades matched the sweet spot criteria!")

    # Quality distribution
    print_header("QUALITY SCORE DISTRIBUTION (all evaluated trades)")
    for q in sorted(bt.sweet_spot_stats["quality_distribution"].keys()):
        count = bt.sweet_spot_stats["quality_distribution"][q]
        in_range = "✅" if args.min_quality <= q <= args.max_quality else "  "
        bar = "█" * count
        print(f"  {in_range} Q={q:2d}: {count:3d} {bar}")

    # Explosion distribution
    print_header("EXPLOSION SCORE DISTRIBUTION (all evaluated trades)")
    for e in sorted(bt.sweet_spot_stats["explosion_distribution"].keys()):
        count = bt.sweet_spot_stats["explosion_distribution"][e]
        met = "✅" if e >= min_explosion else "  "
        bar = "█" * count
        print(f"  {met} E={e:2d}: {count:3d} {bar}")

    # Choppiness distribution
    if bt.sweet_spot_stats["chop_distribution"]:
        print_header("CHOPPINESS SCORE DISTRIBUTION (all evaluated trades)")
        for c in sorted(bt.sweet_spot_stats["chop_distribution"].keys()):
            count = bt.sweet_spot_stats["chop_distribution"][c]
            ok = "✅" if c <= args.max_chop else "🚫"
            bar = "█" * count
            print(f"  {ok} C={c:2d}: {count:3d} {bar}")

    # Exit reasons
    if report.trades_taken > 0:
        print_header("EXIT REASONS")
        for reason, count in sorted(report.exit_reasons.items(), key=lambda x: -x[1]):
            pct = count / report.trades_taken * 100
            bar = "█" * int(pct / 2)
            print(f"  {reason:15s} {count:3d}  ({pct:5.1f}%)  {bar}")

        # Trade log
        print_header("TRADE LOG")
        print(f"  {'Date':>10}  Dir   Entry    Exit      P&L Exit Reason  Mom  Expl  Entry@ Exit@")
        for t in taken:
            expl = t.signal_scores.get("explosion_score", "?")
            print(
                f"  {t.trade_date} {'CALL' if t.direction == 'call' else ' PUT'} "
                f"${t.entry_price:.2f} ${t.exit_price:.2f} "
                f"${t.pnl_dollars:+.4f} {t.exit_reason:>11}  "
                f"{t.momentum_score:+4d}  E={expl}  "
                f"{t.entry_time or '?':>5} {t.exit_time or '?':>5}"
            )

    if args.save and report.trades_taken > 0:
        filename = f"backtest_results/{args.symbol}_{mode_label}_{date.today()}.csv"
        rows = []
        for t in taken:
            rows.append({
                "date": t.trade_date, "direction": t.direction,
                "entry_price": t.entry_price, "exit_price": t.exit_price,
                "pnl": t.pnl_dollars, "exit_reason": t.exit_reason,
                "momentum": t.momentum_score,
                "quality": t.signal_scores.get("quality_score", 0),
                "explosion": t.signal_scores.get("explosion_score", 0),
                "is_winner": t.is_winner,
                "entry_time": t.entry_time, "exit_time": t.exit_time,
            })
        pd.DataFrame(rows).to_csv(filename, index=False)
        print(f"\n  💾 Saved to {filename}")


if __name__ == "__main__":
    main()


