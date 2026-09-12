"""Parameterize a session's underlying price action, then find analog days.

The agent is an opening-range breakout follower: it reads the 09:30–10:29
range, then from 10:30 to 13:59 it buys 0DTE calls or puts in the direction
the tape breaks. So the features that decide whether a day is survivable are
not "was SPY up or down" — they are the *geometry* of the tape relative to
that opening range:

  - how wide the opening range was (the unit of risk for the whole day),
  - whether the post-10:30 move continued or reversed that range,
  - how far price ran against a breakout before going anywhere,
  - how many times price crossed back through the range boundaries,
  - how efficient (trending) vs. round-tripping the scan window was.

Every feature is scale-free — bps of the day's open, or multiples of the
opening-range width — so 2024 SPY at $470 and 2026 SPY at a different level
land in the same space, and SPY and QQQ are comparable.

Typical use:

    table = build_feature_table("SPY", days_back=760)
    ranked = rank_analog_days(table, ref_dates=[date(2026, 9, 1), ...])
    ranked.head(20)

`FEATURE_SPECS` is the dial: each entry carries a default matching weight,
and `parse_weights("follow_through_r=3,chop_score=0")` overrides them from a
CLI so a study can re-aim the similarity metric without touching code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

OR_START = "09:30"
OR_END = "10:29"        # matches OpeningRangeAnalyzer's 60-min window
SCAN_START = "10:30"    # agent's first scan bar
SCAN_END = "13:59"      # agent's last scan bar

_CACHE_DIR = Path(__file__).resolve().parents[2] / "data_cache" / "day_profiles"


@dataclass(frozen=True)
class FeatureSpec:
    """One price-action dimension: what it means and how much it matters."""
    desc: str
    weight: float       # default weight in the similarity metric (0 = ignore)
    signed: bool = True # False → magnitude-only dimensions (ranges, counts)


# Weights are the prior: the "caught on the wrong side" cluster
# (follow_through_r, mae_r, break_flips) is weighted hardest, because that is
# the mechanism that turns a normal-looking tape into a red agent day.
FEATURE_SPECS: dict[str, FeatureSpec] = {
    "gap_bps":            FeatureSpec("Overnight gap, bps of prior close", 0.5),
    "or_width_bps":       FeatureSpec("Opening-range width, bps of open", 1.5, signed=False),
    "or_dir":             FeatureSpec("Opening-range direction (+1/-1)", 0.5),
    "or_body_frac":       FeatureSpec("|OR close-open| / OR width — opening-hour conviction", 1.0, signed=False),
    "or_close_loc":       FeatureSpec("Where 10:29 sits in the OR (0=low, 1=high)", 0.75),
    "or_efficiency":      FeatureSpec("Kaufman efficiency of the opening hour", 1.0, signed=False),
    "follow_through_r":   FeatureSpec("Post-10:30 move in OR direction, in OR widths (<0 = reversal)", 3.0),
    "mae_r":              FeatureSpec("Max adverse excursion vs OR direction, in OR widths", 2.5, signed=False),
    "mfe_r":              FeatureSpec("Max favorable excursion in OR direction, in OR widths", 1.5, signed=False),
    "excursion_ratio":    FeatureSpec("mfe_r / (mfe_r + mae_r) — was there anything to catch?", 2.0, signed=False),
    "break_flips":        FeatureSpec("Times price re-crossed an OR boundary after 10:30", 2.5, signed=False),
    "false_break_frac":   FeatureSpec("Share of OR breakouts that returned inside within 6 bars", 2.0, signed=False),
    "vwap_crosses":       FeatureSpec("VWAP crossings during the scan window", 1.5, signed=False),
    "scan_efficiency":    FeatureSpec("Kaufman efficiency of the 10:30–13:59 scan window", 2.0, signed=False),
    "chop_score":         FeatureSpec("Agent's own chop score over the scan window (0-10)", 2.0, signed=False),
    "scan_range_bps":     FeatureSpec("Scan-window high-low, bps of open", 1.0, signed=False),
    "range_vs_atr":       FeatureSpec("Day range / 14-day daily ATR", 1.0, signed=False),
    "rvol_5m_bps":        FeatureSpec("Std-dev of 5-min returns, bps", 1.0, signed=False),
    "up_bar_frac":        FeatureSpec("Share of up bars in the scan window", 0.75),
    "close_loc_day":      FeatureSpec("Close location in the day's range", 0.5),
    "afternoon_flip":     FeatureSpec("sign(close-14:00) vs sign(OR dir), +1 agree / -1 flip", 1.0),
    "vix":                FeatureSpec("VIX close (lagged one day, as the agent sees it)", 1.0, signed=False),
    "vix_chg_pct":        FeatureSpec("VIX day-over-day change, %", 0.75),
}

FEATURE_NAMES = list(FEATURE_SPECS)
DEFAULT_WEIGHTS = {k: v.weight for k, v in FEATURE_SPECS.items()}


def parse_weights(spec: str | None, base: dict[str, float] | None = None) -> dict[str, float]:
    """Parse "name=w,name=w" into a weight dict layered over the defaults.

    Unknown names raise, because a silently-ignored typo would quietly change
    what a study measured.
    """
    weights = dict(base or DEFAULT_WEIGHTS)
    if not spec:
        return weights
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, val = part.partition("=")
        name = name.strip()
        if name not in FEATURE_SPECS:
            raise KeyError(f"Unknown feature '{name}'. Known: {', '.join(FEATURE_NAMES)}")
        weights[name] = float(val)
    return {k: v for k, v in weights.items() if v > 0}


# ── Per-day feature extraction ─────────────────────────────────────────

def _efficiency(closes: pd.Series) -> float:
    """Kaufman efficiency ratio: |net move| / total path. 1 = clean trend."""
    path = float(closes.diff().abs().sum())
    if path <= 0:
        return 0.0
    return abs(float(closes.iloc[-1]) - float(closes.iloc[0])) / path


def _crossings(series: pd.Series, level: float) -> int:
    """Number of times a series crosses a fixed level."""
    side = np.sign(series.to_numpy(dtype=float) - level)
    side = side[side != 0]
    if len(side) < 2:
        return 0
    return int((np.diff(side) != 0).sum())


def compute_day_features(
    day_bars: pd.DataFrame,
    *,
    prev_close: float | None = None,
    daily_atr: float | None = None,
    vix: float | None = None,
    prev_vix: float | None = None,
) -> dict | None:
    """Reduce one session's 5-min bars to the scale-free feature vector.

    Args:
        day_bars: 5-min OHLCV for a single session, tz-aware NY index.
        prev_close: Prior session's close, for the gap feature.
        daily_atr: 14-day ATR from daily bars, for range normalization.
        vix: VIX the agent would have seen for this session (already lagged).
        prev_vix: The session before that, for the change feature.

    Returns:
        Feature dict, or None if the session is too short to characterize.
    """
    bars = day_bars.between_time("09:30", "16:00")
    if len(bars) < 24:
        return None

    opening = bars.between_time(OR_START, OR_END)
    scan = bars.between_time(SCAN_START, SCAN_END)
    post = bars.between_time(SCAN_START, "16:00")
    if len(opening) < 6 or len(scan) < 6:
        return None

    o = float(opening["Open"].iloc[0])
    if o <= 0:
        return None
    bps = lambda x: float(x) / o * 10_000.0

    or_high = float(opening["High"].max())
    or_low = float(opening["Low"].min())
    or_open = float(opening["Open"].iloc[0])
    or_close = float(opening["Close"].iloc[-1])
    or_width = or_high - or_low
    if or_width <= 0:
        return None

    # Direction the agent would lean from the opening hour. Ties (a perfectly
    # flat opening hour) resolve to where the 10:29 close sits in the range,
    # which is what the analyzer's momentum score keys off.
    or_delta = or_close - or_open
    or_dir = 1.0 if or_delta > 0 else (-1.0 if or_delta < 0 else
                                       (1.0 if or_close >= (or_high + or_low) / 2 else -1.0))

    post_close = post["Close"].astype(float)
    post_high = float(post["High"].max())
    post_low = float(post["Low"].min())
    last = float(post_close.iloc[-1])

    # Signed continuation of the opening-hour direction, measured in OR
    # widths. Negative is the headline failure mode: the range said one way,
    # the rest of the day went the other, and the agent bought the breakout.
    follow_through_r = (last - or_close) * or_dir / or_width

    # Excursions from the 10:30 reference, in the OR direction.
    if or_dir > 0:
        mfe_r = (post_high - or_close) / or_width
        mae_r = (or_close - post_low) / or_width
    else:
        mfe_r = (or_close - post_low) / or_width
        mae_r = (post_high - or_close) / or_width
    mfe_r, mae_r = max(mfe_r, 0.0), max(mae_r, 0.0)
    denom = mfe_r + mae_r
    excursion_ratio = mfe_r / denom if denom > 0 else 0.5

    # Boundary behaviour: how many times price re-crossed either edge of the
    # opening range after 10:30, and how often a break failed back inside.
    break_flips = _crossings(post_close, or_high) + _crossings(post_close, or_low)

    closes = post_close.to_numpy(dtype=float)
    breaks = fails = 0
    i = 0
    while i < len(closes):
        if closes[i] > or_high or closes[i] < or_low:
            up = closes[i] > or_high
            breaks += 1
            window = closes[i + 1:i + 7]
            back_inside = np.where((window <= or_high) & (window >= or_low))[0]
            if len(back_inside):
                fails += 1
                i += int(back_inside[0]) + 1
                continue
            # Skip to the end of this excursion so one sustained trend counts once.
            outside = closes[i:] > or_high if up else closes[i:] < or_low
            inside_idx = np.where(~outside)[0]
            i += int(inside_idx[0]) if len(inside_idx) else len(closes)
            continue
        i += 1
    false_break_frac = fails / breaks if breaks else 0.0

    # Session VWAP, crossed during the scan window.
    tp = (bars["High"] + bars["Low"] + bars["Close"]) / 3.0
    cumvol = bars["Volume"].cumsum().replace(0, np.nan)
    vwap = (tp * bars["Volume"]).cumsum() / cumvol
    scan_idx = scan.index
    vwap_scan = vwap.reindex(scan_idx).ffill()
    dev = (scan["Close"].astype(float) - vwap_scan).dropna()
    vwap_crosses = _crossings(dev, 0.0)

    day_high = float(bars["High"].max())
    day_low = float(bars["Low"].min())
    day_range = day_high - day_low
    close_px = float(bars["Close"].iloc[-1])

    rets = bars["Close"].astype(float).pct_change().dropna()
    at_two = bars.between_time("14:00", "14:05")
    px_1400 = float(at_two["Close"].iloc[0]) if len(at_two) else or_close
    afternoon_flip = float(np.sign((close_px - px_1400) * or_dir) or 0.0)

    # Reuse the agent's own chop scorer so this feature is literally the
    # number the live gate sees, not a lookalike.
    try:
        from src.utils.choppiness import compute_choppiness
        chop = compute_choppiness(scan, vix=vix)
        chop_score = float(chop.chop_score)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("choppiness failed: %s", exc)
        chop_score = float("nan")

    scan_up = (scan["Close"].astype(float).diff() > 0).mean()

    return {
        "gap_bps": bps(o - prev_close) if prev_close else 0.0,
        "or_width_bps": bps(or_width),
        "or_dir": or_dir,
        "or_body_frac": abs(or_delta) / or_width,
        "or_close_loc": (or_close - or_low) / or_width,
        "or_efficiency": _efficiency(opening["Close"].astype(float)),
        "follow_through_r": follow_through_r,
        "mae_r": mae_r,
        "mfe_r": mfe_r,
        "excursion_ratio": excursion_ratio,
        "break_flips": float(break_flips),
        "false_break_frac": false_break_frac,
        "vwap_crosses": float(vwap_crosses),
        "scan_efficiency": _efficiency(scan["Close"].astype(float)),
        "chop_score": chop_score,
        "scan_range_bps": bps(float(scan["High"].max()) - float(scan["Low"].min())),
        "range_vs_atr": (day_range / daily_atr) if daily_atr else np.nan,
        "rvol_5m_bps": float(rets.std(ddof=0)) * 10_000.0 if len(rets) > 2 else np.nan,
        "up_bar_frac": float(scan_up),
        "close_loc_day": (close_px - day_low) / day_range if day_range > 0 else 0.5,
        "afternoon_flip": afternoon_flip,
        "vix": float(vix) if vix else np.nan,
        "vix_chg_pct": ((vix - prev_vix) / prev_vix * 100.0) if (vix and prev_vix) else np.nan,
        # Context columns — carried for reporting, never used for matching.
        "_open": o,
        "_close": close_px,
        "_day_ret_bps": bps(close_px - o),
        "_or_high": or_high,
        "_or_low": or_low,
    }


# ── Table building ─────────────────────────────────────────────────────

def _daily_atr_map(symbol: str, days_back: int) -> dict[date, float]:
    """14-day ATR per session, from daily bars. Empty dict if unavailable."""
    try:
        from src.utils.alpaca_data import fetch_bars
        d = fetch_bars(symbol, days_back=days_back + 30, interval="1day")
    except Exception as exc:
        logger.warning("daily bars for ATR failed (%s) — range_vs_atr will be NaN", exc)
        return {}
    h, l, c = d["High"].astype(float), d["Low"].astype(float), d["Close"].astype(float)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().shift(1)  # shift: only prior sessions are knowable
    return {ts.date(): float(v) for ts, v in atr.items() if pd.notna(v)}


def _vix_map(start: date, end: date) -> dict[date, float]:
    """VIX closes by session date. Empty dict if the fetch fails."""
    try:
        import yfinance as yf
        df = yf.download("^VIX", start=str(start - timedelta(days=10)),
                         end=str(end + timedelta(days=1)), progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return {ts.date(): float(v) for ts, v in df["Close"].items()}
    except Exception as exc:
        logger.warning("VIX fetch failed (%s) — vix features will be NaN", exc)
        return {}


def build_feature_table(
    symbol: str = "SPY",
    *,
    days_back: int = 760,
    bars: pd.DataFrame | None = None,
    vix_lag: int = 1,
    use_cache: bool = True,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Build the per-session feature table for a symbol.

    Args:
        symbol: Underlying ticker.
        days_back: Calendar days of 5-min history to pull.
        bars: Pre-fetched 5-min bars; skips the Alpaca call when supplied
            (used by tests and by callers that already hold the data).
        vix_lag: Sessions to lag VIX by, matching replay's --vix-lag 1, so
            the feature is knowable at scan time rather than look-ahead.
        use_cache: Read/write a Parquet cache keyed by symbol + window.
        force_refresh: Rebuild even if a cache file exists.

    Returns:
        DataFrame indexed by session date, one column per feature, plus
        underscore-prefixed context columns.
    """
    cache = _CACHE_DIR / f"{symbol}_{days_back}d_lag{vix_lag}.parquet"
    if bars is None and use_cache and not force_refresh and cache.exists():
        df = pd.read_parquet(cache)
        df.index = pd.to_datetime(df.index).date
        logger.info("Loaded cached day profiles for %s (%d sessions)", symbol, len(df))
        return df

    if bars is None:
        from src.utils.alpaca_data import fetch_bars
        logger.info("Fetching %dd of 5-min bars for %s …", days_back, symbol)
        bars = fetch_bars(symbol, days_back=days_back, interval="5min")

    sessions = sorted(set(bars.index.date))
    if not sessions:
        return pd.DataFrame()

    atr_map = _daily_atr_map(symbol, days_back) if bars is not None else {}
    vix_by_date = _vix_map(sessions[0], sessions[-1])
    vix_days = sorted(vix_by_date)

    def vix_for(i: int) -> tuple[float | None, float | None]:
        """VIX as of `vix_lag` sessions before session i, plus the one before."""
        if not vix_days:
            return None, None
        day = sessions[i]
        prior = [d for d in vix_days if d <= day]
        if len(prior) <= vix_lag:
            return None, None
        cur = vix_by_date[prior[-1 - vix_lag]]
        prev = vix_by_date[prior[-2 - vix_lag]] if len(prior) > vix_lag + 1 else None
        return cur, prev

    by_date = {d: g for d, g in bars.groupby(bars.index.date)}
    prev_close: float | None = None
    rows, index = [], []
    for i, day in enumerate(sessions):
        day_bars = by_date[day]
        v, pv = vix_for(i)
        feats = compute_day_features(
            day_bars,
            prev_close=prev_close,
            daily_atr=atr_map.get(day),
            vix=v,
            prev_vix=pv,
        )
        rth = day_bars.between_time("09:30", "16:00")
        if len(rth):
            prev_close = float(rth["Close"].iloc[-1])
        if feats is None:
            continue
        rows.append(feats)
        index.append(day)

    table = pd.DataFrame(rows, index=pd.Index(index, name="date"))
    if use_cache and not table.empty:
        cache.parent.mkdir(parents=True, exist_ok=True)
        out = table.copy()
        out.index = pd.to_datetime(out.index)
        out.to_parquet(cache)
    logger.info("Built %d session profiles for %s", len(table), symbol)
    return table


