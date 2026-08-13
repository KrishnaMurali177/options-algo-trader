"""Fund Performance — interactive time-range view, paper + live overlay.
Normalized to a flat 2 contracts/position. Periods: 1W / 1M / 3M / 6M / YTD / All / Custom.
Live keys read from mounted /app/.env.live (paper connection untouched)."""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

import pandas as pd
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
import _charts as charts  # noqa: E402
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
# Tag the account so the combined ("All accounts") view can still break the
# numbers back out per account in the table.
for _df, _acct in ((df_p, "Paper"), (df_l, "Live")):
    if len(_df):
        _df["account"] = _acct
has_live = not df_l.empty
if df_p.empty and not has_live:
    st.warning("No trades found.")
    st.stop()

today = (datetime.now(ET).date() if ET else date.today())
first = df_p["date"].min() if not df_p.empty else df_l["date"].min()

c = st.columns([2, 2, 1])
period = c[0].radio("Period", ["1W", "1M", "3M", "6M", "YTD", "All", "Custom"], horizontal=True, index=2)
_views = ["Paper", "Live", "Both (matched)", "All accounts"] if has_live else ["Paper"]
view = c[1].radio(
    "Account", _views, horizontal=True, index=(len(_views) - 1),
    help="Paper / Live show one account. **Both (matched)** compares them on the "
         "same symbols since live inception — the apples-to-apples view. "
         "**All accounts** is the full fund: every symbol, both accounts, "
         "no clamping.",
)
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


MATCHED, ALLACC = "Both (matched)", "All accounts"

live_start = df_l["date"].min() if not df_l.empty else None
# Matched view only: clamp to where BOTH were live (matched TIME window) for a
# fair comparison. Every other view shows the period exactly as selected.
eff_start = max(start, live_start) if (view == MATCHED and live_start) else start


def clip(df):
    return df[(df["date"] >= eff_start) & (df["date"] <= end)].copy() if len(df) else df


dp, dl = clip(df_p), clip(df_l)
lsyms = sorted(dl["symbol"].unique()) if not dl.empty else []
# Matched view only: compare paper on the SAME symbols live trades — apples-to-apples.
dp_view = dp[dp["symbol"].isin(lsyms)].copy() if (view == MATCHED and lsyms) else dp
# What the KPIs and the per-view charts below are computed over.
if view == "Live":
    primary, plabel = dl, "Live (real money)"
elif view == ALLACC:
    primary = pd.concat([dp, dl], ignore_index=True) if has_live else dp
    plabel = "All accounts"
elif view == MATCHED:
    primary = dp_view
    plabel = ("Paper · " + ", ".join(lsyms)) if lsyms else "Paper"
else:
    primary, plabel = dp, "Paper"

_rng = f"**{eff_start} → {end}** · {plabel}"
if view == MATCHED and lsyms:
    _rng += (f" · matched **{', '.join(lsyms)}** since live inception "
             f"(select *All accounts* for the full fund)")
elif view == ALLACC:
    _rng += " · every symbol, paper **+** live combined"
st.caption(_rng)

if primary.empty:
    st.info(f"No {plabel} trades in this range.")
else:
    daily = primary.groupby("date")["pnl"].sum().sort_index()
    cumser = daily.cumsum()
    net = float(primary["pnl"].sum())
    # Hero figure first, chart under it, supporting stats after — the number is
    # the headline, the curve is how it got there.
    # No sub-line: the range/account caption directly above already says it.
    st.markdown(charts.hero_html(net, "Net P&L"), unsafe_allow_html=True)
    if view == MATCHED and not dl.empty:
        lnet = float(dl["pnl"].sum())
        # NB: '$' must be escaped or Streamlit's markdown treats the pair as LaTeX
        # and swallows the text between them into a math span.
        st.caption(f"**Live** (real money): net **\\${lnet:,.0f}** · WR {dl['win'].mean()*100:.0f}%  |  "
                   f"**Paper** (same symbols): net **\\${net:,.0f}**  |  "
                   f"**intervention/divergence cost (paper − live) = \\${net - lnet:,.0f}**")
    elif view == ALLACC and has_live:
        st.caption(f"Paper **\\${float(dp['pnl'].sum()):,.0f}** + live "
                   f"**\\${float(dl['pnl'].sum()):,.0f}**; paper P&L is simulated, "
                   f"only the live leg is real money.")

# ── Cumulative P&L ───────────────────────────────────────────────────────────


def _cum(df):
    return df.groupby("date")["pnl"].sum().sort_index().cumsum()


_series = []
if view in ("Paper", MATCHED, ALLACC) and not dp_view.empty:
    _pname = f"Paper · {', '.join(lsyms)}" if (view == MATCHED and lsyms) else "Paper"
    _c = _cum(dp_view)
    _series.append((_pname, charts.SERIES["paper"], _c.index, _c.values))
if view in ("Live", MATCHED, ALLACC) and not dl.empty:
    _c = _cum(dl)
    _series.append(("Live (real money)", charts.SERIES["live"], _c.index, _c.values))

if _series:
    st.plotly_chart(charts.trend(_series, height=380), width="stretch",
                    config=charts.CONFIG)

# Supporting stats sit under the hero + curve, not above them.
if not primary.empty:
    k = st.columns(5)
    k[0].metric("Trades", f"{len(primary)}")
    k[1].metric("Win rate", f"{primary['win'].mean() * 100:.0f}%")
    k[2].metric("Avg / trade", f"${net / len(primary):,.0f}")
    k[3].metric("Best day", f"${daily.max():,.0f}")
    k[4].metric("Max drawdown", f"${float((cumser - cumser.cummax()).min()):,.0f}")

# ── Weekly bars + per-symbol for the primary view ────────────────────────────
if not primary.empty:
    primary["week"] = pd.to_datetime(primary["date"]).dt.to_period("W").apply(lambda p: p.start_time.date())
    wk = primary.groupby("week")["pnl"].sum()
    figw = charts.sign_bars([str(x) for x in wk.index], wk.values, hover_label="Week")
    st.subheader(f"Weekly P&L — {plabel} (2ct)")
    charts.style(figw, height=340, crosshair=False, legend=False)
    st.plotly_chart(figw, width="stretch", config=charts.CONFIG)

    # Table view — the WCAG-clean twin of the charts above (every value the
    # tooltips show is reachable here without hovering).
    _by = ["account", "symbol"] if (view == ALLACC and has_live) else ["symbol"]
    g = (primary.groupby(_by)
         .agg(trades=("pnl", "size"), win_rate=("win", "mean"), pnl=("pnl", "sum")).reset_index())
    g["win_rate"] = (g["win_rate"] * 100).round(0).astype(int).astype(str) + "%"
    g["pnl"] = g["pnl"].round(0)
    st.subheader(f"By symbol — {plabel}")
    st.dataframe(g.sort_values("pnl", ascending=False), width="stretch", hide_index=True)
