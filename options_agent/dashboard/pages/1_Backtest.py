"""Backtest page — run sweet-spot backtests and view results in a screenshot-friendly layout."""

from __future__ import annotations

import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)

_dash = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _dash not in sys.path:
    sys.path.insert(0, _dash)

import logging
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.backtester import IntradayBacktester
from src.models.backtest_result import BacktestReport

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_LOGO_ICON = os.path.join(_dash, "assets", "logo.svg")
st.set_page_config(
    page_title="Backtest | Options Agent",
    page_icon=_LOGO_ICON if os.path.exists(_LOGO_ICON) else "📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Swiss Luxury Theme (same as main dashboard) ─────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

html, body, .stMarkdown, .stMetric, .stDataFrame,
.stSelectbox, .stSlider, .stTextInput, .stButton,
.stAlert, .stCaption, .stRadio, .stCheckbox, .stTabs,
h1, h2, h3, h4, h5, h6, p, label,
[data-testid="stMetricLabel"], [data-testid="stMetricValue"],
[data-testid="stMarkdownContainer"],
[data-testid="stCaptionContainer"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}

:root {
    --bg-primary: #1C1C1E;
    --bg-card: #2C2C2E;
    --bg-elevated: #3A3A3C;
    --text-primary: #F5F5F7;
    --text-secondary: #A1A1A6;
    --accent-gold: #8b6cff;
    --accent-gold-dim: rgba(139, 108, 255, 0.15);
    --border-subtle: #3A3A3C;
    --success: #6BBF7A;
    --danger: #E06C6C;
    --radius-md: 12px;
    --shadow-card: 0 2px 12px rgba(0,0,0,0.25);
}

.stApp, [data-testid="stAppViewContainer"] {
    background-color: var(--bg-primary) !important;
}

.stMarkdown h2 {
    font-weight: 600 !important;
    letter-spacing: -0.02em !important;
    padding-bottom: 10px !important;
    border-bottom: 2px solid var(--accent-gold) !important;
    margin-bottom: 20px !important;
    margin-top: 40px !important;
    color: var(--text-primary) !important;
}

[data-testid="stMetric"] {
    background: var(--bg-card) !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: var(--radius-md) !important;
    padding: 16px 18px !important;
    box-shadow: var(--shadow-card) !important;
}

[data-testid="stMetricLabel"] {
    color: var(--text-secondary) !important;
    font-size: 11px !important;
    font-weight: 500 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
}

[data-testid="stMetricValue"] {
    color: var(--text-primary) !important;
    font-weight: 700 !important;
}

[data-testid="stSidebar"] {
    background-color: #1C1C1E !important;
    border-right: 1px solid var(--border-subtle) !important;
}

[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    font-weight: 600 !important;
    color: var(--text-primary) !important;
    border-bottom: none !important;
    font-size: 14px !important;
}

.stButton > button[kind="primary"],
.stButton > button[data-testid="stBaseButton-primary"] {
    background: linear-gradient(135deg, #8b6cff, #7452ff) !important;
    color: #1C1C1E !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    box-shadow: 0 2px 8px rgba(139,108,255,0.25) !important;
}

[data-testid="stExpander"] {
    border: 1px solid var(--border-subtle) !important;
    border-radius: var(--radius-md) !important;
    background: var(--bg-card) !important;
    box-shadow: var(--shadow-card) !important;
}

hr { border-color: var(--border-subtle) !important; opacity: 0.5 !important; }
</style>
""", unsafe_allow_html=True)

# Aurora overrides the Swiss-luxury variables above (gold → violet, charcoal),
# hides Streamlit's dev chrome, sets the sidebar logo and the custom nav.
from _theme import apply_theme  # noqa: E402
import _charts as charts  # noqa: E402
apply_theme()


# ── Sidebar controls ────────────────────────────────────────────
with st.sidebar:
    st.header("Backtest Settings")

    symbol = st.selectbox("Symbol", ["SPY", "QQQ"], index=0)
    period = st.selectbox("Period", ["30d", "60d", "90d", "6mo", "1y"], index=4)

    st.divider()
    st.subheader("Sweet Spot Filter")
    sweet_spot_only = st.toggle("Sweet Spot Only", value=True)

    col_q1, col_q2 = st.columns(2)
    with col_q1:
        min_quality = st.number_input("Min Quality", 0, 11, 4, disabled=not sweet_spot_only)
    with col_q2:
        max_quality = st.number_input("Max Quality", 0, 11, 7, disabled=not sweet_spot_only)

    min_cascade = st.slider("Min Explosion", 0, 10, 4, disabled=not sweet_spot_only)

    col_c1, col_c2 = st.columns(2)
    with col_c1:
        min_chop = st.number_input("Min Chop", 0, 14, 2, disabled=not sweet_spot_only)
    with col_c2:
        max_chop = st.number_input("Max Chop", 0, 14, 5, disabled=not sweet_spot_only)

    st.divider()
    st.subheader("Entry Tuning")
    entry_offset = st.slider("Entry Offset %", -0.20, 0.30, 0.10, 0.05,
                             help="Shift entry vs OR boundary. +ve = more confirmation, -ve = earlier entry")

    run_btn = st.button("Run Backtest", type="primary", use_container_width=True)


# ── Main content ────────────────────────────────────────────────
st.markdown("## Backtest")

if run_btn:
    with st.spinner(f"Running backtest for {symbol} over {period}..."):
        bt = IntradayBacktester(
            entry_offset_pct=entry_offset,
            sweet_spot_only=sweet_spot_only,
            max_chop_score=max_chop,
            min_chop_score=min_chop,
            min_cascade=min_cascade,
            sweet_spot_quality_range=(min_quality, max_quality),
        )
        report = bt.run(symbol, period)
        st.session_state["bt_report"] = report
        st.session_state["bt_symbol"] = symbol
        st.session_state["bt_period"] = period

report: BacktestReport | None = st.session_state.get("bt_report")

if report is None:
    st.info("Configure settings in the sidebar and click **Run Backtest**.")
    st.stop()

# ── Summary metrics ─────────────────────────────────────────────
taken = [t for t in report.trades if t.direction != "skip"]

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Trades", report.trades_taken)
c2.metric("Win Rate", f"{report.win_rate:.1f}%")
c3.metric("Profit Factor", f"{report.profit_factor:.2f}")
c4.metric("Total P&L", f"${report.total_pnl:.2f}")
c5.metric("Avg P&L", f"${report.avg_pnl_per_trade:.4f}")
c6.metric("Trading Days", report.total_days)

st.markdown("---")

c7, c8, c9, c10 = st.columns(4)
c7.metric("Calls", f"{report.call_trades} ({report.call_win_rate:.0f}%)")
c8.metric("Puts", f"{report.put_trades} ({report.put_win_rate:.0f}%)")
c9.metric("Max Win", f"${report.max_win:.4f}")
c10.metric("Max Loss", f"${report.max_loss:.4f}")

# ── Equity curve ────────────────────────────────────────────────
st.markdown("## Equity Curve")

if taken:
    cum_pnl = []
    running = 0.0
    dates = []
    for t in taken:
        running += t.pnl_dollars
        cum_pnl.append(running)
        dates.append(t.trade_date)

    fig = go.Figure()
    # Per-contract P&L runs in cents, so both the tooltip and the axis need
    # more precision than the fund-level dollar charts.
    charts.line(fig, dates, cum_pnl, "Cumulative P&L", charts.SERIES["paper"],
                fmt=",.4f")
    charts.style(fig, height=360, legend=False, y_fmt=",.4f")
    st.plotly_chart(fig, width="stretch", config=charts.CONFIG)
else:
    st.warning("No trades matched the criteria.")

# ── Exit reasons ────────────────────────────────────────────────
st.markdown("## Exit Reasons")

if report.exit_reasons:
    reasons = sorted(report.exit_reasons.items(), key=lambda x: -x[1])
    fig_exit = go.Figure(go.Bar(
        x=[r[0] for r in reasons],
        y=[r[1] for r in reasons],
        marker_color=charts.SERIES["paper"],
        text=[f"{r[1]}" for r in reasons],
        textposition="outside",
        cliponaxis=False,
        textfont=dict(family=charts.FONT, size=11, color=charts.INK),
        hovertemplate="%{x}: <b>%{y}</b> trades<extra></extra>",
    ))
    fig_exit.update_layout(bargap=charts.bar_gap(len(reasons)))
    charts.style(fig_exit, height=300, money_y=False, crosshair=False, legend=False)
    fig_exit.update_yaxes(title="Count", title_font=dict(size=12, color=charts.INK_MUTED))
    st.plotly_chart(fig_exit, width="stretch", config=charts.CONFIG)

# ── Trade log ───────────────────────────────────────────────────
st.markdown("## Trade Log")

if taken:
    rows = []
    for t in taken:
        rows.append({
            "Date": str(t.trade_date),
            "Dir": t.direction.upper(),
            "Entry": f"${t.entry_price:.2f}",
            "Exit": f"${t.exit_price:.2f}",
            "P&L": f"${t.pnl_dollars:+.4f}",
            "Exit Reason": t.exit_reason,
            "Mom": t.momentum_score,
            "Quality": t.signal_scores.get("quality_score", ""),
            "Explosion": t.signal_scores.get("explosion_score", ""),
            "Entry@": t.entry_time or "",
            "Exit@": t.exit_time or "",
            "Winner": "Y" if t.is_winner else "",
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", height=400, hide_index=True)

# ── Signal accuracy ─────────────────────────────────────────────
if report.signal_accuracy:
    st.markdown("## Signal Accuracy")
    sig_rows = []
    for s in report.signal_accuracy:
        if s.times_active == 0:
            continue
        sig_rows.append({
            "Signal": s.signal_name,
            "Active": s.times_active,
            "Wins": s.wins_when_active,
            "Win Rate": f"{s.win_rate:.1f}%",
            "Avg P&L": f"${s.avg_pnl_when_active:.4f}",
        })
    if sig_rows:
        st.dataframe(pd.DataFrame(sig_rows), width="stretch", hide_index=True)
