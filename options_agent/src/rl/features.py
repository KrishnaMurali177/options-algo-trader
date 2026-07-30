"""Daily context vector for the regime bandit.

Decision timing (the thing that makes or breaks this whole layer)
----------------------------------------------------------------
The controller picks one arm per symbol-day *before the strategy can trade*, so
the context may only use data available at that moment. The earliest a trade can
fire is **10:00 ET**, not 10:35: with ``dynamic_or`` on (a golden default) a
decisive 10:00 break collapses the opening range to 30 minutes and moves
scan-start to 10:00 (``replay_sweet_spot.py:249-274``).

So :data:`DECISION_TIME` is 10:00 and every feature is built from bars strictly
*before* it. That costs us the 60-minute opening range — we only get the 30-minute
one — and it is not negotiable: deciding at 10:35 would let the context see the
outcome of trades the decision is supposed to govern.

``test_rl_bandit.py`` enforces this by NaN-ing every bar at or after 10:00 and
asserting the vector is unchanged.
"""

from __future__ import annotations

from datetime import date, time

import numpy as np
import pandas as pd

#: No feature may read a bar at or after this time.
DECISION_TIME = time(10, 0)

#: The 30-minute opening range window, which *is* closed by DECISION_TIME.
OR30_START = time(9, 30)

#: Symbols that are ETFs (vs single names). Single names only list 0DTE on
#: weekly expiries, which is why `has_daily_0dte` is a separate feature.
_ETFS = {
    "SPY", "QQQ", "IWM", "DIA", "GLD", "TLT", "SLV", "USO",
    "XLE", "XLF", "XLK", "EEM", "FXI", "HYG",
}

#: Symbols with a genuine daily 0DTE listing. Measured from the option-chain
#: cache: SPY/QQQ/IWM have ~0% empty chain days, everything else is weekly-only.
_DAILY_0DTE = {"SPY", "QQQ", "IWM"}

#: Symbols that get their own intercept. Only where there is enough data to
#: estimate one — a 21-way one-hot would burn the whole sample budget.
PER_SYMBOL_INTERCEPTS = ("SPY", "QQQ", "IWM")

FEATURE_NAMES: list[str] = [
    "bias",
    "gap_pct",
    "or30_width_atr",
    "or30_close_pos",
    "or30_ret_pct",
    "rv5_pct",
    "vix_prev",
    "vix_chg_pct",
    "vol_ratio_30m",
    "price_vs_sma20_atr",
    "price_vs_sma50_atr",
    "rsi_14",
    "dow_tue",
    "dow_wed",
    "dow_thu",
    "dow_fri",
    "is_etf",
    "has_daily_0dte",
    "log_dollar_adv",
    *(f"sym_{s.lower()}" for s in PER_SYMBOL_INTERCEPTS),
]

N_FEATURES = len(FEATURE_NAMES)


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    if den is None or not np.isfinite(den) or abs(den) < 1e-9:
        return default
    out = num / den
    return float(out) if np.isfinite(out) else default


def visible_bars(day_bars: pd.DataFrame) -> pd.DataFrame:
    """Today's bars the controller is allowed to see (strictly before 10:00)."""
    return day_bars[day_bars.index.time < DECISION_TIME]


