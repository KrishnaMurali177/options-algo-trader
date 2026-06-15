#!/usr/bin/env python
"""Walk-forward parameter sweep for Mag 7 single-name 0DTE agents.

Comprehensive (but bounded) per-symbol grid search over the 6 highest-impact
sweet-spot parameters, with a walk-forward train/holdout split and composite
ranking (Sharpe + Profit Factor + Calmar). Designed to find robust per-symbol
settings without the overfitting of a full 67-param grid.

Key design points
-----------------
* Golden defaults come from replay_sweet_spot.build_parser() — every param NOT
  in the grid is held at the exact live/golden value (no drift).
* Real-pricing only (require_real_options=True): ranks on genuinely tradeable
  0DTE fills, matching the trustworthy subset used in the Mag 7 analysis.
* Walk-forward: tune on the first --train-frac of trading days, then validate
  the top-K combos on the held-out tail. Only combos that survive the holdout
  are recommended.
* The per-day loop mirrors replay_sweet_spot.main() exactly (same VIX sit-out,
  prior-bars warmup, and replay_day call) — we just feed it different date
  slices and a per-combo copy of `args`.

Usage (Docker, per project convention):
    docker-compose --profile live run --rm agent-spy \
        python scripts/sweep_mag7_params.py \
        --symbols MSFT,AAPL,GOOGL,AMZN,META,TSLA,NVDA --days 365
"""

from __future__ import annotations

import argparse
import copy
import itertools
import logging
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.replay_sweet_spot import build_parser, replay_day

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PRIOR_DAYS_CONTEXT = 4  # matches replay_sweet_spot.main()

# ── The grid (6 high-impact params, ~486 combos/symbol) ──────────────────────
GRID = {
    "min_chop": [1, 2, 3],
    "max_chop": [4, 5, 6],
    "min_quality": [2, 3],
    "max_quality": [6, 7, 8],
    "min_cascade": [2, 3, 4],
    "target_mult_mid": [1.25, 1.5, 1.75],
}


def load_vix(trading_days: list[date]) -> dict[date, float]:
    """Fetch ^VIX closes for the replay window (mirrors replay_sweet_spot.main)."""
    import yfinance as yf
    try:
        vix_start = str(trading_days[0] - timedelta(days=5))
        vix_end = str(trading_days[-1] + timedelta(days=1))
        vix_df = yf.download("^VIX", start=vix_start, end=vix_end, progress=False)
        if isinstance(vix_df.columns, pd.MultiIndex):
            vix_df.columns = vix_df.columns.get_level_values(0)
        return {idx.date(): float(row["Close"]) for idx, row in vix_df.iterrows()}
    except Exception as e:
        logger.warning("VIX fetch failed (%s), using default 20.0", e)
        return {}


