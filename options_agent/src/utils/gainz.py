"""GainzAlgoV2-style reversal signal — shared by replay and live agent."""

from __future__ import annotations

import math


def gainz_signal(open_p: float, high: float, low: float, close: float, rsi: float,
                 body_ratio_min: float = 0.5, rsi_overbought: float = 65.0,
                 rsi_oversold: float = 35.0) -> str | None:
    """Return 'buy' | 'sell' | None for a single OHLC bar.

    'buy'  = bullish reversal (oversold RSI + strong bullish candle)
    'sell' = bearish reversal (overbought RSI + strong bearish candle)
    Strong = body / (high-low) >= body_ratio_min.

    Used as an early-exit signal: opposing direction => close position.
    Default thresholds (65/35, 0.5) tuned via 1-year SPY threshold sweep
    — best PF (1.81) and WR (63.6%).
    """
    bar_range = high - low
    if bar_range <= 0 or math.isnan(rsi):
        return None
    body_ratio = abs(close - open_p) / bar_range
    if body_ratio < body_ratio_min:
        return None
    if close > open_p and rsi < rsi_oversold:
        return "buy"
    if close < open_p and rsi > rsi_overbought:
        return "sell"
    return None
