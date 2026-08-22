"""IBKR historical option contract lookup + bar fetching — parallel to alpaca_options.py.

Used by the replay scripts to back triggers with REAL option prices from
Interactive Brokers instead of Alpaca / the delta-gamma synthesizer.

Two layers of cache (matches Alpaca side):
  - Listings:  data_cache/ibkr_opt_list_{SYMBOL}_{YYYY-MM-DD}_{call|put}.json
  - Bars:      data_cache/ibkr_opt_bars_{OCC}_{interval}.parquet

Public API mirrors alpaca_options.py so replay_sweet_spot.py can swap
brokers by import:
  list_contracts(symbol, exp_date, option_type)
  resolve_atm_0dte(symbol, trade_date, option_type, spot)  -> OCC
  resolve_atm_dte(symbol, trade_date, dte, option_type, spot) -> OCC
  resolve_atm_dated(symbol, trade_date, option_type, spot, target_dte, dte_window)
    -> (OCC, expiration_date)
  list_expirations_in_window(symbol, option_type, window_start, window_end) -> list[date]
  fetch_option_bars(occ, start, end, interval="5min") -> DataFrame
  option_close_at(bars, ts) -> float or None
  fetch_intraday_option_bars(occ, trade_date, interval="5min") -> DataFrame

Notes:
  - IBKR historical option bars require OPRA data subscription in TWS.
  - "0DTE" here = expiration == trade_date; if that expiration isn't
    listed for the symbol on that date, resolve_atm_0dte returns None.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.utils.ibkr_client import (
    format_occ,
    get_ib,
    occ_to_option,
    stock_contract,
)

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data_cache"
_CACHE_DIR.mkdir(exist_ok=True)


# ── Contract listings ────────────────────────────────────────────────────────

def _listing_cache_path(symbol: str, exp: date, option_type: str) -> Path:
    return _CACHE_DIR / f"ibkr_opt_list_{symbol}_{exp.isoformat()}_{option_type}.json"


def _exp_cache_path(symbol: str, start: date, end: date, option_type: str) -> Path:
    return _CACHE_DIR / f"ibkr_opt_exps_{symbol}_{start.isoformat()}_{end.isoformat()}_{option_type}.json"


def _chain_params(ib, symbol: str, exchange: str = "SMART"):
    underlying = stock_contract(symbol)
    ib.qualifyContracts(underlying)
    chains = ib.reqSecDefOptParams(underlying.symbol, "", underlying.secType, underlying.conId)
    if not chains:
        return None, None
    chain = next((c for c in chains if c.exchange == exchange), chains[0])
    return underlying, chain


def list_contracts(symbol: str, exp_date: date, option_type: str) -> list[dict]:
    """List all option contracts for a given expiration date.

    Returns [{"symbol": OCC-compressed, "strike": float}]. Cached as JSON.
    """
    cache = _listing_cache_path(symbol, exp_date, option_type)
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except Exception:
            cache.unlink(missing_ok=True)

    ib = get_ib()
    _, chain = _chain_params(ib, symbol)
    if not chain:
        return []
    exp_str = exp_date.strftime("%Y%m%d")
    if exp_str not in chain.expirations:
        cache.write_text("[]")
        return []
    right = "C" if option_type == "call" else "P"
    strikes = sorted(chain.strikes)
    out = [
        {"symbol": format_occ(symbol, exp_date, right, k), "strike": float(k)}
        for k in strikes
    ]
    cache.write_text(json.dumps(out))
    return out


def resolve_atm_0dte(symbol: str, trade_date: date, option_type: str, spot: float) -> str | None:
    """OCC of the closest-to-spot contract expiring on trade_date."""
    contracts = list_contracts(symbol, trade_date, option_type)
    if not contracts:
        return None
    best = min(contracts, key=lambda c: abs(c["strike"] - spot))
    return best["symbol"]


def resolve_atm_dte(symbol: str, trade_date: date, dte: int, option_type: str, spot: float) -> str | None:
    """OCC of the ATM contract at trade_date + dte business days."""
    if dte == 0:
        return resolve_atm_0dte(symbol, trade_date, option_type, spot)
    exp_date = (pd.Timestamp(trade_date) + pd.tseries.offsets.BDay(dte)).date()
    contracts = list_contracts(symbol, exp_date, option_type)
    if not contracts:
        return None
    best = min(contracts, key=lambda c: abs(c["strike"] - spot))
    return best["symbol"]


def list_expirations_in_window(
    symbol: str, option_type: str, window_start: date, window_end: date
) -> list[date]:
    """Distinct expirations available in [window_start, window_end]. Cached."""
    cache = _exp_cache_path(symbol, window_start, window_end, option_type)
    if cache.exists():
        try:
            return sorted({date.fromisoformat(s) for s in json.loads(cache.read_text())})
        except Exception:
            cache.unlink(missing_ok=True)

    ib = get_ib()
    _, chain = _chain_params(ib, symbol)
    if not chain:
        return []
    exps: list[date] = []
    for e in chain.expirations:
        try:
            d = date(int(e[:4]), int(e[4:6]), int(e[6:8]))
        except Exception:
            continue
        if window_start <= d <= window_end:
            exps.append(d)
    exps.sort()
    cache.write_text(json.dumps([d.isoformat() for d in exps]))
    return exps


def resolve_atm_dated(
    symbol: str,
    trade_date: date,
    option_type: str,
    spot: float,
    target_dte: int = 90,
    dte_window: int = 14,
) -> tuple[str, date] | None:
    """ATM contract with expiration closest to trade_date + target_dte."""
    target_exp = trade_date + timedelta(days=target_dte)
    window_start = target_exp - timedelta(days=dte_window)
    window_end = target_exp + timedelta(days=dte_window)
    exps = list_expirations_in_window(symbol, option_type, window_start, window_end)
    if not exps:
        return None
    best_exp = min(exps, key=lambda d: abs((d - target_exp).days))
    contracts = list_contracts(symbol, best_exp, option_type)
    if not contracts:
        return None
    best = min(contracts, key=lambda c: abs(c["strike"] - spot))
    return best["symbol"], best_exp


# ── Bars ─────────────────────────────────────────────────────────────────────

_INTERVAL_TO_IB = {
    "1min":  "1 min",
    "5min":  "5 mins",
    "15min": "15 mins",
    "1hour": "1 hour",
    "1day":  "1 day",
}


def _bars_cache_path(occ: str, interval: str) -> Path:
    return _CACHE_DIR / f"ibkr_opt_bars_{occ}_{interval}.parquet"


def _duration_between(start: datetime, end: datetime) -> str:
    """IBKR duration string covering [start, end]."""
    days = max(1, math.ceil((end - start).total_seconds() / 86400.0))
    if days > 365:
        return f"{math.ceil(days / 365)} Y"
    return f"{days} D"


def fetch_option_bars(
    occ: str,
    start: datetime,
    end: datetime,
    interval: str = "5min",
) -> pd.DataFrame:
    """Fetch historical bars for a single option contract. Cached as Parquet.

    Returns DataFrame indexed by America/New_York timestamps with
    [Open, High, Low, Close, Volume]. Empty frame if IBKR returns nothing.
    """
    if interval not in _INTERVAL_TO_IB:
        raise ValueError(f"Unsupported interval {interval!r}")

    cache = _bars_cache_path(occ, interval)
    if cache.exists():
        try:
            df = pd.read_parquet(cache)
            df.index = pd.to_datetime(df.index)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            df.index = df.index.tz_convert("America/New_York")
            return df
        except Exception:
            cache.unlink(missing_ok=True)

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    ib = get_ib()
    try:
        contract = occ_to_option(occ)
        ib.qualifyContracts(contract)
    except Exception as e:
        logger.warning("occ_to_option failed for %s: %s", occ, e)
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        empty.to_parquet(cache)
        return empty

    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime=end.astimezone(timezone.utc).strftime("%Y%m%d-%H:%M:%S"),
            durationStr=_duration_between(start, end),
            barSizeSetting=_INTERVAL_TO_IB[interval],
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,
        )
    except Exception as e:
        logger.warning("Option bars fetch failed for %s: %s", occ, e)
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        empty.to_parquet(cache)
        return empty

    if not bars:
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        empty.to_parquet(cache)
        return empty

    df = pd.DataFrame([{
        "Open": b.open, "High": b.high, "Low": b.low,
        "Close": b.close, "Volume": b.volume,
    } for b in bars], index=pd.to_datetime([b.date for b in bars]))

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("America/New_York")

    df.to_parquet(cache)
    return df


def option_close_at(bars: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    """Close at-or-just-before `ts`. None if nothing before ts."""
    if bars.empty:
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("America/New_York")
    elif str(ts.tz) != "America/New_York":
        ts = ts.tz_convert("America/New_York")
    sub = bars[bars.index <= ts]
    if sub.empty:
        return None
    return float(sub["Close"].iloc[-1])


def fetch_intraday_option_bars(
    occ: str,
    trade_date: date,
    interval: str = "5min",
) -> pd.DataFrame:
    """One trading day's worth of option bars (09:30-16:00 ET + buffer)."""
    eastern = pd.Timestamp(trade_date, tz="America/New_York")
    start = (eastern + pd.Timedelta(hours=9, minutes=0)).tz_convert("UTC").to_pydatetime()
    end = (eastern + pd.Timedelta(hours=16, minutes=30)).tz_convert("UTC").to_pydatetime()
    return fetch_option_bars(occ, start, end, interval=interval)
