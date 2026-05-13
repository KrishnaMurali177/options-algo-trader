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
from datetime import datetime, timedelta, timezone
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


_INTERVAL_SECONDS = {"5min": 300, "15min": 900, "1hour": 3600, "1day": 86400}


def _drop_partial_trailing_bar(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Drop the trailing bar if it has not yet fully closed.

    Alpaca timestamps bars at their *open*, so a 5-min bar starting at 12:20
    ET is still forming until 12:25 ET. Using a partial bar's Close as the
    terminal value of every indicator (RSI/MACD/ZLEMA/BB/ATR/VWAP) systematically
    biases the live agent's quality score upward relative to the replay's
    closed-bar view.
    """
    if df.empty:
        return df
    bar_sec = _INTERVAL_SECONDS.get(interval)
    if not bar_sec:
        return df
    last_ts = df.index[-1]
    if last_ts.tz is None:
        last_ts = last_ts.tz_localize("UTC")
    now_utc = datetime.now(timezone.utc)
    age_sec = (now_utc - last_ts.tz_convert("UTC").to_pydatetime()).total_seconds()
    if age_sec < bar_sec:
        logger.debug(
            "Dropping partial trailing %s bar (open=%s, age=%.0fs < %ds)",
            interval, last_ts.isoformat(), age_sec, bar_sec,
        )
        return df.iloc[:-1]
    return df


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
        Index: DatetimeIndex in America/New_York timezone.
    """
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    cache = _cache_path(symbol, interval, days_back)

    # Short windows (≤5d) are live-dashboard reads — tighten TTL to ~1 min so the
    # last bar reflects the current session. Longer windows are for backtesting
    # and can stay at the 12h default.
    max_age_hours = 1 / 60 if days_back <= 5 else 12

    if not force_refresh and _is_cache_fresh(cache, max_age_hours=max_age_hours):
        logger.info("Loading cached %s %s data (%d days) from %s", symbol, interval, days_back, cache)
        df = pd.read_parquet(cache)
        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert("America/New_York")
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

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)

    logger.info(
        "Fetching %s %s bars from Alpaca: %s → %s …",
        symbol, interval, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
    )

    client = _get_client()

    # Alpaca may return a lot of data; fetch in one request (they handle pagination)
    # Free Alpaca subscriptions can't query recent SIP data; IEX is the
    # free real-time feed. Volume reflects IEX's slice only, but price
    # levels are accurate for liquid names like SPY.
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=DataFeed.IEX,
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
    df.index = df.index.tz_convert("America/New_York")

    # Drop trailing partial bar: Alpaca returns the in-progress bar (its
    # timestamp is the bar's *open*, so a 12:20 5-min bar is still forming
    # until 12:25). Indicators built off a partial close drift by ~1 quality
    # point and skew the live agent's gates vs. the replay's closed-bar view.
    df = _drop_partial_trailing_bar(df, interval)

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


