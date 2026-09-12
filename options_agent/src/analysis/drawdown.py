"""Drawdown episode detection and comparison for a daily P&L series.

The question this answers: "we are N days and $X into a losing stretch — how
often has a stretch like this happened before, and when?"

Two complementary views, because they answer different things:

  1. Episodes (`find_drawdown_episodes`) — peak-to-trough-to-recovery runs.
     Use when you care about the *shape* of a losing stretch and whether it
     ever recovered. Episodes are unequal length, so comparison is by
     z-scored signature.

  2. Fixed windows (`rolling_window_stats`) — every N-trading-day window in
     history, scored on the same stats. Use when you want *dates*: "give me
     the 20 worst 16-day stretches" is a window question, not an episode one.

Both are P&L-only. Price-action similarity lives in `day_profile`; the two
are joined by date in `scripts/find_analog_days.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd


# ── Loading ────────────────────────────────────────────────────────────

def daily_pnl_from_triggers(
    paths: list[str | Path],
    *,
    pnl_field: str = "option_pnl_sized",
    contracts: int = 1,
) -> pd.Series:
    """Collapse one or more replay `--export-triggers` JSON files to daily P&L.

    Multiple paths are summed per date, which is how a multi-symbol book
    (SPY + QQQ) actually experiences a drawdown — both legs share a day.

    Args:
        paths: JSON files written by replay_sweet_spot.py --export-triggers.
        pnl_field: Trigger field to sum. "option_pnl_sized" is dollars at the
            replay's cascade sizing; "option_pnl_100x" is dollars per contract.
        contracts: Multiplier applied after summing (live trades 1 ct).

    Returns:
        Series of daily P&L in dollars, indexed by `datetime.date`, sorted,
        with no gaps filled (non-trading and no-trigger days are absent).
    """
    totals: dict[date, float] = {}
    for p in paths:
        rows = json.loads(Path(p).read_text())
        for t in rows:
            val = t.get(pnl_field)
            if val is None:
                continue
            d = date.fromisoformat(str(t["date"])[:10])
            totals[d] = totals.get(d, 0.0) + float(val) * contracts
    if not totals:
        return pd.Series(dtype=float)
    s = pd.Series(totals).sort_index()
    s.index.name = "date"
    return s


def equity_curve(pnl: pd.Series, start_equity: float = 0.0) -> pd.Series:
    """Cumulative equity from a daily P&L series."""
    return pnl.cumsum() + start_equity


# ── Episodes ───────────────────────────────────────────────────────────

@dataclass
class DrawdownEpisode:
    """One peak-to-trough(-to-recovery) run in an equity curve."""
    peak_date: date          # last date at the old high
    start_date: date         # first date underwater
    trough_date: date
    end_date: date | None    # date equity regained the peak; None if ongoing
    depth: float             # max dollars below the peak (positive number)
    depth_pct: float         # depth as % of equity at the peak (0 if peak<=0)
    days_to_trough: int      # trading days start → trough, inclusive
    underwater_days: int     # trading days start → recovery (or series end)
    recovery_days: int | None
    red_days: int
    total_days: int          # trading days in start → trough window
    red_frac: float
    longest_red_streak: int
    worst_day: float
    avg_day: float
    ongoing: bool

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("peak_date", "start_date", "trough_date", "end_date"):
            d[k] = str(d[k]) if d[k] is not None else None
        return d

    @property
    def label(self) -> str:
        return f"{self.start_date}→{self.trough_date}"


def _streak(flags: np.ndarray) -> int:
    """Longest run of True in a boolean array."""
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def find_drawdown_episodes(
    pnl: pd.Series,
    *,
    min_depth: float = 0.0,
    min_days: int = 1,
) -> list[DrawdownEpisode]:
    """Split an equity curve into peak-to-trough drawdown episodes.

    An episode opens on the first day equity closes below the running peak and
    closes when equity regains that peak. The final episode is marked
    `ongoing` when the series ends underwater — which is the case you care
    about when asking "is the current stretch normal?".

    Args:
        pnl: Daily P&L, date-indexed and sorted.
        min_depth: Discard episodes shallower than this many dollars.
        min_days: Discard episodes shorter than this many trading days.
    """
    if pnl.empty:
        return []

    eq = equity_curve(pnl)
    dates = list(eq.index)
    vals = eq.to_numpy(dtype=float)
    pnl_vals = pnl.to_numpy(dtype=float)

    episodes: list[DrawdownEpisode] = []
    peak = vals[0]
    peak_i = 0
    open_i: int | None = None
    trough_i = 0

    def close_episode(start_i: int, trough_i: int, end_i: int | None, peak_i: int):
        seg = pnl_vals[start_i:trough_i + 1]
        reds = seg < 0
        depth = float(peak - vals[trough_i])
        peak_eq = float(vals[peak_i])
        last_i = end_i if end_i is not None else len(vals) - 1
        ep = DrawdownEpisode(
            peak_date=dates[peak_i],
            start_date=dates[start_i],
            trough_date=dates[trough_i],
            end_date=dates[end_i] if end_i is not None else None,
            depth=round(depth, 2),
            depth_pct=round(depth / peak_eq * 100, 2) if peak_eq > 0 else 0.0,
            days_to_trough=trough_i - start_i + 1,
            underwater_days=last_i - start_i + 1,
            recovery_days=(end_i - trough_i) if end_i is not None else None,
            red_days=int(reds.sum()),
            total_days=len(seg),
            red_frac=round(float(reds.mean()), 3) if len(seg) else 0.0,
            longest_red_streak=_streak(reds),
            worst_day=round(float(seg.min()), 2) if len(seg) else 0.0,
            avg_day=round(float(seg.mean()), 2) if len(seg) else 0.0,
            ongoing=end_i is None,
        )
        if ep.depth >= min_depth and ep.days_to_trough >= min_days:
            episodes.append(ep)

    for i in range(1, len(vals)):
        if vals[i] >= peak:
            if open_i is not None:
                close_episode(open_i, trough_i, i, peak_i)
                open_i = None
            peak = vals[i]
            peak_i = i
        else:
            if open_i is None:
                open_i = i
                trough_i = i
            elif vals[i] < vals[trough_i]:
                trough_i = i

    if open_i is not None:
        close_episode(open_i, trough_i, None, peak_i)

    return episodes


# ── Fixed-length windows ───────────────────────────────────────────────

_WINDOW_STATS = (
    "net_pnl",
    "max_drawdown",
    "red_frac",
    "longest_red_streak",
    "worst_day",
    "avg_day",
    "pnl_std",
)


def window_stats(pnl: pd.Series) -> dict:
    """Summarize one slice of daily P&L with the stats used for matching."""
    v = pnl.to_numpy(dtype=float)
    if len(v) == 0:
        return {k: 0.0 for k in _WINDOW_STATS} | {"days": 0}
    eq = np.cumsum(v)
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], eq])) -
                np.concatenate([[0.0], eq])).max())
    reds = v < 0
    return {
        "days": len(v),
        "net_pnl": round(float(eq[-1]), 2),
        "max_drawdown": round(dd, 2),
        "red_frac": round(float(reds.mean()), 3),
        "longest_red_streak": _streak(reds),
        "worst_day": round(float(v.min()), 2),
        "avg_day": round(float(v.mean()), 2),
        "pnl_std": round(float(v.std(ddof=0)), 2),
    }


def rolling_window_stats(pnl: pd.Series, window: int) -> pd.DataFrame:
    """Stats for every contiguous `window`-length slice, indexed by end date.

    "Contiguous" means consecutive entries in `pnl` — i.e. trading days that
    produced trades — not calendar days.
    """
    rows = []
    idx = list(pnl.index)
    for end in range(window - 1, len(pnl)):
        sl = pnl.iloc[end - window + 1:end + 1]
        rows.append({"start_date": idx[end - window + 1], "end_date": idx[end],
                     **window_stats(sl)})
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("end_date")


# ── Similarity ─────────────────────────────────────────────────────────

# Depth and duration dominate: a drawdown is "like ours" mostly when it lost
# comparable money over a comparable stretch. Texture stats break ties.
EPISODE_WEIGHTS = {
    "depth": 2.0,
    "days_to_trough": 1.5,
    "red_frac": 1.0,
    "longest_red_streak": 1.0,
    "worst_day": 0.75,
    "avg_day": 0.75,
}

WINDOW_WEIGHTS = {
    "net_pnl": 2.0,
    "max_drawdown": 2.0,
    "red_frac": 1.0,
    "longest_red_streak": 1.0,
    "worst_day": 0.75,
    "avg_day": 0.75,
}


def _weighted_distance(
    table: pd.DataFrame,
    target: pd.Series,
    weights: dict[str, float],
) -> pd.Series:
    """Weighted Euclidean distance to `target`, z-scored per column.

    Columns are standardized against the candidate population so dollars and
    day-counts are commensurable; the target is standardized with the same
    mean/std so it sits in the same space even if it is an outlier.
    """
    cols = [c for c in weights if c in table.columns]
    sub = table[cols].astype(float)
    mu, sd = sub.mean(), sub.std(ddof=0).replace(0, np.nan)
    z = (sub - mu) / sd
    zt = (target[cols].astype(float) - mu) / sd
    w = pd.Series({c: weights[c] for c in cols})
    diff = (z - zt).fillna(0.0)
    return np.sqrt((diff.pow(2) * w).sum(axis=1) / w.sum())


def rank_similar_episodes(
    episodes: list[DrawdownEpisode],
    target: DrawdownEpisode,
    *,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Rank historical episodes by similarity to `target` (closest first)."""
    weights = weights or EPISODE_WEIGHTS
    rows = [e.as_dict() for e in episodes if e.label != target.label]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("start_date")
    df["distance"] = _weighted_distance(df, pd.Series(target.as_dict()), weights)
    return df.sort_values("distance")


def rank_similar_windows(
    stats: pd.DataFrame,
    target: dict | pd.Series,
    *,
    weights: dict[str, float] | None = None,
    exclude_after: date | None = None,
) -> pd.DataFrame:
    """Rank fixed-length windows by similarity to the target window.

    Args:
        stats: output of `rolling_window_stats`.
        target: the reference window's stats (`window_stats` output).
        exclude_after: drop windows ending on/after this date, so the
            reference stretch and its overlapping neighbours don't rank
            themselves as their own best analogs.
    """
    weights = weights or WINDOW_WEIGHTS
    df = stats.copy()
    if exclude_after is not None:
        df = df[df.index < exclude_after]
    if df.empty:
        return df
    df["distance"] = _weighted_distance(df, pd.Series(dict(target)), weights)
    return df.sort_values("distance")
