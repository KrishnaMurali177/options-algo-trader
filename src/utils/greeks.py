"""Simplified Black-Scholes Greeks helpers (for validation / fallback when broker data is missing)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from scipy.stats import norm


@dataclass
class BSGreeks:
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


def black_scholes_greeks(
    S: float,       # current stock price
    K: float,       # strike price
    T: float,       # time to expiration in years
    r: float,       # risk-free rate (annualized)
    sigma: float,   # implied volatility (annualized)
    option_type: str = "call",
) -> BSGreeks:
    """Compute Black-Scholes Greeks for a European option."""
    if T <= 0 or sigma <= 0:
        return BSGreeks(delta=0, gamma=0, theta=0, vega=0, rho=0)

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    nd1 = norm.cdf(d1)
    nd2 = norm.cdf(d2)
    pdf_d1 = norm.pdf(d1)

    if option_type == "call":
        delta = nd1
        theta = (
            -(S * pdf_d1 * sigma) / (2 * sqrt_T)
            - r * K * math.exp(-r * T) * nd2
        ) / 365
        rho = K * T * math.exp(-r * T) * nd2 / 100
    else:  # put
        delta = nd1 - 1
        theta = (
            -(S * pdf_d1 * sigma) / (2 * sqrt_T)
            + r * K * math.exp(-r * T) * norm.cdf(-d2)
        ) / 365
        rho = -K * T * math.exp(-r * T) * norm.cdf(-d2) / 100

    gamma = pdf_d1 / (S * sigma * sqrt_T)
    vega = S * pdf_d1 * sqrt_T / 100

    return BSGreeks(delta=delta, gamma=gamma, theta=theta, vega=vega, rho=rho)

