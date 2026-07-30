"""Build the full-feedback dataset: every arm scored on every symbol-day.

Because ``replay_day`` is a deterministic offline simulator, we can score all
arms on all days and store the complete reward matrix. Everything downstream
(training, walk-forward, oracle bounds, regret) reads this table, so a build is
the expensive step and evaluation is cheap and re-runnable.

Reward
------
Per trade: **return on premium**, ``pnl / est_premium``. In options mode
``replay_day`` overwrites ``pnl`` with the per-contract *option* P&L in premium
points and sets ``est_premium`` to the real entry premium
(``replay_sweet_spot.py:1024-1030``), so this ratio is the actual return on
capital at risk, floored at -1.0 per trade by the defined-risk cap.

This matters for arm selection: an underlying-points reward would rate ``runner``
(wider targets, longer holds) on price movement alone and ignore the theta it
pays to get there. Return-on-premium charges it.

Per symbol-day: the sum over that day's trades, clipped to
:data:`REWARD_CLIP`. ``sit_out`` is exactly 0.0 and is never replayed. Clipping
keeps one freak day from dominating a ridge fit on a few thousand rows.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.rl import presets
from src.rl.features import N_FEATURES, FEATURE_NAMES, build_context

logger = logging.getLogger(__name__)

#: Matches replay_sweet_spot.main()/sweep_mag7_params so SMA warmup is identical.
PRIOR_DAYS_CONTEXT = 4

#: Per-day reward clip. A single trade is already floored at -1.0 (defined risk)
#: and max_trades_per_day defaults to 3, so this bites only on outlier winners.
REWARD_CLIP = (-3.0, 3.0)

#: Minimum bars for a usable session (mirrors the existing drivers).
MIN_BARS_PER_DAY = 24


def _day_reward(triggers: list[dict]) -> tuple[float, dict]:
    """(clipped day reward, diagnostics) from one arm's trigger list."""
    if not triggers:
        return 0.0, {"n_trades": 0, "n_real": 0, "n_synth": 0,
                     "dollars_sized": 0.0, "raw_reward": 0.0}
    r = 0.0
    dollars = 0.0
    n_real = n_synth = 0
    for t in triggers:
        prem = t.get("est_premium")
        pnl = t.get("pnl")
        if prem and prem > 0 and pnl is not None:
            r += float(pnl) / float(prem)
        if t.get("pricing") == "real":
            n_real += 1
        elif t.get("pricing") == "synth":
            n_synth += 1
        sized = t.get("option_pnl_sized")
        if sized is not None:
            dollars += float(sized)
    return float(np.clip(r, *REWARD_CLIP)), {
        "n_trades": len(triggers),
        "n_real": n_real,
        "n_synth": n_synth,
        "dollars_sized": dollars,
        "raw_reward": r,
    }