def build_context(
    symbol: str,
    trade_date: date,
    day_bars: pd.DataFrame,
    prior_bars: pd.DataFrame | None,
    vix_prev: float,
    vix_prev2: float | None,
) -> np.ndarray:
    """Build the context vector for one symbol-day.

    Parameters
    ----------
    day_bars
        All of today's 5-min bars. Sliced to pre-10:00 internally — callers do
        not need to pre-trim, and must not rely on bars after 10:00 being absent.
    prior_bars
        The preceding sessions' bars (the dataset builder passes the same 4-day
        window ``replay_day`` uses), for SMA warmup and prior-session stats.
        May be None early in the series.
    vix_prev, vix_prev2
        ^VIX closes for the prior and second-prior sessions, from
        ``replay_sweet_spot.resolve_day_vix``. Never today's close — see the
        module docstring in that file.
    """
    # Lazy import: src -> scripts is backwards architecturally, but recomputing
    # ATR/SMA/RSI here would let the controller's view drift from the strategy's.
    from scripts.replay_sweet_spot import _build_indicators_from_bars

    today = visible_bars(day_bars)
    if today.empty:
        raise ValueError(f"{symbol} {trade_date}: no bars before {DECISION_TIME}")

    open_px = float(today["Open"].iloc[0])
    last_px = float(today["Close"].iloc[-1])

    # ── Indicators, warmed with prior sessions exactly like replay_day does ──
    if prior_bars is not None and not prior_bars.empty:
        warm = pd.concat([prior_bars, today])
    else:
        warm = today
    ind = _build_indicators_from_bars(warm, symbol=symbol)
    atr = float(ind.atr_14) if np.isfinite(ind.atr_14) and ind.atr_14 > 0 else None

    # ── Overnight gap ──
    prev_close = None
    if prior_bars is not None and not prior_bars.empty:
        prev_close = float(prior_bars["Close"].iloc[-1])
    gap_pct = _safe_div((open_px - prev_close) * 100.0, prev_close) if prev_close else 0.0

    # ── 30-minute opening range (09:30–09:59, closed by DECISION_TIME) ──
    or30 = today[today.index.time >= OR30_START]
    if or30.empty:
        or30 = today
    or_hi = float(or30["High"].max())
    or_lo = float(or30["Low"].min())
    or_width = or_hi - or_lo
    or30_width_atr = _safe_div(or_width, atr) if atr else 0.0
    # Where the last visible close sits in the range: -1 at the low, +1 at the high.
    or30_close_pos = _safe_div((last_px - or_lo) - (or_hi - last_px), or_width)
    or30_ret_pct = _safe_div((last_px - open_px) * 100.0, open_px)

    # ── Prior-session realised vol (close-to-close, in %) ──
    rv5_pct = 0.0
    if prior_bars is not None and not prior_bars.empty:
        daily_close = prior_bars["Close"].astype(float).groupby(prior_bars.index.date).last()
        if len(daily_close) >= 3:
            rets = daily_close.pct_change().dropna()
            if len(rets) >= 2:
                rv5_pct = float(rets.std(ddof=1) * 100.0)
    if not np.isfinite(rv5_pct):
        rv5_pct = 0.0

    # ── Opening volume vs the same window on prior sessions ──
    vol_today = float(or30["Volume"].sum())
    vol_ratio = 1.0
    if prior_bars is not None and not prior_bars.empty:
        prior_open_win = prior_bars[
            (prior_bars.index.time >= OR30_START) & (prior_bars.index.time < DECISION_TIME)
        ]
        if not prior_open_win.empty:
            per_day = prior_open_win["Volume"].astype(float).groupby(
                prior_open_win.index.date).sum()
            if len(per_day) and per_day.mean() > 0:
                vol_ratio = _safe_div(vol_today, float(per_day.mean()), 1.0)

    # ── Trend location, in ATR units so symbols are commensurable ──
    px_vs_sma20 = _safe_div(last_px - float(ind.sma_20), atr) if atr else 0.0
    px_vs_sma50 = _safe_div(last_px - float(ind.sma_50), atr) if atr else 0.0

    rsi = float(ind.rsi_14) if np.isfinite(ind.rsi_14) else 50.0

    # ── Liquidity: log mean dollar volume per prior session ──
    log_dollar_adv = 0.0
    if prior_bars is not None and not prior_bars.empty:
        dollar = (prior_bars["Close"].astype(float) * prior_bars["Volume"].astype(float))
        per_day_dollar = dollar.groupby(prior_bars.index.date).sum()
        mean_dollar = float(per_day_dollar.mean()) if len(per_day_dollar) else 0.0
        if mean_dollar > 0:
            log_dollar_adv = float(np.log10(mean_dollar))

    dow = trade_date.weekday()  # Mon=0; Monday is the reference level
    vix_chg_pct = (
        _safe_div((vix_prev - vix_prev2) * 100.0, vix_prev2) if vix_prev2 else 0.0
    )

    vec = [
        1.0,                            # bias
        gap_pct,
        or30_width_atr,
        or30_close_pos,
        or30_ret_pct,
        rv5_pct,
        float(vix_prev),
        vix_chg_pct,
        vol_ratio,
        px_vs_sma20,
        px_vs_sma50,
        rsi,
        1.0 if dow == 1 else 0.0,
        1.0 if dow == 2 else 0.0,
        1.0 if dow == 3 else 0.0,
        1.0 if dow == 4 else 0.0,
        1.0 if symbol in _ETFS else 0.0,
        1.0 if symbol in _DAILY_0DTE else 0.0,
        log_dollar_adv,
        *(1.0 if symbol == s else 0.0 for s in PER_SYMBOL_INTERCEPTS),
    ]
    out = np.asarray(vec, dtype=float)
    if out.shape[0] != N_FEATURES:
        raise AssertionError(f"built {out.shape[0]} features, expected {N_FEATURES}")
    # A NaN here would silently poison the ridge solve.
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


class Standardizer:
    """Z-scores features, fitted on training rows only.

    Fitting on the full dataset would leak holdout distribution into the model,
    which is the subtler cousin of the VIX look-ahead. The bias column and the
    0/1 dummies are left alone.
    """

    def __init__(self) -> None:
        self.mean_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None
        self._passthrough = np.array(
            [n == "bias" or n.startswith(("dow_", "sym_")) or n in ("is_etf", "has_daily_0dte")
             for n in FEATURE_NAMES]
        )

    def fit(self, X: np.ndarray) -> "Standardizer":
        X = np.atleast_2d(np.asarray(X, dtype=float))
        self.mean_ = X.mean(axis=0)
        scale = X.std(axis=0, ddof=0)
        scale[scale < 1e-9] = 1.0
        self.scale_ = scale
        self.mean_[self._passthrough] = 0.0
        self.scale_[self._passthrough] = 1.0
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.scale_ is None:
            raise RuntimeError("Standardizer.transform() before fit()")
        X = np.atleast_2d(np.asarray(X, dtype=float))
        return (X - self.mean_) / self.scale_

    def fit_transform(self, X: np.ndarray) -> np.ndarray:
        return self.fit(X).transform(X)
