"""Tests for the drawdown / day-profile analog helpers."""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.analysis.drawdown import (
    daily_pnl_from_triggers,
    find_drawdown_episodes,
    rank_similar_windows,
    rolling_window_stats,
    window_stats,
)
from src.analysis.day_profile import (
    build_feature_table,
    compute_day_features,
    parse_weights,
    rank_analog_days,
    rank_analog_windows,
)


# ── synthetic session builder ──────────────────────────────────────────

def make_session(day: date, path: list[float], *, volume: float = 1_000.0) -> pd.DataFrame:
    """Build 5-min RTH bars from a list of closes (78 bars = 09:30→15:55)."""
    idx = pd.date_range(f"{day} 09:30", periods=len(path), freq="5min", tz="America/New_York")
    closes = np.array(path, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame(
        {
            "Open": opens,
            "High": np.maximum(opens, closes) + 0.02,
            "Low": np.minimum(opens, closes) - 0.02,
            "Close": closes,
            "Volume": np.full(len(path), volume),
        },
        index=idx,
    )


def trend_day(day: date, base: float = 500.0, slope: float = 0.02) -> pd.DataFrame:
    """Opening hour drifts up, then keeps going — the agent's good day."""
    return make_session(day, [base + slope * i for i in range(78)])


def reversal_day(day: date, base: float = 500.0) -> pd.DataFrame:
    """Opening hour rips up, then the whole day reverses — the wrong-side day."""
    up = [base + 0.05 * i for i in range(12)]          # 09:30–10:25 up 0.55
    down = [up[-1] - 0.05 * i for i in range(1, 66)]   # reverses all day
    return make_session(day, up + down)


# ── day features ───────────────────────────────────────────────────────

def test_trend_day_has_positive_follow_through():
    f = compute_day_features(trend_day(date(2026, 3, 2)))
    assert f is not None
    assert f["or_dir"] == 1.0
    assert f["follow_through_r"] > 1.0        # continued well beyond the OR
    assert f["mae_r"] < f["mfe_r"]
    assert f["scan_efficiency"] > 0.9         # clean one-way tape
    assert f["excursion_ratio"] > 0.8


def test_reversal_day_is_the_wrong_side_signature():
    f = compute_day_features(reversal_day(date(2026, 3, 3)))
    assert f is not None
    assert f["or_dir"] == 1.0                 # opening hour said "up"
    assert f["follow_through_r"] < 0          # the rest of the day said otherwise
    assert f["mae_r"] > f["mfe_r"]            # ran against the breakout
    assert f["excursion_ratio"] < 0.5
    assert f["afternoon_flip"] == -1.0


def test_features_are_scale_free():
    """The same shape at a different price level scores the same geometry."""
    a = compute_day_features(reversal_day(date(2026, 3, 3), base=500.0))
    b = compute_day_features(reversal_day(date(2026, 3, 4), base=50.0))
    # Ratios in OR-width units must match; bps of open are near-identical too.
    assert a["follow_through_r"] == pytest.approx(b["follow_through_r"], rel=0.05)
    assert a["excursion_ratio"] == pytest.approx(b["excursion_ratio"], rel=0.05)


def test_short_session_is_rejected():
    assert compute_day_features(make_session(date(2026, 3, 5), [500.0] * 10)) is None


def test_gap_uses_prior_close():
    f = compute_day_features(trend_day(date(2026, 3, 6), base=505.0), prev_close=500.0)
    assert f["gap_bps"] == pytest.approx((505.0 - 500.0) / 505.0 * 10_000, rel=1e-6)


# ── feature table + ranking ────────────────────────────────────────────

def _mixed_bars() -> tuple[pd.DataFrame, list[date], list[date]]:
    """20 trend sessions and 10 reversal sessions, interleaved deterministically."""
    days = [date(2026, 1, 5) + timedelta(days=i) for i in range(42)]
    days = [d for d in days if d.weekday() < 5][:30]
    frames, trends, revs = [], [], []
    for i, d in enumerate(days):
        if i % 3 == 2:
            frames.append(reversal_day(d)); revs.append(d)
        else:
            frames.append(trend_day(d)); trends.append(d)
    return pd.concat(frames), trends, revs


def test_feature_table_and_ranking_separates_regimes():
    bars, trends, revs = _mixed_bars()
    table = build_feature_table("TEST", bars=bars, use_cache=False)
    assert len(table) == 30
    assert not table["follow_through_r"].isna().any()

    ranked = rank_analog_days(table, ref_dates=revs[:3], weights=parse_weights(None))
    top = list(ranked.head(6).index)
    # Every close analog of a reversal day should be another reversal day.
    assert set(top) <= set(revs)
    assert ranked.loc[trends]["distance"].min() > ranked.loc[top]["distance"].max()


def test_rank_analog_windows_excludes_reference():
    bars, _, revs = _mixed_bars()
    table = build_feature_table("TEST", bars=bars, use_cache=False)
    ref = list(table.index[-4:])
    out = rank_analog_windows(table, ref, exclude_from=ref[0])
    assert not out.empty
    assert all(end < ref[0] for end in out.index)


def test_parse_weights_rejects_typos():
    w = parse_weights("follow_through_r=5,chop_score=0")
    assert w["follow_through_r"] == 5.0
    assert "chop_score" not in w          # zero weight drops the feature
    with pytest.raises(KeyError):
        parse_weights("follow_througth_r=5")


# ── drawdown side ──────────────────────────────────────────────────────

def _pnl(values: list[float]) -> pd.Series:
    days = pd.Index([date(2026, 1, 1) + timedelta(days=i) for i in range(len(values))],
                    name="date")
    return pd.Series(values, index=days, dtype=float)


def test_episode_depth_and_recovery():
    # up 100, then -30/-40/-30 (trough -100), then +50/+60 back above the peak
    eps = find_drawdown_episodes(_pnl([100, -30, -40, -30, 50, 60]))
    assert len(eps) == 1
    e = eps[0]
    assert e.depth == 100.0
    assert e.days_to_trough == 3
    assert e.red_days == 3
    assert e.longest_red_streak == 3
    assert e.recovery_days == 2
    assert e.ongoing is False


def test_ongoing_episode_is_flagged():
    eps = find_drawdown_episodes(_pnl([100, -20, -20, -20]))
    assert len(eps) == 1 and eps[0].ongoing is True
    assert eps[0].recovery_days is None
    assert eps[0].depth == 60.0


def test_no_drawdown_yields_no_episodes():
    assert find_drawdown_episodes(_pnl([10, 20, 30])) == []


def test_window_stats_and_similarity():
    pnl = _pnl([10, 10, 10, -50, -50, -50, 10, 10, 10, -48, -52, -49])
    roll = rolling_window_stats(pnl, 3)
    assert len(roll) == len(pnl) - 2
    target = window_stats(pnl.iloc[-3:])
    ranked = rank_similar_windows(roll, target, exclude_after=pnl.index[-3])
    # The earlier -50/-50/-50 stretch is the obvious analog.
    assert str(ranked.index[0]) == str(pnl.index[5])


def test_daily_pnl_from_triggers_sums_symbols(tmp_path):
    import json
    a = tmp_path / "spy.json"
    b = tmp_path / "qqq.json"
    a.write_text(json.dumps([{"date": "2026-05-01", "option_pnl_sized": -100.0},
                             {"date": "2026-05-01", "option_pnl_sized": 40.0}]))
    b.write_text(json.dumps([{"date": "2026-05-01", "option_pnl_sized": -25.0},
                             {"date": "2026-05-02", "option_pnl_sized": 10.0}]))
    s = daily_pnl_from_triggers([a, b])
    assert s.loc[date(2026, 5, 1)] == pytest.approx(-85.0)
    assert s.loc[date(2026, 5, 2)] == pytest.approx(10.0)