def prewarm_chains(symbol: str, df: pd.DataFrame, trading_days: list[date]) -> dict:
    """Resolve the ATM 0DTE contract for each session so later replays hit cache.

    Without this, an arm that fires on a day no other arm fired needs a live
    Alpaca lookup mid-build; under ``require_real_options`` a failed lookup makes
    the trigger vanish silently, quietly biasing the dataset toward params whose
    days were already cached. Doing it up front also gives an honest coverage
    number before any training.
    """
    from src.utils.alpaca_options import resolve_atm_0dte

    covered = missing = 0
    for day in trading_days:
        day_bars = df[df.index.date == day]
        if len(day_bars) < MIN_BARS_PER_DAY:
            continue
        spot = float(day_bars["Close"].iloc[len(day_bars) // 3])
        got = False
        for opt_type in ("call", "put"):
            try:
                if resolve_atm_0dte(symbol, day, opt_type, spot):
                    got = True
            except Exception as e:      # network/credential trouble, not fatal
                logger.debug("prewarm %s %s %s: %s", symbol, day, opt_type, e)
        covered += bool(got)
        missing += (not got)
    return {"symbol": symbol, "sessions_with_0dte": covered, "sessions_without": missing}


def build_symbol(symbol: str, days: int = 365, vix_lag: int = 1,
                 require_real_options: bool = True,
                 arms: list[str] | None = None,
                 prewarm: bool = False,
                 slippage: float = 0.0) -> list[dict]:
    """Replay every arm over every session for one symbol. Returns long-format rows."""
    # Imported here, not at module scope: these pull in the whole replay stack,
    # and under multiprocessing we want each worker to do it in its own process.
    from scripts.replay_sweet_spot import build_parser, replay_day, resolve_day_vix
    from scripts.sweep_mag7_params import load_vix
    from src.utils.alpaca_data import fetch_bars

    presets.validate_arms()
    arm_names = arms or presets.ARM_NAMES
    tradable = [a for a in arm_names if presets.ARMS[a] is not None]

    df = fetch_bars(symbol, days_back=days, interval="5min")
    if df is None or df.empty:
        logger.warning("%s: no bars", symbol)
        return []

    trading_days = sorted(set(df.index.date))
    vix_map = load_vix(trading_days)

    if prewarm:
        cov = prewarm_chains(symbol, df, trading_days)
        logger.info("%s prewarm: %d sessions with 0DTE, %d without",
                    symbol, cov["sessions_with_0dte"], cov["sessions_without"])

    # Golden defaults, exactly as the existing sweep derives them.
    base_args = build_parser().parse_args([])
    # --slippage defaults to 0.0 in the replay parser, i.e. free execution. That
    # systematically favours arms that trade more often (permissive fires ~57%
    # more than golden), so any cross-arm conclusion has to be re-checked at a
    # realistic round-trip cost.
    base_args.slippage = float(slippage)
    base = _replay_kwargs(base_args, symbol, require_real_options)

    # One cache per symbol, shared across arms. Safe only because
    # presets.validate_arms() guarantees no arm moves the scan window.
    feature_cache: dict = {}

    rows: list[dict] = []
    for idx, day in enumerate(trading_days):
        day_bars = df[df.index.date == day]
        if len(day_bars) < MIN_BARS_PER_DAY:
            continue

        prior_days = trading_days[max(0, idx - PRIOR_DAYS_CONTEXT):idx]
        prior_bars = (df[pd.Series(df.index.date).isin(set(prior_days)).values]
                      if prior_days else None)

        day_vix, prev_vix = resolve_day_vix(vix_map, trading_days, idx, vix_lag)

        # Context must exist for the day to be usable at all — a day we cannot
        # describe is a day the controller cannot legitimately act on.
        try:
            ctx = build_context(symbol, day, day_bars, prior_bars,
                                vix_prev=day_vix, vix_prev2=prev_vix)
        except Exception as e:
            logger.debug("%s %s: no context (%s)", symbol, day, e)
            continue

        ctx_cols = {f"f_{name}": float(v) for name, v in zip(FEATURE_NAMES, ctx)}

        for arm in arm_names:
            if arm == presets.SIT_OUT:
                rows.append({"symbol": symbol, "date": str(day), "arm": arm,
                             "reward": 0.0, "raw_reward": 0.0, "n_trades": 0,
                             "n_real": 0, "n_synth": 0, "dollars_sized": 0.0,
                             "vix_used": day_vix, **ctx_cols})
                continue

            kwargs = presets.resolve_arm(arm, base)
            try:
                triggers = replay_day(day_bars, day, prior_bars=prior_bars,
                                      vix=day_vix, feature_cache=feature_cache,
                                      **kwargs)
            except Exception as e:
                logger.warning("%s %s arm=%s replay failed: %s", symbol, day, arm, e)
                continue
            reward, diag = _day_reward(triggers)
            rows.append({"symbol": symbol, "date": str(day), "arm": arm,
                         "reward": reward, "vix_used": day_vix,
                         **{k: diag[k] for k in
                            ("raw_reward", "n_trades", "n_real", "n_synth", "dollars_sized")},
                         **ctx_cols})

    # Drop any day where some arm failed to replay: an incomplete reward row
    # would make the oracle and best-fixed-arm baselines incomparable.
    if rows:
        rows = _drop_incomplete_days(rows, expected=len(arm_names))
    logger.info("%s: %d rows over %d sessions", symbol, len(rows),
                len({r["date"] for r in rows}))
    return rows


def _drop_incomplete_days(rows: list[dict], expected: int) -> list[dict]:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["date"]] = counts.get(r["date"], 0) + 1
    bad = {d for d, c in counts.items() if c != expected}
    if bad:
        logger.warning("dropping %d sessions with incomplete arm coverage", len(bad))
    return [r for r in rows if r["date"] not in bad]


def _replay_kwargs(args, symbol: str, require_real_options: bool) -> dict:
    """Translate parsed golden defaults into ``replay_day`` kwargs.

    Mirrors the call in ``sweep_mag7_params.run_days`` so the RL dataset and the
    existing sweep are measuring the same strategy. Only params ``replay_day``
    actually accepts are passed; ``day_bars``/``trade_date``/``prior_bars``/
    ``vix``/``feature_cache`` are supplied per call by the caller.
    """
    import inspect
    from scripts.replay_sweet_spot import replay_day

    accepted = set(inspect.signature(replay_day).parameters)
    per_call = {"day_bars", "trade_date", "prior_bars", "vix", "feature_cache",
                "symbol", "real_options", "require_real_options"}
    out = {k: v for k, v in vars(args).items() if k in accepted and k not in per_call}

    # Resolve the paired --x/--no-x flags the parser exposes.
    for name in ("real_options", "dynamic_or", "tiered_stagnation", "gainz_exit",
                 "cluster_penalty", "pb_ema", "regime_guard"):
        neg = getattr(args, f"no_{name}", False)
        if name in accepted:
            out[name] = bool(getattr(args, name, True)) and not neg

    out["symbol"] = symbol
    out["real_options"] = True
    out["require_real_options"] = require_real_options
    return out


def _worker(payload: tuple) -> list[dict]:
    symbol, days, vix_lag, require_real, arms, prewarm, slippage = payload
    try:
        return build_symbol(symbol, days=days, vix_lag=vix_lag,
                            require_real_options=require_real, arms=arms,
                            prewarm=prewarm, slippage=slippage)
    except Exception as e:                      # never let one symbol kill a build
        logger.exception("symbol %s failed: %s", symbol, e)
        return []


def default_workers() -> int:
    """Conservative worker count.

    Deliberately caps at 4 and leaves cores free: builds run on the same box as
    the live trading agents and must not starve them.
    """
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def build_dataset(symbols: list[str], days: int = 365, vix_lag: int = 1,
                  require_real_options: bool = True,
                  arms: list[str] | None = None,
                  workers: int | None = None,
                  prewarm: bool = False,
                  slippage: float = 0.0,
                  out_path: str | Path | None = None) -> pd.DataFrame:
    """Build across symbols (parallel by symbol) and optionally persist."""
    presets.validate_arms()
    workers = workers or default_workers()
    payloads = [(s, days, vix_lag, require_real_options, arms, prewarm, slippage)
                for s in symbols]

    if workers == 1 or len(symbols) == 1:
        chunks = [_worker(p) for p in payloads]
    else:
        # One cache per process; the cache is per-symbol anyway so nothing is lost.
        with mp.get_context("fork").Pool(processes=workers) as pool:
            chunks = pool.map(_worker, payloads)

    rows = [r for chunk in chunks for r in chunk]
    if not rows:
        raise RuntimeError("dataset build produced no rows")
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values(["date", "symbol", "arm"]).reset_index(drop=True)

    if out_path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out_path, index=False)
        logger.info("wrote %d rows to %s", len(df), out_path)
    return df


