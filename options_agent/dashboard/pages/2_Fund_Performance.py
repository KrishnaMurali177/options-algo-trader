"""Fund Performance — interactive time-range view of the paper track record.
Normalized to a flat 2 contracts/position. Periods: 1W / 1M / 3M / 6M / YTD / All / Custom.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> /app
if _root not in sys.path:
    sys.path.insert(0, _root)

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

st.set_page_config(page_title="Fund Performance", layout="wide")
st.title("📈 Fund Performance")
st.caption("Paper track record · normalized to a flat **2 contracts/position** "
           "(strips duplication & lot-size differences).")


@st.cache_data(ttl=600, show_spinner="Loading trades from Alpaca…")
def load_trades() -> pd.DataFrame:
    from src.utils.alpaca_paper import AlpacaPaperTrader
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    t = AlpacaPaperTrader()
    out, seen, until = [], set(), None
    while True:
        b = t.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500,
                                                 direction="desc", until=until))
        if not b:
            break
        nn = 0
        for o in b:
            if str(o.id) in seen:
                continue
            seen.add(str(o.id)); out.append(o); nn += 1
        until = b[-1].submitted_at
        if nn == 0 or len(b) < 500:
            break

    def und(s):
        return s[:-15] if len(s) > 15 else s

    pos = defaultdict(lambda: {"bc": 0.0, "bq": 0, "sc": 0.0, "sq": 0, "u": None, "cp": None, "k": None})
    for o in out:
        s = o.symbol
        if len(s) <= 15 or o.status.value != "filled" or not o.filled_avg_price:
            continue
        et = (o.filled_at or o.submitted_at).astimezone(ET)
        p = pos[(et.date(), s)]
        p["u"] = und(s); p["cp"] = s[-9]; p["k"] = int(int(s[-8:]) / 1000)
        px, q = float(o.filled_avg_price), int(float(o.qty))
        if o.side.value == "buy":
            p["bc"] += px * q; p["bq"] += q
        else:
            p["sc"] += px * q; p["sq"] += q
    rows = []
    for (d, s), p in pos.items():
        if p["bq"] == 0:
            continue
        ab = p["bc"] / p["bq"]
        av = (p["sc"] / p["sq"]) if p["sq"] else 0.0
        rows.append({"date": d, "symbol": p["u"], "dir": "CALL" if p["cp"] == "C" else "PUT",
                     "strike": p["k"], "pnl": round((av - ab) * 2 * 100, 2)})
    df = pd.DataFrame(rows)
    if len(df):
        df["win"] = df["pnl"] > 0
        df = df.sort_values("date").reset_index(drop=True)
    return df


df = load_trades()
if df.empty:
    st.warning("No trades found on the account.")
    st.stop()

today = (datetime.now(ET).date() if ET else date.today())
first = df["date"].min()

top = st.columns([3, 1])
period = top[0].radio("Period", ["1W", "1M", "3M", "6M", "YTD", "All", "Custom"],
                      horizontal=True, index=2)
if top[1].button("🔄 Refresh data"):
    load_trades.clear()
    st.rerun()

if period == "Custom":
    c1, c2 = st.columns(2)
    start = c1.date_input("Start", value=first, min_value=first, max_value=today)
    end = c2.date_input("End", value=today, min_value=first, max_value=today)
else:
    end = today
    start = {
        "1W": today - timedelta(days=7),
        "1M": today - timedelta(days=30),
        "3M": today - timedelta(days=91),
        "6M": today - timedelta(days=182),
        "YTD": date(today.year, 1, 1),
        "All": first,
    }[period]

d = df[(df["date"] >= start) & (df["date"] <= end)].copy()
st.caption(f"**{start} → {end}** · {len(d)} trades")
if d.empty:
    st.info("No trades in this range.")
    st.stop()

daily = d.groupby("date")["pnl"].sum().sort_index()
cum = daily.cumsum()
mdd = float((cum - cum.cummax()).min())
net = float(d["pnl"].sum())
wr = float(d["win"].mean() * 100)

k = st.columns(6)
k[0].metric("Net P&L (2ct)", f"${net:,.0f}")
k[1].metric("Trades", f"{len(d)}")
k[2].metric("Win rate", f"{wr:.0f}%")
k[3].metric("Avg / trade", f"${net / len(d):,.0f}")
k[4].metric("Best day", f"${daily.max():,.0f}")
k[5].metric("Max drawdown", f"${mdd:,.0f}")

# Equity curve with range slider
fig = go.Figure()
fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines", name="Cumulative P&L",
                         fill="tozeroy", line=dict(color="#2e7d32", width=2)))
fig.update_layout(title="Cumulative P&L (2ct)", height=400, margin=dict(t=40, b=10),
                  xaxis=dict(rangeslider=dict(visible=True)))
st.plotly_chart(fig, use_container_width=True)

# Weekly bars
d["week"] = pd.to_datetime(d["date"]).dt.to_period("W").apply(lambda p: p.start_time.date())
wk = d.groupby("week")["pnl"].sum()
figw = go.Figure(go.Bar(x=[str(x) for x in wk.index], y=wk.values,
                        marker_color=["#2e7d32" if v >= 0 else "#c62828" for v in wk.values]))
figw.update_layout(title="Weekly P&L (2ct)", height=320, margin=dict(t=40, b=10))
st.plotly_chart(figw, use_container_width=True)

# Per-symbol
g = (d.groupby("symbol")
     .agg(trades=("pnl", "size"), win_rate=("win", "mean"), pnl=("pnl", "sum"))
     .reset_index())
g["win_rate"] = (g["win_rate"] * 100).round(0).astype(int).astype(str) + "%"
g["pnl"] = g["pnl"].round(0)
g = g.sort_values("pnl", ascending=False)
st.subheader("By symbol")
st.dataframe(g, use_container_width=True, hide_index=True)

with st.expander("Daily P&L table"):
    st.dataframe(daily.reset_index().rename(columns={"pnl": "P&L (2ct)"}),
                 use_container_width=True, hide_index=True)
