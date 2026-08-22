"""IBKR Market Data Provider — parallel to alpaca_data.py.

Fetches & caches historical equity bars and live options chains via
Interactive Brokers TWS/IB Gateway. Same public API shape as
alpaca_data.py so callers can be broker-swapped by import alone.

Requires TWS or IB Gateway running with API enabled — see ibkr_client.py.

Usage:
  from src.utils.ibkr_data import fetch_bars, get_0dte_chain

  df = fetch_bars("SPY", days_back=365, interval="5min")
  chain = get_0dte_chain("SPY", option_type="call", target_delta=0.50)
"""

from __future__ import annotations

import logging
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.utils.ibkr_client import get_ib, stock_contract

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data_cache"
_CACHE_DIR.mkdir(exist_ok=True)


# ── Historical equity bars ───────────────────────────────────────────────────

_INTERVAL_TO_IB = {
    "1min":  "1 min",
    "5min":  "5 mins",
    "15min": "15 mins",
    "1hour": "1 hour",
    "1day":  "1 day",
}
_INTERVAL_SECONDS = {"1min": 60, "5min": 300, "15min": 900, "1hour": 3600, "1day": 86400}


def _cache_path(symbol: str, interval: str, days_back: int) -> Path:
    return _CACHE_DIR / f"ibkr_{symbol}_{interval}_{days_back}d.parquet"


def _is_cache_fresh(path: Path, max_age_hours: float = 12) -> bool:
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return (datetime.now() - mtime) < timedelta(hours=max_age_hours)


def _drop_partial_trailing_bar(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Match alpaca_data: don't let an in-progress bar bias indicators."""
    if df.empty:
        return df
    bar_sec = _INTERVAL_SECONDS.get(interval)
    if not bar_sec:
        return df
    last_ts = df.index[-1]
    if last_ts.tz is None:
        last_ts = last_ts.tz_localize("UTC")
    age_sec = (datetime.now(timezone.utc) - last_ts.tz_convert("UTC").to_pydatetime()).total_seconds()
    if age_sec < bar_sec:
        return df.iloc[:-1]
    return df


def _duration_str(days_back: int) -> str:
    """IBKR expects duration like '365 D', '2 Y'. Max daily bar duration is
    50 years; 5-min max is ~365 days. Chunking not needed for typical use."""
    if days_back > 365:
        years = math.ceil(days_back / 365)
        return f"{years} Y"
    return f"{days_back} D"


def fetch_bars(
    symbol: str,
    days_back: int = 365,
    interval: str = "5min",
    force_refresh: bool = False,
    use_rth: bool = True,
) -> pd.DataFrame:
    """Fetch historical equity bars from IBKR with local Parquet caching.

    Args:
        symbol: Ticker (e.g. "SPY").
        days_back: Calendar days lookback.
        interval: "1min" | "5min" | "15min" | "1hour" | "1day".
        force_refresh: Skip cache and re-download.
        use_rth: Regular trading hours only. Match Alpaca's IEX behavior.

    Returns DataFrame [Open, High, Low, Close, Volume] indexed in
    America/New_York.
    """
    if interval not in _INTERVAL_TO_IB:
        raise ValueError(f"Unsupported interval {interval!r}; use one of {list(_INTERVAL_TO_IB)}")

    cache = _cache_path(symbol, interval, days_back)
    max_age_hours = 1 / 60 if days_back <= 5 else 12
    if not force_refresh and _is_cache_fresh(cache, max_age_hours=max_age_hours):
        try:
            df = pd.read_parquet(cache)
            df.index = pd.to_datetime(df.index)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            df.index = df.index.tz_convert("America/New_York")
            return df
        except Exception as e:
            logger.warning("Corrupt cache %s (%s) — refetching", cache, e)
            try:
                cache.unlink()
            except OSError:
                pass

    ib = get_ib()
    contract = stock_contract(symbol)
    ib.qualifyContracts(contract)

    logger.info("IBKR fetch %s %s over %s (RTH=%s) …", symbol, interval, _duration_str(days_back), use_rth)
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",  # now
        durationStr=_duration_str(days_back),
        barSizeSetting=_INTERVAL_TO_IB[interval],
        whatToShow="TRADES",
        useRTH=use_rth,
        formatDate=2,  # UTC epoch — timezone-safe
    )
    if not bars:
        raise ValueError(f"No IBKR data for {symbol} ({interval}, {days_back}d) — "
                         f"check symbol / market-data subscription")

    df = pd.DataFrame([{
        "Open": b.open, "High": b.high, "Low": b.low,
        "Close": b.close, "Volume": b.volume,
    } for b in bars], index=pd.to_datetime([b.date for b in bars]))

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("America/New_York")

    df = _drop_partial_trailing_bar(df, interval)

    # Atomic write, same pattern as alpaca_data.py
    tmp = cache.with_suffix(f".parquet.tmp.{os.getpid()}")
    try:
        df.to_parquet(tmp)
        os.replace(tmp, cache)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    logger.info("Cached %d %s bars for %s at %s", len(df), interval, symbol, cache)
    return df


