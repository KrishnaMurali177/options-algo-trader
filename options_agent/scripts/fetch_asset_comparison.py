"""Dump daily total-return series for a set of tickers as compact JSON.

Used to feed the standalone gold-vs-equities comparison chart.
Prices are split- AND dividend-adjusted, so the series are total-return
(the only fair basis for comparing a 0%-yield asset like gold to SPY/QQQ).

Also attaches a daily CPI deflator (FRED CPIAUCSL, monthly, linearly
interpolated) so the chart can show real as well as nominal returns.
CPI publishes with a ~2-week lag; days past the last observation carry the
last value forward and are reported via "cpi_last".
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


FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL"


def fetch_cpi(index: pd.DatetimeIndex) -> tuple[list[float], str]:
    """Daily CPI deflator aligned to `index`, interpolated from monthly FRED data."""
    with urllib.request.urlopen(FRED_CSV, timeout=30) as r:
        raw = r.read().decode()
    cpi = pd.read_csv(io.StringIO(raw))
    cpi.columns = ["date", "cpi"]
    cpi["date"] = pd.to_datetime(cpi["date"]).dt.tz_localize("America/New_York")
    cpi = cpi.dropna().set_index("date")["cpi"].astype(float).sort_index()
    last = cpi.index[-1]

    # Union the monthly stamps with the trading days, interpolate on the time
    # axis, then read back only the trading days. Past the final observation
    # pandas' time interpolation holds the last value, which is what we want.
    joined = cpi.reindex(cpi.index.union(index)).interpolate(method="time").ffill()
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

    cpi, cpi_last = fetch_cpi(close.index)

    payload = {
        "dates": [d.strftime("%Y-%m-%d") for d in close.index],
        "series": {s: [round(float(v), 4) for v in close[s]] for s in syms},
        "cpi": cpi,
        "cpi_last": cpi_last,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"{len(close)} sessions {close.index[0].date()} -> {close.index[-1].date()}")
    for s in syms:
        print(f"  {s}: {close[s].iloc[0]:.2f} -> {close[s].iloc[-1]:.2f} "
              f"({close[s].iloc[-1] / close[s].iloc[0] - 1:+.1%})")
    print(f"  CPI: {cpi[0]:.1f} -> {cpi[-1]:.1f} ({cpi[-1] / cpi[0] - 1:+.1%}), "
          f"last actual observation {cpi_last}")
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
