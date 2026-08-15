"""Fund Performance — interactive time-range view, paper + live overlay.

Money figures come from each broker account's activities ledger, so they
reconcile to the account (equity minus funding) rather than to a reconstruction
from option fills, which misses fees and exercised contracts' share legs. The
2-contract normalization survives only in the Both (matched) comparison, where
paper and live need a common lot size.

Periods: 1W / 1M / 3M / 6M / YTD / All / Custom.
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
                   page_icon=_LOGO_ICON if os.path.exists(_LOGO_ICON) else "📈",
                   initial_sidebar_state="expanded")

# Aurora theme: dev chrome hidden, gold → violet, sidebar logo + custom nav.
from _theme import apply_theme  # noqa: E402
import _charts as charts  # noqa: E402
import _ledger  # noqa: E402
apply_theme()

st.title("📈 Fund Performance")
st.caption("Money figures come from the broker's own ledger — fees, exercises and their "
           "share legs included — so they reconcile to the account. Only "
           "**Both (matched)** normalizes to a flat 2 contracts/position, because "
           "comparing paper to live needs a common lot size.")

EMPTY = pd.DataFrame(columns=["date", "symbol", "dir", "strike", "pnl", "pnl_2ct",
                              "qty", "win"])


def _env_keys(path: str):
    """Read an ALPACA key pair out of a mounted .env file (live or shadow)."""
    k = s = None
    try:
        for ln in open(path):
            ln = ln.strip()
            if ln.startswith("ALPACA_API_KEY="):
                k = ln.split("=", 1)[1].strip().strip('"').strip("'")
            elif ln.startswith("ALPACA_SECRET_KEY="):
                s = ln.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        return None
    return (k, s) if k and s else None


def _live_keys():
    return _env_keys("/app/.env.live")


def _shadow_keys():
    # 2nd PAPER account running main's NEW-golden code — the A/B against the
    # old-golden paper agents. Same file shape as .env.live.
    return _env_keys("/app/.env.shadow")


@st.cache_data(ttl=600, show_spinner="Loading trades…")
def load_trades(account: str) -> pd.DataFrame:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    if account == "paper":
        from src.utils.alpaca_paper import AlpacaPaperTrader
        client = AlpacaPaperTrader().client
    elif account == "shadow":
        keys = _shadow_keys()
        if not keys:
            return EMPTY.copy()
        client = TradingClient(keys[0], keys[1], paper=True)
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
        rows.append({
            "date": d, "symbol": p["u"], "dir": "CALL" if p["cp"] == "C" else "PUT",
            "strike": p["k"], "qty": p["bq"],
            # What the account actually made: real cash out minus real cash in.
            "pnl": round(p["sc"] * 100 - p["bc"] * 100, 2),
            # Same trade at a flat 2 contracts — only meaningful for comparing
            # paper against live, whose lot sizes differ.
            "pnl_2ct": round((av - ab) * 2 * 100, 2),
        })
    df = pd.DataFrame(rows)
    if len(df):
        df["win"] = df["pnl"] > 0
        df = df.sort_values("date").reset_index(drop=True)
    return df if len(df) else EMPTY.copy()


@st.cache_data(ttl=600, show_spinner=False)
def load_ledger(account: str) -> dict:
    """Daily realized P&L straight from the broker's activities ledger.

    See dashboard/_ledger.py for why this exists and why it is FIFO by position
    rather than a sum of daily cash. Reconciles to equity-minus-funding: live
    exactly, paper within about a dollar."""
    if account == "paper":
        base = "https://paper-api.alpaca.markets"
        key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    elif account == "shadow":
        base = "https://paper-api.alpaca.markets"
        keys = _shadow_keys()
        key, sec = keys if keys else (None, None)
    else:
        base = "https://api.alpaca.markets"
        keys = _live_keys()
        key, sec = keys if keys else (None, None)
    if not key or not sec:
        return {}
    try:
        return _ledger.realized_by_day(_ledger.fetch_activities(base, key, sec))
    except Exception:
        return {}


df_p = load_trades("paper")
df_l = load_trades("live")
df_s = load_trades("shadow")
# Tag the account so the combined ("All accounts") view can still break the
# numbers back out per account in the table.
for _df, _acct in ((df_p, "Paper"), (df_l, "Live"), (df_s, "Shadow")):
    if len(_df):
        _df["account"] = _acct
has_live = not df_l.empty
has_shadow = not df_s.empty
if df_p.empty and not has_live and not has_shadow:
    st.warning("No trades found.")
    st.stop()

today = (datetime.now(ET).date() if ET else date.today())
first = min(d["date"].min() for d in (df_p, df_l, df_s) if not d.empty)

c = st.columns([2, 2, 1])
period = c[0].radio("Period", ["1W", "1M", "3M", "6M", "YTD", "All", "Custom"], horizontal=True, index=2)
_views = (["Paper", "Live", "Both (matched)", "All accounts"] if has_live else ["Paper"])
if has_shadow:
    _views.append("Shadow")
view = c[1].radio(
    "Account", _views, horizontal=True,
    index=(_views.index("All accounts") if "All accounts" in _views else 0),
    help="Paper / Live show one account. **Both (matched)** compares them on the "
         "same symbols since live inception — the apples-to-apples view. "
         "**All accounts** is the full fund: every symbol, both accounts, "
         "no clamping. **Shadow** is a 2nd paper account running the NEW golden "
         "config (old-vs-new A/B on the same symbols).",
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


MATCHED, ALLACC, SHADOW = "Both (matched)", "All accounts", "Shadow"

live_start = df_l["date"].min() if not df_l.empty else None
# Matched view only: clamp to where BOTH were live (matched TIME window) for a
# fair comparison. Every other view shows the period exactly as selected.
eff_start = max(start, live_start) if (view == MATCHED and live_start) else start


def clip(df):
    return df[(df["date"] >= eff_start) & (df["date"] <= end)].copy() if len(df) else df


dp, dl, ds = clip(df_p), clip(df_l), clip(df_s)

# Which P&L basis this view is denominated in. Everything downstream reads the
# `pnl` column, so swap it here once instead of threading a column name through.
NORMALIZED = view == MATCHED
for _d in (dp, dl, ds):
    if len(_d):
        _d["pnl"] = _d["pnl_2ct" if NORMALIZED else "pnl"]
        _d["win"] = _d["pnl"] > 0
# The money basis: the broker's ledger, which already contains fees, exercises
# and the share legs they create. Not used by the matched view, whose whole
# point is the normalized per-trade figure. Only load the ledgers this view
# needs — the Shadow view also pulls paper, for the old-vs-new A/B line.
if NORMALIZED:
    _need = ()
elif view == SHADOW:
    _need = ("shadow", "paper")
elif view == ALLACC:
    _need = ("paper", "live") if has_live else ("paper",)
elif view == "Live":
    _need = ("live",)
else:
    _need = ("paper",)
_LEDGER = {a: load_ledger(a) for a in _need}


def ledger_daily(accounts):
    """Fee/exercise-inclusive daily P&L for one or more accounts, clipped to
    the selected window."""
    agg = defaultdict(float)
    for a in accounts:
        for d, v in _LEDGER.get(a, {}).items():
            if eff_start <= d <= end:
                agg[d] += v
    return pd.Series(agg).sort_index() if agg else pd.Series(dtype=float)


def daily_pnl(df, accounts=()):
    """Daily P&L for a view: the ledger where we are talking about money, the
    normalized per-trade sum where we are talking about the comparison."""
    if not NORMALIZED and accounts:
        return ledger_daily(accounts)
    return df.groupby("date")["pnl"].sum().sort_index()


lsyms = sorted(dl["symbol"].unique()) if not dl.empty else []
# Matched view only: compare paper on the SAME symbols live trades — apples-to-apples.
dp_view = dp[dp["symbol"].isin(lsyms)].copy() if (view == MATCHED and lsyms) else dp
# What the KPIs and the per-view charts below are computed over. `accts` picks
# which broker ledgers supply this view's money figures.
if view == "Live":
    primary, plabel, accts = dl, "Live (real money)", ("live",)
elif view == SHADOW:
    primary, plabel, accts = ds, "Shadow (new golden)", ("shadow",)
elif view == ALLACC:
    primary = pd.concat([dp, dl], ignore_index=True) if has_live else dp
    plabel = "All accounts"
    accts = ("paper", "live") if has_live else ("paper",)
elif view == MATCHED:
    primary = dp_view
    plabel = ("Paper · " + ", ".join(lsyms)) if lsyms else "Paper"
    accts = ()
else:
    primary, plabel, accts = dp, "Paper", ("paper",)

_rng = f"**{eff_start} → {end}** · {plabel}"
if view == MATCHED and lsyms:
    _rng += (f" · matched **{', '.join(lsyms)}** on a flat **2 contracts**, "
             f"since live inception — normalized, *not* account dollars")
elif view == ALLACC:
    _rng += " · every symbol, paper **+** live, as traded"
st.caption(_rng)

if primary.empty:
    st.info(f"No {plabel} trades in this range.")
else:
    daily = daily_pnl(primary, accts)
    cumser = daily.cumsum()
    net = float(daily.sum())
    # Hero figure first, chart under it, supporting stats after — the number is
    # the headline, the curve is how it got there.
    _sub = ("normalized to 2 contracts/position — comparison basis, not account dollars"
            if NORMALIZED else
            "from the broker ledger — includes fees, exercises and their share legs")
    st.markdown(charts.hero_html(net, "Net P&L", _sub), unsafe_allow_html=True)
    if view == MATCHED and not dl.empty:
        lnet = float(dl["pnl"].sum())
        # NB: '$' must be escaped or Streamlit's markdown treats the pair as LaTeX
        # and swallows the text between them into a math span.
        st.caption(f"Both legs at 2 contracts — **live** net **\\${lnet:,.0f}** "
                   f"· WR {dl['win'].mean()*100:.0f}%  |  **paper** (same symbols) net "
                   f"**\\${net:,.0f}**  |  **divergence (paper − live) = \\${net - lnet:,.0f}**. "
                   f"Select *Live* for what the account actually made.")
    elif view == ALLACC and has_live:
        st.caption(f"Live (real money) **\\${float(ledger_daily(('live',)).sum()):,.0f}** "
                   f"+ paper (simulated) **\\${float(ledger_daily(('paper',)).sum()):,.0f}**; "
                   f"only the live leg is money that exists.")
    elif view == SHADOW:
        # The A/B this account exists for: new-golden (shadow) vs old-golden
        # (paper) on the SAME symbols and window. Option fills only, so both
        # legs share a basis; the hero above is shadow's fee-inclusive ledger.
        ssyms = sorted(ds["symbol"].unique())
        snet_fill = float(ds["pnl"].sum())
        opaper_fill = float(dp[dp["symbol"].isin(ssyms)]["pnl"].sum()) if ssyms else 0.0
        _div = snet_fill - opaper_fill
        st.caption(
            f"A/B on **{', '.join(ssyms) or '—'}**, same window · option fills only. "
            f"**New golden** (shadow) **\\${snet_fill:,.0f}**  |  **old golden** "
            f"(paper, same symbols) **\\${opaper_fill:,.0f}**  |  **divergence "
            f"(new − old) = \\${_div:,.0f}**. The hero above is the shadow account's "
            f"fee-inclusive ledger; both accounts run on the same 2nd-paper sizing.")

# ── Cumulative P&L ───────────────────────────────────────────────────────────


_series = []
if view in ("Paper", MATCHED, ALLACC) and not dp_view.empty:
    _pname = f"Paper · {', '.join(lsyms)}" if (view == MATCHED and lsyms) else "Paper"
    _c = daily_pnl(dp_view, ("paper",)).cumsum()
    _series.append((_pname, charts.SERIES["paper"], _c.index, _c.values))
if view in ("Live", MATCHED, ALLACC) and not dl.empty:
    _c = daily_pnl(dl, ("live",)).cumsum()
    _lname = "Live (2ct)" if NORMALIZED else "Live (real money)"
    _series.append((_lname, charts.SERIES["live"], _c.index, _c.values))
# Shadow view overlays the A/B directly: new-golden (shadow) against old-golden
# (paper) on the same symbols. Per-fill cumulative so both lines share a basis.
if view == SHADOW and not ds.empty:
    _cs = ds.groupby("date")["pnl"].sum().sort_index().cumsum()
    _series.append(("Shadow (new golden)", charts.SERIES["combined"], _cs.index, _cs.values))
    _ssyms = sorted(ds["symbol"].unique())
    _op = dp[dp["symbol"].isin(_ssyms)]
    if not _op.empty:
        _co = _op.groupby("date")["pnl"].sum().sort_index().cumsum()
        _series.append(("Paper (old golden)", charts.SERIES["paper"], _co.index, _co.values))

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
    # Roll up the fee-adjusted daily series, not the raw trades, so the bars
    # sum to the hero figure.
    wk = daily.groupby(pd.to_datetime(daily.index).to_period("W").start_time.date).sum()
    figw = charts.sign_bars([str(x) for x in wk.index], wk.values, hover_label="Week")
    # "(2ct)" only where it is true — everywhere else these are real dollars.
    st.subheader(f"Weekly P&L — {plabel}" + (" (2ct)" if NORMALIZED else ""))
    charts.style(figw, height=340, crosshair=False, legend=False, zoom=False)
    st.plotly_chart(figw, width="stretch", config=charts.CONFIG_STATIC)

    # Table view — the WCAG-clean twin of the charts above (every value the
    # tooltips show is reachable here without hovering).
    _by = ["account", "symbol"] if (view == ALLACC and has_live) else ["symbol"]
    g = (primary.groupby(_by)
         .agg(trades=("pnl", "size"), win_rate=("win", "mean"), pnl=("pnl", "sum")).reset_index())
    g["win_rate"] = (g["win_rate"] * 100).round(0).astype(int).astype(str) + "%"
    g["pnl"] = g["pnl"].round(0)
    st.subheader(f"By symbol — {plabel}")
    st.dataframe(g.sort_values("pnl", ascending=False), width="stretch", hide_index=True)
    _gap = net - float(g["pnl"].sum())
    if not NORMALIZED and abs(_gap) >= 1:
        st.caption(f"Per-trade rows are option fills only, so they sum to "
                   f"\\${float(g['pnl'].sum()):,.0f} — \\${abs(_gap):,.0f} "
                   f"{'less' if _gap > 0 else 'more'} than the headline. The "
                   f"difference is fees and any exercised contract's share leg, "
                   f"which belong to the account rather than to one trade.")
