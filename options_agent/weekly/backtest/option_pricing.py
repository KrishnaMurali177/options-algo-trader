"""Weekly option pricing — real Alpaca bars + synthetic delta-gamma-theta fallback.

Real path: wraps list_contracts() / fetch_option_bars() from alpaca_options.py
for weekly expirations (not 0DTE).

Synthetic path: delta-gamma model with weekly theta decay curve.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone

import pandas as pd

logger = logging.getLogger(__name__)


# ── Friday Expiry Resolution ──

def nearest_friday(from_date: date, min_dte: int = 3, max_dte: int = 7) -> date | None:
    """Find the nearest Friday expiration within [min_dte, max_dte] calendar days.

    Returns None if no Friday falls in the window.
    """
    start = from_date + timedelta(days=min_dte)
    end = from_date + timedelta(days=max_dte)
    d = start
    while d <= end:
        if d.weekday() == 4:  # Friday
            return d
        d += timedelta(days=1)
    return None


# ── Delta Estimation (no scipy) ──

def _norm_cdf(x: float) -> float:
    """Rational approximation of the standard normal CDF (Abramowitz & Stegun)."""
    if x < -8:
        return 0.0
    if x > 8:
        return 1.0
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x_abs = abs(x)
    t = 1.0 / (1.0 + p * x_abs)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x_abs * x_abs / 2)
    return 0.5 * (1.0 + sign * y)


def estimate_delta(
    spot: float,
    strike: float,
    dte: int,
    option_type: str = "call",
    iv: float = 0.20,
    r: float = 0.05,
) -> float:
    """Estimate option delta using Black-Scholes d1."""
    if spot <= 0 or strike <= 0 or dte <= 0:
        return 0.50
    t = dte / 365.0
    try:
        d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    except (ValueError, ZeroDivisionError):
        return 0.50
    if option_type == "call":
        return _norm_cdf(d1)
    else:
        return _norm_cdf(d1) - 1.0


def estimate_iv_from_atr(atr: float, spot: float) -> float:
    """Rough IV estimate from daily ATR: annualized vol ~ (ATR/spot) * sqrt(252)."""
    if spot <= 0:
        return 0.20
    return max(0.10, min(0.80, (atr / spot) * math.sqrt(252)))


# ── Real Pricing (Alpaca) ──

def resolve_weekly_otm(
    symbol: str,
    trade_date: date,
    option_type: str,
    spot: float,
    target_delta: float = 0.35,
    expiration: date | None = None,
    min_dte: int = 3,
    max_dte: int = 7,
    iv: float = 0.20,
) -> dict | None:
    """Find the weekly contract closest to target_delta.

    Returns dict with occ_symbol, strike, expiration, estimated_delta, dte
    or None if no listing.
    """
    from src.utils.alpaca_options import list_contracts

    if expiration is None:
        expiration = nearest_friday(trade_date, min_dte, max_dte)
    if expiration is None:
        return None

    dte = (expiration - trade_date).days
    contracts = list_contracts(symbol, expiration, option_type)
    if not contracts:
        return None

    best = None
    best_diff = float("inf")
    for c in contracts:
        strike = c["strike"]
        delta = abs(estimate_delta(spot, strike, dte, option_type, iv))
        diff = abs(delta - target_delta)
        if diff < best_diff:
            best_diff = diff
            best = {
                "occ_symbol": c["symbol"],
                "strike": strike,
                "expiration": expiration,
                "estimated_delta": round(delta, 3),
                "dte": dte,
            }

    return best


def fetch_weekly_option_bars(
    occ: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Fetch daily option bars for a weekly contract across the hold period."""
    from src.utils.alpaca_options import fetch_option_bars

    start_dt = datetime(start_date.year, start_date.month, start_date.day, 9, 30, tzinfo=timezone.utc)
    end_dt = datetime(end_date.year, end_date.month, end_date.day, 20, 0, tzinfo=timezone.utc)

    return fetch_option_bars(occ, start_dt, end_dt, interval="1day")


def weekly_option_close_on_date(bars: pd.DataFrame, target_date: date) -> float | None:
    """Return option close price on a specific date."""
    if bars is None or bars.empty:
        return None
    for ts, row in bars.iterrows():
        if hasattr(ts, 'date'):
            if ts.date() == target_date:
                return float(row["Close"])
        else:
            d = pd.Timestamp(ts).date()
            if d == target_date:
                return float(row["Close"])
    return None


# ── Synthetic Pricing ──

def synth_weekly_premium(
    spot: float,
    strike: float,
    dte: int,
    atr: float,
    vix: float = 20.0,
    option_type: str = "call",
) -> float:
    """Estimate weekly option premium using ATR-scaled model.

    Premium ~ atr * delta_estimate * sqrt(dte/5) * vix_scalar
    """
    iv = estimate_iv_from_atr(atr, spot)
    delta = abs(estimate_delta(spot, strike, dte, option_type, iv))
    vix_scalar = max(0.5, vix / 20.0)
    premium = atr * delta * math.sqrt(max(1, dte) / 5.0) * vix_scalar
    return max(0.15, round(premium, 2))


def synth_weekly_pnl(
    entry_premium: float,
    underlying_move: float,
    dte_at_entry: int,
    days_held: int,
    delta: float = 0.35,
    gamma: float = 0.02,
    slippage: float = 0.0,
) -> float:
    """Compute synthetic P&L for a weekly option position.

    Uses delta-gamma model with weekly theta decay:
      theta_decay = premium * 0.70 * (1 - sqrt(dte_remaining / dte_at_entry))

    P&L capped at -entry_premium (defined risk).
    """
    if dte_at_entry <= 0:
        dte_at_entry = 1

    abs_move = abs(underlying_move)
    delta_pnl = delta * abs_move
    gamma_pnl = 0.5 * gamma * abs_move * abs_move

    dte_remaining = max(0, dte_at_entry - days_held)
    if dte_at_entry > 0:
        theta_decay = entry_premium * 0.70 * (1.0 - math.sqrt(dte_remaining / dte_at_entry))
    else:
        theta_decay = entry_premium * 0.70

    # Direction: move in favor of the trade = positive P&L
    if underlying_move >= 0:
        raw_pnl = delta_pnl + gamma_pnl - theta_decay
    else:
        raw_pnl = -(delta_pnl + gamma_pnl) - theta_decay

    pnl = raw_pnl - slippage
    return max(-entry_premium, round(pnl, 4))


# ── Business day helpers ──

def sessions_between(start: date, end: date) -> int:
    """Count business days between start (exclusive) and end (inclusive)."""
    count = 0
    d = start + timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:
            count += 1
        d += timedelta(days=1)
    return count