# ── Similarity ─────────────────────────────────────────────────────────

def zscore_table(table: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    """Z-score the matched feature columns against the whole population."""
    cols = [c for c in weights if c in table.columns]
    sub = table[cols].astype(float)
    sd = sub.std(ddof=0).replace(0, np.nan)
    return ((sub - sub.mean()) / sd).fillna(0.0)


def reference_profile(table: pd.DataFrame, ref_dates: list[date]) -> pd.Series:
    """Mean feature vector of the reference days (the stretch being explained)."""
    present = [d for d in ref_dates if d in table.index]
    if not present:
        raise ValueError("None of the reference dates are in the feature table")
    return table.loc[present].mean(numeric_only=True)


def rank_analog_days(
    table: pd.DataFrame,
    ref_dates: list[date],
    *,
    weights: dict[str, float] | None = None,
    mode: str = "centroid",
    exclude_ref: bool = True,
) -> pd.DataFrame:
    """Rank every session by price-action similarity to the reference days.

    Args:
        table: output of `build_feature_table`.
        ref_dates: the sessions being explained (e.g. the drawdown's days).
        weights: per-feature weights; defaults to `FEATURE_SPECS` weights.
        mode: "centroid" scores distance to the reference mean vector — the
            regime as a whole. "nearest" scores distance to the closest
            single reference day, which surfaces one-off lookalikes that a
            centroid would average away.
        exclude_ref: drop the reference days themselves from the ranking.

    Returns:
        The table with a `distance` column, sorted closest-first.
    """
    weights = weights or DEFAULT_WEIGHTS
    weights = {k: v for k, v in weights.items() if v > 0 and k in table.columns}
    z = zscore_table(table, weights)
    w = pd.Series(weights)
    present = [d for d in ref_dates if d in table.index]
    if not present:
        raise ValueError("None of the reference dates are in the feature table")

    if mode == "centroid":
        target = z.loc[present].mean()
        diff = z - target
        dist = np.sqrt((diff.pow(2) * w).sum(axis=1) / w.sum())
    elif mode == "nearest":
        dists = []
        for d in present:
            diff = z - z.loc[d]
            dists.append(np.sqrt((diff.pow(2) * w).sum(axis=1) / w.sum()))
        dist = pd.concat(dists, axis=1).min(axis=1)
    else:
        raise ValueError(f"mode must be 'centroid' or 'nearest', got {mode!r}")

    out = table.copy()
    out["distance"] = dist
    if exclude_ref:
        out = out.drop(index=[d for d in present if d in out.index])
    return out.sort_values("distance")


def rank_analog_windows(
    table: pd.DataFrame,
    ref_dates: list[date],
    *,
    weights: dict[str, float] | None = None,
    window: int | None = None,
    exclude_from: date | None = None,
) -> pd.DataFrame:
    """Rank rolling windows of sessions by similarity to the reference stretch.

    Day-level matching finds lookalike sessions; window matching finds
    lookalike *regimes* — a run of days whose average geometry matches. That
    is the right unit when the question is about a drawdown rather than a
    single bad trade.

    Args:
        window: window length in sessions; defaults to len(ref_dates).
        exclude_from: drop windows ending on/after this date (normally the
            reference window's start) so the reference can't match itself.
    """
    weights = weights or DEFAULT_WEIGHTS
    weights = {k: v for k, v in weights.items() if v > 0 and k in table.columns}
    z = zscore_table(table, weights)
    w = pd.Series(weights)
    present = [d for d in ref_dates if d in table.index]
    if not present:
        raise ValueError("None of the reference dates are in the feature table")
    n = window or len(present)
    if n < 1 or n > len(z):
        raise ValueError(f"window {n} is out of range for {len(z)} sessions")

    target = z.loc[present].mean()
    rolled = z.rolling(n).mean().dropna()
    diff = rolled - target
    dist = np.sqrt((diff.pow(2) * w).sum(axis=1) / w.sum())

    idx = list(z.index)
    starts = [idx[idx.index(d) - n + 1] for d in rolled.index]
    out = pd.DataFrame({"start_date": starts, "distance": dist}, index=rolled.index)
    out.index.name = "end_date"
    if exclude_from is not None:
        out = out[out.index < exclude_from]
    return out.sort_values("distance")
