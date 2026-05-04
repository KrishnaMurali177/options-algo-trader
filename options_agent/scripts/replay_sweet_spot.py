#!/usr/bin/env python
"""Replay Sweet Spot Agent — simulates the agent on recent historical days.

Unlike the backtester (which evaluates only at 10:30 with 12 bars),
this replays the FULL day checking every 5 minutes — exactly as the
live agent would operate.

This is an EXACT REPLICA of the live sweet-spot agent logic:
  - Uses OpeningRangeAnalyzer (with bars_5m= for replay)
  - Uses RecentMomentumAnalyzer (with bars_5m= for replay)
  - Uses MomentumCascadeDetector (with bars_5m= for replay)
  - Uses compute_quality_score and compute_choppiness
  - Same entry confirmation, target multipliers, and regime guard

Usage:
    cd options_agent
    python scripts/replay_sweet_spot.py --days 365             # Golden defaults (live agent settings)
    python scripts/replay_sweet_spot.py --days 365 --no-gainz-exit   # Baseline (no Gainz)
    python scripts/replay_sweet_spot.py --days 365 --research-mode   # Loose pre-golden defaults
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.opening_range import OpeningRangeAnalyzer
from src.recent_momentum import RecentMomentumAnalyzer
from src.momentum_cascade import MomentumCascadeDetector
from src.models.market_data import MarketIndicators
from src.utils.choppiness import compute_choppiness
from src.utils.quality_scorer import compute_quality_score
from src.utils.gainz import gainz_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _build_indicators_from_bars(bars: pd.DataFrame, symbol: str = "SPY") -> MarketIndicators:
    """Build a MarketIndicators snapshot from historical 5-min bars.

    Mimics what MarketAnalyzer.analyze() produces, but from pre-fetched data
    so we don't hit yfinance during replay.
    """
    close = bars["Close"].astype(float)
    high = bars["High"].astype(float)
    low = bars["Low"].astype(float)
    vol = bars["Volume"].astype(float)
    price = float(close.iloc[-1])

    # RSI
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi_series = 100 - (100 / (1 + rs))
        rsi_val = float(rsi_series.iloc[-1])
        if np.isnan(rsi_val):
            rsi_val = 50.0
    else:
        rsi_val = 50.0

    # SMAs (from intraday bars — same as live agent's 5-min data)
    sma_20 = float(close.iloc[-20:].mean()) if len(close) >= 20 else price
    sma_50 = float(close.iloc[-50:].mean()) if len(close) >= 50 else price
    sma_200 = float(close.iloc[-min(200, len(close)):].mean()) if len(close) >= 20 else price

    # Bollinger Bands
    bb_period = min(20, len(close) - 1) if len(close) > 2 else 2
    bb_mid = float(close.rolling(bb_period).mean().iloc[-1])
    bb_std = float(close.rolling(bb_period).std().iloc[-1]) if bb_period > 1 else 0.0
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # MACD
    if len(close) >= 26:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal
        macd_val = float(macd_line.iloc[-1])
        macd_sig = float(signal.iloc[-1])
        macd_hist = float(hist.iloc[-1])
    else:
        macd_val = macd_sig = macd_hist = 0.0

    # ATR
    if len(close) >= 15:
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
    else:
        atr = 1.0

    # Volume
    current_volume = int(vol.iloc[-1])
    vol_sma_20 = float(vol.rolling(min(20, len(vol))).mean().iloc[-1])

    # ZLEMA
    if len(close) >= 21:
        lag_fast = (8 - 1) // 2
        lag_slow = (21 - 1) // 2
        comp_fast = 2 * close - close.shift(lag_fast)
        comp_slow = 2 * close - close.shift(lag_slow)
        zlema_fast = float(comp_fast.ewm(span=8, adjust=False).mean().iloc[-1])
        zlema_slow = float(comp_slow.ewm(span=21, adjust=False).mean().iloc[-1])
        if zlema_fast > zlema_slow * 1.0002:
            zlema_trend = "bullish"
        elif zlema_fast < zlema_slow * 0.9998:
            zlema_trend = "bearish"
        else:
            zlema_trend = "neutral"
    else:
        zlema_fast = zlema_slow = price
        zlema_trend = "neutral"

    return MarketIndicators(
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        current_price=price,
        timeframe="15min",
        vix=20.0,  # Not available in replay — use neutral default
        rsi_14=rsi_val,
        rsi_5min=rsi_val,
        sma_20=sma_20,
        sma_50=sma_50,
        sma_200=sma_200,
        bb_upper=bb_upper,
        bb_middle=bb_mid,
        bb_lower=bb_lower,
        macd=macd_val,
        macd_signal=macd_sig,
        macd_histogram=macd_hist,
        atr_14=atr,
        volume=current_volume,
        volume_sma_20=vol_sma_20,
        zlema_fast=zlema_fast,
        zlema_slow=zlema_slow,
        zlema_trend=zlema_trend,
    )


def replay_day(day_bars: pd.DataFrame, trade_date: date, max_chop: int = 5,
               min_cascade: int = 4, min_quality: int = 4, max_quality: int = 7,
               breakout_pct: float = 0.25, cooldown_bars: int = 3,
               scan_end: str = "13:59",
               scan_start: str = "11:30",
               target_mult_low: float = 1.0, target_mult_mid: float = 1.25,
               target_mult_high: float = 1.5,
               regime_guard: bool = True,
               symbol: str = "SPY",
               max_trades_per_day: int = 3,
               max_stops_per_day: int = 2,
               gainz_exit: bool = True,
               gainz_body_ratio: float = 0.5,
               gainz_rsi_overbought: float = 65.0,
               gainz_rsi_oversold: float = 35.0,
               cascade_sizing: bool = False,
               simulate_options: bool = True,
               option_delta: float = 0.50,
               option_gamma: float = 0.05,
               premium_atr_pct: float = 0.40) -> list[dict]:
    """Replay one day scanning every 5 min window after 10:35.

    Uses the EXACT same analyzers as the live agent:
      - OpeningRangeAnalyzer (with bars_5m=)
      - RecentMomentumAnalyzer (with bars_5m=)
      - MomentumCascadeDetector (with bars_5m=)
      - compute_quality_score
      - compute_choppiness

    Returns list of trigger dicts with simulated outcomes.
    """
    or_analyzer = OpeningRangeAnalyzer()
    rc_analyzer = RecentMomentumAnalyzer()
    cascade_detector = MomentumCascadeDetector()

    # Need bars from 9:30 onward for OR
    or_bars = day_bars.between_time("09:30", "10:29")
    if len(or_bars) < 6:
        return []

    range_high = float(or_bars["High"].max())
    range_low = float(or_bars["Low"].min())
    range_width = range_high - range_low
    if range_width <= 0:
        return []

    # Post-OR bars (10:30 onward) — scan windows
    post_or = day_bars[day_bars.index > or_bars.index[-1]]
    if len(post_or) < 3:
        return []

    triggers = []
    last_trigger_idx = -999
    stops_today = 0  # Track stop-outs for daily loss limit

    # Scan every bar (5 min) from scan_start to scan_end
    scan_bars = post_or.between_time(scan_start, scan_end)

    # ── Precompute cumulative series once for the whole day (perf optimization) ──
    day_close = day_bars["Close"].astype(float)
    day_high = day_bars["High"].astype(float)
    day_low = day_bars["Low"].astype(float)
    day_vol = day_bars["Volume"].astype(float)

    # Precompute RSI components (rolling gain/loss for the whole day)
    day_delta = day_close.diff()
    day_gain = day_delta.where(day_delta > 0, 0.0).rolling(14).mean()
    day_loss = (-day_delta.where(day_delta < 0, 0.0)).rolling(14).mean()
    day_rs = day_gain / day_loss.replace(0, np.nan)
    day_rsi = 100 - (100 / (1 + day_rs))

    # Precompute MACD for the whole day
    day_ema12 = day_close.ewm(span=12, adjust=False).mean()
    day_ema26 = day_close.ewm(span=26, adjust=False).mean()
    day_macd_line = day_ema12 - day_ema26
    day_macd_signal = day_macd_line.ewm(span=9, adjust=False).mean()
    day_macd_hist = day_macd_line - day_macd_signal

    # Precompute ATR for the whole day
    day_tr = pd.concat([day_high - day_low, (day_high - day_close.shift()).abs(),
                        (day_low - day_close.shift()).abs()], axis=1).max(axis=1)
    day_atr = day_tr.rolling(14).mean()

    # Precompute Bollinger Bands
    day_bb_mid = day_close.rolling(20).mean()
    day_bb_std = day_close.rolling(20).std()

    # Precompute ZLEMA
    lag_fast = (8 - 1) // 2
    lag_slow = (21 - 1) // 2
    comp_fast = 2 * day_close - day_close.shift(lag_fast)
    comp_slow = 2 * day_close - day_close.shift(lag_slow)
    day_zlema_fast = comp_fast.ewm(span=8, adjust=False).mean()
    day_zlema_slow = comp_slow.ewm(span=21, adjust=False).mean()

    # Precompute VWAP
    day_typical = (day_high + day_low + day_close) / 3
    day_cumvol = day_vol.cumsum()
    day_cum_tp_vol = (day_typical * day_vol).cumsum()

    # Precompute volume SMA
    day_vol_sma = day_vol.rolling(min(20, len(day_vol))).mean()

    for i, (ts, bar) in enumerate(scan_bars.iterrows()):
        # ── Daily limits ──
        if max_trades_per_day > 0 and len(triggers) >= max_trades_per_day:
            break
        if max_stops_per_day > 0 and stops_today >= max_stops_per_day:
            break

        # Cooldown: skip if triggered within last N bars
        if i - last_trigger_idx < cooldown_bars:
            continue

        # Use all bars up to current time for indicators
        bars_to_now = day_bars[day_bars.index <= ts]
        n = len(bars_to_now)
        price = float(day_close.iloc[:n].iloc[-1])

        # ── Build MarketIndicators from precomputed series (FAST) ──
        rsi_val = float(day_rsi.iloc[:n].iloc[-1]) if n >= 15 and not np.isnan(day_rsi.iloc[:n].iloc[-1]) else 50.0
        sma_20 = float(day_close.iloc[:n].iloc[-20:].mean()) if n >= 20 else price
        sma_50 = float(day_close.iloc[:n].iloc[-50:].mean()) if n >= 50 else price
        sma_200 = float(day_close.iloc[:n].iloc[-min(200, n):].mean()) if n >= 20 else price

        bb_mid_val = float(day_bb_mid.iloc[:n].iloc[-1]) if n >= 20 and not np.isnan(day_bb_mid.iloc[:n].iloc[-1]) else price
        bb_std_val = float(day_bb_std.iloc[:n].iloc[-1]) if n >= 20 and not np.isnan(day_bb_std.iloc[:n].iloc[-1]) else 0.0
        bb_upper = bb_mid_val + 2 * bb_std_val
        bb_lower = bb_mid_val - 2 * bb_std_val

        if n >= 26:
            macd_val = float(day_macd_line.iloc[:n].iloc[-1])
            macd_sig = float(day_macd_signal.iloc[:n].iloc[-1])
            macd_hist_val = float(day_macd_hist.iloc[:n].iloc[-1])
        else:
            macd_val = macd_sig = macd_hist_val = 0.0

        atr_val = float(day_atr.iloc[:n].iloc[-1]) if n >= 15 and not np.isnan(day_atr.iloc[:n].iloc[-1]) else 1.0

        current_volume = int(day_vol.iloc[:n].iloc[-1])
        vol_sma_val = float(day_vol_sma.iloc[:n].iloc[-1]) if not np.isnan(day_vol_sma.iloc[:n].iloc[-1]) else float(current_volume)

        if n >= 21:
            zf = float(day_zlema_fast.iloc[:n].iloc[-1])
            zs = float(day_zlema_slow.iloc[:n].iloc[-1])
            if zf > zs * 1.0002:
                zlema_trend = "bullish"
            elif zf < zs * 0.9998:
                zlema_trend = "bearish"
            else:
                zlema_trend = "neutral"
        else:
            zf = zs = price
            zlema_trend = "neutral"

        indicators = MarketIndicators(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            current_price=price,
            timeframe="15min",
            vix=20.0,
            rsi_14=rsi_val,
            rsi_5min=rsi_val,
            sma_20=sma_20,
            sma_50=sma_50,
            sma_200=sma_200,
            bb_upper=bb_upper,
            bb_middle=bb_mid_val,
            bb_lower=bb_lower,
            macd=macd_val,
            macd_signal=macd_sig,
            macd_histogram=macd_hist_val,
            atr_14=atr_val,
            volume=current_volume,
            volume_sma_20=vol_sma_val,
            zlema_fast=zf,
            zlema_slow=zs,
            zlema_trend=zlema_trend,
        )

        # ── Opening Range Analysis (EXACT same as live agent) ──
        or_result = or_analyzer.analyze(indicators, bars_5m=bars_to_now)
        or_direction = or_result.breakout_direction.value  # "bullish", "bearish", "neutral"
        or_momentum = or_result.momentum_score

        # ── Recent Momentum Analysis (EXACT same as live agent) ──
        rc_result = rc_analyzer.analyze(indicators, bars_5m=bars_to_now)
        recent_dir = rc_result.direction
        recent_momentum = rc_result.momentum_score

        # ── Direction decision (same as live agent) ──
        if or_momentum >= 25:
            direction = "buy_call"
        elif or_momentum <= -25:
            direction = "buy_put"
        else:
            continue

        # ── Regime guard (same as live agent dashboard guardrails) ──
        if regime_guard:
            rsi_val = indicators.rsi_14
            sma_20 = indicators.sma_20
            sma_50 = indicators.sma_50
            bullish_regime = sma_20 > sma_50
            bearish_regime = sma_20 < sma_50

            if direction == "buy_put" and bullish_regime and rsi_val <= 70:
                continue
            if direction == "buy_call" and bearish_regime and rsi_val >= 30:
                continue

        # ── Quality Score (EXACT same as live agent) ──
        quality_result = compute_quality_score(
            direction=direction,
            current_price=indicators.current_price,
            sma_20=indicators.sma_20,
            sma_50=indicators.sma_50,
            vix=indicators.vix,
            volume=1.0,
            volume_sma_20=1.0,
            or_direction=or_direction,
            or_momentum=or_momentum,
            or_confirmed=abs(or_momentum) >= 40,
            recent_dir=recent_dir,
            recent_momentum=recent_momentum,
        )
        quality = quality_result.score

        if not (min_quality <= quality <= max_quality):
            continue

        # ── Momentum Cascade (EXACT same as live agent) ──
        cascade = cascade_detector.analyze(
            indicators,
            quality_score=quality,
            or_momentum=or_momentum,
            recent_momentum=recent_momentum,
            bars_5m=bars_to_now,
        )
        explosion = cascade.explosion_score

        if explosion < min_cascade:
            continue

        # ── Choppiness ──
        chop = compute_choppiness(bars_to_now)
        if chop.chop_score > max_chop:
            continue

        # ── TRIGGER! ──
        last_trigger_idx = i

        # ── Entry confirmation: price must be in upper/lower N% of range or beyond ──
        breakout_threshold = range_width * breakout_pct
        if direction == "buy_call" and price < (range_high - breakout_threshold):
            continue
        if direction == "buy_put" and price > (range_low + breakout_threshold):
            continue

        # Target mult (same as live agent)
        if explosion >= 8:
            target_mult = target_mult_high
        elif explosion >= 6:
            target_mult = target_mult_mid
        else:
            target_mult = target_mult_low

        if direction == "buy_call":
            entry = price
            stop = (range_high + range_low) / 2
            risk = entry - stop
            if risk <= 0:
                continue
            target = entry + risk * target_mult
        else:
            entry = price
            stop = (range_high + range_low) / 2
            risk = stop - entry
            if risk <= 0:
                continue
            target = entry - risk * target_mult

        # ── Walk forward to determine outcome ──
        future_bars = day_bars[day_bars.index > ts]
        outcome = "eod"
        exit_price = float(future_bars["Close"].iloc[-1]) if len(future_bars) > 0 else price

        for _, fb in future_bars.iterrows():
            fh, fl, fc = float(fb["High"]), float(fb["Low"]), float(fb["Close"])
            if direction == "buy_call":
                if fl <= stop:
                    outcome = "stop"; exit_price = stop; break
                if fh >= target:
                    outcome = "target"; exit_price = target; break
            else:
                if fh >= stop:
                    outcome = "stop"; exit_price = stop; break
                if fl <= target:
                    outcome = "target"; exit_price = target; break

            # GainzAlgoV2 reversal exit (opposing signal closes position at bar close)
            if gainz_exit:
                fo = float(fb["Open"])
                try:
                    bar_rsi = float(day_rsi.loc[fb.name])
                except (KeyError, TypeError):
                    bar_rsi = float("nan")
                gz = gainz_signal(fo, fh, fl, fc, bar_rsi,
                                  body_ratio_min=gainz_body_ratio,
                                  rsi_overbought=gainz_rsi_overbought,
                                  rsi_oversold=gainz_rsi_oversold)
                if direction == "buy_call" and gz == "sell":
                    outcome = "gainz_exit"; exit_price = fc; break
                if direction == "buy_put" and gz == "buy":
                    outcome = "gainz_exit"; exit_price = fc; break

            # Time stop 15:30
            if fb.name.strftime("%H:%M") >= "15:30":
                outcome = "time_stop"; exit_price = fc; break

        pnl = (exit_price - entry) if direction == "buy_call" else (entry - exit_price)

        # ── 0DTE Option P&L approximation ──
        underlying_move = pnl  # signed move in underlying
        if simulate_options:
            # Estimate premium: ATR * premium_atr_pct (rough ATM 0DTE premium)
            est_premium = atr_val * premium_atr_pct
            if est_premium < 0.10:
                est_premium = 0.10  # floor

            # Delta-gamma approximation: Δpremium ≈ δ × Δprice + 0.5 × γ × Δprice²
            abs_move = abs(underlying_move)
            delta_pnl = option_delta * abs_move
            gamma_pnl = 0.5 * option_gamma * abs_move ** 2

            # Theta decay for 0DTE: estimate remaining fraction of day
            # Entry bar timestamp gives us minutes since 9:30
            entry_minutes = (ts.hour * 60 + ts.minute) - (9 * 60 + 30)
            total_minutes = 390  # 9:30 to 16:00
            remaining_frac = max(0, (total_minutes - entry_minutes) / total_minutes)
            # 0DTE theta is aggressive — assume full daily theta ≈ 60-80% of premium
            # Decay proportional to sqrt of remaining time (accelerates near close)
            theta_decay = est_premium * 0.70 * (1.0 - remaining_frac ** 0.5)

            if underlying_move > 0:  # winner direction
                option_pnl_per_contract = delta_pnl + gamma_pnl - theta_decay
            else:  # loser direction
                option_pnl_per_contract = -(delta_pnl + gamma_pnl) - theta_decay

            # Cap loss at premium paid (defined risk)
            option_pnl_per_contract = max(option_pnl_per_contract, -est_premium)
            # P&L per contract (x100 multiplier)
            option_pnl_total = option_pnl_per_contract * 100
            pnl = option_pnl_per_contract  # per-contract P&L for reporting
        else:
            option_pnl_total = None
            est_premium = None

        if outcome == "stop":
            stops_today += 1

        # Cascade-tiered contracts: 1 for E 4-5, 2 for E 6-7, 3 for E 8+
        if cascade_sizing:
            if explosion >= 8:
                size = 3.0
            elif explosion >= 6:
                size = 2.0
            else:
                size = 1.0
        else:
            size = 1.0

        # Scale option P&L by number of contracts
        if simulate_options and option_pnl_total is not None:
            option_pnl_total_sized = option_pnl_total * size
        else:
            option_pnl_total_sized = option_pnl_total

        triggers.append({
            "date": str(trade_date),
            "time": ts.strftime("%H:%M"),
            "direction": direction,
            "quality": quality,
            "explosion": explosion,
            "chop": chop.chop_score,
            "momentum": or_momentum,
            "recent_momentum": recent_momentum,
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "target_mult": target_mult,
            "exit_price": round(exit_price, 2),
            "outcome": outcome,
            "pnl": round(pnl, 4),
            "underlying_move": round(underlying_move, 4),
            "option_pnl_100x": round(option_pnl_total, 2) if option_pnl_total is not None else None,
            "option_pnl_sized": round(option_pnl_total_sized, 2) if option_pnl_total_sized is not None else None,
            "est_premium": round(est_premium, 2) if est_premium is not None else None,
            "size": size,
            "sized_pnl": round(pnl * size, 4),
            "is_winner": pnl > 0,
            "mode": "0dte_option" if simulate_options else "shares",
        })

    return triggers


def main():
    parser = argparse.ArgumentParser(description="Replay Sweet Spot Agent on historical data")
    # Defaults match GOLDEN parameters (see README) — produces validated 1-yr SPY
    # results: 154 trades, 63.6% WR, PF 1.81, +$62.41, Sharpe 1.98.
    # Override individual flags to explore. Use --research-mode to revert to
    # loose pre-golden defaults (chop 10, no caps, 10:35 scan, no Gainz).
    parser.add_argument("--symbol", "-s", default="SPY")
    parser.add_argument("--days", type=int, default=30, help="Number of recent trading days")
    parser.add_argument("--max-chop", type=int, default=5, help="Max choppiness (golden: 5)")
    parser.add_argument("--multi", action="store_true", help="Allow multiple triggers per day")
    parser.add_argument("--min-quality", type=int, default=4, help="Minimum quality score (golden: 4)")
    parser.add_argument("--max-quality", type=int, default=7, help="Maximum quality score (golden: 7)")
    parser.add_argument("--min-cascade", type=int, default=4, help="Minimum cascade proxy")
    parser.add_argument("--breakout-pct", type=float, default=0.25, help="Breakout percentage of range")
    parser.add_argument("--cooldown-bars", type=int, default=3, help="Cooldown period in bars")
    parser.add_argument("--scan-end", type=str, default="13:59", help="End time for scanning (HH:MM, golden: 13:59)")
    parser.add_argument("--scan-start", type=str, default="11:00", help="Start time for scanning (HH:MM, golden: 11:00)")
    parser.add_argument("--target-mult-low", type=float, default=1.0, help="Target multiple for low explosion")
    parser.add_argument("--target-mult-mid", type=float, default=1.25, help="Target multiple for mid explosion")
    parser.add_argument("--target-mult-high", type=float, default=1.5, help="Target multiple for high explosion")
    parser.add_argument("--no-regime-guard", action="store_true", help="Disable regime guardrails")
    parser.add_argument("--max-trades-per-day", type=int, default=3, help="Max trades per day (0=unlimited, golden: 3)")
    parser.add_argument("--max-stops-per-day", type=int, default=2, help="Stop trading after N stop-outs (0=unlimited, golden: 2)")
    parser.add_argument("--no-gainz-exit", action="store_true",
                        help="Disable GainzAlgoV2 reversal early-exit (golden: enabled)")
    parser.add_argument("--gainz-body-ratio", type=float, default=0.5, help="Min candle body/range ratio for Gainz signal (golden: 0.5)")
    parser.add_argument("--gainz-rsi-overbought", type=float, default=60.0, help="RSI threshold for Gainz SELL signal (golden: 60)")
    parser.add_argument("--gainz-rsi-oversold", type=float, default=40.0, help="RSI threshold for Gainz BUY signal (golden: 40)")
    parser.add_argument("--no-cascade-sizing", action="store_true",
                        help="Disable cascade contract sizing (default: ON — 1ct E4-5, 2ct E6-7, 3ct E8+)")
    parser.add_argument("--research-mode", action="store_true",
                        help="Loose pre-golden defaults for exploration (chop 10, max-quality 8, no caps, 10:35 scan, no Gainz)")
    parser.add_argument("--shares", action="store_true",
                        help="Simulate share P&L instead of 0DTE options (default: options)")
    parser.add_argument("--option-delta", type=float, default=0.50,
                        help="Assumed delta for 0DTE ATM option (default: 0.50)")
    parser.add_argument("--option-gamma", type=float, default=0.05,
                        help="Assumed gamma for 0DTE ATM option (default: 0.05)")
    parser.add_argument("--premium-atr-pct", type=float, default=0.40,
                        help="Estimated option premium as fraction of ATR (default: 0.40)")
    args = parser.parse_args()

    if args.research_mode:
        args.max_chop = 10
        args.max_quality = 8
        args.scan_start = "10:35"
        args.max_trades_per_day = 0
        args.max_stops_per_day = 0
        args.no_gainz_exit = True
        logger.info("research-mode: loose defaults applied (chop 10, max-Q 8, scan from 10:35, no caps, no Gainz)")

    # Fetch data via Alpaca
    try:
        from src.utils.alpaca_data import fetch_bars
        logger.info("Fetching %d days of 5-min data from Alpaca for %s...", args.days, args.symbol)
        df = fetch_bars(args.symbol, days_back=args.days, interval="5min")
    except Exception as e:
        logger.warning("Alpaca failed (%s), trying yfinance...", e)
        import yfinance as yf
        period = f"{args.days}d" if args.days <= 60 else "60d"
        df = yf.download(args.symbol, period=period, interval="5m", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert("US/Eastern")

    if df.empty:
        print("No data available")
        return

    trading_days = sorted(set(df.index.date))
    logger.info("Replaying %d trading days...", len(trading_days))

    all_triggers = []
    for day in trading_days:
        day_bars = df[df.index.date == day]
        if len(day_bars) < 24:
            continue
        triggers = replay_day(day_bars, day, max_chop=args.max_chop,
                              min_quality=args.min_quality, max_quality=args.max_quality,
                              min_cascade=args.min_cascade, breakout_pct=args.breakout_pct,
                              cooldown_bars=args.cooldown_bars, scan_end=args.scan_end,
                              scan_start=args.scan_start,
                              target_mult_low=args.target_mult_low, target_mult_mid=args.target_mult_mid,
                              target_mult_high=args.target_mult_high,
                              regime_guard=not args.no_regime_guard,
                              symbol=args.symbol,
                              max_trades_per_day=args.max_trades_per_day,
                              max_stops_per_day=args.max_stops_per_day,
                              gainz_exit=not args.no_gainz_exit,
                              gainz_body_ratio=args.gainz_body_ratio,
                              gainz_rsi_overbought=args.gainz_rsi_overbought,
                              gainz_rsi_oversold=args.gainz_rsi_oversold,
                              cascade_sizing=not args.no_cascade_sizing,
                              simulate_options=not args.shares,
                              option_delta=args.option_delta,
                              option_gamma=args.option_gamma,
                              premium_atr_pct=args.premium_atr_pct)
        all_triggers.extend(triggers)

    # ── Results ──
    print(f"\n{'═' * 70}")
    print(f"  SWEET SPOT REPLAY: {args.symbol} — {len(trading_days)} days")
    mode_label = "SHARES" if args.shares else f"0DTE OPTIONS (Δ={args.option_delta}, γ={args.option_gamma})"
    print(f"  Mode: {mode_label}")
    print(f"  Filter: Quality {args.min_quality}-{args.max_quality}, Cascade ≥ {args.min_cascade}, Chop ≤ {args.max_chop}, Regime Guard: {'ON' if not args.no_regime_guard else 'OFF'}")
    print(f"  Analyzers: OpeningRange + RecentMomentum + MomentumCascade (exact replica)")
    print(f"{'═' * 70}")

    if not all_triggers:
        print("\n  No sweet spot triggers found in this period.")
        return

    wins = [t for t in all_triggers if t["is_winner"]]
    losses = [t for t in all_triggers if not t["is_winner"]]
    pnl_field = "sized_pnl" if not args.no_cascade_sizing else "pnl"
    total_pnl = sum(t[pnl_field] for t in all_triggers)
    avg_pnl = total_pnl / len(all_triggers)
    win_rate = len(wins) / len(all_triggers) * 100
    avg_win = np.mean([t[pnl_field] for t in wins]) if wins else 0
    avg_loss = np.mean([t[pnl_field] for t in losses]) if losses else 0
    gross_profit = sum(t[pnl_field] for t in wins)
    gross_loss = abs(sum(t[pnl_field] for t in losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    sizing_label = " (cascade-sized)" if not args.no_cascade_sizing else ""
    print(f"\n  Triggers:        {len(all_triggers)} ({len(all_triggers)/len(trading_days):.1f}/day)")
    print(f"  Win Rate:        {win_rate:.1f}% ({len(wins)}/{len(all_triggers)})")
    print(f"  Profit Factor:   {pf:.2f}")
    print(f"  Total P&L{sizing_label}: ${total_pnl:+.2f}")
    print(f"  Avg P&L/Trade:   ${avg_pnl:+.4f}")
    print(f"  Avg Winner:      ${avg_win:+.4f}")
    print(f"  Avg Loser:       ${avg_loss:+.4f}")
    print(f"  R:R Ratio:       {abs(avg_win/avg_loss):.2f}" if avg_loss != 0 else "")

    # ── Risk-adjusted metrics: Sharpe, Sortino, Max Drawdown ──
    # Aggregate P&L by date (zero-fill no-trade days for honest daily Sharpe)
    daily_pnl_map: dict[str, float] = {}
    for t in all_triggers:
        daily_pnl_map[t["date"]] = daily_pnl_map.get(t["date"], 0.0) + t[pnl_field]
    daily_series = [daily_pnl_map.get(str(d), 0.0) for d in trading_days]

    n_days = len(daily_series)
    if n_days > 1:
        mean_d = sum(daily_series) / n_days
        var_d = sum((x - mean_d) ** 2 for x in daily_series) / (n_days - 1)
        std_d = var_d ** 0.5
        sharpe = (mean_d / std_d) * (252 ** 0.5) if std_d > 0 else float("inf")
        # Sortino: target downside deviation (target=0, divisor=N total)
        # TDD = sqrt(mean(min(0, r)^2 over all N days))
        downside_sq_sum = sum(x ** 2 for x in daily_series if x < 0)
        tdd = (downside_sq_sum / n_days) ** 0.5
        sortino = (mean_d / tdd) * (252 ** 0.5) if tdd > 0 else float("inf")
    else:
        sharpe = sortino = 0.0

    # Equity curve + max drawdown
    equity = 0.0
    peak = 0.0
    mdd = 0.0
    days_underwater = 0
    max_underwater = 0
    peak_idx = 0
    mdd_start_idx = 0
    mdd_end_idx = 0
    for i, pnl in enumerate(daily_series):
        equity += pnl
        if equity > peak:
            peak = equity
            peak_idx = i
            days_underwater = 0
        else:
            days_underwater += 1
            dd = peak - equity
            if dd > mdd:
                mdd = dd
                mdd_start_idx = peak_idx
                mdd_end_idx = i
            if days_underwater > max_underwater:
                max_underwater = days_underwater
    mdd_pct = (mdd / peak * 100) if peak > 0 else 0.0
    calmar = (total_pnl / mdd) if mdd > 0 else float("inf")

    print(f"\n  Risk-Adjusted Metrics (annualized via 252 trading days):")
    print(f"    Sharpe Ratio:         {sharpe:.2f}   (>1.0 good, >2.0 excellent)")
    print(f"    Sortino Ratio:        {sortino:.2f}   (downside-only volatility)")
    print(f"    Max Drawdown:         ${mdd:.2f} ({mdd_pct:.1f}% of peak)")
    print(f"    Calmar Ratio:         {calmar:.2f}   (annual return / max DD)")
    print(f"    Longest Underwater:   {max_underwater} days")
    if mdd > 0:
        print(f"    DD Window:            {trading_days[mdd_start_idx]} → {trading_days[mdd_end_idx]}")

    # ── Cascade tier breakdown (always shown — informs whether sizing helps) ──
    print(f"\n  Cascade Tier Breakdown:")
    print(f"    {'Tier':<14} {'N':>4} {'WR%':>6} {'PF':>5} {'TotPnL':>9} {'AvgPnL':>8}")
    tiers = [
        ("E 4-5 (1ct)",  lambda e: e <= 5),
        ("E 6-7 (2ct)",  lambda e: 6 <= e <= 7),
        ("E 8+  (3ct)",  lambda e: e >= 8),
    ]
    for label, pred in tiers:
        tier = [t for t in all_triggers if pred(t["explosion"])]
        if not tier:
            print(f"    {label:<14} {0:>4} {'—':>6} {'—':>5} {'—':>9} {'—':>8}")
            continue
        tw = [t for t in tier if t["is_winner"]]
        tl = [t for t in tier if not t["is_winner"]]
        tgp = sum(t["pnl"] for t in tw)
        tgl = abs(sum(t["pnl"] for t in tl))
        tpf = tgp / tgl if tgl > 0 else float("inf")
        tpnl = sum(t["pnl"] for t in tier)
        print(f"    {label:<14} {len(tier):>4} {len(tw)/len(tier)*100:>5.1f}% "
              f"{tpf:>5.2f} ${tpnl:>+7.2f} ${tpnl/len(tier):>+6.3f}")

    # Outcomes
    outcomes = {}
    for t in all_triggers:
        outcomes[t["outcome"]] = outcomes.get(t["outcome"], 0) + 1
    print(f"\n  Exit Breakdown:")
    for o, c in sorted(outcomes.items(), key=lambda x: -x[1]):
        print(f"    {o:<12} {c:>3} ({c/len(all_triggers)*100:.0f}%)")

    # Trade log
    if not args.shares:
        print(f"\n  {'Date':<12} {'Time':<6} {'Dir':<5} {'Q':>2} {'E':>2} {'C':>2} {'Ct':>3} {'Entry':>8} {'Exit':>8} {'Δ$':>7} {'Opt$/ct':>8} {'Tot$':>8} {'Outcome':<8}")
        print(f"  {'─'*12} {'─'*6} {'─'*5} {'─'*2} {'─'*2} {'─'*2} {'─'*3} {'─'*8} {'─'*8} {'─'*7} {'─'*8} {'─'*8} {'─'*8}")
        for t in all_triggers:
            d = "CALL" if "call" in t["direction"] else "PUT"
            w = "✅" if t["is_winner"] else "❌"
            opt_pnl = t.get("option_pnl_100x")
            opt_sized = t.get("option_pnl_sized")
            opt_str = f"${opt_pnl:>+7.0f}" if opt_pnl is not None else "    N/A"
            tot_str = f"${opt_sized:>+7.0f}" if opt_sized is not None else "    N/A"
            ct = int(t["size"])
            print(f"  {t['date']:<12} {t['time']:<6} {d:<5} {t['quality']:>2} {t['explosion']:>2} {t['chop']:>2} {ct:>3} ${t['entry']:>7.2f} ${t['exit_price']:>7.2f} ${t['underlying_move']:>+5.2f} {opt_str} {tot_str} {t['outcome']:<8} {w}")
        # Options summary
        total_opt_pnl = sum(t.get("option_pnl_100x", 0) or 0 for t in all_triggers)
        total_opt_sized = sum(t.get("option_pnl_sized", 0) or 0 for t in all_triggers)
        print(f"\n  Total Option P&L (per contract, ×100 multiplier): ${total_opt_pnl:+,.0f}")
        if not args.no_cascade_sizing:
            print(f"  Total Option P&L (cascade-sized, ×100 multiplier): ${total_opt_sized:+,.0f}")
    else:
        print(f"\n  {'Date':<12} {'Time':<6} {'Dir':<5} {'Q':>2} {'E':>2} {'C':>2} {'Mult':>5} {'Entry':>8} {'Exit':>8} {'P&L':>8} {'Outcome':<8}")
        print(f"  {'─'*12} {'─'*6} {'─'*5} {'─'*2} {'─'*2} {'─'*2} {'─'*5} {'─'*8} {'─'*8} {'─'*8} {'─'*8}")
        for t in all_triggers:
            d = "CALL" if "call" in t["direction"] else "PUT"
            w = "✅" if t["is_winner"] else "❌"
            print(f"  {t['date']:<12} {t['time']:<6} {d:<5} {t['quality']:>2} {t['explosion']:>2} {t['chop']:>2} {t['target_mult']:>4.2f}x ${t['entry']:>7.2f} ${t['exit_price']:>7.2f} ${t['pnl']:>+7.2f} {t['outcome']:<8} {w}")


if __name__ == "__main__":
    main()

