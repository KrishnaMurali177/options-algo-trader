"""Fund Performance — interactive time-range view, paper + live overlay.
Normalized to a flat 2 contracts/position. Periods: 1W / 1M / 3M / 6M / YTD / All / Custom.
Live keys read from mounted /app/.env.live (paper connection untouched)."""
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

_dash = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _dash not in sys.path:
    sys.path.insert(0, _dash)

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

_LOGO_ICON = os.path.join(_dash, "assets", "logo.svg")
st.set_page_config(page_title="Fund Performance", layout="wide",
                   page_icon=_LOGO_ICON if os.path.exists(_LOGO_ICON) else "📈")

# Aurora theme: dev chrome hidden, gold → violet, sidebar logo + custom nav.
from _theme import apply_theme  # noqa: E402
apply_theme()

st.title("📈 Fund Performance")
st.caption("Normalized to a flat **2 contracts/position** (strips duplication & lot-size differences).")

EMPTY = pd.DataFrame(columns=["date", "symbol", "dir", "strike", "pnl", "win"])


def _live_keys():
    k = s = None
    try:
        for ln in open("/app/.env.live"):
            ln = ln.strip()
            if ln.startswith("ALPACA_API_KEY="):
                k = ln.split("=", 1)[1].strip().strip('"').strip("'")
            elif ln.startswith("ALPACA_SECRET_KEY="):
                s = ln.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        return None
    return (k, s) if k and s else None


@st.cache_data(ttl=600, show_spinner="Loading trades…")
def load_trades(account: str) -> pd.DataFrame:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    if account == "paper":
        from src.utils.alpaca_paper import AlpacaPaperTrader
        client = AlpacaPaperTrader().client
    else:
        keys = _live_keys()
        if not keys:
            return EMPTY.copy()
        client = TradingClient(keys[0], keys[1], paper=False)

    out, seen, until = [], set(), None
    while True:
        b = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500,
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

    def und(x):
        return x[:-15] if len(x) > 15 else x

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
    return df if len(df) else EMPTY.copy()


df_p = load_trades("paper")
df_l = load_trades("live")
has_live = not df_l.empty
if df_p.empty and not has_live:
    st.warning("No trades found.")
    st.stop()

today = (datetime.now(ET).date() if ET else date.today())
first = df_p["date"].min() if not df_p.empty else df_l["date"].min()

c = st.columns([2, 2, 1])
period = c[0].radio("Period", ["1W", "1M", "3M", "6M", "YTD", "All", "Custom"], horizontal=True, index=2)
view = c[1].radio("Account", ["Paper", "Live", "Both"] if has_live else ["Paper"],
                  horizontal=True, index=(2 if has_live else 0))
if c[2].button("🔄 Refresh"):
    load_trades.clear()
    st.rerun()

if period == "Custom":
    cc = st.columns(2)
    start = cc[0].date_input("Start", value=first, min_value=first, max_value=today)
    end = cc[1].date_input("End", value=today, min_value=first, max_value=today)
else:
    end = today
    start = {"1W": today - timedelta(days=7), "1M": today - timedelta(days=30),
             "3M": today - timedelta(days=91), "6M": today - timedelta(days=182),
             "YTD": date(today.year, 1, 1), "All": first}[period]


live_start = df_l["date"].min() if not df_l.empty else None
# In "Both", clamp to where BOTH were live (matched TIME window) for a fair comparison.
eff_start = max(start, live_start) if (view == "Both" and live_start) else start


def clip(df):
    return df[(df["date"] >= eff_start) & (df["date"] <= end)].copy() if len(df) else df


dp, dl = clip(df_p), clip(df_l)
lsyms = sorted(dl["symbol"].unique()) if not dl.empty else []
# In "Both", compare paper on the SAME symbols live trades (SPY/QQQ) — apples-to-apples.
dp_view = dp[dp["symbol"].isin(lsyms)].copy() if (view == "Both" and lsyms) else dp
_rng = f"**{eff_start} → {end}**"
if view == "Both" and lsyms:
    _rng += f" · matched **{', '.join(lsyms)}** since live inception (select *Paper* for the full fund)"
st.caption(_rng)

# KPIs for the selected view (Live if Live, else Paper)
primary = dl if view == "Live" else dp_view
plabel = "Live (real money)" if view == "Live" else ("Paper · SPY/QQQ" if view == "Both" else "Paper")
if primary.empty:
    st.info(f"No {plabel} trades in this range.")
else:
    daily = primary.groupby("date")["pnl"].sum().sort_index()
    cumser = daily.cumsum()
    net = float(primary["pnl"].sum())
    k = st.columns(6)
    k[0].metric(f"{plabel} · Net P&L", f"${net:,.0f}")
    k[1].metric("Trades", f"{len(primary)}")
    k[2].metric("Win rate", f"{primary['win'].mean() * 100:.0f}%")
    k[3].metric("Avg / trade", f"${net / len(primary):,.0f}")
    k[4].metric("Best day", f"${daily.max():,.0f}")
    k[5].metric("Max drawdown", f"${float((cumser - cumser.cummax()).min()):,.0f}")
    if view == "Both" and not dl.empty:
        lnet = float(dl["pnl"].sum())
        st.caption(f"🔴 **Live** (real money): net **${lnet:,.0f}** · WR {dl['win'].mean()*100:.0f}%  |  "
                   f"🟢 **Paper** (same symbols): net **${net:,.0f}**  |  "
                   f"**intervention/divergence cost (paper − live) = ${net - lnet:,.0f}**")

# Equity curve overlay
fig = go.Figure()
if view in ("Paper", "Both") and not dp_view.empty:
    cp = dp_view.groupby("date")["pnl"].sum().sort_index().cumsum()
    _pname = "Paper · SPY/QQQ (untouched)" if view == "Both" else "Paper (untouched)"
    fig.add_trace(go.Scatter(x=cp.index, y=cp.values, name=_pname,
                             mode="lines", line=dict(color="#2e7d32", width=2)))
if view in ("Live", "Both") and not dl.empty:
    cl = dl.groupby("date")["pnl"].sum().sort_index().cumsum()
    fig.add_trace(go.Scatter(x=cl.index, y=cl.values, name="Live (real money)",
                             mode="lines", line=dict(color="#c62828", width=2)))
fig.update_layout(title="Cumulative P&L (2ct)", height=400, margin=dict(t=40, b=10),
                  hovermode="x unified", xaxis=dict(rangeslider=dict(visible=True)))
st.plotly_chart(fig, use_container_width=True)

# Weekly bars + per-symbol for the primary view
if not primary.empty:
    primary["week"] = pd.to_datetime(primary["date"]).dt.to_period("W").apply(lambda p: p.start_time.date())
    wk = primary.groupby("week")["pnl"].sum()
    figw = go.Figure(go.Bar(x=[str(x) for x in wk.index], y=wk.values,
                            marker_color=["#2e7d32" if v >= 0 else "#c62828" for v in wk.values]))
    figw.update_layout(title=f"Weekly P&L — {plabel} (2ct)", height=320, margin=dict(t=40, b=10))
    st.plotly_chart(figw, use_container_width=True)

    g = (primary.groupby("symbol")
         .agg(trades=("pnl", "size"), win_rate=("win", "mean"), pnl=("pnl", "sum")).reset_index())
    g["win_rate"] = (g["win_rate"] * 100).round(0).astype(int).astype(str) + "%"
    g["pnl"] = g["pnl"].round(0)
    st.subheader(f"By symbol — {plabel}")
    st.dataframe(g.sort_values("pnl", ascending=False), use_container_width=True, hide_index=True)
