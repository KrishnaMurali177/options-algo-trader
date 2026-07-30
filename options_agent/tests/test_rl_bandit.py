"""Tests for the RL regime-bandit layer.

The load-bearing test here is :func:`test_context_ignores_bars_at_or_after_decision_time`.
Everything else in this layer is only as trustworthy as the guarantee that the
context vector cannot see the outcome of the day it is deciding about.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.rl import presets
from src.rl.bandit import LinTS, LinUCB, greedy_select
from src.rl.dataset import REWARD_CLIP, _day_reward, _drop_incomplete_days
from src.rl.features import (
    DECISION_TIME,
    FEATURE_NAMES,
    N_FEATURES,
    Standardizer,
    build_context,
    visible_bars,
)


# ── fixtures ─────────────────────────────────────────────────────────────
def _session(day: str, base: float = 500.0, n: int = 78, seed: int = 0) -> pd.DataFrame:
    """One synthetic 5-min RTH session (09:30–16:00) in ET."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(f"{day} 09:30", periods=n, freq="5min", tz="America/New_York")
    steps = rng.normal(0, 0.35, n).cumsum()
    close = base + steps
    high = close + np.abs(rng.normal(0, 0.15, n))
    low = close - np.abs(rng.normal(0, 0.15, n))
    open_ = np.r_[base, close[:-1]]
    vol = rng.integers(1_000, 20_000, n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


@pytest.fixture
def day_bars() -> pd.DataFrame:
    return _session("2026-06-10", seed=1)


@pytest.fixture
def prior_bars() -> pd.DataFrame:
    return pd.concat([_session("2026-06-08", base=498.0, seed=2),
                      _session("2026-06-09", base=499.0, seed=3)])


# ── the no-look-ahead guarantee ───────────────────────────────────────────
def test_context_ignores_bars_at_or_after_decision_time(day_bars, prior_bars):
    """Corrupting every bar at/after 10:00 must not move the context vector.

    If this ever fails, the bandit is choosing an arm using information about how
    the day it is choosing for turned out, and every downstream number is void.
    """
    from datetime import date

    good = build_context("SPY", date(2026, 6, 10), day_bars, prior_bars,
                         vix_prev=17.0, vix_prev2=16.5)

    poisoned = day_bars.copy()
    after = poisoned.index.time >= DECISION_TIME
    assert after.sum() > 0, "fixture must contain post-decision bars"
    poisoned.loc[after, ["Open", "High", "Low", "Close", "Volume"]] = np.nan

    also = build_context("SPY", date(2026, 6, 10), poisoned, prior_bars,
                         vix_prev=17.0, vix_prev2=16.5)
    np.testing.assert_allclose(good, also, rtol=0, atol=0)


def test_context_ignores_wild_future_values(day_bars, prior_bars):
    """Same guarantee, but with absurd finite values rather than NaN.

    NaN could be masked by nan_to_num; a 100x price spike could not.
    """
    from datetime import date

    good = build_context("SPY", date(2026, 6, 10), day_bars, prior_bars, 17.0, 16.5)
    poisoned = day_bars.copy()
    after = poisoned.index.time >= DECISION_TIME
    poisoned.loc[after, ["Open", "High", "Low", "Close"]] *= 100.0
    poisoned.loc[after, "Volume"] *= 1000
    also = build_context("SPY", date(2026, 6, 10), poisoned, prior_bars, 17.0, 16.5)
    np.testing.assert_allclose(good, also, rtol=0, atol=0)


def test_visible_bars_is_strictly_before_decision_time(day_bars):
    vis = visible_bars(day_bars)
    assert len(vis) > 0
    assert all(t < DECISION_TIME for t in vis.index.time)
    # 09:30..09:55 inclusive is 6 five-minute bars.
    assert len(vis) == 6


def test_context_shape_and_finiteness(day_bars, prior_bars):
    from datetime import date

    x = build_context("QQQ", date(2026, 6, 10), day_bars, prior_bars, 17.0, 16.5)
    assert x.shape == (N_FEATURES,)
    assert len(FEATURE_NAMES) == N_FEATURES
    assert np.all(np.isfinite(x))
    assert x[0] == 1.0, "bias term must be first"


def test_context_survives_missing_prior_bars(day_bars):
    """First sessions in a series have no warmup; that must not crash the build."""
    from datetime import date

    x = build_context("SPY", date(2026, 6, 10), day_bars, None, 17.0, None)
    assert np.all(np.isfinite(x))


def test_symbol_dummies_are_exclusive(day_bars, prior_bars):
    from datetime import date

    i_etf = FEATURE_NAMES.index("is_etf")
    i_0dte = FEATURE_NAMES.index("has_daily_0dte")
    spy = build_context("SPY", date(2026, 6, 10), day_bars, prior_bars, 17.0, 16.5)
    nvda = build_context("NVDA", date(2026, 6, 10), day_bars, prior_bars, 17.0, 16.5)
    assert spy[i_etf] == 1.0 and spy[i_0dte] == 1.0
    assert nvda[i_etf] == 0.0 and nvda[i_0dte] == 0.0
    assert spy[FEATURE_NAMES.index("sym_spy")] == 1.0
    assert spy[FEATURE_NAMES.index("sym_qqq")] == 0.0


# ── the feature-cache invariant ──────────────────────────────────────────
def test_no_arm_varies_scan_window():
    """Guards the shared feature_cache in dataset.build_symbol().

    The cache is keyed (date, scan_bar_ordinal). If an arm moved the scan window
    the ordinals would mean different bars per arm and arms would read each
    other's opening-range features.
    """
    presets.validate_arms()  # must not raise
    for name, delta in presets.ARMS.items():
        if delta:
            assert not set(delta) & set(presets.SCAN_WINDOW_PARAMS), name


def test_validate_arms_rejects_scan_window_change():
    with pytest.raises(ValueError, match="feature_cache"):
        presets.validate_arms({"bad": {"scan_end": "12:00"}})


def test_sit_out_is_present_and_not_replayed():
    assert presets.SIT_OUT in presets.ARMS
    assert presets.ARMS[presets.SIT_OUT] is None
    assert presets.SIT_OUT not in presets.tradable_arms()
    assert presets.ARM_NAMES[presets.SIT_OUT_INDEX] == presets.SIT_OUT


def test_resolve_arm_rejects_unknown_arm():
    with pytest.raises(KeyError, match="Unknown arm"):
        presets.resolve_arm("no_such_arm", {"min_chop": 2})


def test_resolve_arm_rejects_params_replay_day_does_not_accept():
    """Catches typos at the arm definition instead of deep inside replay_day."""
    base = {"min_chop": 2, "max_chop": 5}
    presets.ARMS["_tmp"] = {"not_a_replay_param": 1}
    try:
        with pytest.raises(KeyError, match="does not accept"):
            presets.resolve_arm("_tmp", base)
    finally:
        presets.ARMS.pop("_tmp", None)


def test_resolve_arm_does_not_mutate_base():
    base = {"min_cascade": 3, "min_quality": 4, "max_quality": 7, "max_chop": 5}
    out = presets.resolve_arm("selective", base)
    assert base["min_cascade"] == 3, "base must not be mutated"
    assert out["min_cascade"] == 4


# ── reward shaping ───────────────────────────────────────────────────────
def test_day_reward_uses_return_on_premium():
    """Reward is pnl/est_premium, not raw pnl — theta must be charged."""
    triggers = [
        {"pnl": 0.50, "est_premium": 2.00, "pricing": "real", "option_pnl_sized": 150.0},
        {"pnl": -1.00, "est_premium": 2.00, "pricing": "real", "option_pnl_sized": -300.0},
    ]
    r, diag = _day_reward(triggers)
    assert r == pytest.approx(0.25 - 0.50)
    assert diag["n_trades"] == 2 and diag["n_real"] == 2
    assert diag["dollars_sized"] == pytest.approx(-150.0)


def test_day_reward_is_clipped():
    triggers = [{"pnl": 10.0, "est_premium": 1.0, "pricing": "real"}] * 3
    r, diag = _day_reward(triggers)
    assert r == REWARD_CLIP[1]
    assert diag["raw_reward"] > REWARD_CLIP[1], "clip must not overwrite the raw value"


def test_day_reward_empty_is_zero():
    r, diag = _day_reward([])
    assert r == 0.0 and diag["n_trades"] == 0


def test_day_reward_skips_unusable_premium():
    """A trigger with no premium can't be scored; it must not become a NaN."""
    r, _ = _day_reward([{"pnl": 1.0, "est_premium": 0.0, "pricing": "synth"},
                        {"pnl": 1.0, "est_premium": None, "pricing": "synth"}])
    assert r == 0.0


def test_drop_incomplete_days():
    rows = [{"date": "d1"}, {"date": "d1"}, {"date": "d2"}]
    assert len(_drop_incomplete_days(rows, expected=2)) == 2


# ── bandit learning ──────────────────────────────────────────────────────
def _synthetic_problem(n: int = 4000, seed: int = 0):
    """Two-feature contexts where the best of 3 arms flips on feature 1."""
    rng = np.random.default_rng(seed)
    X = np.c_[np.ones(n), rng.normal(size=n), rng.normal(size=n)]
    # arm 0 good when x1<0, arm 1 good when x1>0, arm 2 (sit-out analogue) is 0.
    R = np.c_[-2.0 * X[:, 1], 2.0 * X[:, 1], np.zeros(n)]
    R = R + rng.normal(0, 0.3, R.shape)
    return X, R


def test_linucb_learns_context_dependent_best_arm():
    X, R = _synthetic_problem()
    model = LinUCB(n_arms=3, n_features=X.shape[1], alpha=0.0)
    model.fit_full_feedback(X[:3000], R[:3000])
    chosen = greedy_select(model, X[3000:])
    best = R[3000:].argmax(axis=1)
    assert (chosen == best).mean() > 0.9


def test_linucb_regret_shrinks_with_more_data():
    X, R = _synthetic_problem(n=6000, seed=3)
    Xte, Rte = X[5000:], R[5000:]
    oracle = Rte.max(axis=1).sum()

    def regret(n_train: int) -> float:
        m = LinUCB(3, X.shape[1], alpha=0.0).fit_full_feedback(X[:n_train], R[:n_train])
        picks = greedy_select(m, Xte)
        return oracle - Rte[np.arange(len(picks)), picks].sum()

    assert regret(2000) < regret(60)


def test_bandit_prefers_sit_out_when_every_arm_loses():
    """The whole point of the sit-out arm: it must win when trading is -EV."""
    rng = np.random.default_rng(1)
    n = 2000
    X = np.c_[np.ones(n), rng.normal(size=n)]
    # arms 0,1 lose money regardless of context; arm 2 is flat (sit out).
    R = np.c_[rng.normal(-0.8, 0.2, n), rng.normal(-0.5, 0.2, n), np.zeros(n)]
    model = LinUCB(3, X.shape[1], alpha=0.0).fit_full_feedback(X, R)
    picks = greedy_select(model, X)
    assert (picks == 2).mean() > 0.95


def test_lints_learns_and_is_seed_reproducible():
    X, R = _synthetic_problem(n=3000, seed=5)
    a = LinTS(3, X.shape[1], seed=42).fit_full_feedback(X[:2000], R[:2000])
    b = LinTS(3, X.shape[1], seed=42).fit_full_feedback(X[:2000], R[:2000])
    x = X[2500]
    np.testing.assert_allclose(a.sample_rewards(x), b.sample_rewards(x))
    picks = greedy_select(a, X[2000:])
    assert (picks == R[2000:].argmax(axis=1)).mean() > 0.85


def test_full_feedback_matches_incremental_updates():
    """Vectorised fit must equal the row-by-row update it replaces."""
    X, R = _synthetic_problem(n=300, seed=7)
    batch = LinUCB(3, X.shape[1]).fit_full_feedback(X, R)
    incr = LinUCB(3, X.shape[1])
    for i in range(len(X)):
        for a in range(3):
            incr.update(a, X[i], R[i, a])
    np.testing.assert_allclose(batch.A, incr.A, rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(batch.b, incr.b, rtol=1e-9, atol=1e-9)


def test_linucb_rejects_mismatched_shapes():
    m = LinUCB(3, 4)
    with pytest.raises(ValueError):
        m.fit_full_feedback(np.zeros((5, 4)), np.zeros((4, 3)))
    with pytest.raises(ValueError):
        m.fit_full_feedback(np.zeros((5, 4)), np.zeros((5, 2)))


# ── standardizer ─────────────────────────────────────────────────────────
def test_standardizer_leaves_dummies_and_bias_alone():
    rng = np.random.default_rng(0)
    X = rng.normal(5.0, 3.0, size=(500, N_FEATURES))
    X[:, 0] = 1.0
    s = Standardizer().fit(X)
    Z = s.transform(X)
    np.testing.assert_allclose(Z[:, 0], 1.0)
    for i, name in enumerate(FEATURE_NAMES):
        if name.startswith(("dow_", "sym_")) or name in ("bias", "is_etf", "has_daily_0dte"):
            np.testing.assert_allclose(Z[:, i], X[:, i])
        else:
            assert abs(Z[:, i].mean()) < 1e-9


def test_standardizer_handles_constant_column():
    X = np.ones((10, N_FEATURES))
    Z = Standardizer().fit_transform(X)
    assert np.all(np.isfinite(Z))


def test_standardizer_requires_fit():
    with pytest.raises(RuntimeError):
        Standardizer().transform(np.ones((2, N_FEATURES)))


# ── walk-forward plumbing ────────────────────────────────────────────────
def test_walk_forward_purges_and_reports(monkeypatch):
    from src.rl import evaluate

    n_days, n_arms = 400, len(presets.ARM_NAMES)
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2025-08-01", periods=n_days)
    X = np.c_[np.ones(n_days), rng.normal(size=(n_days, N_FEATURES - 1))]
    R = rng.normal(0, 0.5, (n_days, n_arms))
    R[:, presets.SIT_OUT_INDEX] = 0.0
    keys = pd.DataFrame({"date": dates.date, "symbol": "SPY"})

    per_day, folds = evaluate.walk_forward(X, R, keys, alpha=0.0)
    assert len(folds) > 0
    assert not per_day.empty

    # Purge: no test day may fall in the embargo window after a fold start.
    for f in folds:
        fold_days = pd.to_datetime(per_day.loc[per_day.fold == f.fold, "date"])
        start = pd.Period(f.fold, freq="M").start_time
        assert fold_days.min() >= start + pd.Timedelta(days=evaluate.EMBARGO_DAYS)

    summary = evaluate.summarize(per_day)
    assert set(summary.policy) == {"bandit", "golden_always", "best_fixed_arm", "oracle"}
    g = summary.set_index("policy")["total_R"]
    # Oracle is an upper bound by construction; it must dominate everything.
    assert g["oracle"] >= g["bandit"] - 1e-9
    assert g["oracle"] >= g["best_fixed_arm"] - 1e-9


def test_verdict_is_strict_about_best_fixed_arm():
    from src.rl.evaluate import verdict

    def s(bandit, golden, fixed, oracle):
        return pd.DataFrame({
            "policy": ["bandit", "golden_always", "best_fixed_arm", "oracle"],
            "total_R": [bandit, golden, fixed, oracle]})

    assert verdict(s(10, 5, 7, 20)).startswith("PROCEED")
    # Beats golden but not the best static arm => not worth the machinery.
    assert "STOP (use static tuning)" in verdict(s(6, 5, 9, 20))
    assert verdict(s(3, 5, 9, 20)).startswith("STOP")
    assert verdict(pd.DataFrame()).startswith("NO RESULT")
