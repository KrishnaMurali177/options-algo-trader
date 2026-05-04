"""Alpaca Market Data Provider — fetch & cache historical 5-min bars.

Provides 1+ year of 5-minute OHLCV data for backtesting, replacing
yfinance's 60-day limit. Data is cached locally as Parquet files
so subsequent runs don't re-download.

Setup:
  1. Sign up for a free Alpaca paper trading account at https://alpaca.markets
  2. Get API key & secret from the dashboard
  3. Add to your .env file:
       ALPACA_API_KEY=your_key_here
       ALPACA_SECRET_KEY=your_secret_here

Usage:
  from src.utils.alpaca_data import fetch_bars, fetch_daily_bars

  df_5m = fetch_bars("SPY", days_back=365, interval="5min")
  df_1d = fetch_daily_bars("SPY", days_back=365)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ── Cache directory ──
_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data_cache"
_CACHE_DIR.mkdir(exist_ok=True)


def _get_client():
    """Create Alpaca StockHistoricalDataClient (lazy, requires API keys)."""
    from alpaca.data.historical import StockHistoricalDataClient
    from dotenv import load_dotenv

    load_dotenv()

    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")

    if not api_key or not secret_key:
        raise RuntimeError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in your .env file. "
            "Sign up for a free paper trading account at https://alpaca.markets "
            "and add your keys to options_agent/.env"
        )

    # Data API works the same for paper and live keys
    return StockHistoricalDataClient(api_key=api_key, secret_key=secret_key, raw_data=False)


def _cache_path(symbol: str, interval: str, days_back: int) -> Path:
    """Return path for the cached Parquet file."""
    return _CACHE_DIR / f"{symbol}_{interval}_{days_back}d.parquet"


def _is_cache_fresh(path: Path, max_age_hours: int = 12) -> bool:
    """Check if cache file exists and is less than max_age_hours old."""
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return (datetime.now() - mtime) < timedelta(hours=max_age_hours)


def fetch_bars(
    symbol: str,
    days_back: int = 365,
    interval: str = "5min",
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Fetch historical bars from Alpaca with local Parquet caching.

    Args:
        symbol: Ticker symbol (e.g., "SPY").
        days_back: Number of calendar days to look back.
        interval: Bar interval — "5min", "15min", "1hour", "1day".
        force_refresh: If True, skip cache and re-download.

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume
        Index: DatetimeIndex in US/Eastern timezone.
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    cache = _cache_path(symbol, interval, days_back)

    if not force_refresh and _is_cache_fresh(cache):
        logger.info("Loading cached %s %s data (%d days) from %s", symbol, interval, days_back, cache)
        df = pd.read_parquet(cache)
        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert("US/Eastern")
        return df

    # Map interval string to Alpaca TimeFrame
    tf_map = {
        "5min": TimeFrame(5, TimeFrameUnit.Minute),
        "15min": TimeFrame(15, TimeFrameUnit.Minute),
        "1hour": TimeFrame(1, TimeFrameUnit.Hour),
        "1day": TimeFrame(1, TimeFrameUnit.Day),
    }
    timeframe = tf_map.get(interval)
    if timeframe is None:
        raise ValueError(f"Unsupported interval: {interval}. Use: {list(tf_map)}")

    end = datetime.now()
    start = end - timedelta(days=days_back)

    logger.info(
        "Fetching %s %s bars from Alpaca: %s → %s …",
        symbol, interval, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
    )

    client = _get_client()

    # Alpaca may return a lot of data; fetch in one request (they handle pagination)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
    )
    bars = client.get_stock_bars(request)
    df = bars.df

    if df.empty:
        raise ValueError(f"No Alpaca data returned for {symbol} ({interval}, {days_back}d)")

    # Alpaca returns MultiIndex (symbol, timestamp) — drop symbol level
    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel("symbol")

    # Normalize column names to match yfinance convention
    col_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        "trade_count": "TradeCount",
        "vwap": "VWAP",
    }
    df = df.rename(columns=col_map)

    # Keep only OHLCV + VWAP
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume", "VWAP"] if c in df.columns]
    df = df[keep]

    # Ensure timezone
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("US/Eastern")

    # Cache to Parquet
    df.to_parquet(cache)
    logger.info(
        "Cached %d %s bars for %s (%s → %s) at %s",
        len(df), interval, symbol,
        df.index[0].strftime("%Y-%m-%d"),
        df.index[-1].strftime("%Y-%m-%d"),
        cache,
    )

    return df


def fetch_daily_bars(
    symbol: str,
    days_back: int = 365,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Convenience wrapper to fetch daily bars."""
    return fetch_bars(symbol, days_back=days_back, interval="1day", force_refresh=force_refresh)


def clear_cache(symbol: str | None = None):
    """Remove cached data files. If symbol is None, clear all."""
    for f in _CACHE_DIR.glob("*.parquet"):
        if symbol is None or f.name.startswith(symbol):
            f.unlink()
            logger.info("Removed cache: %s", f)