def run_days(args, df: pd.DataFrame, trading_days: list[date],
             vix_map: dict[date, float], feature_cache: dict | None = None) -> list[dict]:
    """Replay a set of trading days. Verbatim mirror of replay_sweet_spot.main()'s
    per-day loop, parameterised by `args` (a golden namespace with grid overrides).

    feature_cache: optional shared dict memoizing the combo-independent analyzer
    outputs (OpeningRange + RecentMomentum) keyed by (trade_date, bar index). Pass
    the SAME dict across combos of one symbol to compute analyzers once. Must NOT
    be reused across symbols (keys are (date, i) and would collide across tickers
    sharing a trading date)."""
    all_triggers: list[dict] = []
    full_days = trading_days  # for prior-bar / prev-VIX lookback continuity
    for idx, day in enumerate(full_days):
        day_bars = df[df.index.date == day]
        if len(day_bars) < 24:
            continue

        prior_days = full_days[max(0, idx - PRIOR_DAYS_CONTEXT):idx]
        if prior_days:
            prior_bars = df[pd.Series(df.index.date).isin(set(prior_days)).values]
        else:
            prior_bars = None

        day_vix = vix_map.get(day, 20.0)
        if day_vix == 20.0 and vix_map:
            for prev_day in reversed(full_days[:idx]):
                if prev_day in vix_map:
                    day_vix = vix_map[prev_day]
                    break

        if args.vix_max > 0 and day_vix > args.vix_max:
            continue
        if args.vix_spike_pct > 0 and idx > 0:
            prev_vix = None
            for prev_day in reversed(full_days[:idx]):
                if prev_day in vix_map:
                    prev_vix = vix_map[prev_day]
                    break
            if prev_vix and prev_vix > 0:
                spike_pct = (day_vix - prev_vix) / prev_vix * 100
                if spike_pct > args.vix_spike_pct:
                    continue

        triggers = replay_day(day_bars, day, max_chop=args.max_chop,
                              min_chop=args.min_chop,
                              min_quality=args.min_quality, max_quality=args.max_quality,
                              min_cascade=args.min_cascade, min_cascade_call=args.min_cascade_call,
                              cluster_penalty=args.cluster_penalty,
                              cluster_penalty_window_min=args.cluster_penalty_window_min,
                              cluster_penalty_cap=args.cluster_penalty_cap,
                              vix_stop_slope=args.vix_stop_slope, vix_stop_anchor=args.vix_stop_anchor,
                              breakout_pct=args.breakout_pct,
                              cooldown_bars=args.cooldown_bars, scan_end=args.scan_end,
                              scan_start=args.scan_start,
                              target_mult_low=args.target_mult_low, target_mult_mid=args.target_mult_mid,
                              target_mult_high=args.target_mult_high,
                              regime_guard=args.regime_guard,
                              or_threshold=args.or_threshold,
                              symbol=args.symbol,
                              max_trades_per_day=args.max_trades_per_day,
                              max_stops_per_day=args.max_stops_per_day,
                              max_consecutive_losses=args.max_consecutive_losses,
                              daily_loss_limit=args.daily_loss_limit,
                              confirmation_bar=args.confirmation_bar,
                              stagnation_bars=args.stagnation_bars,
                              stagnation_threshold=args.stagnation_threshold,
                              gainz_exit=not args.no_gainz_exit,
                              gainz_body_ratio=args.gainz_body_ratio,
                              gainz_rsi_overbought=args.gainz_rsi_overbought,
                              gainz_rsi_oversold=args.gainz_rsi_oversold,
                              gainz_min_profit_r=args.gainz_min_profit_r,
                              cascade_sizing=not args.no_cascade_sizing,
                              cascade_size_low=args.cascade_size_low,
                              cascade_size_mid=args.cascade_size_mid,
                              cascade_size_high=args.cascade_size_high,
                              simulate_options=not args.shares,
                              option_delta=args.option_delta,
                              option_gamma=args.option_gamma,
                              premium_atr_pct=args.premium_atr_pct,
                              slippage=args.slippage,
                              vix=day_vix,
                              prior_bars=prior_bars,
                              dynamic_or=args.dynamic_or,
                              dynamic_or_threshold=args.dynamic_or_threshold,
                              real_options=args.real_options and not args.no_real_options,
                              require_real_options=args.require_real_options,
                              decay_aware_targets=not args.no_decay_aware_targets,
                              decay_target_floor=args.decay_target_floor,
                              decay_halflife_bars=args.decay_halflife_bars,
                              active_range=not args.no_active_range,
                              active_range_bars=args.active_range_bars,
                              active_range_blend=args.active_range_blend,
                              pb_ema=args.pb_ema and not args.no_pb_ema,
                              pb_ema_fast=args.pb_ema_fast,
                              pb_ema_slow=args.pb_ema_slow,
                              tiered_stagnation=args.tiered_stagnation and not args.no_tiered_stagnation,
                              tiered_stag_early_bar=args.tiered_stag_early_bar,
                              tiered_stag_pnl_lo=args.tiered_stag_pnl_lo,
                              tiered_stag_pnl_hi=args.tiered_stag_pnl_hi,
                              stag_cooldown_bars=args.stag_cooldown_bars,
                              r_anchored_risk=args.r_anchored_risk,
                              r_anchor_pct=args.r_anchor_pct,
                              extension_veto=args.extension_veto,
                              extension_max_atr=args.extension_max_atr,
                              momentum_flip=args.momentum_flip,
                              momentum_flip_threshold=args.momentum_flip_threshold,
                              max_flip_trades=args.max_flip_trades,
                              vwap_slope_override_t=args.vwap_slope_override_t,
                              vwap_slope_override_k=args.vwap_slope_override_k,
                              vwap_slope_override_cmax=args.vwap_slope_override_cmax,
                              chop_formula_v2b=args.chop_formula_v2b,
                              chop_formula_2xci=args.chop_formula_2xci,
                              feature_cache=feature_cache,
                              debug=False)
        # Keep only triggers that fall in the requested window (prior days are
        # included in the loop only for SMA/VIX warmup continuity).
        all_triggers.extend(triggers)
    return all_triggers


