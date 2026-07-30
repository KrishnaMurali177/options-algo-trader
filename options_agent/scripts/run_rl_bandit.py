#!/usr/bin/env python3
"""CLI for the regime-bandit layer (Stage 0+1).

This is a research driver only — it never places an order and is not imported by
the live agent. Run it from the isolated RL compose stack so it cannot disturb
the trading containers:

    docker-compose -p rl -f docker-compose.rl.yml run --rm rl \
        python scripts/run_rl_bandit.py build-dataset --symbols SPY,QQQ --prewarm

    docker-compose -p rl -f docker-compose.rl.yml run --rm rl \
        python scripts/run_rl_bandit.py evaluate --dataset rl_runs/dataset_2026-07-29.parquet
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.rl import presets                                    # noqa: E402
from src.rl.dataset import build_dataset, default_workers, to_matrices   # noqa: E402
from src.rl.evaluate import arm_usage, summarize, verdict, walk_forward  # noqa: E402

logger = logging.getLogger("run_rl_bandit")

DEFAULT_OUT_DIR = Path("rl_runs")

#: The 21 symbols with a full 365d 5-min cache. SPY/QQQ/IWM are the only ones
#: with daily 0DTE; the rest are weekly-expiry only and contribute far fewer
#: real-priced sessions.
ALL_SYMBOLS = ["SPY", "QQQ", "IWM", "DIA", "GLD", "TLT", "SLV", "USO",
               "XLE", "XLF", "XLK", "EEM", "FXI", "HYG",
               "AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"]

HEADLINE_SYMBOLS = ["SPY", "QQQ"]


def _resolve_symbols(spec: str) -> list[str]:
    if spec == "all":
        return ALL_SYMBOLS
    if spec == "headline":
        return HEADLINE_SYMBOLS
    return [s.strip().upper() for s in spec.split(",") if s.strip()]


def cmd_build_dataset(args) -> int:
    symbols = _resolve_symbols(args.symbols)
    out = Path(args.out) if args.out else DEFAULT_OUT_DIR / f"dataset_{date.today()}.parquet"
    logger.info("building: %d symbols, %dd, vix_lag=%d, real_only=%s, workers=%d",
                len(symbols), args.days, args.vix_lag, not args.allow_synth,
                args.workers or default_workers())
    df = build_dataset(symbols, days=args.days, vix_lag=args.vix_lag,
                       require_real_options=not args.allow_synth,
                       workers=args.workers, prewarm=args.prewarm, out_path=out)

    traded = df[df.arm != presets.SIT_OUT]
    cov = traded.groupby("symbol").agg(real=("n_real", "sum"), synth=("n_synth", "sum"))
    cov["real_pct"] = (cov.real / (cov.real + cov.synth).replace(0, pd.NA) * 100).round(1)

    lines = [
        f"Rows: {len(df):,}  sessions: {df[['symbol', 'date']].drop_duplicates().shape[0]:,}"
        f"  symbols: {df.symbol.nunique()}",
        f"Date range: {df.date.min()} → {df.date.max()}",
        f"Written: {out}",
        "",
        "Per-arm totals (all symbols, IN-SAMPLE — NOT a result):",
        traded.groupby("arm").agg(
            days=("reward", "size"), total_R=("reward", "sum"),
            mean_R=("reward", "mean"), trades=("n_trades", "sum"),
            real=("n_real", "sum"), synth=("n_synth", "sum"),
            dollars=("dollars_sized", "sum"),
        ).round(2).to_string(),
        "",
        "Real-pricing coverage by symbol:",
        cov.to_string(),
    ]
    text = "\n".join(lines)
    print("\n" + text)

    # Persist beside the parquet: these builds run for tens of minutes and a
    # dropped SSH session should not cost us the summary.
    side = out.with_suffix(".summary.txt")
    side.write_text(text + "\n")
    print(f"\nSummary also written: {side}")
    return 0


def _report(df: pd.DataFrame, label: str, args) -> tuple[str, pd.DataFrame]:
    X, R, keys = to_matrices(df)
    per_day, folds = walk_forward(X, R, keys, model_kind=args.model,
                                  alpha=args.alpha, seed=args.seed)
    summary = summarize(per_day)
    lines = [f"\n{'=' * 72}", f"{label}  (model={args.model}, alpha={args.alpha})",
             f"{'=' * 72}",
             f"sessions={len(keys):,}  test days={len(per_day):,}  folds={len(folds)}"]
    if summary.empty:
        lines.append("No test folds — not enough history for a walk-forward split.")
        return "\n".join(lines), summary
    lines += ["", summary.round(3).to_string(index=False), "",
              "Arm usage by the bandit:", arm_usage(per_day).round(1).to_string(index=False),
              "", "Per-fold best fixed arm (hindsight):",
              *[f"  {f.fold}: {f.rewards['best_fixed_arm_name']:<11}"
                f" bandit={f.rewards['bandit']:+7.2f}R"
                f" golden={f.rewards['golden_always']:+7.2f}R"
                f" fixed={f.rewards['best_fixed_arm']:+7.2f}R"
                f" oracle={f.rewards['oracle']:+7.2f}R" for f in folds],
              "", f"VERDICT: {verdict(summary)}"]
    return "\n".join(lines), summary


def cmd_evaluate(args) -> int:
    df = pd.read_parquet(args.dataset)
    df["date"] = pd.to_datetime(df["date"]).dt.date

    cuts: list[tuple[str, pd.DataFrame]] = [
        ("ALL SYMBOLS (pooled)", df),
        ("HEADLINE: SPY + QQQ", df[df.symbol.isin(HEADLINE_SYMBOLS)]),
    ]
    # Real-priced-only cut: keep sessions where no arm needed a synth fallback.
    synth_sessions = (df.groupby(["symbol", "date"])["n_synth"].sum() > 0)
    bad = set(synth_sessions[synth_sessions].index)
    real_only = df[~df.set_index(["symbol", "date"]).index.isin(bad)]
    cuts.append(("REAL-PRICED SESSIONS ONLY", real_only))

    chunks = []
    for label, sub in cuts:
        if sub.empty or sub[["symbol", "date"]].drop_duplicates().shape[0] < 50:
            chunks.append(f"\n{label}: skipped (too few sessions)")
            continue
        text, _ = _report(sub, label, args)
        chunks.append(text)

    report = "\n".join(chunks)
    print(report)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        header = (f"# RL Regime Bandit — Stage 1 walk-forward\n\n"
                  f"- Dataset: `{args.dataset}`\n- Model: {args.model}"
                  f" (alpha={args.alpha})\n- Arms: {presets.ARM_NAMES}\n"
                  f"- Generated: {date.today()}\n\n```\n")
        out.write_text(header + report + "\n```\n")
        print(f"\nReport written: {out}")
    return 0


def cmd_arms(args) -> int:
    presets.validate_arms()
    print("Arms (deltas over golden defaults):")
    for name, delta in presets.ARMS.items():
        print(f"  {name:<11} {'(no replay, reward 0)' if delta is None else delta}")
    print("\nfeature-cache invariant: OK — no arm varies "
          f"{presets.SCAN_WINDOW_PARAMS}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build-dataset", help="replay every arm over every symbol-day")
    b.add_argument("--symbols", default="headline",
                   help="'headline' (SPY,QQQ), 'all' (21 cached), or a comma list")
    b.add_argument("--days", type=int, default=365)
    b.add_argument("--vix-lag", type=int, default=1,
                   help="passed through to resolve_day_vix; 1 = no look-ahead")
    b.add_argument("--allow-synth", action="store_true",
                   help="keep synth-priced trades (default: real-options only)")
    b.add_argument("--prewarm", action="store_true",
                   help="resolve 0DTE chains up front so no arm silently loses days")
    b.add_argument("--workers", type=int, default=None,
                   help=f"parallel symbols (default {default_workers()}, capped to "
                        "leave cores for the live agents)")
    b.add_argument("--out", default=None)
    b.set_defaults(func=cmd_build_dataset)

    e = sub.add_parser("evaluate", help="purged monthly walk-forward + verdict")
    e.add_argument("--dataset", required=True)
    e.add_argument("--model", choices=["linucb", "lints"], default="linucb")
    e.add_argument("--alpha", type=float, default=0.0,
                   help="UCB exploration width; 0 = greedy (the right default offline)")
    e.add_argument("--seed", type=int, default=0)
    e.add_argument("--out", default=None, help="write a markdown report here")
    e.set_defaults(func=cmd_evaluate)

    a = sub.add_parser("arms", help="print the arm table and check invariants")
    a.set_defaults(func=cmd_arms)

    args = p.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
