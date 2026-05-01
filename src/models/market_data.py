"""Pydantic models for market data, indicators, and regime classification."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MarketRegime(str, Enum):
    """Market regime classification driving strategy selection."""
    LOW_VOL_BULLISH = "low_vol_bullish"
    LOW_VOL_NEUTRAL = "low_vol_neutral"
    RANGE_BOUND_HV = "range_bound_high_vol"
    HIGH_VOL_BEARISH = "high_vol_bearish"
    TRENDING_BEARISH = "trending_bearish"


class MarketIndicators(BaseModel):
    """Snapshot of all computed technical indicators for a symbol."""
    symbol: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    current_price: float
    timeframe: str = Field(default="daily", description="Analysis timeframe: 15min, 1hour, daily, weekly")

    # Volatility
    vix: float

    # Momentum
    rsi_14: float
    rsi_5min: Optional[float] = Field(default=None, description="RSI(14) on 5-min candles — more responsive for intraday decisions")

    # Trend — Moving Averages
    sma_20: float
    sma_50: float
    sma_200: float

    # Bollinger Bands
    bb_upper: float
    bb_middle: float
    bb_lower: float

    # MACD
    macd: float
    macd_signal: float
    macd_histogram: float

    # Volatility / Sizing
    atr_14: float

    # Volume
    volume: int = Field(default=0, description="Current bar volume")
    volume_sma_20: float = Field(default=0, description="20-period average volume")

    # Earnings
    next_earnings_date: Optional[date] = None
    days_to_earnings: Optional[int] = None

    # Zero-Lag EMA (ZLEMA) — multi-timeframe trend signals
    zlema_fast: Optional[float] = Field(default=None, description="ZLEMA(8) — fast trend on 5-min candles")
    zlema_slow: Optional[float] = Field(default=None, description="ZLEMA(21) — slow trend on 5-min candles")
    zlema_trend: Optional[str] = Field(default=None, description="'bullish', 'bearish', or 'neutral' based on ZLEMA crossover")


class OHLCV(BaseModel):
    """Single candlestick bar."""
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int