def compute_metrics(all_triggers: list[dict], window_days: list[date]) -> dict:
    """Replicates the risk metrics from replay_sweet_spot.main() (cascade-sized)."""
    n_window = len(window_days)
    if not all_triggers:
        return {"trades": 0, "wr": 0.0, "pf": 0.0, "pnl": 0.0,
                "sharpe": 0.0, "calmar": 0.0, "mdd_pct": 0.0}

    pnl_field = "sized_pnl"  # cascade sizing is ON in golden defaults
    wins = [t for t in all_triggers if t["is_winner"]]
    losses = [t for t in all_triggers if not t["is_winner"]]
    total_pnl = sum(t[pnl_field] for t in all_triggers)
    gross_p = sum(t[pnl_field] for t in wins)
    gross_l = abs(sum(t[pnl_field] for t in losses))
    pf = gross_p / gross_l if gross_l > 0 else float("inf")

    # Daily Sharpe (zero-fill no-trade days across the window)
    daily_map: dict[str, float] = {}
    for t in all_triggers:
        daily_map[t["date"]] = daily_map.get(t["date"], 0.0) + t[pnl_field]
    daily = [daily_map.get(str(d), 0.0) for d in window_days]
    if len(daily) > 1:
        mean_d = sum(daily) / len(daily)
        var_d = sum((x - mean_d) ** 2 for x in daily) / (len(daily) - 1)
        std_d = var_d ** 0.5
        sharpe = (mean_d / std_d) * (252 ** 0.5) if std_d > 0 else 0.0
    else:
        sharpe = 0.0

    # Equity curve / max drawdown
    equity = peak = mdd = 0.0
    for x in daily:
        equity += x
        if equity > peak:
            peak = equity
        elif peak - equity > mdd:
            mdd = peak - equity
    mdd_pct = (mdd / peak * 100) if peak > 0 else 0.0
    calmar = (total_pnl / mdd) if mdd > 0 else float("inf")

    return {
        "trades": len(all_triggers),
        "wr": len(wins) / len(all_triggers) * 100,
        "pf": pf,
        "pnl": total_pnl,
        "sharpe": sharpe,
        "calmar": calmar,
        "mdd_pct": mdd_pct,
    }


def _finite(x: float, big: float = 1e6) -> float:
    """Map inf/nan to a large/sentinel finite for ranking."""
    if x == float("inf"):
        return big
    if x != x:  # nan
        return -big
    return x


def composite_rank(rows: list[dict]) -> None:
    """Annotate each row with rank_* and a composite (lower = better)."""
    n = len(rows)
    for metric in ("sharpe", "pf", "calmar"):
        order = sorted(range(n), key=lambda i: -_finite(rows[i][metric]))
        for rank, i in enumerate(order, start=1):
            rows[i][f"rank_{metric}"] = rank
    for r in rows:
        r["composite"] = (r["rank_sharpe"] + r["rank_pf"] + r["rank_calmar"]) / 3.0