def fetch_daily_bars(symbol: str, days_back: int = 365, force_refresh: bool = False) -> pd.DataFrame:
    return fetch_bars(symbol, days_back=days_back, interval="1day", force_refresh=force_refresh)


def clear_cache(symbol: str | None = None):
    for f in _CACHE_DIR.glob("ibkr_*.parquet"):
        if symbol is None or f.name.startswith(f"ibkr_{symbol}"):
            f.unlink()
            logger.info("Removed cache: %s", f)


def is_trading_day(d: datetime | None = None) -> bool:
    """Rough weekday check — IBKR calendar API is heavy; use pandas_market_calendars
    if available, else fall back to weekday."""
    dt = (d or datetime.now(timezone.utc)).date()
    try:
        import pandas_market_calendars as mcal
        nyse = mcal.get_calendar("NYSE")
        sched = nyse.schedule(start_date=str(dt), end_date=str(dt))
        return len(sched) > 0
    except Exception:
        return dt.weekday() < 5


# ── Live options chain (with greeks) ─────────────────────────────────────────

def _latest_stock_price(ib, symbol: str) -> float | None:
    """Snapshot the underlying spot via ib.reqMktData (needs market-data sub).

    Polls up to 4s for a real price to arrive — first-request market data can
    take a few seconds on delayed feed. Falls back to close price if
    marketPrice() returns NaN but the last-close tick has arrived.
    """
    try:
        c = stock_contract(symbol)
        ib.qualifyContracts(c)
        ticker = ib.reqMktData(c, "", False, False)
        # Poll up to 4s for a valid price (mid, last, or close).
        px = None
        for _ in range(8):  # 8 × 0.5s = 4s max
            ib.sleep(0.5)
            candidate = ticker.marketPrice()
            if candidate and not math.isnan(candidate):
                px = candidate
                break
            # fall back to last-close if streaming price hasn't landed
            if ticker.close and not math.isnan(ticker.close):
                px = float(ticker.close)
                break
        ib.cancelMktData(c)
        if px:
            return float(px)
    except Exception as e:
        logger.warning("Underlying spot fetch failed for %s: %s", symbol, e)
    return None


