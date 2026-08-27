"""Dump daily total-return series for a set of tickers as compact JSON.

Used to feed the standalone gold-vs-equities comparison chart.
Prices are split- AND dividend-adjusted, so the series are total-return
(the only fair basis for comparing a 0%-yield asset like gold to SPY/QQQ).

Also attaches, aligned to the same trading days:
  * several denominators to deflate by (see DEFLATORS) -- headline CPI plus
    three official price measures that disagree with it, and M2, so the chart
    can show how much the choice of yardstick moves the answer;
  * DTWEXBGS, the broad trade-weighted dollar index (external dollar value).
All publish on a lag; days past the last observation carry it forward and that
date ships alongside the values.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import io

import pandas as pd


FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"

# Official price indices that measure the same decade differently, plus M2.
# Chained CPI is the only NSA series here; over multi-year windows that is
# immaterial, but it adds a seasonal wiggle to very short ones.
#
# M2 carries kind="quantity" because it is NOT a price index: it measures how
# many dollars exist, not what they buy. Deflating by it answers "did this asset
# outgrow the money supply", which is a real question but a different one, and
# it ignores that real output grew over the same period. The UI keeps it
# visually separate for that reason.
DEFLATORS = {
    "cpi":     ("CPIAUCSL",    "CPI-U",           "headline, seasonally adj.",   "price"),
    "pce":     ("PCEPI",       "PCE",             "the Fed's target measure",    "price"),
    "chained": ("SUUR0000SA0", "Chained CPI",     "allows for substitution, NSA", "price"),
    "core":    ("CPILFESL",    "Core CPI",        "ex food and energy",          "price"),
    "m2":      ("M2SL",        "M2 money supply", "a quantity, not a price",     "quantity"),
}


def fred_daily(series_id: str, index: pd.DatetimeIndex) -> tuple[list[float], str]:
    """A FRED series aligned to `index`, plus its last real observation date.

    Monthly series (CPI) are interpolated on the time axis; daily series (the
    dollar index) mostly line up already and just need holidays filled. Both are
    carried forward past the final observation, since FRED publishes on a lag —
    hence returning that date so the UI can mark the tail as provisional.
    """
    with urllib.request.urlopen(FRED_CSV.format(series_id), timeout=30) as r:
        raw = r.read().decode()
    df = pd.read_csv(io.StringIO(raw))
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")   # FRED writes "." for gaps
    ser = df.dropna().set_index("date")["value"].astype(float).sort_index()
    ser.index = ser.index.tz_localize("America/New_York")
    last = ser.index[-1]

    joined = ser.reindex(ser.index.union(index)).interpolate(method="time").ffill()
    return [round(float(v), 4) for v in joined.reindex(index)], last.strftime("%Y-%m-%d")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="GLD,SPY,QQQ")
    ap.add_argument("--years", type=float, default=11.0)
    ap.add_argument("--out", default="data_cache/asset_comparison.json")
    args = ap.parse_args()

    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    key, sec = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec:
        sys.exit("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    client = StockHistoricalDataClient(api_key=key, secret_key=sec, raw_data=False)

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    end = datetime.now(timezone.utc) - timedelta(minutes=20)
    start = end - timedelta(days=int(365 * args.years))

    req = StockBarsRequest(
        symbol_or_symbols=syms,
        timeframe=TimeFrame(1, TimeFrameUnit.Day),
        start=start,
        end=end,
        feed=DataFeed.SIP,
        adjustment=Adjustment.ALL,
    )
    df = client.get_stock_bars(req).df
    if df.empty:
        sys.exit("no bars returned")

    close = df["close"].unstack(level="symbol")
    close.index = pd.to_datetime(close.index).tz_convert("America/New_York").normalize()
    # Only keep dates where every series trades, so rebasing is always well-defined.
    close = close.dropna(how="any").sort_index()

    deflators = {}
    for key, (fred_id, label, sub, kind) in DEFLATORS.items():
        vals, last = fred_daily(fred_id, close.index)
        deflators[key] = {"label": label, "sub": sub, "fred": fred_id,
                          "kind": kind, "last": last, "values": vals}
    usd, usd_last = fred_daily("DTWEXBGS", close.index)

    payload = {
        "dates": [d.strftime("%Y-%m-%d") for d in close.index],
        "series": {s: [round(float(v), 4) for v in close[s]] for s in syms},
        "deflators": deflators,
        "usd": usd,
        "usd_last": usd_last,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"{len(close)} sessions {close.index[0].date()} -> {close.index[-1].date()}")
    for s in syms:
        print(f"  {s}: {close[s].iloc[0]:.2f} -> {close[s].iloc[-1]:.2f} "
              f"({close[s].iloc[-1] / close[s].iloc[0] - 1:+.1%})")
    n_yrs = (close.index[-1] - close.index[0]).days / 365.25
    for key, d in deflators.items():
        v = d["values"]
        tot = v[-1] / v[0]
        print(f"  {d['label']:<16} {v[0]:9.1f} -> {v[-1]:9.1f}  {tot - 1:+7.1%} cumulative, "
              f"{tot ** (1 / n_yrs) - 1:+.2%}/yr  =>  $1 share {1 / tot - 1:+.1%}  "
              f"[{d['kind']}, last obs {d['last']})]")
    print(f"  USD (broad): {usd[0]:.1f} -> {usd[-1]:.1f} ({usd[-1] / usd[0] - 1:+.1%}), "
          f"last obs {usd_last}")
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
