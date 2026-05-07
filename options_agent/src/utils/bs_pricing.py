"""Minimal Black-Scholes pricer for synth-fallback option valuation.

Used by replay_sweet_spot.py when real Alpaca historical bars are unavailable
for a longer-dated (e.g. ~90 DTE) contract. Intentionally tiny — no greeks
chain, just call/put prices.

Assumes:
  - European exercise (acceptable for SPY index options; OK approximation for
    long-dated equity options where early exercise is rare)
  - Continuous dividend yield (default 0 — set q for SPY-like underlyings)
  - Constant volatility over the holding period
"""

from __future__ import annotations

import math


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float,
    option_type: str,
    r: float = 0.045,
    q: float = 0.0,
) -> float:
    """Black-Scholes-Merton price for a European call/put.

    Args:
        spot: underlying price
        strike: strike price
        t_years: time to expiration in years (e.g. 90/365 for 90 days)
        sigma: volatility (e.g. 0.18 for 18% annualized)
        option_type: "call" or "put"
        r: risk-free rate (default 4.5% — rough current short-rate)
        q: dividend yield (default 0; SPY ~0.013)
    """
    if t_years <= 0 or sigma <= 0:
        # Intrinsic value at expiration
        if option_type == "call":
            return max(spot - strike, 0.0)
        return max(strike - spot, 0.0)

    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    df_r = math.exp(-r * t_years)
    df_q = math.exp(-q * t_years)

    if option_type == "call":
        return spot * df_q * _norm_cdf(d1) - strike * df_r * _norm_cdf(d2)
    return strike * df_r * _norm_cdf(-d2) - spot * df_q * _norm_cdf(-d1)
