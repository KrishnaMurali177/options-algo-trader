"""Weekly option chain selector — pick nearest Friday expiry with target delta.

Self-contained module that wraps the Alpaca SDK to fetch weekly options.
Does NOT modify or import from src/utils/alpaca_data.py.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def get_weekly_chain(
    symbol: str,
    option_type: str = "call",
    target_delta: float = 0.35,
    delta_tolerance: float = 0.15,
    min_dte: int = 3,
    max_dte: int = 7,
    spot_price: float | None = None,
) -> dict | None:
    """Fetch weekly options chain and pick the contract closest to target_delta.

    Targets the nearest Friday expiry within [min_dte, max_dte] days from today.

    Returns:
        Dict with contract details or None if no suitable contract found.
        Includes all fields from 0DTE chain plus 'dte'.
    """
    from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import OptionSnapshotRequest, StockLatestTradeRequest
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOptionContractsRequest
    from dotenv import load_dotenv

    load_dotenv()

    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")

    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")

    trading_client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)
    today = date.today()

    exp_start = today + timedelta(days=min_dte)
    exp_end = today + timedelta(days=max_dte)

    target_friday = _nearest_friday(exp_start, exp_end)
    if target_friday is None:
        logger.warning("No Friday found between %s and %s", exp_start, exp_end)
        return None

    logger.info(
        "Weekly chain: %s %s targeting Friday %s (DTE=%d)",
        symbol, option_type, target_friday, (target_friday - today).days,
    )

    try:
        contracts = []
        page_token = None
        while True:
            req = GetOptionContractsRequest(
                underlying_symbols=[symbol],
                expiration_date=target_friday,
                type=option_type,
                status="active",
                page_token=page_token,
            )
            resp = trading_client.get_option_contracts(req)
            if not resp:
                break
            contracts.extend(resp.option_contracts or [])
            page_token = getattr(resp, "next_page_token", None)
            if not page_token:
                break
    except Exception as e:
        logger.error("Failed to fetch weekly chain for %s: %s", symbol, e)
        return None

    if not contracts:
        logger.warning("No weekly %s contracts for %s expiring %s", option_type, symbol, target_friday)
        return None

    snapshots = {}
    try:
        option_client = OptionHistoricalDataClient(api_key=api_key, secret_key=secret_key)
        occ_symbols = [c.symbol for c in contracts]
        for i in range(0, len(occ_symbols), 100):
            batch = occ_symbols[i:i + 100]
            batch_snaps = option_client.get_option_snapshot(
                OptionSnapshotRequest(symbol_or_symbols=batch)
            )
            if batch_snaps:
                snapshots.update(batch_snaps)
    except Exception as e:
        logger.warning("Failed to fetch option snapshots: %s — using strike proximity", e)

    _spot = spot_price
    if _spot is None or _spot <= 0:
        try:
            stock_client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
            trade_resp = stock_client.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=symbol)
            )
            if symbol in trade_resp:
                _spot = float(trade_resp[symbol].price)
        except Exception:
            pass

    MAX_STRIKE_PCT = 0.05  # 5% for weeklies (wider than 0DTE's 2%)
    dte = (target_friday - today).days

    best = _select_by_delta(contracts, snapshots, _spot, target_delta, delta_tolerance,
                            MAX_STRIKE_PCT, symbol, option_type, target_friday, dte)
    if best:
        return best

    return _select_by_strike_proximity(contracts, snapshots, _spot, MAX_STRIKE_PCT,
                                       symbol, option_type, target_friday, dte)


def _select_by_delta(
    contracts, snapshots, spot, target_delta, delta_tolerance,
    max_strike_pct, symbol, option_type, expiration, dte,
) -> dict | None:
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
            continue

        bid = float(snap.latest_quote.bid_price) if snap and snap.latest_quote else 0.0
        ask = float(snap.latest_quote.ask_price) if snap and snap.latest_quote else 0.0
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else 0.0

        if mid <= 0.05:
            continue

        strike = float(contract.strike_price)
        if spot and spot > 0:
            if abs(strike - spot) / spot > max_strike_pct:
                continue

        delta_diff = abs(delta - target_delta)
        if delta_diff < best_delta_diff and delta_diff <= delta_tolerance:
            best_delta_diff = delta_diff
            best = {
                "occ_symbol": occ,
                "underlying": symbol,
                "strike": strike,
                "expiration": str(expiration),
                "option_type": option_type,
                "delta": delta,
                "gamma": gamma,
                "theta": theta,
                "iv": iv,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "dte": dte,
            }

    if best:
        logger.info(
            "Selected weekly %s: %s strike=$%.2f delta=%.2f mid=$%.2f DTE=%d",
            option_type, best["occ_symbol"], best["strike"], best["delta"], best["mid"], dte,
        )
    return best


def _select_by_strike_proximity(
    contracts, snapshots, spot, max_strike_pct,
    symbol, option_type, expiration, dte,
) -> dict | None:
    if not spot or spot <= 0:
        logger.warning("No weekly %s contract found for %s (no spot for fallback)", option_type, symbol)
        return None

    best_strike_diff = float("inf")
    best_contract = None
    best_snap = None
    for contract in contracts:
        strike = float(contract.strike_price)
        if abs(strike - spot) / spot > max_strike_pct:
            continue
        strike_diff = abs(strike - spot)
        if strike_diff < best_strike_diff:
            best_strike_diff = strike_diff
            best_contract = contract
            best_snap = snapshots.get(contract.symbol)

    if best_contract is None:
        logger.warning("No weekly %s contract within %.0f%% of spot $%.2f for %s",
                       option_type, max_strike_pct * 100, spot, symbol)
        return None

    strike = float(best_contract.strike_price)
    bid = float(best_snap.latest_quote.bid_price) if best_snap and best_snap.latest_quote else 0.0
    ask = float(best_snap.latest_quote.ask_price) if best_snap and best_snap.latest_quote else 0.0
    mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else 0.0

    if mid <= 0.05:
        logger.warning(
            "Weekly %s closest-strike %s (strike=$%.2f) has no quote — skipping",
            option_type, best_contract.symbol, strike,
        )
        return None

    result = {
        "occ_symbol": best_contract.symbol,
        "underlying": symbol,
        "strike": strike,
        "expiration": str(expiration),
        "option_type": option_type,
        "delta": target_delta_for_type(option_type, strike, spot),
        "gamma": 0.0,
        "theta": 0.0,
        "iv": 0.0,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "dte": dte,
    }

    logger.info(
        "Selected weekly %s by strike proximity: %s strike=$%.2f spot=$%.2f mid=$%.2f DTE=%d",
        option_type, result["occ_symbol"], strike, spot, mid, dte,
    )
    return result


def target_delta_for_type(option_type: str, strike: float, spot: float) -> float:
    """Estimate delta when greeks are unavailable."""
    if spot <= 0:
        return 0.35
    moneyness = (spot - strike) / spot if option_type == "call" else (strike - spot) / spot
    if moneyness > 0.02:
        return 0.60
    elif moneyness < -0.02:
        return 0.30
    return 0.50


def _nearest_friday(start: date, end: date) -> date | None:
    """Find the nearest Friday within [start, end]."""
    d = start
    while d <= end:
        if d.weekday() == 4:  # Friday
            return d
        d += timedelta(days=1)
    return None
