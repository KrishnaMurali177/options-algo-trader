"""Print a REAL-vs-SHADOW daily P&L table by reading both perf DBs at once.

Saves hand-running two SQLite queries: it opens the real-money DB and the shadow
(new-golden paper) DB written by report_broker_pnl.py, joins their daily_pnl rows
by date, and prints a side-by-side table with the per-day edge (shadow − real)
and running cumulatives — so you can see at a glance whether the new goldens are
beating the live account.

Run (Docker, with perf/ mounted at /app/perf like the report wrappers):
  docker-compose --profile live run --rm --no-deps -v \
    "$PWD/options_agent/perf:/app/perf" agent-spy python scripts/compare_perf.py

Override the DBs / P&L column if needed:
  ... compare_perf.py --real /app/perf/performance.db \
      --shadow /app/perf/performance_shadow.db --metric total_realized
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def _load(path: str, metric: str) -> dict[str, float]:
    """date -> metric value, from a report_broker_pnl.py daily_pnl DB."""
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"DB not found: {path} (run report_broker_pnl.py first)")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"SELECT date, {metric} FROM daily_pnl ORDER BY date").fetchall()
    finally:
        con.close()
    return {d: v for d, v in rows if v is not None}


def _fmt(v: float | None) -> str:
    return "     —" if v is None else f"{'+' if v >= 0 else ''}{v:,.0f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--real", default="/app/perf/performance.db")
    ap.add_argument("--shadow", default="/app/perf/performance_shadow.db")
    ap.add_argument("--metric", default="today_pnl",
                    help="daily_pnl column to compare (today_pnl or total_realized)")
    args = ap.parse_args()

    real = _load(args.real, args.metric)
    shadow = _load(args.shadow, args.metric)
    dates = sorted(set(real) | set(shadow))
    if not dates:
        raise SystemExit("No daily_pnl rows in either DB.")

    print(f"REAL vs SHADOW — daily {args.metric} ($)\n")
    print(f"{'date':<12}{'real':>10}{'shadow':>10}{'edge':>10}"
          f"{'cum·real':>11}{'cum·shad':>11}")
    print("-" * 64)
    cr = cs = 0.0
    days = wins = 0
    for d in dates:
        r, s = real.get(d), shadow.get(d)
        edge = (s - r) if (r is not None and s is not None) else None
        cr += r or 0.0
        cs += s or 0.0
        if edge is not None:
            days += 1
            wins += edge > 0
        print(f"{d:<12}{_fmt(r):>10}{_fmt(s):>10}{_fmt(edge):>10}"
              f"{_fmt(cr):>11}{_fmt(cs):>11}")
    print("-" * 64)
    print(f"{'TOTAL':<12}{_fmt(cr):>10}{_fmt(cs):>10}{_fmt(cs - cr):>10}")
    if days:
        print(f"\nShadow edge: {_fmt(cs - cr)} over {days} shared day(s) "
              f"({wins} won / {days - wins} lost).")


if __name__ == "__main__":
    main()
