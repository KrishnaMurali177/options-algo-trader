"""Choppiness Detection — filters out low-conviction choppy market conditions.

Computes real-time choppiness metrics from 5-min bars to prevent false
sweet spot triggers on range-bound, whipsaw days.

Metrics:
  1. Choppiness Index (CI): Kaufman-style, based on net movement vs total path.
     CI > 0.6 → choppy, CI < 0.4 → trending.
  2. Direction Consistency: % of consecutive bars moving in the same direction.
  3. Bar Range Ratio: Avg 5-min bar range vs day range — small bars = chop.
  4. Direction Flip Rate: How often the signal direction (call/put) flips.

Used by:
  - scripts/scan_sweet_spot_today.py (suppress triggers on choppy days)
  - dashboard/app.py (display choppiness warning)
  - src/backtester.py (optional chop filter)
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class ChoppinessResult:
    """Result of the choppiness analysis."""
    choppiness_index: float       # 0.0 (trending) to 1.0 (choppy)
    direction_reversal_pct: float # % of bars that reverse direction
    avg_bar_range: float          # average 5-min bar high-low
    day_range: float              # day high - day low
    bar_range_ratio: float        # day_range / avg_bar_range (low = choppy)
    max_consecutive: int          # longest streak of same-direction bars
    is_choppy: bool               # overall choppy verdict
    chop_score: int               # 0-10 composite score (higher = choppier)
    summary: str


def compute_choppiness(
    bars: pd.DataFrame,
    *,
    lookback: int = 30,           # number of bars to consider
    ci_threshold: float = 0.55,   # choppiness index threshold
    reversal_threshold: float = 50.0,  # % direction reversals
    min_consecutive: int = 3,     # min consecutive same-dir bars expected in a trend
    bar_ratio_threshold: float = 10.0,  # day_range / avg_bar < this = choppy
) -> ChoppinessResult:
    """Compute choppiness metrics from 5-min bars.

    Args:
        bars: DataFrame with OHLCV columns, time-indexed.
        lookback: Number of recent bars to analyze.
        ci_threshold: CI above this = choppy.
        reversal_threshold: Direction reversal % above this = choppy.
        min_consecutive: Expect at least this many consecutive same-dir bars in a trend.
        bar_ratio_threshold: Day range / avg bar range below this = choppy.
    """
    if len(bars) < 6:
        return ChoppinessResult(
            choppiness_index=0.5, direction_reversal_pct=50.0,
            avg_bar_range=0.0, day_range=0.0, bar_range_ratio=0.0,
            max_consecutive=0, is_choppy=False, chop_score=5,
            summary="Insufficient data for choppiness analysis",
        )

    recent = bars.tail(lookback) if len(bars) > lookback else bars
    closes = recent["Close"].astype(float)
    highs = recent["High"].astype(float)
    lows = recent["Low"].astype(float)

    # ── 1. Choppiness Index (Kaufman efficiency ratio inverted) ──
    # CI = 1 - (net_movement / total_path)
    # CI near 1.0 = price went nowhere despite lots of movement = choppy
    # CI near 0.0 = price moved efficiently in one direction = trending
    net_movement = abs(float(closes.iloc[-1]) - float(closes.iloc[0]))
    total_path = float(closes.diff().abs().sum())
    if total_path > 0:
        efficiency = net_movement / total_path
        ci = 1.0 - efficiency
    else:
        ci = 0.5

    # ── 2. Direction Reversal Rate ──
    directions = closes.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    dir_changes = (directions.diff().abs() > 0).sum()
    reversal_pct = (dir_changes / max(len(recent) - 1, 1)) * 100

    # ── 3. Bar Range Metrics ──
    bar_ranges = (highs - lows).astype(float)
    avg_bar = float(bar_ranges.mean()) if len(bar_ranges) > 0 else 0.001
    day_high = float(highs.max())
    day_low = float(lows.min())
    day_range = day_high - day_low
    bar_ratio = day_range / avg_bar if avg_bar > 0 else 0

    # ── 4. Max Consecutive Same-Direction Bars ──
    max_consec = 1
    cur_consec = 1
    dir_vals = directions.values
    for i in range(2, len(dir_vals)):
        if dir_vals[i] == dir_vals[i-1] and dir_vals[i] != 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 1

    # ── Composite Chop Score (0-10) ──
    chop_score = 0

    # CI contribution (0-3)
    if ci >= 0.70:
        chop_score += 3
    elif ci >= 0.60:
        chop_score += 2
    elif ci >= ci_threshold:
        chop_score += 1

    # Reversal rate contribution (0-3)
    if reversal_pct >= 60:
        chop_score += 3
    elif reversal_pct >= 50:
        chop_score += 2
    elif reversal_pct >= reversal_threshold * 0.9:
        chop_score += 1

    # Bar ratio contribution (0-2): low ratio = small bars relative to range = chop
    if bar_ratio < 6:
        chop_score += 2
    elif bar_ratio < bar_ratio_threshold:
        chop_score += 1

    # Max consecutive contribution (0-2): short streaks = chop
    if max_consec <= 2:
        chop_score += 2
    elif max_consec < min_consecutive:
        chop_score += 1

    chop_score = min(10, chop_score)

    # ── Overall Verdict ──
    is_choppy = chop_score >= 6

    # ── Summary ──
    if chop_score >= 8:
        label = "🌊 EXTREMELY CHOPPY"
        advice = "Avoid trading — whipsaw risk very high."
    elif chop_score >= 6:
        label = "🌊 CHOPPY"
        advice = "Reduce position size or wait for cleaner setup."
    elif chop_score >= 4:
        label = "⚠️ MIXED"
        advice = "Proceed with caution — some chop detected."
    else:
        label = "✅ TRENDING"
        advice = "Good conditions for breakout trades."

    summary = (
        f"{label} (chop score {chop_score}/10)\n"
        f"  CI={ci:.2f} | Reversals={reversal_pct:.0f}% | "
        f"Max streak={max_consec} bars | "
        f"Day range=${day_range:.2f} / Avg bar=${avg_bar:.3f} ({bar_ratio:.1f}x)\n"
        f"  {advice}"
    )

    return ChoppinessResult(
        choppiness_index=round(ci, 3),
        direction_reversal_pct=round(reversal_pct, 1),
        avg_bar_range=round(avg_bar, 3),
        day_range=round(day_range, 2),
        bar_range_ratio=round(bar_ratio, 1),
        max_consecutive=max_consec,
        is_choppy=is_choppy,
        chop_score=chop_score,
        summary=summary,
    )


def compute_direction_stability(
    direction_history: list[str],
    min_stable_count: int = 2,
) -> tuple[bool, int]:
    """Check if the most recent direction has been stable for N evaluations.

    Args:
        direction_history: List of recent direction strings ("BUY CALL", "BUY PUT").
        min_stable_count: Require this many consecutive same-direction evaluations.

    Returns:
        (is_stable, streak_length)
    """
    if not direction_history:
        return False, 0

    current = direction_history[-1]
    streak = 0
    for d in reversed(direction_history):
        if d == current:
            streak += 1
        else:
            break

    return streak >= min_stable_count, streak


