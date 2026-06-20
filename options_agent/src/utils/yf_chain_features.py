"""Live-agent options-chain feature snapshot via yfinance.

yfinance returns the *current* 0DTE chain snapshot — volume, openInterest, IV,
bid/ask, lastTradeDate per strike. There is no historical query, so this is a
live-only feature collector: each call captures the chain state at "now."

Intended use: emit one record per scan iteration into the verdict JSONL so that
after 60-90 days of live logging, we have a feature column to bucket trigger
outcomes against — same diagnostic pattern that produced FOMC and failed-bounce
promotions, but on a feature dimension the existing scorers cannot see.

Designed to be robust: any failure returns None for the whole snapshot rather
than raising, so live-agent scans are never interrupted by a yfinance hiccup.
"""

from __future__ import annotations

import logging
import time
from datetime import date

logger = logging.getLogger(__name__)

_TICKER_CACHE: dict[str, object] = {}
_CHAIN_CACHE: dict[tuple[str, str, int], dict] = {}
_CACHE_TTL_SEC = 60  # re-fetch at most once per minute per symbol


def _get_ticker(symbol: str):
    if symbol not in _TICKER_CACHE:
        import yfinance as yf
        _TICKER_CACHE[symbol] = yf.Ticker(symbol)
    return _TICKER_CACHE[symbol]


def snapshot(symbol: str, spot: float | None = None) -> dict | None:
    """Capture a 0DTE-chain feature snapshot for `symbol` at current time.

    Returns a dict with:
      - exp: 0DTE expiration date string used
      - n_calls / n_puts: strike counts
      - call_vol_total / put_vol_total: full-chain volume sums
      - pc_ratio_vol: put / call total volume
      - call_oi_total / put_oi_total: full-chain open interest
      - pc_ratio_oi: put / call total OI
      - atm_call_iv / atm_put_iv: IV of the strike nearest `spot`
      - iv_skew_25d: (put_iv − call_iv) at ~25-delta proxy (5% OTM strikes)
      - otm_call_vol_pct / otm_put_vol_pct: % of total chain volume in OTM wings
      - max_pain_proxy: strike with peak (call OI × call_dist + put OI × put_dist)
      - fetch_ms: how long the yfinance call took

    Returns None on any failure.
    """
    now = time.time()
    cache_key = (symbol, date.today().isoformat(), int(now // _CACHE_TTL_SEC))
    if cache_key in _CHAIN_CACHE:
        return _CHAIN_CACHE[cache_key]

    try:
        t0 = time.time()
        ticker = _get_ticker(symbol)
        exps = ticker.options
        if not exps:
            return None

        today_iso = date.today().isoformat()
        if today_iso in exps:
            exp = today_iso
        else:
            exp = exps[0]

        chain = ticker.option_chain(exp)
        calls, puts = chain.calls, chain.puts
        if calls is None or puts is None or calls.empty or puts.empty:
            return None

        fetch_ms = (time.time() - t0) * 1000

        call_vol = float(calls["volume"].fillna(0).sum())
        put_vol = float(puts["volume"].fillna(0).sum())
        call_oi = float(calls["openInterest"].fillna(0).sum())
        put_oi = float(puts["openInterest"].fillna(0).sum())

        snap = {
            "exp": exp,
            "n_calls": int(len(calls)),
            "n_puts": int(len(puts)),
            "call_vol_total": call_vol,
            "put_vol_total": put_vol,
            "pc_ratio_vol": (put_vol / call_vol) if call_vol > 0 else None,
            "call_oi_total": call_oi,
            "put_oi_total": put_oi,
            "pc_ratio_oi": (put_oi / call_oi) if call_oi > 0 else None,
            "fetch_ms": round(fetch_ms, 1),
        }

        if spot is not None and spot > 0:
            try:
                atm_call_idx = (calls["strike"] - spot).abs().idxmin()
                atm_put_idx = (puts["strike"] - spot).abs().idxmin()
                snap["atm_strike"] = float(calls.loc[atm_call_idx, "strike"])
                snap["atm_call_iv"] = float(calls.loc[atm_call_idx, "impliedVolatility"])
                snap["atm_put_iv"] = float(puts.loc[atm_put_idx, "impliedVolatility"])

                # 25-delta proxy: ~5% OTM each side
                otm_call_target = spot * 1.005
                otm_put_target = spot * 0.995
                otm_c_idx = (calls["strike"] - otm_call_target).abs().idxmin()
                otm_p_idx = (puts["strike"] - otm_put_target).abs().idxmin()
                otm_call_iv = float(calls.loc[otm_c_idx, "impliedVolatility"])
                otm_put_iv = float(puts.loc[otm_p_idx, "impliedVolatility"])
                snap["iv_skew_25d"] = otm_put_iv - otm_call_iv

                # OTM wing share of total volume
                otm_call_mask = calls["strike"] > spot
                otm_put_mask = puts["strike"] < spot
                otm_call_vol = float(calls.loc[otm_call_mask, "volume"].fillna(0).sum())
                otm_put_vol = float(puts.loc[otm_put_mask, "volume"].fillna(0).sum())
                if call_vol > 0:
                    snap["otm_call_vol_pct"] = otm_call_vol / call_vol
                if put_vol > 0:
                    snap["otm_put_vol_pct"] = otm_put_vol / put_vol

                # Cheap max-pain proxy: strike with min total |OI × distance| product
                # (lower = pain center). Restrict to ±3% of spot for speed.
                near_calls = calls[(calls["strike"] >= spot * 0.97) & (calls["strike"] <= spot * 1.03)]
                near_puts = puts[(puts["strike"] >= spot * 0.97) & (puts["strike"] <= spot * 1.03)]
                if not near_calls.empty and not near_puts.empty:
                    strikes = sorted(set(near_calls["strike"]).union(near_puts["strike"]))
                    best_k, best_pain = None, float("inf")
                    for k in strikes:
                        c_pain = float((near_calls.loc[near_calls["strike"] < k, "openInterest"].fillna(0) *
                                        (k - near_calls.loc[near_calls["strike"] < k, "strike"])).sum())
                        p_pain = float((near_puts.loc[near_puts["strike"] > k, "openInterest"].fillna(0) *
                                        (near_puts.loc[near_puts["strike"] > k, "strike"] - k)).sum())
                        total = c_pain + p_pain
                        if total < best_pain:
                            best_pain = total
                            best_k = k
                    if best_k is not None:
                        snap["max_pain_proxy"] = float(best_k)
            except Exception as e:
                logger.debug("yf_chain_features: spot-relative calc failed: %s", e)

        _CHAIN_CACHE[cache_key] = snap
        return snap

    except Exception as e:
        logger.debug("yf_chain_features.snapshot(%s) failed: %s", symbol, e)
        return None