def _chain_for_expiration(ib, symbol: str, expiration: date, right: str,
                          exchange: str = "SMART",
                          spot: float | None = None,
                          strike_window_pct: float = 0.10) -> list:
    """Return qualified Option contracts for one expiration date.

    IBKR returns one SecDef entry per (exchange, trading_class) combo, and
    daily-expiration contracts often live under a DIFFERENT trading class
    from the weekly/monthly ones. Search every returned chain — pick the
    first one that actually lists the requested expiration, prefer SMART.

    Pre-filters the strike ladder to ±strike_window_pct around spot before
    qualifying. Full ladders can be 200+ strikes; most are deep ITM/OTM and
    qualifyContracts fails on strikes that don't exist for this expiration,
    returning contracts with conId=0 that must be filtered out anyway.
    """
    from ib_async import Option
    underlying = stock_contract(symbol)
    ib.qualifyContracts(underlying)
    chains = ib.reqSecDefOptParams(underlying.symbol, "", underlying.secType, underlying.conId)
    if not chains:
        return []

    exp_str = expiration.strftime("%Y%m%d")
    candidates = [c for c in chains if exp_str in c.expirations]
    if not candidates:
        return []
    chain = next((c for c in candidates if c.exchange == exchange), candidates[0])

    strikes = sorted(chain.strikes)
    # Pre-filter strikes to near-ATM window if we have a spot reference.
    if spot and spot > 0 and strike_window_pct > 0:
        lo, hi = spot * (1 - strike_window_pct), spot * (1 + strike_window_pct)
        strikes = [k for k in strikes if lo <= k <= hi]
    if not strikes:
        return []

    contracts = [
        Option(symbol=symbol, lastTradeDateOrContractMonth=exp_str,
               strike=k, right=right, exchange=chain.exchange, currency="USD",
               tradingClass=chain.tradingClass)
        for k in strikes
    ]
    qualified = ib.qualifyContracts(*contracts)
    # Drop contracts that failed to qualify (unknown strike on this expiry
    # → conId=0). Keeping them causes downstream AttributeError on .strike.
    return [c for c in qualified if c and getattr(c, "conId", 0)]


def _snapshot_chain(ib, contracts: list) -> dict:
    """Batch-snapshot bid/ask/greeks for a list of Option contracts.

    Adaptive polling: check every 0.5s for up to 8s and exit as soon as at
    least 60% of contracts have populated bid/ask. Beats blind sleeps —
    fast-quoting symbols (SPY) return in ~1-2s; slower ones (IWM) get the
    full window. Prevents the 2026-08-21 IWM issue where 5s wasn't enough
    for IWM options to populate.
    """
    tickers = [ib.reqMktData(c, "100,101,104,106", False, False) for c in contracts]
    if not tickers:
        return {}
    target_populated = max(1, int(len(tickers) * 0.6))
    for _ in range(16):  # 16 × 0.5s = 8s max
        ib.sleep(0.5)
        populated = sum(
            1 for t in tickers
            if t.bid and not math.isnan(t.bid) and t.ask and not math.isnan(t.ask)
        )
        if populated >= target_populated:
            # Give stragglers a final tick before snapshotting
            ib.sleep(0.5)
            break
    out: dict = {}
    for t in tickers:
        c = t.contract
        greeks = t.modelGreeks or t.bidGreeks or t.askGreeks or t.lastGreeks
        out[c.conId] = {
            "contract": c,
            "bid": float(t.bid) if t.bid and not math.isnan(t.bid) else 0.0,
            "ask": float(t.ask) if t.ask and not math.isnan(t.ask) else 0.0,
            "delta": abs(float(greeks.delta)) if greeks and greeks.delta is not None else 0.0,
            "gamma": float(greeks.gamma) if greeks and greeks.gamma is not None else 0.0,
            "theta": float(greeks.theta) if greeks and greeks.theta is not None else 0.0,
            "iv":    float(greeks.impliedVol) if greeks and greeks.impliedVol is not None else 0.0,
        }
    for t in tickers:
        try:
            ib.cancelMktData(t.contract)
        except Exception:
            pass
    return out


def _pick_by_delta(snaps: dict, target_delta: float, delta_tolerance: float,
                   spot: float | None, max_strike_pct: float = 0.02):
    """Delta-primary pick with strike-proximity fallback."""
    best, best_diff = None, float("inf")
    for _, s in snaps.items():
        strike = s["contract"].strike
        mid = (s["bid"] + s["ask"]) / 2 if s["bid"] > 0 and s["ask"] > 0 else 0.0
        if mid <= 0.01:
            continue
        if spot and spot > 0 and abs(strike - spot) / spot > max_strike_pct:
            continue
        d_diff = abs(s["delta"] - target_delta)
        if d_diff < best_diff and d_diff <= delta_tolerance:
            best, best_diff = s, d_diff

    if best is not None:
        return best, "delta"

    # Fallback: closest strike to spot with a real quote
    if not spot or spot <= 0:
        return None, "no-spot"
    best, best_diff = None, float("inf")
    for _, s in snaps.items():
        strike = s["contract"].strike
        mid = (s["bid"] + s["ask"]) / 2 if s["bid"] > 0 and s["ask"] > 0 else 0.0
        if mid <= 0.01:
            continue
        if abs(strike - spot) / spot > max_strike_pct:
            continue
        d = abs(strike - spot)
        if d < best_diff:
            best, best_diff = s, d
    return best, ("proximity" if best else "no-quote")


