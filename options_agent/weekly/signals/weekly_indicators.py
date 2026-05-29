"""Build MarketIndicators from daily bars for the weekly agent.

Separate from _build_indicators_replay_parity() in run_sweet_spot_agent.py
which interleaves multi-day 5-min "extended_bars" with today's 5-min
"day_bars". This version operates purely on daily OHLCV data.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.models.market_data import MarketIndicators

logger = logging.getLogger(__name__)


def build_weekly_indicators(
    daily_bars: pd.DataFrame,
    symbol: str,
    vix: float,
) -> MarketIndicators:
    """Build MarketIndicators from 30+ days of daily OHLCV bars.

    Args:
        daily_bars: DataFrame with OHLCV columns, date-indexed. Should have
            60+ rows for SMA-200 warmup (degrades gracefully with less).
        symbol: Ticker symbol.
        vix: Current VIX level.
    """
    n = len(daily_bars)
    if n == 0:
        raise ValueError("daily_bars is empty")

    close = daily_bars["Close"].astype(float)
    high = daily_bars["High"].astype(float)
    low = daily_bars["Low"].astype(float)
    vol = daily_bars["Volume"].astype(float)
    price = float(close.iloc[-1])

    # RSI-14
    if n >= 15:
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

    # SMAs
    sma_20 = float(close.iloc[-20:].mean()) if n >= 20 else price
    sma_50 = float(close.iloc[-50:].mean()) if n >= 50 else float(close.mean())
    sma_200 = float(close.iloc[-200:].mean()) if n >= 200 else float(close.mean())

    # Bollinger Bands (20-period)
    if n >= 20:
        bb_mid = float(close.rolling(20).mean().iloc[-1])
        bb_std = float(close.rolling(20).std().iloc[-1])
        if np.isnan(bb_mid):
            bb_mid = price
            bb_std = 0.0
    else:
        bb_mid = price
        bb_std = 0.0
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # MACD (12/26/9)
    if n >= 26:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal_line
        macd_val = float(macd_line.iloc[-1])
        macd_sig = float(signal_line.iloc[-1])
        macd_hist = float(hist.iloc[-1])
    else:
        macd_val = macd_sig = macd_hist = 0.0

    # ATR-14
    if n >= 15:
        tr = pd.concat(
            [high - low,
             (high - close.shift()).abs(),
             (low - close.shift()).abs()],
            axis=1,
        ).max(axis=1)
        atr_val = float(tr.rolling(14).mean().iloc[-1])
        if np.isnan(atr_val):
            atr_val = 1.0
    else:
        atr_val = 1.0

    # Volume
    current_volume = int(vol.iloc[-1])
    vol_sma = float(vol.rolling(min(20, n)).mean().iloc[-1])
    if np.isnan(vol_sma):
        vol_sma = float(current_volume)

    # ZLEMA 8/21
    if n >= 21:
        lag_fast = (8 - 1) // 2
        lag_slow = (21 - 1) // 2
        comp_fast = 2 * close - close.shift(lag_fast)
        comp_slow = 2 * close - close.shift(lag_slow)
        zf = float(comp_fast.ewm(span=8, adjust=False).mean().iloc[-1])
        zs = float(comp_slow.ewm(span=21, adjust=False).mean().iloc[-1])
        if zf > zs * 1.0002:
            zlema_trend = "bullish"
        elif zf < zs * 0.9998:
            zlema_trend = "bearish"
        else:
            zlema_trend = "neutral"
    else:
        zf = zs = price
        zlema_trend = "neutral"

    return MarketIndicators(
        symbol=symbol,
        timestamp=datetime.now(ZoneInfo("UTC")),
        current_price=price,
        timeframe="daily",
        vix=vix,
        rsi_14=rsi_val,
        sma_20=sma_20,
        sma_50=sma_50,
        sma_200=sma_200,
        bb_upper=bb_upper,
        bb_middle=bb_mid,
        bb_lower=bb_lower,
        macd=macd_val,
        macd_signal=macd_sig,
        macd_histogram=macd_hist,
        atr_14=atr_val,
        volume=current_volume,
        volume_sma_20=vol_sma,
        zlema_fast=zf,
        zlema_slow=zs,
        zlema_trend=zlema_trend,
    )
