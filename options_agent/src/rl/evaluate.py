"""Purged walk-forward evaluation of the regime bandit.

What "honest" means here
------------------------
The existing sweep (``sweep_mag7_params.sweep_symbol``) uses a single
chronological 75/25 split with no purge gap, and reports the best-of-486 combos
on the holdout. That overstates: picking the top-K on train and then reading
their holdout numbers is fine, but with one split there is exactly one holdout
observation, and a 486-wide search finds a train winner by luck.

This module instead:

* **Expanding-window walk-forward.** Fit on everything strictly before month T,
  decide month T, roll. Every out-of-sample day is predicted by a model that
  never saw it.
* **Purge/embargo.** ``prior_bars`` spans :data:`dataset.PRIOR_DAYS_CONTEXT`
  sessions, so a day near a fold boundary shares warmup bars with the training
  window. Days within :data:`EMBARGO_DAYS` of the boundary are dropped from the
  test fold rather than being quietly leaked.
* **Standardizer fitted per fold**, on training rows only.
* **Three reference policies**, because "beats golden" alone is a weak claim:

  ``golden_always``      the incumbent — the bar that actually matters
  ``best_fixed_arm``     the single best arm chosen *in hindsight* on the test
                         fold — what a perfect grid search would have found.
                         A bandit that cannot beat this adds nothing over
                         static tuning.
  ``oracle``             per-day best arm in hindsight — an upper bound, not
                         attainable; its gap to the bandit is the headroom.

Reported numbers are the R-multiple rewards the dataset stores, plus dollar
totals via the same ``compute_metrics`` the existing sweep uses, so results are
comparable to the historical reports in ``options_agent/docs/``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.rl import presets
from src.rl.bandit import LinTS, LinUCB, greedy_select
from src.rl.dataset import PRIOR_DAYS_CONTEXT
from src.rl.features import Standardizer

logger = logging.getLogger(__name__)

#: Test-fold days this close to the training boundary are dropped. One more than
#: the warmup window, so no test day shares prior_bars with a training day.
EMBARGO_DAYS = PRIOR_DAYS_CONTEXT + 1

#: Don't fit a fold on a handful of rows; skip until there is enough history.
MIN_TRAIN_ROWS = 200


@dataclass
class FoldResult:
    fold: str
    n_train: int
    n_test: int
    rewards: dict[str, float] = field(default_factory=dict)
    arm_counts: dict[str, int] = field(default_factory=dict)


def month_folds(dates: pd.Series) -> list[pd.Period]:
    """Sorted list of month periods present in the data (each becomes a test fold)."""
    return sorted(pd.PeriodIndex(pd.to_datetime(dates), freq="M").unique())


def walk_forward(X: np.ndarray, R: np.ndarray, keys: pd.DataFrame,
                 model_kind: str = "linucb", alpha: float = 0.0,
                 arm_names: list[str] | None = None,
                 seed: int = 0) -> tuple[pd.DataFrame, list[FoldResult]]:
    """Run expanding-window monthly walk-forward.

    ``alpha`` defaults to 0 (greedy). Exploration is a cost paid to learn online;
    charging it to an offline backtest understates a policy that has already seen
    the training window. Set alpha > 0 to see how much the UCB bonus perturbs
    selection — if the edge only survives at alpha=0, the model is not confident.

    Returns per-test-day selections and per-fold summaries.
    """
    arm_names = arm_names or presets.ARM_NAMES
    n_arms = R.shape[1]
    dates = pd.to_datetime(keys["date"])
    months = month_folds(keys["date"])

    per_day: list[dict] = []
    folds: list[FoldResult] = []

    for period in months:
        test_start = period.start_time
        test_mask = (dates >= test_start) & (dates <= period.end_time)
        train_mask = dates < test_start

        if train_mask.sum() < MIN_TRAIN_ROWS or test_mask.sum() == 0:
            continue

        # Purge: drop test days within the embargo of the training boundary.
        embargo_cut = test_start + pd.Timedelta(days=EMBARGO_DAYS)
        purged = test_mask & (dates < embargo_cut)
        test_mask = test_mask & ~purged
        if test_mask.sum() == 0:
            continue

        scaler = Standardizer().fit(X[train_mask.to_numpy()])
        Xtr = scaler.transform(X[train_mask.to_numpy()])
        Xte = scaler.transform(X[test_mask.to_numpy()])
        Rtr = R[train_mask.to_numpy()]
        Rte = R[test_mask.to_numpy()]

        if model_kind == "linucb":
            model = LinUCB(n_arms, Xtr.shape[1], alpha=alpha)
        elif model_kind == "lints":
            model = LinTS(n_arms, Xtr.shape[1], seed=seed)
        else:
            raise ValueError(f"unknown model_kind {model_kind!r}")
        model.fit_full_feedback(Xtr, Rtr)

        if model_kind == "linucb" and alpha > 0:
            chosen = np.array([model.select(x) for x in Xte])
        else:
            chosen = greedy_select(model, Xte)

        golden_i = arm_names.index("golden")
        bandit_r = Rte[np.arange(len(chosen)), chosen]
        golden_r = Rte[:, golden_i]
        oracle_r = Rte.max(axis=1)
        # Best fixed arm chosen in hindsight ON THE TEST FOLD — deliberately
        # generous to the baseline.
        best_fixed_i = int(np.argmax(Rte.sum(axis=0)))
        best_fixed_r = Rte[:, best_fixed_i]

        fr = FoldResult(
            fold=str(period), n_train=int(train_mask.sum()), n_test=int(test_mask.sum()),
            rewards={
                "bandit": float(bandit_r.sum()),
                "golden_always": float(golden_r.sum()),
                "best_fixed_arm": float(best_fixed_r.sum()),
                "oracle": float(oracle_r.sum()),
            },
            arm_counts={arm_names[a]: int((chosen == a).sum()) for a in range(n_arms)},
        )
        fr.rewards["best_fixed_arm_name"] = arm_names[best_fixed_i]
        folds.append(fr)

        te_keys = keys[test_mask.to_numpy()].reset_index(drop=True)
        for i in range(len(chosen)):
            per_day.append({
                "fold": str(period),
                "date": te_keys.loc[i, "date"],
                "symbol": te_keys.loc[i, "symbol"],
                "arm": arm_names[int(chosen[i])],
                "reward_bandit": float(bandit_r[i]),
                "reward_golden": float(golden_r[i]),
                "reward_oracle": float(oracle_r[i]),
                "reward_best_fixed": float(best_fixed_r[i]),
            })

    return pd.DataFrame(per_day), folds


def summarize(per_day: pd.DataFrame) -> pd.DataFrame:
    """Aggregate walk-forward output into the headline comparison table."""
    if per_day.empty:
        return pd.DataFrame()
    policies = {
        "bandit": "reward_bandit",
        "golden_always": "reward_golden",
        "best_fixed_arm": "reward_best_fixed",
        "oracle": "reward_oracle",
    }
    rows = []
    for name, col in policies.items():
        r = per_day[col].to_numpy(dtype=float)
        # Per-day reward is already risk-normalised (return on premium), so a
        # daily Sharpe on it is meaningful; annualise on 252 like the sweep does.
        sd = r.std(ddof=1) if len(r) > 1 else 0.0
        rows.append({
            "policy": name,
            "days": len(r),
            "total_R": r.sum(),
            "mean_R": r.mean(),
            "sharpe": (r.mean() / sd * np.sqrt(252)) if sd > 1e-12 else np.nan,
            "win_days_pct": float((r > 0).mean() * 100.0),
            "flat_days_pct": float((r == 0).mean() * 100.0),
            "worst_day_R": r.min(),
            "max_dd_R": _max_drawdown(r),
        })
    out = pd.DataFrame(rows)
    base = float(out.loc[out.policy == "golden_always", "total_R"].iloc[0])
    out["vs_golden_R"] = out["total_R"] - base
    return out


def _max_drawdown(r: np.ndarray) -> float:
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(eq)
    return float((peak - eq).max()) if len(eq) else 0.0


def arm_usage(per_day: pd.DataFrame) -> pd.DataFrame:
    """How often the bandit picked each arm — including how often it sat out."""
    if per_day.empty:
        return pd.DataFrame()
    counts = per_day["arm"].value_counts()
    return (counts.rename("days").to_frame()
            .assign(pct=lambda d: d["days"] / d["days"].sum() * 100.0)
            .reset_index(names="arm"))


def verdict(summary: pd.DataFrame) -> str:
    """The go/no-go sentence. Deliberately strict.

    The bandit has to clear BOTH the incumbent and the best hindsight-chosen
    fixed arm. Clearing only golden means static per-symbol tuning would have got
    the same win with none of the machinery.
    """
    if summary.empty:
        return "NO RESULT: walk-forward produced no test folds."
    g = summary.set_index("policy")
    b = float(g.loc["bandit", "total_R"])
    gold = float(g.loc["golden_always", "total_R"])
    fixed = float(g.loc["best_fixed_arm", "total_R"])
    oracle = float(g.loc["oracle", "total_R"])
    if b > gold and b > fixed:
        headroom = (b - fixed) / abs(oracle - fixed) * 100 if abs(oracle - fixed) > 1e-9 else float("nan")
        return (f"PROCEED: bandit {b:+.1f}R beats golden {gold:+.1f}R and "
                f"best-fixed {fixed:+.1f}R, capturing {headroom:.0f}% of the "
                f"oracle headroom.")
    if b > gold:
        return (f"STOP (use static tuning): bandit {b:+.1f}R beats golden "
                f"{gold:+.1f}R but NOT best-fixed-arm {fixed:+.1f}R — a single "
                f"static arm would have done as well with none of the complexity.")
    return (f"STOP: bandit {b:+.1f}R does not beat golden {gold:+.1f}R "
            f"out of sample. Keep the current config.")
