"""Dump daily total-return series for a set of tickers as compact JSON.

Used to feed the standalone gold-vs-equities comparison chart.
Prices are split- AND dividend-adjusted, so the series are total-return
(the only fair basis for comparing a 0%-yield asset like gold to SPY/QQQ).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


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

    payload = {
        "dates": [d.strftime("%Y-%m-%d") for d in close.index],
        "series": {s: [round(float(v), 4) for v in close[s]] for s in syms},
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"{len(close)} sessions {close.index[0].date()} -> {close.index[-1].date()}")
    for s in syms:
        print(f"  {s}: {close[s].iloc[0]:.2f} -> {close[s].iloc[-1]:.2f} "
              f"({close[s].iloc[-1] / close[s].iloc[0] - 1:+.1%})")
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