def _to_chain_dict(s: dict, symbol: str, option_type: str) -> dict:
    c = s["contract"]
    bid, ask = s["bid"], s["ask"]
    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
    return {
        "occ_symbol": c.localSymbol.replace(" ", "") if c.localSymbol else "",
        "underlying": symbol,
        "strike": float(c.strike),
        "expiration": c.lastTradeDateOrContractMonth,
        "option_type": option_type,
        "delta": s["delta"],
        "gamma": s["gamma"],
        "theta": s["theta"],
        "iv":    s["iv"],
        "bid":   bid,
        "ask":   ask,
        "mid":   mid,
    }


def get_0dte_chain(
    symbol: str,
    option_type: str = "call",
    target_delta: float = 0.50,
    delta_tolerance: float = 0.15,
    spot_price: float | None = None,
) -> dict | None:
    """Pick today's ATM contract closest to target_delta. Mirrors alpaca_data.get_0dte_chain."""
    return get_dte_chain(symbol, dte=0, option_type=option_type,
                         target_delta=target_delta, delta_tolerance=delta_tolerance,
                         spot_price=spot_price)


def get_dte_chain(
    symbol: str,
    dte: int = 0,
    option_type: str = "call",
    target_delta: float = 0.50,
    delta_tolerance: float = 0.15,
    spot_price: float | None = None,
) -> dict | None:
    """Pick contract expiring `today + dte business days`."""
    ib = get_ib()
    right = "C" if option_type == "call" else "P"
    spot = spot_price if (spot_price and spot_price > 0) else _latest_stock_price(ib, symbol)

    # Find the Nth listed expiration on/after today (skips holidays via probing).
    today = date.today()
    underlying = stock_contract(symbol)
    ib.qualifyContracts(underlying)
    chains = ib.reqSecDefOptParams(underlying.symbol, "", underlying.secType, underlying.conId)
    if not chains:
        logger.warning("No option chains listed for %s", symbol)
        return None
    # Aggregate expirations across ALL returned chains (SMART / AMEX / CBOE
    # etc., and every trading class). Daily-expiration contracts often live
    # under a different trading class from weekly ones — searching just one
    # chain (previous behavior) misses today's expiry for IWM/DIA and any
    # symbol with per-class listings.
    exp_set: set[date] = set()
    for c in chains:
        for e in c.expirations:
            try:
                exp_set.add(date(int(e[:4]), int(e[4:6]), int(e[6:8])))
            except (ValueError, TypeError):
                continue
    listed_exps = sorted(exp_set)

    if dte == 0:
        target_exp = next((e for e in listed_exps if e == today), None)
    else:
        forward = [e for e in listed_exps if e > today]
        target_exp = forward[dte - 1] if len(forward) >= dte else None

    if target_exp is None:
        logger.warning("No %dDTE %s expiration for %s (today=%s)", dte, option_type, symbol, today)
        return None

    contracts = _chain_for_expiration(ib, symbol, target_exp, right, spot=spot)
    if not contracts:
        return None

    # Tighten further to ±5% of spot for the actual market-data snapshot.
    # _chain_for_expiration already pre-filtered to ±10%.
    if spot:
        contracts = [c for c in contracts if abs(c.strike - spot) / spot <= 0.05]
    if not contracts:
        return None

    snaps = _snapshot_chain(ib, contracts)
    best, source = _pick_by_delta(snaps, target_delta, delta_tolerance, spot)
    if not best:
        logger.warning("No suitable %dDTE %s contract for %s (source=%s)", dte, option_type, symbol, source)
        return None

    result = _to_chain_dict(best, symbol, option_type)
    logger.info(
        "IBKR selected %dDTE %s (%s): %s strike=$%.2f delta=%.2f mid=$%.2f",
        dte, option_type, source, result["occ_symbol"], result["strike"],
        result["delta"], result["mid"],
    )
    return result
