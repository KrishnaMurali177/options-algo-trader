"""Bandit arms: named parameter presets handed to ``replay_day``.

Each arm is a *delta* over the golden defaults (``build_parser().parse_args([])``),
so an arm only names the params it actually changes and automatically inherits
every future golden-default change.

``SIT_OUT`` is a real arm, not a degenerate one: the single biggest lever the
controller has is declining to trade a day at all, and it needs to be learnable
on the same footing as the others.

Invariant enforced by :func:`validate_arms`
-------------------------------------------
No arm may vary the scan-window params. ``replay_day``'s ``feature_cache`` is
keyed ``(trade_date, scan_bar_ordinal)`` where the ordinal indexes ``scan_bars``,
which is itself derived from ``scan_start``/``scan_end``/``dynamic_or``/
``dynamic_or_threshold``. Vary any of those between arms while sharing a cache
and arm N silently reads arm M's opening-range/momentum features. The dataset
builder shares one cache per symbol for a ~2.5x speedup, so this invariant is
what makes that safe.
"""

from __future__ import annotations

SIT_OUT = "sit_out"

#: Params that change how ``scan_bars`` is built. Sharing a ``feature_cache``
#: across arms is only valid while every arm leaves these at their defaults.
SCAN_WINDOW_PARAMS = ("scan_start", "scan_end", "dynamic_or", "dynamic_or_threshold")

#: Arm name -> delta over golden defaults. ``SIT_OUT`` maps to None: it is never
#: replayed, its reward is exactly 0.
ARMS: dict[str, dict | None] = {
    # Trade nothing. Reward 0 by construction, no replay call.
    SIT_OUT: None,

    # The incumbent. This is the baseline the bandit has to beat; if it can't,
    # the honest conclusion is to keep the static config.
    "golden": {},

    # Fewer, higher-conviction trades: demand a stronger cascade and a calmer
    # tape. Expected to help in chop, cost money in clean trends.
    "selective": {
        "min_cascade": 4,
        "min_quality": 3,
        "max_quality": 7,
        "max_chop": 4,
    },

    # More trades, looser gates. Expected to help on clean trend days where the
    # golden gates are leaving money on the table.
    "permissive": {
        "min_cascade": 2,
        "min_quality": 2,
        "max_quality": 8,
        "max_chop": 6,
    },

    # Same entries as golden, hold for more: wider targets.
    "runner": {
        "target_mult_mid": 1.75,
        "target_mult_high": 1.75,
    },

    # Same entries as golden, take profit sooner.
    "scalper": {
        "target_mult_mid": 1.25,
        "target_mult_low": 1.0,
    },
}

#: Stable ordering — the bandit indexes arms positionally, so this must not be
#: reordered once a dataset or fitted model has been written.
ARM_NAMES: list[str] = list(ARMS)

#: Index of the sit-out arm in :data:`ARM_NAMES`.
SIT_OUT_INDEX: int = ARM_NAMES.index(SIT_OUT)


def tradable_arms() -> list[str]:
    """Arm names that actually call ``replay_day`` (everything but sit-out)."""
    return [name for name, delta in ARMS.items() if delta is not None]


def validate_arms(arms: dict[str, dict | None] | None = None) -> None:
    """Raise if any arm violates the feature-cache invariant.

    Called by the dataset builder before any replay work, and asserted directly
    in the test suite.
    """
    arms = ARMS if arms is None else arms
    offenders = {
        name: sorted(set(delta) & set(SCAN_WINDOW_PARAMS))
        for name, delta in arms.items()
        if delta and set(delta) & set(SCAN_WINDOW_PARAMS)
    }
    if offenders:
        raise ValueError(
            "These arms vary scan-window params, which breaks the shared "
            f"feature_cache: {offenders}. Either drop the param from the arm, or "
            "give that arm its own cache in dataset.py."
        )


def resolve_arm(arm: str, base: dict) -> dict | None:
    """Apply an arm's delta on top of ``base`` (the golden-default kwargs).

    Returns None for the sit-out arm. ``base`` is never mutated. Unknown param
    names are rejected here rather than silently ignored by ``replay_day``'s
    ``**kwargs``-free signature, which would otherwise raise far from the cause.
    """
    if arm not in ARMS:
        raise KeyError(f"Unknown arm {arm!r}; known arms: {ARM_NAMES}")
    delta = ARMS[arm]
    if delta is None:
        return None
    unknown = sorted(set(delta) - set(base))
    if unknown:
        raise KeyError(
            f"Arm {arm!r} sets params that replay_day does not accept: {unknown}"
        )
    return {**base, **delta}
