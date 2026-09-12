#!/usr/bin/env python3
"""Find historical sessions whose tape — and whose damage — look like now.

Answers two linked questions about a losing stretch:

  1. P&L side: over the last N days, which stretches lost money the same way
     this one is? (depth, length, red-day density, worst day)
  2. Tape side: which sessions had the same price-action geometry — a wide
     opening range that reversed, breakouts that failed back inside, a
     round-tripping scan window — and what did the agent make on them?

The second is the useful one. If the days that look like ours were also
losers, the drawdown is the strategy meeting a regime it does not trade well,
and it is forecastable. If they were mixed, the drawdown is variance.

Usage (on the Ubuntu box, per CLAUDE.md — never local python3):

  # 1. Export a trigger history on the config you want to measure
  docker-compose --profile live run --rm agent-spy python scripts/replay_sweet_spot.py \
      --symbol SPY --days 760 --slippage 0.035 \
      --export-triggers data_cache/analogs/spy_760d.json

  # 2. Find the analogs
  docker-compose --profile live run --rm agent-spy python scripts/find_analog_days.py \
      --triggers data_cache/analogs/spy_760d.json data_cache/analogs/qqq_760d.json \
      --feature-symbol SPY --ref-start 2026-08-19 --top 20
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from src.analysis.day_profile import (
    DEFAULT_WEIGHTS,
    FEATURE_SPECS,
    build_feature_table,
    parse_weights,
    rank_analog_days,
    rank_analog_windows,
)
from src.analysis.drawdown import (
    daily_pnl_from_triggers,
    equity_curve,
    find_drawdown_episodes,
    rank_similar_episodes,
    rank_similar_windows,
    rolling_window_stats,
    window_stats,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RULE = "═" * 78
THIN = "─" * 78


def _fmt(v, nd=2):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return str(v)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Find historical analogs for the current drawdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--triggers", nargs="+", required=True,
                   help="One or more replay --export-triggers JSON files. Multiple "
                        "files are summed per date (a multi-symbol book shares days).")
    p.add_argument("--pnl-field", default="option_pnl_sized",
                   help="Trigger field to sum (default: option_pnl_sized)")
    p.add_argument("--contracts", type=int, default=1,
                   help="Contract multiplier applied to replay P&L (default: 1)")
    p.add_argument("--feature-symbol", default="SPY",
                   help="Underlying whose price action is profiled (default: SPY)")
    p.add_argument("--days", type=int, default=760,
                   help="Calendar days of history to profile (default: 760 ≈ 730 trading-day ask)")
    p.add_argument("--ref-start", required=True,
                   help="First date of the reference stretch, YYYY-MM-DD "
                        "(e.g. the date the new model went live)")
    p.add_argument("--ref-end", default=None,
                   help="Last date of the reference stretch (default: last session available)")
    p.add_argument("--top", type=int, default=15, help="Rows per ranking (default: 15)")
    p.add_argument("--mode", choices=["centroid", "nearest"], default="centroid",
                   help="Day-level matching: to the reference mean, or to the "
                        "closest single reference day (default: centroid)")
    p.add_argument("--weights", default=None,
                   help="Override feature weights: 'follow_through_r=3,chop_score=0'")
    p.add_argument("--list-features", action="store_true",
                   help="Print the feature dictionary with default weights and exit")
    p.add_argument("--min-depth", type=float, default=0.0,
                   help="Ignore drawdown episodes shallower than this ($)")
    p.add_argument("--force-refresh", action="store_true",
                   help="Rebuild the day-profile cache from fresh bars")
    p.add_argument("--out", default="data_cache/analogs",
                   help="Directory for CSV/JSON output (default: data_cache/analogs)")
    p.add_argument("--no-write", action="store_true", help="Skip writing output files")
    return p


def main() -> int:
    args = build_parser().parse_args()

    if args.list_features:
        print(f"\n{RULE}\nPRICE-ACTION FEATURE DICTIONARY\n{RULE}")
        print(f"{'feature':<20} {'wt':>5}  description")
        print(THIN)
        for name, spec in FEATURE_SPECS.items():
            print(f"{name:<20} {spec.weight:>5.2f}  {spec.desc}")
        print(f"\nOverride with --weights 'name=w,name=w' (0 disables a feature).\n")
        return 0

    weights = parse_weights(args.weights)
    ref_start = date.fromisoformat(args.ref_start)
    ref_end = date.fromisoformat(args.ref_end) if args.ref_end else None

    # ── P&L series ──────────────────────────────────────────────────
    pnl = daily_pnl_from_triggers(args.triggers, pnl_field=args.pnl_field,
                                  contracts=args.contracts)
    if pnl.empty:
        print("No triggers found in the supplied export(s).")
        return 1
    logger.info("Loaded %d trading days of P&L (%s → %s)", len(pnl), pnl.index[0], pnl.index[-1])

    # ── Price-action features ───────────────────────────────────────
    table = build_feature_table(args.feature_symbol, days_back=args.days,
                                force_refresh=args.force_refresh)
    if table.empty:
        print("No sessions profiled — check bar availability.")
        return 1

    if ref_end is None:
        ref_end = max(table.index[-1], pnl.index[-1])
    ref_dates = [d for d in table.index if ref_start <= d <= ref_end]
    if not ref_dates:
        print(f"No sessions between {ref_start} and {ref_end} in the feature table.")
        return 1

    # P&L aligned to every profiled session, zero-filled: a day the agent sat
    # out is a real outcome (it made nothing), not a missing observation.
    pnl_all = pnl.reindex(table.index).fillna(0.0)
    traded = pnl.reindex(table.index).notna()

    ref_pnl = pnl_all.loc[ref_dates]
    ref_stats = window_stats(ref_pnl)

    print(f"\n{RULE}")
    print(f"ANALOG SEARCH — {args.feature_symbol} price action, "
          f"{len(table)} sessions ({table.index[0]} → {table.index[-1]})")
    print(RULE)

    # ── 1. The reference stretch ────────────────────────────────────
    print(f"\n▌ 1. REFERENCE STRETCH  {ref_start} → {ref_end}  ({len(ref_dates)} sessions)\n")
    print(f"  net P&L          ${_fmt(ref_stats['net_pnl'])}")
    print(f"  max drawdown     ${_fmt(ref_stats['max_drawdown'])}")
    print(f"  red days         {ref_stats['red_frac']*100:.0f}%  "
          f"(longest red streak {ref_stats['longest_red_streak']})")
    print(f"  worst day        ${_fmt(ref_stats['worst_day'])}")
    print(f"  avg day          ${_fmt(ref_stats['avg_day'])}")
    print(f"  days with trades {int(traded.loc[ref_dates].sum())} of {len(ref_dates)}")

    # ── 2. Where this sits in the distribution ──────────────────────
    win = len(ref_dates)
    roll = rolling_window_stats(pnl_all, win)
    hist = roll[roll.index < ref_start]
    if not hist.empty:
        pct_net = float((hist["net_pnl"] < ref_stats["net_pnl"]).mean() * 100)
        pct_dd = float((hist["max_drawdown"] > ref_stats["max_drawdown"]).mean() * 100)
        print(f"\n  vs {len(hist)} prior {win}-session windows:")
        print(f"    net P&L      {pct_net:.1f}th percentile "
              f"({'worse than' if pct_net < 50 else 'better than'} "
              f"{pct_net:.0f}% of history)")
        print(f"    drawdown     deeper than {100 - pct_dd:.0f}% of windows")

    # ── 3. Similar drawdowns by P&L shape ───────────────────────────
    print(f"\n▌ 2. WINDOWS THAT LOST MONEY THE SAME WAY ({win}-session, P&L shape only)\n")
    sim_win = rank_similar_windows(roll, ref_stats, exclude_after=ref_start)
    if sim_win.empty:
        print("  (no prior windows)")
    else:
        print(f"  {'window':<26} {'net$':>9} {'maxDD$':>9} {'red%':>6} {'streak':>7} {'dist':>6}")
        print(f"  {THIN[:70]}")
        for end, r in sim_win.head(args.top).iterrows():
            label = f"{r['start_date']}→{end}"
            print(f"  {label:<26} {r['net_pnl']:>9,.0f} {r['max_drawdown']:>9,.0f} "
                  f"{r['red_frac']*100:>5.0f}% {int(r['longest_red_streak']):>7} "
                  f"{r['distance']:>6.2f}")

    episodes = find_drawdown_episodes(pnl_all, min_depth=args.min_depth)
    current = next((e for e in episodes if e.ongoing), None)
    if current is None and episodes:
        current = max(episodes, key=lambda e: e.trough_date)
    if current is not None:
        print(f"\n  Current episode: peak {current.peak_date} → trough {current.trough_date}"
              f"  depth ${_fmt(current.depth)} ({current.depth_pct:.1f}%)"
              f"  {current.days_to_trough} sessions"
              f"{'  [ongoing]' if current.ongoing else ''}")
        sim_ep = rank_similar_episodes(episodes, current)
        if not sim_ep.empty:
            print(f"\n  Closest prior episodes:")
            print(f"  {'start→trough':<26} {'depth$':>9} {'sess':>5} {'red%':>6} "
                  f"{'recov':>6} {'dist':>6}")
            print(f"  {THIN[:70]}")
            for st, r in sim_ep.head(args.top).iterrows():
                label = f"{st}→{r['trough_date']}"
                rec = "never" if r["recovery_days"] is None else f"{int(r['recovery_days'])}d"
                print(f"  {label:<26} {r['depth']:>9,.0f} {int(r['days_to_trough']):>5} "
                      f"{r['red_frac']*100:>5.0f}% {rec:>6} {r['distance']:>6.2f}")

    # ── 4. What the tape actually looked like ───────────────────────
    matched = [c for c in weights if c in table.columns]
    z_all = (table[matched].astype(float) - table[matched].astype(float).mean()) / \
            table[matched].astype(float).std(ddof=0).replace(0, np.nan)
    ref_z = z_all.loc[ref_dates].mean().sort_values(key=abs, ascending=False)

    print(f"\n▌ 3. PRICE-ACTION SIGNATURE OF THE REFERENCE STRETCH")
    print(f"     (z vs the {len(table)}-session population; |z| ≥ 0.3 shown)\n")
    print(f"  {'feature':<20} {'z':>6} {'ref mean':>11} {'pop mean':>11}  description")
    print(f"  {THIN[:76]}")
    shown = 0
    for feat, z in ref_z.items():
        if abs(z) < 0.3:
            continue
        shown += 1
        bar = ("▲" if z > 0 else "▼") * min(int(abs(z) / 0.25), 8)
        print(f"  {feat:<20} {z:>6.2f} {table.loc[ref_dates, feat].mean():>11,.2f} "
              f"{table[feat].mean():>11,.2f}  {bar} {FEATURE_SPECS[feat].desc[:34]}")
    if not shown:
        print("  (nothing stands out — the tape was unremarkable, which points at variance)")

    # ── 5. Analog days by price action, with their realized P&L ─────
    ranked = rank_analog_days(table, ref_dates, weights=weights, mode=args.mode)
    ranked = ranked.join(pnl_all.rename("pnl")).join(traded.rename("traded"))

    print(f"\n▌ 4. CLOSEST ANALOG SESSIONS BY PRICE ACTION ({args.mode} match)\n")
    print(f"  {'date':<12} {'dist':>5} {'pnl$':>8} {'ftR':>6} {'maeR':>6} "
          f"{'flips':>6} {'chop':>5} {'ORbps':>7} {'scanEff':>8}")
    print(f"  {THIN[:74]}")
    for d, r in ranked.head(args.top).iterrows():
        print(f"  {str(d):<12} {r['distance']:>5.2f} {r['pnl']:>8,.0f} "
              f"{r['follow_through_r']:>6.2f} {r['mae_r']:>6.2f} "
              f"{int(r['break_flips']):>6} {r['chop_score']:>5.0f} "
              f"{r['or_width_bps']:>7.0f} {r['scan_efficiency']:>8.2f}")

    # ── 6. Does the tape shape actually predict the damage? ─────────
    print(f"\n▌ 5. DOES THE SIGNATURE PREDICT P&L?\n")
    body = ranked[ranked["traded"]]
    if len(body) >= 20:
        n = max(len(body) // 10, 5)
        near, far = body.head(n), body.tail(n)
        rest = body.iloc[n:]
        rho = float(body["distance"].corr(body["pnl"], method="spearman"))
        print(f"  Spearman corr(distance, daily P&L) = {rho:+.3f}   "
              f"({'closer days lost more' if rho > 0.05 else 'no usable relationship' if abs(rho) <= 0.05 else 'closer days made more'})")
        print(f"  Nearest {n} sessions   avg ${near['pnl'].mean():>8,.2f}   "
              f"win rate {float((near['pnl'] > 0).mean()) * 100:>5.1f}%")
        print(f"  All other sessions    avg ${rest['pnl'].mean():>8,.2f}   "
              f"win rate {float((rest['pnl'] > 0).mean()) * 100:>5.1f}%")
        print(f"  Farthest {n} sessions  avg ${far['pnl'].mean():>8,.2f}   "
              f"win rate {float((far['pnl'] > 0).mean()) * 100:>5.1f}%")
        print(f"\n  Reference stretch     avg ${ref_pnl.mean():>8,.2f}   "
              f"win rate {float((ref_pnl > 0).mean()) * 100:>5.1f}%")
    else:
        print("  (too few traded sessions for a meaningful split)")

    # ── 7. Analog regimes (windows of tape, not single days) ────────
    print(f"\n▌ 6. CLOSEST ANALOG REGIMES ({win}-session windows of price action)\n")
    aw = rank_analog_windows(table, ref_dates, weights=weights, exclude_from=ref_start)
    print(f"  {'window':<26} {'dist':>6} {'net P&L$':>10} {'red%':>6}")
    print(f"  {THIN[:54]}")
    for end, r in aw.head(args.top).iterrows():
        seg = pnl_all.loc[r["start_date"]:end]
        print(f"  {str(r['start_date']) + '→' + str(end):<26} {r['distance']:>6.2f} "
              f"{seg.sum():>10,.0f} {float((seg < 0).mean()) * 100:>5.0f}%")

    # ── Output files ────────────────────────────────────────────────
    if not args.no_write:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = out / f"analogs_{args.feature_symbol}_{args.ref_start}_{stamp}"
        ranked.to_csv(f"{base}_days.csv")
        aw.to_csv(f"{base}_regimes.csv")
        summary = {
            "generated": stamp,
            "feature_symbol": args.feature_symbol,
            "reference": {"start": str(ref_start), "end": str(ref_end),
                          "sessions": len(ref_dates), **ref_stats},
            "weights": weights,
            "reference_zscores": {k: round(float(v), 3) for k, v in ref_z.items()},
            "top_pnl_windows": [
                {"start": str(r["start_date"]), "end": str(e),
                 "net_pnl": r["net_pnl"], "max_drawdown": r["max_drawdown"],
                 "distance": round(float(r["distance"]), 3)}
                for e, r in sim_win.head(args.top).iterrows()
            ] if not sim_win.empty else [],
            "top_analog_days": [
                {"date": str(d), "distance": round(float(r["distance"]), 3),
                 "pnl": round(float(r["pnl"]), 2)}
                for d, r in ranked.head(args.top).iterrows()
            ],
            "episodes": [e.as_dict() for e in episodes],
        }
        Path(f"{base}_summary.json").write_text(json.dumps(summary, indent=2, default=str))
        print(f"\n  Wrote {base}_days.csv, _regimes.csv, _summary.json")

    print(f"\n{RULE}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