def sweep_symbol(symbol: str, days: int, train_frac: float, topk: int,
                 min_trades: int, base_args) -> dict:
    from src.utils.alpaca_data import fetch_bars
    print(f"\n{'#' * 70}\n# {symbol}\n{'#' * 70}", flush=True)
    print(f"  Loading {days}d of {symbol} 5-min bars...", flush=True)
    df = fetch_bars(symbol, days_back=days, interval="5min")
    if df.empty:
        print(f"  ✗ No data for {symbol}, skipping.")
        return {"symbol": symbol, "error": "no data"}
    trading_days = sorted(set(df.index.date))
    vix_map = load_vix(trading_days)

    split = int(len(trading_days) * train_frac)
    train_days = trading_days[:split]
    holdout_days = trading_days[split:]
    print(f"  {len(trading_days)} days → train {len(train_days)} "
          f"({train_days[0]}→{train_days[-1]}), holdout {len(holdout_days)} "
          f"({holdout_days[0]}→{holdout_days[-1]})", flush=True)

    keys = list(GRID.keys())
    combos = list(itertools.product(*[GRID[k] for k in keys]))
    print(f"  Sweeping {len(combos)} combos on train...", flush=True)

    # Per-symbol feature cache: the OpeningRange + RecentMomentum analyzer
    # outputs are identical across combos (they don't depend on any swept
    # param), so compute them once on the first combo and reuse thereafter.
    # Fresh per symbol — keys are (date, bar index) and would collide across
    # tickers that share trading dates.
    feat_cache: dict = {}

    rows = []
    for n, vals in enumerate(combos, 1):
        a = copy.copy(base_args)
        a.symbol = symbol
        overrides = dict(zip(keys, vals))
        for k, v in overrides.items():
            setattr(a, k, v)
        triggers = run_days(a, df, train_days, vix_map, feature_cache=feat_cache)
        m = compute_metrics(triggers, train_days)
        rows.append({**overrides, **m})
        if n % 60 == 0:
            print(f"    ...{n}/{len(combos)} combos", flush=True)

    eligible = [r for r in rows if r["trades"] >= min_trades]
    if not eligible:
        print(f"  ✗ No combo reached {min_trades} train trades for {symbol}.")
        return {"symbol": symbol, "error": "insufficient trades", "rows": rows}
    composite_rank(eligible)
    eligible.sort(key=lambda r: r["composite"])

    # ── Validate top-K on holdout ──
    top = eligible[:topk]
    for r in top:
        a = copy.copy(base_args)
        a.symbol = symbol
        for k in keys:
            setattr(a, k, r[k])
        ht = run_days(a, df, holdout_days, vix_map, feature_cache=feat_cache)
        hm = compute_metrics(ht, holdout_days)
        r["h_trades"], r["h_pf"], r["h_sharpe"], r["h_pnl"] = (
            hm["trades"], hm["pf"], hm["sharpe"], hm["pnl"])

    return {"symbol": symbol, "train_days": train_days, "holdout_days": holdout_days,
            "top": top, "n_eligible": len(eligible), "n_combos": len(combos)}


def fmt(x: float) -> str:
    if x == float("inf"):
        return "  inf"
    return f"{x:6.2f}"


