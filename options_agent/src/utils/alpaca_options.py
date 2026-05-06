"""Historical 0DTE option contract lookup + 5-min bar fetching from Alpaca.

Used by the replay scripts to back triggers with REAL option prices instead of
the delta/gamma/theta synthesizer in replay_sweet_spot.py.

Two layers of cache:
  - Listings:  data_cache/opt_list_{SYMBOL}_{YYYY-MM-DD}_{call|put}.json
  - Bars:      data_cache/opt_bars_{OCC}_5min.parquet

Public API:
  resolve_atm_0dte(symbol, trade_date, option_type, spot)  -> OCC symbol or None
  fetch_option_bars(occ, start, end, interval="5min")      -> DataFrame (US/Eastern)
  option_close_at(bars, ts)                                -> float or None

Notes:
  - Alpaca historical option data starts ~Feb 2024.
  - Free tier returns delayed quotes/snapshots, but historical BARS are full.
  - "0DTE" here = expiration == trade_date. If no 0DTE list exists for the
    symbol on that date (e.g. NVDA on a non-expiry day), resolve_atm_0dte
    returns None and the caller should fall back.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data_cache"
_CACHE_DIR.mkdir(exist_ok=True)


def _get_keys() -> tuple[str, str]:
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set in .env")
    return api_key, secret_key


def _trading_client():
    from alpaca.trading.client import TradingClient
    api_key, secret_key = _get_keys()
    return TradingClient(api_key=api_key, secret_key=secret_key, paper=True)


def _option_data_client():
    from alpaca.data.historical import OptionHistoricalDataClient
    api_key, secret_key = _get_keys()
    return OptionHistoricalDataClient(api_key=api_key, secret_key=secret_key)


# ── Contract listings ────────────────────────────────────────────────────────

def _listing_cache_path(symbol: str, exp: date, option_type: str) -> Path:
    return _CACHE_DIR / f"opt_list_{symbol}_{exp.isoformat()}_{option_type}.json"


def list_contracts(symbol: str, exp_date: date, option_type: str) -> list[dict]:
    """List all option contracts (active + inactive) for a given expiration.

    Returns a list of {"symbol": OCC, "strike": float} dicts.
    Cached as JSON — listings for past dates never change.
    """
    cache = _listing_cache_path(symbol, exp_date, option_type)
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except Exception:
            cache.unlink(missing_ok=True)

    from alpaca.trading.enums import AssetStatus, ContractType
    from alpaca.trading.requests import GetOptionContractsRequest

    client = _trading_client()
    ctype = ContractType.CALL if option_type == "call" else ContractType.PUT
    contracts: dict[str, float] = {}

    # Active first (live contracts on dates near today), then inactive (expired).
    for status in (AssetStatus.ACTIVE, AssetStatus.INACTIVE):
        page_token = None
        while True:
            req = GetOptionContractsRequest(
                underlying_symbols=[symbol],
                expiration_date=exp_date,
                type=ctype,
                status=status,
                limit=10000,
                page_token=page_token,
            )
            try:
                resp = client.get_option_contracts(req)
            except Exception as e:
                logger.warning("Listing %s %s %s (status=%s) failed: %s",
                               symbol, exp_date, option_type, status, e)
                break
            for c in (resp.option_contracts or []):
                try:
                    contracts[c.symbol] = float(c.strike_price)
                except Exception:
                    continue
            page_token = getattr(resp, "next_page_token", None)
            if not page_token:
                break

    out = [{"symbol": occ, "strike": k} for occ, k in sorted(contracts.items(), key=lambda x: x[1])]
    cache.write_text(json.dumps(out))
    return out


def resolve_atm_0dte(symbol: str, trade_date: date, option_type: str, spot: float) -> str | None:
    """Return the OCC symbol of the contract whose strike is closest to spot,
    expiring on `trade_date` (i.e. 0DTE on that date). None if no listing exists.
    """
    contracts = list_contracts(symbol, trade_date, option_type)
    if not contracts:
        return None
    best = min(contracts, key=lambda c: abs(c["strike"] - spot))
    return best["symbol"]


# ── Bars ─────────────────────────────────────────────────────────────────────

def _bars_cache_path(occ: str, interval: str) -> Path:
    return _CACHE_DIR / f"opt_bars_{occ}_{interval}.parquet"


def fetch_option_bars(
    occ: str,
    start: datetime,
    end: datetime,
    interval: str = "5min",
) -> pd.DataFrame:
    """Fetch historical bars for a single option contract. Cached as parquet.

    Returns DataFrame indexed by US/Eastern timestamps with columns
    [Open, High, Low, Close, Volume, VWAP] (subset that's available).
    Empty frame if Alpaca returns nothing.
    """
    cache = _bars_cache_path(occ, interval)
    if cache.exists():
        try:
            df = pd.read_parquet(cache)
            df.index = pd.to_datetime(df.index)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            df.index = df.index.tz_convert("US/Eastern")
            return df
        except Exception:
            cache.unlink(missing_ok=True)

    from alpaca.data.requests import OptionBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    tf_map = {
        "1min": TimeFrame(1, TimeFrameUnit.Minute),
        "5min": TimeFrame(5, TimeFrameUnit.Minute),
        "15min": TimeFrame(15, TimeFrameUnit.Minute),
        "1hour": TimeFrame(1, TimeFrameUnit.Hour),
        "1day": TimeFrame(1, TimeFrameUnit.Day),
    }
    timeframe = tf_map.get(interval)
    if timeframe is None:
        raise ValueError(f"Unsupported interval: {interval}")

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    client = _option_data_client()
    req = OptionBarsRequest(
        symbol_or_symbols=occ,
        timeframe=timeframe,
        start=start,
        end=end,
    )
    try:
        bars = client.get_option_bars(req)
    except Exception as e:
        logger.warning("Option bars fetch failed for %s: %s", occ, e)
        # Cache an empty frame so we don't retry every replay.
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "VWAP"])
        empty.to_parquet(cache)
        return empty

    df = bars.df
    if df is None or df.empty:
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "VWAP"])
        empty.to_parquet(cache)
        return empty

    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel("symbol")

    col_map = {"open": "Open", "high": "High", "low": "Low",
               "close": "Close", "volume": "Volume",
               "trade_count": "TradeCount", "vwap": "VWAP"}
    df = df.rename(columns=col_map)
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume", "VWAP"] if c in df.columns]
    df = df[keep]

    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("US/Eastern")

    df.to_parquet(cache)
    return df


def option_close_at(bars: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    """Return option close price at-or-just-before `ts`. None if no bars before ts."""
    if bars.empty:
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("US/Eastern")
    elif str(ts.tz) != "US/Eastern":
        ts = ts.tz_convert("US/Eastern")
    sub = bars[bars.index <= ts]
    if sub.empty:
        return None
    return float(sub["Close"].iloc[-1])


def fetch_intraday_option_bars(
    occ: str,
    trade_date: date,
    interval: str = "5min",
) -> pd.DataFrame:
    """Convenience: fetch a full trading day's worth of option bars in one call.

    Spans 09:30 ET → 16:00 ET converted to UTC, with a small buffer.
    """
    eastern = pd.Timestamp(trade_date, tz="US/Eastern")
    start = (eastern + pd.Timedelta(hours=9, minutes=0)).tz_convert("UTC").to_pydatetime()
    end = (eastern + pd.Timedelta(hours=16, minutes=30)).tz_convert("UTC").to_pydatetime()
    return fetch_option_bars(occ, start, end, interval=interval)
