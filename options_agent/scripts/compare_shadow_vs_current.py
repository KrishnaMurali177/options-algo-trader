"""Compare the SHADOW (NEW-golden) vs CURRENT (OLD-golden) paper agents.

Aggregates realized results from the two journal trees and prints a side-by-side
A/B per symbol over their common date window (so it's apples-to-apples even though
shadow started later).

  CURRENT / OLD golden : options_agent/sweet_spot_journal/         (agent-spy/qqq)
  SHADOW  / NEW golden : options_agent/sweet_spot_journal_shadow/  (agent-shadow-*)

Run (Docker, per project convention):
  docker-compose run --rm --no-deps dashboard python scripts/compare_shadow_vs_current.py
  ... --symbols SPY,QQQ --since 2026-07-01 --until 2026-07-11
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter, defaultdict


def _date_of(path: str) -> str:
    # .../2026-07-10_SPY.json -> 2026-07-10
    return os.path.basename(path).split("_")[0]


def load_trades(journal_dir: str, symbol: str) -> list[tuple[str, dict]]:
    out = []
    for f in sorted(glob.glob(os.path.join(journal_dir, f"*_{symbol}.json"))):
        try:
            day_trades = json.load(open(f))
        except (json.JSONDecodeError, OSError):
            continue
        for t in day_trades:
            out.append((_date_of(f), t))
    return out


def _executed_closed(t: dict) -> bool:
    return bool(t.get("closed")) and t.get("trade_mode") == "0dte_option" and not t.get("discard")


# Exit-reason win/loss proxy — used only when a trade has no reconciled pnl
# (mirrors the agent's own consecutive-loss heuristic).
_WIN_REASONS = {"decay_target", "target", "gainz_exit", "gainz", "theta_exit"}
_LOSS_REASONS = {"stop", "stagnation", "time_stop"}


def _proxy_win(t: dict):
    r = t.get("exit_reason")
    if r in _WIN_REASONS:
        return True
    if r in _LOSS_REASONS:
        return False
    return None


def _pnl(t: dict):
    """Return (dollars, per_contract_points) or None if unreconciled."""
    pts = t.get("pnl")
    if pts is None:
        entry, exit_p = t.get("actual_entry"), t.get("exit_price")
        if entry is None or exit_p is None:
            return None
        pts = exit_p - entry  # options are long premium (call or put)
    return pts * 100 * (t.get("num_contracts") or 1), pts


def summarize(trades: list[tuple[str, dict]]) -> dict:
    dollars, reasons, days = [], Counter(), set()
    closed = win_proxy = 0
    for date, t in trades:
        if not _executed_closed(t):
            continue
        closed += 1
        days.add(date)
        reasons[t.get("exit_reason", "?")] += 1
        pl = _pnl(t)
        if pl is not None:
            dollars.append(pl[0])
        else:  # unreconciled → exit-reason win/loss proxy
            w = _proxy_win(t)
            if w:
                win_proxy += 1
    n = len(dollars)  # reconciled trades
    wins = [d for d in dollars if d > 0]
    losses = [d for d in dollars if d < 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    cum = mdd = peak = 0.0
    for d in dollars:
        cum += d
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    # win rate: from $ if we have reconciled trades, else the exit-reason proxy
    if n:
        win_rate, wr_basis = len(wins) / n * 100, "$"
    elif closed:
        win_rate, wr_basis = win_proxy / closed * 100, "proxy"
    else:
        win_rate, wr_basis = 0.0, "-"
    return {
        "closed": closed, "reconciled": n, "days": len(days),
        "win_rate": win_rate, "wr_basis": wr_basis,
        "total": sum(dollars), "avg": (sum(dollars) / n) if n else 0.0,
        "pf": (gross_win / gross_loss) if gross_loss else (float("inf") if gross_win else 0.0),
        "mdd": mdd, "reasons": reasons,
    }


def _common_window(old, new):
    o = [d for d, _ in old]
    n = [d for d, _ in new]
    if not o or not n:
        return None, None
    return max(min(o), min(n)), min(max(o), max(n))


def _fmt(s: dict) -> str:
    if s["reconciled"]:
        pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
        money = f"${s['total']:>+9.0f}  ${s['avg']:>+7.1f}  {pf:>5}  ${s['mdd']:>+8.0f}"
    else:
        money = f"{'(unreconciled — no P&L)':>34}"
    wr = f"{s['win_rate']:>4.0f}%{'*' if s['wr_basis'] == 'proxy' else ' '}"
    return f"{s['closed']:>4} ({s['reconciled']:>2}✓)  {wr}  {money}"


def main():
    ap = argparse.ArgumentParser(description="Compare shadow (NEW) vs current (OLD) golden agents")
    ap.add_argument("--old", default="sweet_spot_journal", help="CURRENT/OLD journal dir")
    ap.add_argument("--new", default="sweet_spot_journal_shadow", help="SHADOW/NEW journal dir")
    ap.add_argument("--symbols", default="SPY,QQQ")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD (default: common-window start)")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD (default: common-window end)")
    args = ap.parse_args()

    hdr = f"{'closed(recon)':>12}  {'WR':>5}  {'total$':>9}  {'avg$':>8}  {'PF':>5}  {'maxDD$':>9}"
    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        old_all, new_all = load_trades(args.old, sym), load_trades(args.new, sym)
        w0, w1 = _common_window(old_all, new_all)
        since = args.since or w0
        until = args.until or w1
        keep = lambda ts: [(d, t) for d, t in ts if (not since or d >= since) and (not until or d <= until)]
        so, sn = summarize(keep(old_all)), summarize(keep(new_all))

        print(f"\n══════ {sym}  (window {since or '…'} → {until or '…'}) ══════")
        print(f"                {hdr}")
        print(f"  CURRENT (OLD)  {_fmt(so)}")
        print(f"  SHADOW  (NEW)  {_fmt(sn)}")
        if so["reconciled"] and sn["reconciled"]:
            print(f"  Δ NEW−OLD:    ${sn['total'] - so['total']:+.0f} total   "
                  f"{sn['win_rate'] - so['win_rate']:+.0f}pp WR")
        else:
            miss = "SHADOW" if not sn["reconciled"] else "CURRENT"
            print(f"  ⚠️  {miss} has no reconciled P&L in this window — $ comparison not possible")
        for label, s in (("OLD", so), ("NEW", sn)):
            if s["reasons"]:
                print(f"     {label} exits: " + "  ".join(f"{k}:{v}" for k, v in s["reasons"].most_common()))

    print("\ncolumns: closed(reconciled✓)  WR(* = exit-reason proxy when no $)  "
          "$P&L = pnl×100×contracts. Both tracks use 3-contract sizing.")


if __name__ == "__main__":
    main()