def main():
    ap = argparse.ArgumentParser(description="Walk-forward param sweep for Mag 7 single names")
    ap.add_argument("--symbols", default="MSFT,AAPL,GOOGL,AMZN,META,TSLA,NVDA")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--train-frac", type=float, default=0.75)
    ap.add_argument("--topk", type=int, default=8)
    ap.add_argument("--min-trades", type=int, default=20)
    ap.add_argument("--out", default=None, help="Markdown report path")
    args = ap.parse_args()

    # Golden defaults from the real replay parser, then force real-pricing-only.
    base_args = build_parser().parse_args([])
    base_args.require_real_options = True

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    results = []
    for sym in symbols:
        try:
            results.append(sweep_symbol(sym, args.days, args.train_frac,
                                        args.topk, args.min_trades, base_args))
        except Exception as e:
            logger.exception("Sweep failed for %s", sym)
            results.append({"symbol": sym, "error": str(e)})

    # ── Report ──
    lines = []
    lines.append(f"# Mag 7 Walk-Forward Parameter Sweep")
    lines.append("")
    lines.append(f"- Grid: {GRID}")
    lines.append(f"- Combos/symbol: {int(np.prod([len(v) for v in GRID.values()]))}")
    lines.append(f"- Lookback: {args.days}d | train_frac: {args.train_frac} | "
                 f"top-K validated on holdout: {args.topk} | min train trades: {args.min_trades}")
    lines.append(f"- Pricing: real-only (require_real_options=True). All non-grid params = golden defaults.")
    lines.append(f"- Composite = mean of (Sharpe rank, PF rank, Calmar rank); lower is better.")
    lines.append("")

    for res in results:
        sym = res["symbol"]
        lines.append(f"## {sym}")
        if res.get("error"):
            lines.append(f"\n⚠️ {res['error']}\n")
            continue
        lines.append(f"\nTrain {res['train_days'][0]}→{res['train_days'][-1]} "
                     f"({len(res['train_days'])}d), "
                     f"Holdout {res['holdout_days'][0]}→{res['holdout_days'][-1]} "
                     f"({len(res['holdout_days'])}d). "
                     f"{res['n_eligible']}/{res['n_combos']} combos eligible.\n")
        hdr = ("| rank | chop | qual | casc | tgt | TR trades | TR PF | TR Shrp | "
               "TR Cal | TR MDD% | HO trades | HO PF | HO Shrp | HO P&L |")
        lines.append(hdr)
        lines.append("|" + "---|" * 14)
        for i, r in enumerate(res["top"], 1):
            lines.append(
                f"| {i} | {r['min_chop']}-{r['max_chop']} | {r['min_quality']}-{r['max_quality']} | "
                f"{r['min_cascade']} | {r['target_mult_mid']} | {r['trades']} | "
                f"{fmt(r['pf']).strip()} | {fmt(r['sharpe']).strip()} | {fmt(r['calmar']).strip()} | "
                f"{r['mdd_pct']:.0f} | {r['h_trades']} | {fmt(r['h_pf']).strip()} | "
                f"{fmt(r['h_sharpe']).strip()} | ${r['h_pnl']:+.2f} |")
        # Robust pick: best train combo that also holds up out-of-sample.
        robust = next((r for r in res["top"]
                       if r["h_trades"] >= 5 and r["h_pf"] >= 1.1 and r["h_sharpe"] > 0), None)
        if robust:
            lines.append(f"\n**Robust pick:** chop {robust['min_chop']}-{robust['max_chop']}, "
                         f"quality {robust['min_quality']}-{robust['max_quality']}, "
                         f"cascade≥{robust['min_cascade']}, target_mid {robust['target_mult_mid']} "
                         f"— holdout PF {fmt(robust['h_pf']).strip()}, "
                         f"Sharpe {fmt(robust['h_sharpe']).strip()}, P&L ${robust['h_pnl']:+.2f}.")
        else:
            lines.append(f"\n**Robust pick:** none — no top-{args.topk} combo held up on the holdout "
                         f"(overfitting signal; keep golden defaults for {sym}).")
        lines.append("")

    report = "\n".join(lines)
    print("\n" + report)
    out = args.out or f"docs/{date.today().isoformat()}_mag7_param_sweep.md"
    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(report + "\n")
    print(f"\n✅ Report written to {out}")


if __name__ == "__main__":
    main()