def get_0dte_chain(
    symbol: str,
    option_type: str = "call",
    target_delta: float = 0.50,
    delta_tolerance: float = 0.15,
    spot_price: float | None = None,
) -> dict | None:
    """Fetch today's 0DTE options chain and pick the contract closest to target_delta.

    Args:
        symbol: Underlying ticker (e.g., "SPY").
        option_type: "call" or "put".
        target_delta: Desired delta magnitude (default 0.50 = ATM).
        delta_tolerance: Acceptable delta range around target.
        spot_price: Underlying spot. Used as ATM proxy when greeks are unavailable.
            Fetched via Alpaca latest-trade if not provided.

    Returns:
        Dict with contract details (occ_symbol, strike, expiration, delta, bid, ask, mid,
        greeks) or None if no suitable contract found.
    """
    from alpaca.data.historical import OptionHistoricalDataClient
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOptionContractsRequest
    from dotenv import load_dotenv
    from datetime import date

    load_dotenv()

    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")

    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")

    trading_client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)
    today = date.today()

    # Fetch 0DTE contracts expiring today. Alpaca paginates at 100 contracts/page;
    # SPY has ~200+ strikes so without paging we'd only see the lowest strikes (deep ITM).
    try:
        contracts = []
        page_token = None
        while True:
            req = GetOptionContractsRequest(
                underlying_symbols=[symbol],
                expiration_date=today,
                type=option_type,
                status="active",
                page_token=page_token,
            )
            contracts_resp = trading_client.get_option_contracts(req)
            if not contracts_resp:
                break
            contracts.extend(contracts_resp.option_contracts or [])
            page_token = getattr(contracts_resp, "next_page_token", None)
            if not page_token:
                break
    except Exception as e:
        logger.error("Failed to fetch 0DTE chain for %s: %s", symbol, e)
        return None

    if not contracts:
        logger.warning("No 0DTE %s contracts found for %s expiring %s", option_type, symbol, today)
        return None

    # Get snapshots for greeks (via options data client). Alpaca caps per-request
    # symbol count at 100; batch in chunks so we cover the full chain.
    snapshots = {}
    try:
        option_client = OptionHistoricalDataClient(api_key=api_key, secret_key=secret_key)
        occ_symbols = [c.symbol for c in contracts]
        from alpaca.data.requests import OptionSnapshotRequest
        for i in range(0, len(occ_symbols), 100):
            batch = occ_symbols[i:i + 100]
            batch_snaps = option_client.get_option_snapshot(
                OptionSnapshotRequest(symbol_or_symbols=batch)
            )
            if batch_snaps:
                snapshots.update(batch_snaps)
    except Exception as e:
        logger.warning("Failed to fetch option snapshots: %s — using strike proximity only", e)

    # ── Resolve spot price for strike sanity check ──
    _spot = spot_price
    if _spot is None or _spot <= 0:
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestTradeRequest
            stock_client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
            trade_resp = stock_client.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=symbol)
            )
            if symbol in trade_resp:
                _spot = float(trade_resp[symbol].price)
        except Exception:
            pass

    # Max strike distance from spot (percentage) to prevent deep ITM/OTM picks
    MAX_STRIKE_PCT_FROM_SPOT = 0.02  # 2% — for SPY ~$737, that's ~$15

    best = None
    best_delta_diff = float("inf")

    for contract in contracts:
        occ = contract.symbol
        snap = snapshots.get(occ)

        if snap and snap.greeks:
            delta = abs(snap.greeks.delta) if snap.greeks.delta else 0.0
            iv = snap.greeks.implied_volatility or 0.0
            gamma = snap.greeks.gamma or 0.0
            theta = snap.greeks.theta or 0.0
        else:
            delta = 0.0
            iv = gamma = theta = 0.0

        bid = float(snap.latest_quote.bid_price) if snap and snap.latest_quote else 0.0
        ask = float(snap.latest_quote.ask_price) if snap and snap.latest_quote else 0.0
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else 0.0

        # Skip illiquid contracts
        if mid <= 0.01:
            continue

        # Strike sanity check: reject contracts too far from spot (prevents deep ITM picks
        # when greeks are stale/wrong on paper feed)
        strike = float(contract.strike_price)
        if _spot and _spot > 0:
            if abs(strike - _spot) / _spot > MAX_STRIKE_PCT_FROM_SPOT:
                continue

        delta_diff = abs(delta - target_delta)
        if delta_diff < best_delta_diff and delta_diff <= delta_tolerance:
            best_delta_diff = delta_diff
            best = {
                "occ_symbol": occ,
                "underlying": symbol,
                "strike": float(contract.strike_price),
                "expiration": str(contract.expiration_date),
                "option_type": option_type,
                "delta": delta,
                "gamma": gamma,
                "theta": theta,
                "iv": iv,
                "bid": bid,
                "ask": ask,
                "mid": mid,
            }

    if best:
        logger.info(
            "Selected 0DTE %s: %s strike=$%.2f delta=%.2f mid=$%.2f",
            option_type, best["occ_symbol"], best["strike"], best["delta"], best["mid"],
        )
        return best

    # ── Fallback: greeks unavailable on paper feed → pick by strike proximity to spot.
    # Paper feed often returns zero quotes for ATM 0DTE strikes while still quoting deep-ITM
    # (intrinsic value as a floor), so the previous mid>0.01 filter pushed us into deep-ITM.
    # Now: pick the closest-strike contract from the full chain, bound to MAX_STRIKE_PCT_FROM_SPOT,
    # and synthesize a mid from intrinsic + minimal time-value when quotes are missing.
    if not _spot or _spot <= 0:
        logger.warning("No 0DTE %s contract near delta %.2f for %s (no spot for fallback)",
                       option_type, target_delta, symbol)
        return None

    best_strike_diff = float("inf")
    best_contract = None
    best_snap = None
    for contract in contracts:
        strike = float(contract.strike_price)
        if abs(strike - _spot) / _spot > MAX_STRIKE_PCT_FROM_SPOT:
            continue
        strike_diff = abs(strike - _spot)
        if strike_diff < best_strike_diff:
            best_strike_diff = strike_diff
            best_contract = contract
            best_snap = snapshots.get(contract.symbol)

    if best_contract is None:
        logger.warning("No 0DTE %s contract within %.0f%% of spot $%.2f for %s",
                       option_type, MAX_STRIKE_PCT_FROM_SPOT * 100, _spot, symbol)
        return None

    strike = float(best_contract.strike_price)
    bid = float(best_snap.latest_quote.bid_price) if best_snap and best_snap.latest_quote else 0.0
    ask = float(best_snap.latest_quote.ask_price) if best_snap and best_snap.latest_quote else 0.0
    mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else 0.0

    # If paper feed returns no quote on the closest-strike ATM contract, refuse the trade
    # rather than fabricate a premium. Downstream risk/theta math is keyed on entry_premium,
    # so a synthesized mid would corrupt stops and theta-exit thresholds on live fills.
    if mid <= 0.01:
        logger.warning(
            "0DTE %s closest-strike contract %s (strike=$%.2f, spot=$%.2f) has no quote — "
            "skipping trade rather than trading on synthesized premium",
            option_type, best_contract.symbol, strike, _spot,
        )
        return None

    best = {
        "occ_symbol": best_contract.symbol,
        "underlying": symbol,
        "strike": strike,
        "expiration": str(best_contract.expiration_date),
        "option_type": option_type,
        "delta": 0.50,  # ATM proxy
        "gamma": 0.0,
        "theta": 0.0,
        "iv": 0.0,
        "bid": bid,
        "ask": ask,
        "mid": mid,
    }

    logger.info(
        "Selected 0DTE %s by strike proximity (greeks unavailable): %s strike=$%.2f spot=$%.2f mid=$%.2f",
        option_type, best["occ_symbol"], best["strike"], _spot, best["mid"],
    )
    return best
