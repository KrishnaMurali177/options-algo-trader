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
    python scripts/replay_sweet_spot.py --days 365             # Golden defaults (incl. decay-aware targets + real options)
    python scripts/replay_sweet_spot.py --days 365 --no-gainz-exit   # Baseline (no Gainz)
    python scripts/replay_sweet_spot.py --days 365 --no-decay-aware-targets  # Disable decay targets
    python scripts/replay_sweet_spot.py --days 365 --no-real-options  # Use synth pricing instead of real Alpaca
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
               min_cascade: int = 4, min_cascade_call: int | None = None,
               vix_stop_slope: float = 0.0, vix_stop_anchor: float = 15.0,
               min_quality: int = 4, max_quality: int = 7,
               breakout_pct: float = 0.25, cooldown_bars: int = 3,
               scan_end: str = "13:59",
               scan_start: str = "11:30",
               target_mult_low: float = 1.0, target_mult_mid: float = 1.5,
               target_mult_high: float = 1.5,
               regime_guard: bool = True,
               or_threshold: int = 25,
               symbol: str = "SPY",
               max_trades_per_day: int = 3,
               max_stops_per_day: int = 1,
               max_consecutive_losses: int = 2,
               daily_loss_limit: float = 0.0,
               confirmation_bar: bool = False,
               stagnation_bars: int = 12,
               stagnation_threshold: float = 0.3,
               gainz_exit: bool = True,
               gainz_body_ratio: float = 0.7,
               gainz_rsi_overbought: float = 70.0,
               gainz_rsi_oversold: float = 30.0,
               gainz_min_profit_r: float = 0.3,
               cascade_sizing: bool = False,
               cascade_size_low: int = 3,
               cascade_size_mid: int = 3,
               cascade_size_high: int = 3,
               simulate_options: bool = True,
               option_delta: float = 0.50,
               option_gamma: float = 0.05,
               premium_atr_pct: float = 0.40,
               slippage: float = 0.0,
               vix: float = 20.0,
               prior_bars: pd.DataFrame | None = None,
               dynamic_or: bool = False,
               real_options: bool = False,
               decay_aware_targets: bool = False,
                decay_target_floor: float = 0.4,
                decay_halflife_bars: int = 8,
                active_range: bool = False,
                active_range_bars: int = 6,
                active_range_blend: float = 1.0,
                 pb_ema: bool = False,
                 pb_ema_fast: int = 9,
                 pb_ema_slow: int = 21,
                 tiered_stagnation: bool = False,
                 tiered_stag_early_bar: int = 8,
                 tiered_stag_pnl_lo: float = -0.1,
                 tiered_stag_pnl_hi: float = 0.2,
                 stag_cooldown_bars: int = 6,
                 r_anchored_risk: bool = False,
                 r_anchor_pct: float = 0.25,
                 extension_veto: bool = False,
                 extension_max_atr: float = 2.0) -> list[dict]:
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

    # ── Dynamic Opening Range: use 30-min quick OR if breakout is decisive by 10:00 ──
    # This allows scanning from 10:00 on strong-trend mornings instead of waiting until 10:30+
    effective_scan_start = scan_start
    if dynamic_or:
        quick_or_bars = day_bars.between_time("09:30", "09:59")
        if len(quick_or_bars) >= 6:
            quick_high = float(quick_or_bars["High"].max())
            quick_low = float(quick_or_bars["Low"].min())
            quick_width = quick_high - quick_low
            # Check if the 10:00 bar already broke out of the 30-min range decisively
            bars_at_10 = day_bars.between_time("10:00", "10:04")
            if len(bars_at_10) > 0 and quick_width > 0:
                price_at_10 = float(bars_at_10["Close"].iloc[-1])
                # Decisive = price moved >50% of quick range beyond the range boundary
                if price_at_10 > quick_high + quick_width * 0.5 or \
                   price_at_10 < quick_low - quick_width * 0.5:
                    # Use 30-min OR and allow earlier scanning
                    range_high = quick_high
                    range_low = quick_low
                    range_width = quick_width
                    effective_scan_start = "10:00"

    # Post-OR bars — scan windows
    # When dynamic_or fired with the 30-min OR, scan from 10:00; otherwise 10:30+
    if dynamic_or and effective_scan_start == "10:00":
        post_or = day_bars[day_bars.index >= quick_or_bars.index[-1]]
    else:
        post_or = day_bars[day_bars.index > or_bars.index[-1]]
    if len(post_or) < 3:
        return []

    triggers = []
    last_trigger_idx = -999
    last_was_stagnation = False  # Track for extended stag cooldown
    stops_today = 0  # Track stop-outs for daily loss limit
    consecutive_losses = 0  # Track streak for streak breaker
    daily_pnl_cumulative = 0.0  # Track cumulative daily P&L

    # Scan every bar (5 min) from scan_start to scan_end
    scan_bars = post_or.between_time(effective_scan_start if dynamic_or else scan_start, scan_end)

    # ── Prepend prior days' bars for multi-day SMA context ──
    if prior_bars is not None and len(prior_bars) > 0:
        extended_bars = pd.concat([prior_bars, day_bars])
    else:
        extended_bars = day_bars
    # Number of prior context bars (to offset indices into extended series)
    n_prior = len(extended_bars) - len(day_bars)

    # ── Precompute cumulative series once for the whole day (perf optimization) ──
    # Use extended_bars for SMA/MACD/ATR so they have multi-day context
    ext_close = extended_bars["Close"].astype(float)
    ext_high = extended_bars["High"].astype(float)
    ext_low = extended_bars["Low"].astype(float)
    ext_vol = extended_bars["Volume"].astype(float)

    # day_* still references today-only for RSI (intraday) and scan indexing
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

    # Precompute PB EMA bands (two EMAs on close — between them = chop zone)
    day_pb_ema_fast = day_close.ewm(span=pb_ema_fast, adjust=False).mean() if pb_ema else None
    day_pb_ema_slow = day_close.ewm(span=pb_ema_slow, adjust=False).mean() if pb_ema else None

    for i, (ts, bar) in enumerate(scan_bars.iterrows()):
        # ── Daily limits ──
        if max_trades_per_day > 0 and len(triggers) >= max_trades_per_day:
            break
        if max_stops_per_day > 0 and stops_today >= max_stops_per_day:
            break
        if max_consecutive_losses > 0 and consecutive_losses >= max_consecutive_losses:
            break
        if daily_loss_limit > 0 and daily_pnl_cumulative <= -daily_loss_limit:
            break

        # Cooldown: skip if triggered within last N bars
        effective_cooldown = stag_cooldown_bars if (tiered_stagnation and last_was_stagnation) else cooldown_bars
        if i - last_trigger_idx < effective_cooldown:
            continue

        # Use all bars up to current time for indicators
        bars_to_now = day_bars[day_bars.index <= ts]
        n = len(bars_to_now)
        price = float(day_close.iloc[:n].iloc[-1])

        # ── Build MarketIndicators from precomputed series (FAST) ──
        rsi_val = float(day_rsi.iloc[:n].iloc[-1]) if n >= 15 and not np.isnan(day_rsi.iloc[:n].iloc[-1]) else 50.0
        sma_20 = float(day_close.iloc[:n].iloc[-20:].mean()) if n >= 20 else price
        # Use extended (multi-day) close series for SMA-50 and SMA-200
        ext_n = n_prior + n  # total bars available in extended series
        sma_50 = float(ext_close.iloc[:ext_n].iloc[-50:].mean()) if ext_n >= 50 else float(ext_close.iloc[:ext_n].mean())
        sma_200 = float(ext_close.iloc[:ext_n].iloc[-200:].mean()) if ext_n >= 200 else float(ext_close.iloc[:ext_n].mean())

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
            vix=vix,
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
        if or_momentum >= or_threshold:
            direction = "buy_call"
        elif or_momentum <= -or_threshold:
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
        # Compute real intraday VWAP at this point in time
        _cumvol_n = float(day_cumvol.iloc[:n].iloc[-1]) if n > 0 else 0
        _vwap_val = float(day_cum_tp_vol.iloc[:n].iloc[-1] / _cumvol_n) if _cumvol_n > 0 else None

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
            zlema_trend=indicators.zlema_trend,
            vwap=_vwap_val,
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

        # Side-asymmetric cascade gate: optional separate floor for CALLs to
        # correct cascade-score bull-bias (CALLs at low E underperform PUTs at
        # the same E in 730d SPY data — see strategy_callput_calibration_skew).
        effective_min_cascade = (
            min_cascade_call if (min_cascade_call is not None and direction == "buy_call")
            else min_cascade
        )
        if explosion < effective_min_cascade:
            continue

        # ── Choppiness ──
        # vol_factor is computed inside compute_choppiness (available on result)
        # but the gate uses the golden-calibrated fixed max_chop threshold.
        # Low-vol adaptive tightening (VIX<15 → max_chop-1) showed +5.7% per-trade
        # efficiency but −0.2% total P&L over 2yr — insufficient for golden default.
        chop = compute_choppiness(bars_to_now, vix=vix, atr=atr_val)
        if chop.chop_score > max_chop:
            continue

        # ── PB EMA inside-band gate (symmetric chop reject) ──
        # Reject when price is *between* the two EMAs — the indicator's
        # "no zone" state. Asymmetric: does NOT block direction, only chop.
        if pb_ema and day_pb_ema_fast is not None:
            ema_f = float(day_pb_ema_fast.iloc[:n].iloc[-1])
            ema_s = float(day_pb_ema_slow.iloc[:n].iloc[-1])
            band_hi, band_lo = max(ema_f, ema_s), min(ema_f, ema_s)
            if band_lo < price < band_hi:
                continue

        # ── Extension veto (mean-reversion exhaustion filter) ──
        # Reject buy_call when price >> SMA-20 by N×ATR (call already overextended,
        # likely to revert before target). Mirror for buy_put when price << SMA-20.
        # Asymmetric / directional: only vetoes trend-continuation entries; a counter-
        # trend buy_put on an overextended-up move stays eligible.
        if extension_veto and atr_val > 0:
            ext_atr = (price - indicators.sma_20) / atr_val
            if direction == "buy_call" and ext_atr > extension_max_atr:
                continue
            if direction == "buy_put" and -ext_atr > extension_max_atr:
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

        # ── Active range: blend OR range with recent N bars for stop/target ──
        # blend=0.0 → pure OR range (golden default), blend=1.0 → pure active range
        # blend=0.5 → average of OR and recent range
        if active_range:
            recent_bars = bars_to_now.iloc[-active_range_bars:]
            ar_high = float(recent_bars["High"].max())
            ar_low = float(recent_bars["Low"].min())
            ar_width = ar_high - ar_low
            if ar_width > 0:
                b = active_range_blend
                ar_range_high = range_high * (1 - b) + ar_high * b
                ar_range_low = range_low * (1 - b) + ar_low * b
            else:
                ar_range_high = range_high
                ar_range_low = range_low
        else:
            ar_range_high = range_high
            ar_range_low = range_low

        if r_anchored_risk:
            # R-anchored geometry: risk is a fixed fraction of the range, independent
            # of where in the breakout zone entry triggered. Removes endogeneity
            # between entry-vs-mid distance and risk size, so target_mult means
            # the same dollar distance regardless of bar timing.
            ar_range_width = ar_range_high - ar_range_low
            risk = r_anchor_pct * ar_range_width
            if risk <= 0:
                continue
            entry = price
            if direction == "buy_call":
                stop = entry - risk
                target = entry + risk * target_mult
            else:
                stop = entry + risk
                target = entry - risk * target_mult
        elif direction == "buy_call":
            entry = price
            mid = (ar_range_high + ar_range_low) / 2
            # VIX-conditional stop buffer: widens in turbulent regimes, anchored
            # to baseline 0.10 at VIX <= vix_stop_anchor.
            buffer_pct = 0.10 + vix_stop_slope * max(0.0, vix - vix_stop_anchor)
            stop = mid + buffer_pct * (ar_range_high - ar_range_low)
            risk = entry - stop
            if risk <= 0:
                continue
            target = entry + risk * target_mult
        else:
            entry = price
            mid = (ar_range_high + ar_range_low) / 2
            buffer_pct = 0.10 + vix_stop_slope * max(0.0, vix - vix_stop_anchor)
            stop = mid - buffer_pct * (ar_range_high - ar_range_low)
            risk = stop - entry
            if risk <= 0:
                continue
            target = entry - risk * target_mult

        # ── Walk forward to determine outcome ──
        future_bars = day_bars[day_bars.index > ts]

        # ── Confirmation bar: require next bar to close in trade direction ──
        if confirmation_bar and len(future_bars) >= 1:
            conf_bar = future_bars.iloc[0]
            conf_close = float(conf_bar["Close"])
            if direction == "buy_call" and conf_close <= entry:
                continue  # Next bar didn't confirm bullish — skip
            if direction == "buy_put" and conf_close >= entry:
                continue  # Next bar didn't confirm bearish — skip
            # Confirmed — shift entry to confirmation bar's close, adjust levels
            entry = conf_close
            if direction == "buy_call":
                risk = entry - stop
                if risk <= 0:
                    continue
                target = entry + risk * target_mult
            else:
                risk = stop - entry
                if risk <= 0:
                    continue
                target = entry - risk * target_mult
            # Future bars now start AFTER the confirmation bar
            future_bars = future_bars.iloc[1:]
            ts = conf_bar.name  # Update entry timestamp

        outcome = "eod"
        if len(future_bars) > 0:
            exit_price = float(future_bars["Close"].iloc[-1])
            exit_ts = future_bars.index[-1]
        else:
            exit_price = price
            exit_ts = ts

        # ── Precompute entry theta context for decay-aware targets ──
        if decay_aware_targets and simulate_options:
            entry_minutes_since_open = (ts.hour * 60 + ts.minute) - (9 * 60 + 30)
            total_market_minutes = 390  # 9:30–16:00
            # Premium at entry (same formula as synth pricing)
            _dat_premium = atr_val * premium_atr_pct
            if _dat_premium < 0.10:
                _dat_premium = 0.10
            # Original target distance in underlying terms
            original_target_dist = abs(target - entry)

        # Track Maximum Favorable Excursion for smart stagnation exit
        max_favorable_excursion = 0.0

        for bar_j, (_, fb) in enumerate(future_bars.iterrows()):
            fh, fl, fc = float(fb["High"]), float(fb["Low"]), float(fb["Close"])

            # Update MFE: best intrabar P&L the trade has seen
            if direction == "buy_call":
                bar_best = fh - entry
            else:
                bar_best = entry - fl
            max_favorable_excursion = max(max_favorable_excursion, bar_best)

            # ── Decay-aware dynamic target: shrink target as theta erodes ──
            # The idea: as time passes, theta eats premium. The underlying must
            # move MORE just to offset theta. We flip this: shrink the *take-profit*
            # target so we grab profits before theta eats them.
            #
            # Formula: effective_target_mult = target_mult × decay_factor
            #   decay_factor = max(floor, 0.5^(bars_elapsed / halflife))
            #   This decays the target exponentially — fast at first, then floor.
            #
            # Additionally: "theta breakeven exit" — if current P&L is positive but
            # the projected theta burn over the next 2 bars exceeds remaining upside
            # to the (now-decayed) target, take profit immediately.
            if decay_aware_targets and simulate_options:
                bars_elapsed = bar_j + 1
                _eff_floor = decay_target_floor
                decay_factor = max(_eff_floor,
                                   0.5 ** (bars_elapsed / decay_halflife_bars))
                decayed_dist = original_target_dist * decay_factor
                if direction == "buy_call":
                    effective_target = entry + decayed_dist
                else:
                    effective_target = entry - decayed_dist

                # Check decayed target hit (instead of original)
                if direction == "buy_call":
                    if fh >= effective_target:
                        outcome = "decay_target"; exit_price = effective_target; exit_ts = fb.name; break
                else:
                    if fl <= effective_target:
                        outcome = "decay_target"; exit_price = effective_target; exit_ts = fb.name; break

                # Theta breakeven exit: if in profit but theta will eat it in next 2 bars
                current_pnl_raw = (fc - entry) if direction == "buy_call" else (entry - fc)
                if current_pnl_raw > 0 and bars_elapsed >= 2:
                    bar_minutes = entry_minutes_since_open + bars_elapsed * 5
                    remaining_frac_now = max(0, (total_market_minutes - bar_minutes) / total_market_minutes)
                    remaining_frac_next = max(0, (total_market_minutes - bar_minutes - 10) / total_market_minutes)
                    # Theta burn over next 2 bars (sqrt decay model)
                    theta_now = _dat_premium * 0.70 * (1.0 - remaining_frac_now ** 0.5)
                    theta_next = _dat_premium * 0.70 * (1.0 - remaining_frac_next ** 0.5)
                    theta_burn_2bars = theta_next - theta_now
                    # Convert current underlying P&L to option-equivalent via delta
                    option_profit_approx = option_delta * current_pnl_raw
                    if theta_burn_2bars >= option_profit_approx * 0.8:
                        outcome = "theta_exit"; exit_price = fc; exit_ts = fb.name; break

            if direction == "buy_call":
                if fl <= stop:
                    outcome = "stop"; exit_price = stop; exit_ts = fb.name; break
                if not decay_aware_targets and fh >= target:
                    outcome = "target"; exit_price = target; exit_ts = fb.name; break
            else:
                if fh >= stop:
                    outcome = "stop"; exit_price = stop; exit_ts = fb.name; break
                if not decay_aware_targets and fl <= target:
                    outcome = "target"; exit_price = target; exit_ts = fb.name; break

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
                    gainz_pnl = (fc - entry) if risk > 0 else 0
                    if gainz_min_profit_r <= 0 or gainz_pnl >= risk * gainz_min_profit_r:
                        outcome = "gainz_exit"; exit_price = fc; exit_ts = fb.name; break
                if direction == "buy_put" and gz == "buy":
                    gainz_pnl = (entry - fc) if risk > 0 else 0
                    if gainz_min_profit_r <= 0 or gainz_pnl >= risk * gainz_min_profit_r:
                        outcome = "gainz_exit"; exit_price = fc; exit_ts = fb.name; break

            # Stagnation exit: if after N bars trade hasn't moved 0.5R, cut it
            # Enhancement: skip stagnation if trade reached >0.5R (MFE) — let target/stop resolve
            bars_since_entry = len(day_bars[(day_bars.index > ts) & (day_bars.index <= fb.name)])

            # ── Tiered stagnation: early exit at bar 8 if trade is flat (between -0.1R and +0.2R) ──
            # Cuts dead-money trades sooner, preserving capital for better setups.
            # Also imposes a longer post-stagnation cooldown to avoid re-entering chop.
            if tiered_stagnation and bars_since_entry == tiered_stag_early_bar:
                current_pnl = (fc - entry) if direction == "buy_call" else (entry - fc)
                pnl_r = current_pnl / risk if risk > 0 else 0
                mfe_ratio = max_favorable_excursion / risk if risk > 0 else 0
                if tiered_stag_pnl_lo <= pnl_r <= tiered_stag_pnl_hi and mfe_ratio < 0.5:
                    outcome = "stagnation"; exit_price = fc; exit_ts = fb.name; break

            if bars_since_entry >= stagnation_bars:
                current_pnl = (fc - entry) if direction == "buy_call" else (entry - fc)
                mfe_ratio = max_favorable_excursion / risk if risk > 0 else 0
                if current_pnl < risk * stagnation_threshold and mfe_ratio < 0.5:
                    outcome = "stagnation"; exit_price = fc; exit_ts = fb.name; break

            # Hard time stop 15:30
            if fb.name.strftime("%H:%M") >= "15:30":
                outcome = "time_stop"; exit_price = fc; exit_ts = fb.name; break

        pnl = (exit_price - entry) if direction == "buy_call" else (entry - exit_price)

        # ── 0DTE Option P&L: prefer REAL Alpaca bars, fall back to synth ──
        underlying_move = pnl  # signed move in underlying
        priced_real = False
        occ_symbol: str | None = None
        if simulate_options and real_options:
            try:
                from src.utils.alpaca_options import (
                    fetch_intraday_option_bars,
                    option_close_at,
                    resolve_atm_0dte,
                )
                opt_type = "call" if direction == "buy_call" else "put"
                occ_symbol = resolve_atm_0dte(symbol, trade_date, opt_type, price)
                if occ_symbol:
                    opt_bars = fetch_intraday_option_bars(occ_symbol, trade_date, "5min")
                    entry_premium = option_close_at(opt_bars, ts)
                    exit_premium = option_close_at(opt_bars, exit_ts)
                    if entry_premium and entry_premium > 0 and exit_premium is not None:
                        # Long-option P&L is identical for call & put — direction
                        # is encoded in which contract we bought.
                        option_pnl_per_contract = exit_premium - entry_premium
                        # Cap loss at premium paid (defined risk for long options)
                        option_pnl_per_contract = max(option_pnl_per_contract, -entry_premium)
                        option_pnl_per_contract -= slippage
                        option_pnl_total = option_pnl_per_contract * 100
                        est_premium = entry_premium
                        pnl = option_pnl_per_contract
                        priced_real = True
            except Exception as e:
                logger.debug("Real-options pricing failed for %s %s: %s — falling back to synth",
                             symbol, ts, e)

        if simulate_options and not priced_real:
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
            # Deduct slippage (round-trip: entry + exit)
            option_pnl_per_contract -= slippage
            # P&L per contract (x100 multiplier)
            option_pnl_total = option_pnl_per_contract * 100
            pnl = option_pnl_per_contract  # per-contract P&L for reporting
        elif not simulate_options:
            option_pnl_total = None
            est_premium = None

        if outcome == "stop":
            stops_today += 1

        # Track stagnation for extended cooldown
        last_was_stagnation = (outcome == "stagnation")

        # ── Update daily risk trackers ──
        daily_pnl_cumulative += pnl
        if pnl <= 0:
            consecutive_losses += 1
        else:
            consecutive_losses = 0

        # Cascade-tiered contracts
        if cascade_sizing:
            if explosion >= 8:
                size = float(cascade_size_high)
            elif explosion >= 6:
                size = float(cascade_size_mid)
            else:
                size = float(cascade_size_low)
        else:
            size = 1.0

        # ── VWAP divergence risk adjustment ──
        # When real VWAP and SMA20 disagree on which side price is on,
        # institutional flow conflicts with trend. The flag is available
        # on quality_result.vwap_divergence for callers/dashboard to display.
        # NOTE: Sizing reduction disabled after backtest showed net negative
        # impact — the OR momentum already prices VWAP into the trigger,
        # so divergence is informational, not actionable for sizing.
        # if quality_result.vwap_divergence and size > 1.0:
        #     size = max(1.0, size - 1.0)

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
            "occ_symbol": occ_symbol if simulate_options else None,
            "pricing": ("real" if (simulate_options and priced_real)
                        else "synth" if simulate_options else "shares"),
        })

    return triggers


