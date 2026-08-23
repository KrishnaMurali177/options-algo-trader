"""Overnight-drift study: is "buy after-hours, sell at the open" a real edge?

Decomposes each session into legs and reports every sense of
"more money cumulatively" separately, because they disagree:

  intraday   : open(t)      -> close(t)         (RTH only)
  overnight  : close(t)     -> open(t+1)        (4pm print -> next open)
  ah_to_open : lastAH(t)    -> open(t+1)        (the actual thesis)
  buy_hold   : close(t)     -> close(t+1)       (= intraday x overnight)

Usage:
  python scripts/overnight_drift_study.py --symbols MU,NVDA,SPY --years 3
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ET = "America/New_York"


def _client():
    from alpaca.data.historical import StockHistoricalDataClient

    key = os.getenv("ALPACA_API_KEY")
    sec = os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec:
        sys.exit("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    return StockHistoricalDataClient(api_key=key, secret_key=sec, raw_data=False)


def fetch_5min(symbol: str, years: float, feed_name: str) -> pd.DataFrame:
    """5-minute bars INCLUDING extended hours, split/dividend adjusted."""
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    end = datetime.now(timezone.utc) - timedelta(minutes=20)
    start = end - timedelta(days=int(365 * years))
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(5, TimeFrameUnit.Minute),
        start=start,
        end=end,
        feed=DataFeed.SIP if feed_name == "sip" else DataFeed.IEX,
        adjustment=Adjustment.ALL,
    )
    df = _client().get_stock_bars(req).df
    if df.empty:
        raise SystemExit(f"no bars for {symbol}")
    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel("symbol")
    df.index = pd.to_datetime(df.index).tz_convert(ET)
    return df.sort_index()


def sessionize(df: pd.DataFrame) -> pd.DataFrame:
    """One row per trading day with RTH/AH reference prices."""
    t = df.index
    mins = t.hour * 60 + t.minute
    rth = (mins >= 9 * 60 + 30) & (mins < 16 * 60)
    ah = (mins >= 16 * 60) & (mins < 20 * 60)
    pre = (mins >= 4 * 60) & (mins < 9 * 60 + 30)

    out = []
    for day, idx in df.groupby(t.normalize()).groups.items():
        d = df.loc[idx]
        m = idx.hour * 60 + idx.minute
        r = d[(m >= 9 * 60 + 30) & (m < 16 * 60)]
        a = d[(m >= 16 * 60) & (m < 20 * 60)]
        p = d[(m >= 4 * 60) & (m < 9 * 60 + 30)]
        if r.empty or len(r) < 30:  # skip half-days / data gaps
            continue
        row = {
            "date": day.date(),
            "rth_open": float(r["open"].iloc[0]),
            "rth_open5c": float(r["close"].iloc[0]),   # close of the first 5-min bar
            "rth_close": float(r["close"].iloc[-1]),
            "rth_vol": float(r["volume"].sum()),
            "ah_bars": int(len(a)),
            "ah_vol": float(a["volume"].sum()) if not a.empty else 0.0,
            "ah_last": float(a["close"].iloc[-1]) if not a.empty else np.nan,
            "ah_last_hi": float(a["high"].iloc[-1]) if not a.empty else np.nan,
            "ah_last_t": a.index[-1].strftime("%H:%M") if not a.empty else "",
            "ah_1700": float(a[a.index.hour < 17]["close"].iloc[-1]) if not a[a.index.hour < 17].empty else np.nan,
            "pre_bars": int(len(p)),
        }
        out.append(row)
    s = pd.DataFrame(out).set_index("date").sort_index()
    return s


def legs(s: pd.DataFrame) -> pd.DataFrame:
    nxt_open = s["rth_open"].shift(-1)
    s = s.copy()
    s["intraday"] = s["rth_close"] / s["rth_open"] - 1
    s["overnight"] = nxt_open / s["rth_close"] - 1
    s["ah_to_open"] = nxt_open / s["ah_last"] - 1
    s["ah1700_to_open"] = nxt_open / s["ah_1700"] - 1
    nxt_o5c = s["rth_open5c"].shift(-1)
    # realistic: you cannot get the auction print; you buy the last AH bar and
    # sell into the first 5 minutes. _adv also pays the top of the AH bar.
    s["ah_to_open5"] = nxt_o5c / s["ah_last"] - 1
    s["ah_to_open_adv"] = nxt_o5c / s["ah_last_hi"] - 1
    s["ah_move"] = s["ah_last"] / s["rth_close"] - 1   # 4pm -> last AH print
    s["buy_hold"] = s["rth_close"].shift(-1) / s["rth_close"] - 1
    return s.iloc[:-1]


def stats(r: pd.Series, cost_bps: float = 0.0, notional: float = 10_000.0) -> dict:
    r = r.dropna()
    if r.empty:
        return {}
    net = r - cost_bps / 1e4
    eq = (1 + net).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    sd = net.std(ddof=1)
    return {
        "n": len(net),
        "mean_bps": net.mean() * 1e4,
        "med_bps": net.median() * 1e4,
        "sd_bps": sd * 1e4,
        "hit%": (net > 0).mean() * 100,
        "sum_$": net.sum() * notional,          # fixed-notional, no compounding
        "compound_x": eq.iloc[-1],              # growth multiple of $1
        "compound_$": (eq.iloc[-1] - 1) * notional,
        "maxDD%": dd * 100,
        "sharpe": (net.mean() / sd * np.sqrt(252)) if sd > 0 else np.nan,
        "t_stat": (net.mean() / (sd / np.sqrt(len(net)))) if sd > 0 else np.nan,
        "be_cost_bps": r.mean() * 1e4,          # cost that zeroes the edge
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="MU,NVDA,SPY")
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--feed", default="iex", choices=["iex", "sip"])
    ap.add_argument("--cost-bps", type=float, default=0.0,
                    help="round-trip friction in bps applied to each leg")
    ap.add_argument("--notional", type=float, default=10_000.0)
    ap.add_argument("--outdir", default="data_cache/overnight_drift")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    LEGS = ["intraday", "overnight", "ah_to_open", "ah1700_to_open",
            "ah_to_open5", "ah_to_open_adv", "buy_hold"]
    rows = []

    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        df = fetch_5min(sym, args.years, args.feed)
        s = legs(sessionize(df))
        s.to_csv(outdir / f"{sym}_sessions.csv")

        cov = s["ah_bars"].gt(0).mean() * 100
        print(f"\n=== {sym}  {s.index[0]} -> {s.index[-1]}  ({len(s)} sessions, feed={args.feed}) ===")
        print(f"  AH coverage: {cov:.1f}% of days have >=1 AH bar | "
              f"median AH bars/day {s['ah_bars'].median():.0f} | "
              f"median AH vol {s['ah_vol'].median():,.0f} "
              f"({(s['ah_vol'] / s['rth_vol'].replace(0, np.nan)).median() * 100:.2f}% of RTH) | "
              f"median last AH print {s['ah_last_t'].replace('', np.nan).dropna().mode().iloc[0] if cov else 'n/a'}")
        big = s[["intraday", "overnight", "ah_to_open"]].abs().gt(0.25).any(axis=1).sum()
        print(f"  sessions with any |leg| > 25% (split/data suspects): {big}")
        if big:
            sus = s[s[["intraday", "overnight", "ah_to_open"]].abs().gt(0.25).any(axis=1)]
            print(sus[["rth_open", "rth_close", "ah_last", "intraday", "overnight", "ah_to_open"]].to_string())

        hdr = f"  {'leg':<16}{'n':>5}{'mean bps':>10}{'hit%':>7}{'sum $':>11}{'compound $':>12}{'maxDD%':>9}{'Sharpe':>8}{'t':>7}"
        print(hdr)
        for leg in LEGS:
            st = stats(s[leg], args.cost_bps, args.notional)
            if not st:
                continue
            print(f"  {leg:<16}{st['n']:>5}{st['mean_bps']:>10.1f}{st['hit%']:>7.1f}"
                  f"{st['sum_$']:>11,.0f}{st['compound_$']:>12,.0f}{st['maxDD%']:>9.1f}"
                  f"{st['sharpe']:>8.2f}{st['t_stat']:>7.2f}")
            rows.append({"symbol": sym, "leg": leg, **st})

        # --- concentration: how much rides on a few earnings gaps? ---
        print(f"  {'leg':<16}{'top5 share%':>12}{'ex-top5 bps':>13}{'ex-top5 t':>11}{'BE cost bps':>13}")
        for leg in LEGS:
            r = s[leg].dropna()
            if r.empty:
                continue
            top5 = r.abs().nlargest(5).index
            ex = r.drop(top5)
            share = (r.loc[top5].sum() / r.sum() * 100) if r.sum() != 0 else np.nan
            t_ex = ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))
            print(f"  {leg:<16}{share:>12.0f}{ex.mean() * 1e4:>13.1f}{t_ex:>11.2f}{r.mean() * 1e4:>13.1f}")

        # --- per-calendar-year stability of the thesis leg ---
        yr = s.groupby(pd.to_datetime(s.index).year)
        print(f"  {'year':<16}{'n':>5}{'ah_to_open bps':>16}{'overnight bps':>15}{'intraday bps':>14}")
        for y, g in yr:
            print(f"  {y:<16}{len(g):>5}{g['ah_to_open'].mean() * 1e4:>16.1f}"
                  f"{g['overnight'].mean() * 1e4:>15.1f}{g['intraday'].mean() * 1e4:>14.1f}")

        # --- cost sensitivity on the thesis leg ---
        line = "  ah_to_open net mean bps @ cost: " + "  ".join(
            f"{c}bp={(s['ah_to_open'].dropna().mean() * 1e4 - c):+.1f}" for c in (0, 5, 10, 20, 30))
        print(line)

    pd.DataFrame(rows).to_csv(outdir / "summary.csv", index=False)
    print(f"\nwrote {outdir}/summary.csv")


if __name__ == "__main__":
    main()