def no_opportunity_mask(df: pd.DataFrame) -> pd.Series:
    """Symbol-days where no arm could trade at all, indexed by (date, symbol).

    Two causes, both meaning "not a real decision":

    * the symbol had no 0DTE listing that session (most days for single names —
      measured 58/251 for EEM, 52/251 for HYG), so under
      ``require_real_options`` every trigger is dropped;
    * every arm's gates rejected the whole day.

    Either way all arms score exactly 0.0 and are tied with ``sit_out``, so the
    row carries no information about which arm to pick. Worse, keeping them
    flatters the controller: it inflates the share of days it appears to
    "correctly sit out" when in fact nothing was on offer. They are kept in the
    stored dataset (they are facts about coverage) but excluded from training
    and evaluation.
    """
    traded = df[df.arm != presets.SIT_OUT]
    per_day = traded.groupby(["date", "symbol"])["n_trades"].sum()
    return per_day == 0


def to_matrices(df: pd.DataFrame, arm_names: list[str] | None = None,
                drop_no_opportunity: bool = True
                ) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """Long dataset -> (X contexts, R full-feedback rewards, key frame).

    Returns ``X`` (n_days, n_features), ``R`` (n_days, n_arms) and a key frame
    carrying symbol/date per row so evaluation can split chronologically.

    ``drop_no_opportunity`` removes sessions where no arm could trade — see
    :func:`no_opportunity_mask`. Leave it on unless you are specifically
    measuring coverage.
    """
    arm_names = arm_names or presets.ARM_NAMES
    feat_cols = [f"f_{n}" for n in FEATURE_NAMES]
    missing = [c for c in feat_cols if c not in df.columns]
    if missing:
        raise KeyError(f"dataset is missing feature columns: {missing}")

    wide = df.pivot_table(index=["date", "symbol"], columns="arm",
                          values="reward", aggfunc="first")
    absent = [a for a in arm_names if a not in wide.columns]
    if absent:
        raise KeyError(f"dataset has no rows for arms: {absent}")
    wide = wide[arm_names].dropna()

    if drop_no_opportunity:
        dead = no_opportunity_mask(df)
        dead = dead[dead].index
        before = len(wide)
        wide = wide.drop(index=[i for i in dead if i in wide.index])
        dropped = before - len(wide)
        if dropped:
            logger.info("dropped %d/%d sessions with no tradable opportunity (%.1f%%)",
                        dropped, before, dropped / before * 100)
    if wide.empty:
        raise ValueError("no usable sessions left after filtering")

    ctx = (df.drop_duplicates(["date", "symbol"])
             .set_index(["date", "symbol"])[feat_cols])
    ctx = ctx.loc[wide.index]

    keys = wide.reset_index()[["date", "symbol"]]
    X = ctx.to_numpy(dtype=float)
    R = wide.to_numpy(dtype=float)
    if X.shape[1] != N_FEATURES:
        raise AssertionError(f"X has {X.shape[1]} cols, expected {N_FEATURES}")
    return X, R, keys