def main():
    parser = argparse.ArgumentParser(description="Replay Sweet Spot Agent on historical data")
    # Defaults match GOLDEN parameters (see README) — produces validated 2-yr SPY
    # results (real Alpaca options): 608 trades, +$5,053/contract, +$15,159 cascade-sized.
    # Tighter stops (60% of range), decay floor 0.4, mid-tier target 1.5R.
    # Stagnation: tiered (bar 8 early exit + bar 12 standard), MFE skip at 0.5R.
    # Tiered stagnation: exits flat trades (-0.1R to +0.2R) at bar 8 (40 min);
    #   extends cooldown to 6 bars (30 min) after stagnation exits.
    #   Validated 730d SPY: PF 1.51→1.60, Sharpe 2.31→2.63, MDD −33%, Calmar +60%.
    # Stagnation threshold: 0.3R (was 0.5R — keeps trades with some momentum alive).
    # Streak breaker: 2 consecutive losses → stop for day.
    # Cascade ≥ 2 (lowered from 4 — E2-E3 trades profitable with other filters).
    # Decay-aware targets (golden: ON, floor=0.4, halflife=8 bars/40min, was 6/30min).
    # Real options pricing (golden: ON, Alpaca historical 0DTE bars).
    # MFE stagnation skip (golden: ON, threshold=0.5R — validated +3% on SPY & QQQ).
    # PB EMA inside-band gate (golden: ON, 13/55 — validated 730d SPY:
    #   PF 1.41→1.48, Sharpe 1.89→2.26, MDD $30.81→$27.03 / -12%).
    # Override individual flags to explore. Use --research-mode to revert to
    # loose pre-golden defaults (chop 10, no caps, 10:35 scan, no Gainz, no decay).
    parser.add_argument("--symbol", "-s", default="SPY")
    parser.add_argument("--days", type=int, default=30, help="Number of recent trading days")
    parser.add_argument("--max-chop", type=int, default=5, help="Max choppiness (golden: 5)")
    parser.add_argument("--multi", action="store_true", help="Allow multiple triggers per day")
    parser.add_argument("--min-quality", type=int, default=3, help="Minimum quality score (golden: 3)")
    parser.add_argument("--max-quality", type=int, default=7, help="Maximum quality score (golden: 7)")
    parser.add_argument("--min-cascade", type=int, default=2, help="Minimum cascade proxy (golden: 2, was 4)")
    parser.add_argument("--min-cascade-call", type=int, default=None,
                        help="Side-asymmetric: separate min cascade for CALLs only "
                             "(corrects cascade bull-bias). Default: None (use --min-cascade for both).")
    parser.add_argument("--vix-stop-slope", type=float, default=0.0,
                        help="VIX-conditional stop buffer slope (default: 0.0 = static 10%% buffer). "
                             "Buffer = 0.10 + slope * max(0, VIX - vix_stop_anchor). "
                             "Try 0.005 (10%% at VIX≤15, 15%% at VIX 25).")
    parser.add_argument("--vix-stop-anchor", type=float, default=15.0,
                        help="VIX level at which stop buffer slope kicks in (default: 15.0).")
    parser.add_argument("--breakout-pct", type=float, default=0.25, help="Breakout percentage of range")
    parser.add_argument("--cooldown-bars", type=int, default=3, help="Cooldown period in bars")
    parser.add_argument("--scan-end", type=str, default="13:59", help="End time for scanning (HH:MM, golden: 13:59)")
    parser.add_argument("--scan-start", type=str, default="10:30", help="Start time for scanning (HH:MM, golden: 10:30)")
    parser.add_argument("--target-mult-low", type=float, default=1.0, help="Target multiple for low explosion")
    parser.add_argument("--target-mult-mid", type=float, default=1.5, help="Target multiple for mid explosion (was 1.25, tightened-stop change: 1.5)")
    parser.add_argument("--target-mult-high", type=float, default=1.5, help="Target multiple for high explosion")
    parser.add_argument("--no-regime-guard", action="store_true", default=True,
                        help="Disable regime guardrails (golden: OFF — regime guard disabled by default)")
    parser.add_argument("--regime-guard", action="store_true",
                        help="Enable regime guard (blocks counter-trend trades unless RSI extreme)")
    parser.add_argument("--or-threshold", type=int, default=25,
                        help="OR momentum threshold for direction decision (golden: 25, test: 30/35)")
    parser.add_argument("--max-trades-per-day", type=int, default=3, help="Max trades per day (0=unlimited, golden: 3)")
    parser.add_argument("--max-stops-per-day", type=int, default=1, help="Stop trading after N stop-outs (0=unlimited, golden: 1)")
    parser.add_argument("--max-consecutive-losses", type=int, default=2,
                        help="Stop trading after N consecutive losses in a day (0=disabled, golden: 2)")
    parser.add_argument("--daily-loss-limit", type=float, default=0.0,
                        help="Stop trading if cumulative daily P&L drops below -$X (0=disabled, golden: 0 — streak breaker sufficient)")
    parser.add_argument("--no-confirmation-bar", action="store_true", default=True,
                        help="Disable confirmation bar requirement (default: disabled — confirmation bar hurt performance)")
    parser.add_argument("--confirmation-bar", action="store_true", default=False,
                        help="Enable confirmation bar (require next bar to close in trade direction before entry)")
    parser.add_argument("--stagnation-bars", type=int, default=12,
                        help="Bars before stagnation exit fires (golden: 12 = 60 min, was 10). "
                             "Validated 730d SPY+QQQ+VOO: PF +0.03, Sharpe +0.05, MDD -10pp, Calmar +86%%.")
    parser.add_argument("--stagnation-threshold", type=float, default=0.3,
                        help="Minimum P&L as fraction of R to avoid stagnation exit (golden: 0.3, was 0.5). "
                             "Lower threshold keeps trades with some momentum alive for decaying target.")
    parser.add_argument("--no-gainz-exit", action="store_true",
                        help="Disable GainzAlgoV2 reversal early-exit (golden: enabled)")
    parser.add_argument("--gainz-body-ratio", type=float, default=0.7, help="Min candle body/range ratio for Gainz signal (golden: 0.7)")
    parser.add_argument("--gainz-rsi-overbought", type=float, default=70.0, help="RSI threshold for Gainz SELL signal (golden: 70)")
    parser.add_argument("--gainz-rsi-oversold", type=float, default=30.0, help="RSI threshold for Gainz BUY signal (golden: 30)")
    parser.add_argument("--gainz-min-profit-r", type=float, default=0.3,
                        help="Min profit as fraction of R to allow Gainz exit (golden: 0.3, 0=disabled)")
    parser.add_argument("--no-cascade-sizing", action="store_true",
                        help="Disable cascade contract sizing (default: ON)")
    parser.add_argument("--cascade-size-low", type=int, default=3,
                        help="Contracts for E 4-5 tier (default: 3)")
    parser.add_argument("--cascade-size-mid", type=int, default=3,
                        help="Contracts for E 6-7 tier (default: 3)")
    parser.add_argument("--cascade-size-high", type=int, default=3,
                        help="Contracts for E 8+ tier (default: 3)")
    parser.add_argument("--dynamic-or", action="store_true",
                        help="Enable dynamic opening range (30-min quick OR when breakout is decisive by 10:00)")
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
    parser.add_argument("--slippage", type=float, default=0.0,
                        help="Per-contract slippage in $ (e.g., 0.05 for $5/contract). Deducted from each trade P&L.")
    parser.add_argument("--real-options", action="store_true", default=True,
                        help="Price each trigger with REAL Alpaca historical 0DTE bars (default: ON; "
                             "falls back to synthesized greeks if a contract or bars are unavailable). "
                             "Note: Alpaca options data starts ~Feb 2024.")
    parser.add_argument("--no-real-options", action="store_true",
                        help="Disable real Alpaca options pricing; use synthesized delta-gamma model instead.")
    parser.add_argument("--no-decay-aware-targets", action="store_true",
                        help="Disable time-decay-aware targets (golden: enabled). "
                             "When enabled, take-profit shrinks as theta erodes "
                             "(exponential decay with floor) and adds theta-breakeven exit.")
    parser.add_argument("--decay-target-floor", type=float, default=0.4,
                        help="Minimum decay factor for target (0.4 = target never shrinks below 40%% of original, was 0.3)")
    parser.add_argument("--decay-halflife-bars", type=int, default=8,
                        help="Bars (5-min each) for target to decay to 50%% (golden: 8 = 40 min, was 6). "
                             "Slower decay keeps targets ~15%% higher at 30 min, especially benefits QQQ.")
    parser.add_argument("--active-range", action="store_true", default=True,
                        help="Use blended OR+recent range for stop/target (golden: ON, blend 0.25)")
    parser.add_argument("--no-active-range", action="store_true",
                        help="Disable active range blending (use pure OR range)")
    parser.add_argument("--active-range-bars", type=int, default=6,
                        help="Number of recent 5-min bars for active range (default: 6 = 30 min)")
    parser.add_argument("--active-range-blend", type=float, default=0.25,
                        help="Blend ratio: 0.0=pure OR, 0.25=golden, 0.5=50/50, 1.0=pure active (default: 0.25)")
    parser.add_argument("--pb-ema", action="store_true", default=True,
                        help="Enable PB EMA inside-band gate (golden: ON, 13/55). "
                             "Rejects entries where price is between the fast/slow EMAs "
                             "(chop zone). Symmetric — does not block direction. "
                             "Validated 730d SPY: PF 1.41→1.48, Sharpe 1.89→2.26, MDD −12%%.")
    parser.add_argument("--no-pb-ema", action="store_true",
                        help="Disable PB EMA inside-band gate.")
    parser.add_argument("--pb-ema-fast", type=int, default=13,
                        help="Fast EMA length for PB EMA band (golden: 13)")
    parser.add_argument("--pb-ema-slow", type=int, default=55,
                        help="Slow EMA length for PB EMA band (golden: 55)")
    parser.add_argument("--vix-max", type=float, default=30.0,
                        help="Skip trading days where VIX > this level (0=disabled, default: 30)")
    parser.add_argument("--vix-spike-pct", type=float, default=20.0,
                        help="Skip days where VIX spiked >N%% day-over-day (0=disabled, default: 20)")
    # ── Tiered Stagnation (golden default: ON) ──
    parser.add_argument("--tiered-stagnation", action="store_true", default=True,
                        help="Enable tiered stagnation: early exit at bar 8 if trade is flat (-0.1R to +0.2R),"
                             " plus extended 6-bar cooldown after stagnation exits (golden: ON)")
    parser.add_argument("--no-tiered-stagnation", action="store_true",
                        help="Disable tiered stagnation (revert to bar-12-only stagnation)")
    parser.add_argument("--tiered-stag-early-bar", type=int, default=8,
                        help="Bar at which tiered stagnation checks for flat trades (default: 8 = 40 min)")
    parser.add_argument("--tiered-stag-pnl-lo", type=float, default=-0.1,
                        help="Min P&L as fraction of R for early stag exit (default: -0.1)")
    parser.add_argument("--tiered-stag-pnl-hi", type=float, default=0.2,
                        help="Max P&L as fraction of R for early stag exit (default: 0.2)")
    parser.add_argument("--stag-cooldown-bars", type=int, default=6,
                        help="Extended cooldown bars after stagnation exit (default: 6 = 30 min)")
    parser.add_argument("--r-anchored-risk", action="store_true", default=False,
                        help="A/B: anchor risk to a fixed fraction of range width (default: 25%%) "
                             "instead of (entry - mid_stop). Removes endogeneity between entry "
                             "location and risk size, so target_mult means a consistent distance.")
    parser.add_argument("--r-anchor-pct", type=float, default=0.25,
                        help="Risk as fraction of range width when --r-anchored-risk is on (default: 0.25)")
    parser.add_argument("--extension-veto", action="store_true", default=False,
                        help="A/B: veto entries when price is overextended from SMA-20 by N×ATR. "
                             "Asymmetric — only vetoes trend-continuation; counter-trend stays eligible. "
                             "Tests Malyarovich-style mean-reversion exhaustion idea.")
    parser.add_argument("--extension-max-atr", type=float, default=2.0,
                        help="Max |price − SMA-20| / ATR before extension-veto rejects entry (default: 2.0)")
    args = parser.parse_args()

    if args.research_mode:
        args.max_chop = 10
        args.max_quality = 8
        args.scan_start = "10:35"
        args.max_trades_per_day = 0
        args.max_stops_per_day = 0
        args.no_gainz_exit = True
        args.no_decay_aware_targets = True
        args.no_pb_ema = True
        logger.info("research-mode: loose defaults applied (chop 10, max-Q 8, scan from 10:35, no caps, no Gainz, no decay targets, no PB EMA)")

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
        df.index = df.index.tz_convert("America/New_York")

    if df.empty:
        print("No data available")
        return

    trading_days = sorted(set(df.index.date))
    logger.info("Replaying %d trading days...", len(trading_days))

    # ── Fetch historical VIX for realistic quality scoring ──
    import yfinance as yf
    logger.info("Fetching historical ^VIX data...")
    try:
        from datetime import timedelta
        vix_start = str(trading_days[0] - timedelta(days=5))
        vix_end = str(trading_days[-1] + timedelta(days=1))
        vix_df = yf.download("^VIX", start=vix_start, end=vix_end, progress=False)
        if isinstance(vix_df.columns, pd.MultiIndex):
            vix_df.columns = vix_df.columns.get_level_values(0)
        vix_map: dict[date, float] = {}
        for idx, row in vix_df.iterrows():
            vix_map[idx.date()] = float(row["Close"])
        logger.info("  VIX data: %d days loaded", len(vix_map))
    except Exception as e:
        logger.warning("VIX fetch failed (%s), using default 20.0", e)
        vix_map = {}

    # ── Number of prior context bars for multi-day SMA (3 days ≈ 234 bars covers SMA-200) ──
    PRIOR_DAYS_CONTEXT = 4  # prepend 4 prior trading days' bars for SMA-50/200 warmup

    all_triggers = []
    for idx, day in enumerate(trading_days):
        day_bars = df[df.index.date == day]
        if len(day_bars) < 24:
            continue

        # Build prior_bars from preceding days in the dataset
        prior_days = trading_days[max(0, idx - PRIOR_DAYS_CONTEXT):idx]
        if prior_days:
            prior_bars = df[pd.Series(df.index.date).isin(set(prior_days)).values]
        else:
            prior_bars = None

        # Look up VIX for this day (fall back to most recent available or 20.0)
        day_vix = vix_map.get(day, 20.0)
        if day_vix == 20.0 and vix_map:
            # Try previous trading day
            for prev_day in reversed(trading_days[:idx]):
                if prev_day in vix_map:
                    day_vix = vix_map[prev_day]
                    break

        # ── VIX sit-out filter: skip days with extreme volatility ──
        if args.vix_max > 0 and day_vix > args.vix_max:
            continue
        if args.vix_spike_pct > 0 and idx > 0:
            prev_vix = None
            for prev_day in reversed(trading_days[:idx]):
                if prev_day in vix_map:
                    prev_vix = vix_map[prev_day]
                    break
            if prev_vix and prev_vix > 0:
                spike_pct = (day_vix - prev_vix) / prev_vix * 100
                if spike_pct > args.vix_spike_pct:
                    continue

        triggers = replay_day(day_bars, day, max_chop=args.max_chop,
                              min_quality=args.min_quality, max_quality=args.max_quality,
                              min_cascade=args.min_cascade, min_cascade_call=args.min_cascade_call,
                              vix_stop_slope=args.vix_stop_slope, vix_stop_anchor=args.vix_stop_anchor,
                              breakout_pct=args.breakout_pct,
                              cooldown_bars=args.cooldown_bars, scan_end=args.scan_end,
                              scan_start=args.scan_start,
                              target_mult_low=args.target_mult_low, target_mult_mid=args.target_mult_mid,
                              target_mult_high=args.target_mult_high,
                              regime_guard=args.regime_guard,
                              or_threshold=args.or_threshold,
                              symbol=args.symbol,
                              max_trades_per_day=args.max_trades_per_day,
                              max_stops_per_day=args.max_stops_per_day,
                              max_consecutive_losses=args.max_consecutive_losses,
                              daily_loss_limit=args.daily_loss_limit,
                              confirmation_bar=args.confirmation_bar,
                              stagnation_bars=args.stagnation_bars,
                              stagnation_threshold=args.stagnation_threshold,
                              gainz_exit=not args.no_gainz_exit,
                              gainz_body_ratio=args.gainz_body_ratio,
                              gainz_rsi_overbought=args.gainz_rsi_overbought,
                              gainz_rsi_oversold=args.gainz_rsi_oversold,
                              gainz_min_profit_r=args.gainz_min_profit_r,
                              cascade_sizing=not args.no_cascade_sizing,
                              cascade_size_low=args.cascade_size_low,
                              cascade_size_mid=args.cascade_size_mid,
                              cascade_size_high=args.cascade_size_high,
                              simulate_options=not args.shares,
                              option_delta=args.option_delta,
                              option_gamma=args.option_gamma,
                              premium_atr_pct=args.premium_atr_pct,
                              slippage=args.slippage,
                              vix=day_vix,
                              prior_bars=prior_bars,
                              dynamic_or=args.dynamic_or,
                              real_options=args.real_options and not args.no_real_options,
                              decay_aware_targets=not args.no_decay_aware_targets,
                              decay_target_floor=args.decay_target_floor,
                              decay_halflife_bars=args.decay_halflife_bars,
                              active_range=not args.no_active_range,
                              active_range_bars=args.active_range_bars,
                              active_range_blend=args.active_range_blend,
                              pb_ema=args.pb_ema and not args.no_pb_ema,
                              pb_ema_fast=args.pb_ema_fast,
                              pb_ema_slow=args.pb_ema_slow,
                              tiered_stagnation=args.tiered_stagnation and not args.no_tiered_stagnation,
                              tiered_stag_early_bar=args.tiered_stag_early_bar,
                              tiered_stag_pnl_lo=args.tiered_stag_pnl_lo,
                              tiered_stag_pnl_hi=args.tiered_stag_pnl_hi,
                              stag_cooldown_bars=args.stag_cooldown_bars,
                              r_anchored_risk=args.r_anchored_risk,
                              r_anchor_pct=args.r_anchor_pct,
                              extension_veto=args.extension_veto,
                              extension_max_atr=args.extension_max_atr)
        all_triggers.extend(triggers)

    # ── Results ──
    print(f"\n{'═' * 70}")
    print(f"  SWEET SPOT REPLAY: {args.symbol} — {len(trading_days)} days")
    if args.shares:
        mode_label = "SHARES"
    elif args.real_options and not args.no_real_options:
        mode_label = "0DTE OPTIONS (Alpaca historical bars; synth fallback)"
    else:
        mode_label = f"0DTE OPTIONS (synth Δ={args.option_delta}, γ={args.option_gamma})"
    print(f"  Mode: {mode_label}")
    print(f"  Filter: Quality {args.min_quality}-{args.max_quality}, Cascade ≥ {args.min_cascade}, Chop ≤ {args.max_chop}, Regime Guard: {'ON' if args.regime_guard else 'OFF'}")
    print(f"  Analyzers: OpeningRange + RecentMomentum + MomentumCascade (exact replica)")
    if not args.no_decay_aware_targets:
        print(f"  Decay-Aware Targets: ON (floor={args.decay_target_floor}, halflife={args.decay_halflife_bars} bars / {args.decay_halflife_bars*5} min)")
    else:
        print(f"  Decay-Aware Targets: OFF")
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
        # Sortino: Frank-Sortino-original formulation. Divide by the count of
        # *negative* days only, so the denominator measures realized downside
        # risk rather than smearing it across calm/zero days. This is what
        # GIPS/Pertrac/most fund admin systems report.
        downside_returns = [x for x in daily_series if x < 0]
        if downside_returns:
            tdd = (sum(x ** 2 for x in downside_returns) / len(downside_returns)) ** 0.5
            sortino = (mean_d / tdd) * (252 ** 0.5) if tdd > 0 else float("inf")
        else:
            sortino = float("inf")
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
    cl, cm, ch = args.cascade_size_low, args.cascade_size_mid, args.cascade_size_high
    tiers = [
        (f"E 2-3 ({cl}ct)",  lambda e: e <= 3),
        (f"E 4-5 ({cl}ct)",  lambda e: 4 <= e <= 5),
        (f"E 6-7 ({cm}ct)",  lambda e: 6 <= e <= 7),
        (f"E 8+  ({ch}ct)",  lambda e: e >= 8),
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

    # ── Call vs Put Aggregation ──
    print(f"\n  Call vs Put Aggregation{sizing_label}:")
    print(f"    {'Side':<6} {'N':>4} {'%Tot':>5} {'WR%':>6} {'PF':>5} {'TotPnL':>10} {'AvgPnL':>9}")
    for side_label, side_pred in [("CALL", lambda d: "call" in d), ("PUT", lambda d: "put" in d)]:
        side = [t for t in all_triggers if side_pred(t["direction"])]
        if not side:
            print(f"    {side_label:<6} {0:>4} {'—':>5} {'—':>6} {'—':>5} {'—':>10} {'—':>9}")
            continue
        sw = [t for t in side if t["is_winner"]]
        sl = [t for t in side if not t["is_winner"]]
        sgp = sum(t[pnl_field] for t in sw)
        sgl = abs(sum(t[pnl_field] for t in sl))
        spf = sgp / sgl if sgl > 0 else float("inf")
        spnl = sum(t[pnl_field] for t in side)
        pct_of_total = len(side) / len(all_triggers) * 100
        print(f"    {side_label:<6} {len(side):>4} {pct_of_total:>4.1f}% {len(sw)/len(side)*100:>5.1f}% "
              f"{spf:>5.2f} ${spnl:>+8.2f} ${spnl/len(side):>+7.3f}")

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
        if args.real_options and not args.no_real_options:
            n_real = sum(1 for t in all_triggers if t.get("pricing") == "real")
            n_synth = sum(1 for t in all_triggers if t.get("pricing") == "synth")
            print(f"  Pricing source:  real={n_real}  synth_fallback={n_synth}")
    else:
        print(f"\n  {'Date':<12} {'Time':<6} {'Dir':<5} {'Q':>2} {'E':>2} {'C':>2} {'Mult':>5} {'Entry':>8} {'Exit':>8} {'P&L':>8} {'Outcome':<8}")
        print(f"  {'─'*12} {'─'*6} {'─'*5} {'─'*2} {'─'*2} {'─'*2} {'─'*5} {'─'*8} {'─'*8} {'─'*8} {'─'*8}")
        for t in all_triggers:
            d = "CALL" if "call" in t["direction"] else "PUT"
            w = "✅" if t["is_winner"] else "❌"
            print(f"  {t['date']:<12} {t['time']:<6} {d:<5} {t['quality']:>2} {t['explosion']:>2} {t['chop']:>2} {t['target_mult']:>4.2f}x ${t['entry']:>7.2f} ${t['exit_price']:>7.2f} ${t['pnl']:>+7.2f} {t['outcome']:<8} {w}")


if __name__ == "__main__":
    main()

