"""
Options Trading Agent — Streamlit Simulation Dashboard

Run:
    cd options_agent
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import os
import sys

# ── Ensure project root is on path ──
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from streamlit_autorefresh import st_autorefresh

from src.market_analyzer import MarketAnalyzer
from src.models.market_data import MarketIndicators, MarketRegime
from src.models.options import Greeks, OptionContract, OptionLeg, OptionOrder
from src.models.portfolio import (
    AccountInfo,
    PortfolioSummary,
    RiskAssessment,
    StockPosition,
)
from src.risk_manager import RiskManager
from src.strategies import STRATEGIES
from src.strategies.buy_call import BuyCallStrategy
from src.strategies.buy_put import BuyPutStrategy
from src.strategy_selector import REGIME_STRATEGY_MAP, StrategySelector
from src.entry_analyzer import EntryAnalyzer, EntrySignal
from src.execution_guide import build_execution_guide
from src.opening_range import OpeningRangeAnalyzer, BreakoutDirection
from src.recent_momentum import RecentMomentumAnalyzer
from src.utils.quality_scorer import compute_quality_score
from src.momentum_cascade import MomentumCascadeDetector
from src.utils.choppiness import compute_choppiness

# ── Page config ──────────────────────────────────────────────────

st.set_page_config(
    page_title="Options Trading Simulator",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Swiss Luxury Theme CSS ────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,0,0');

/* ── Global typography ── */
html, body, .stMarkdown, .stMetric, .stDataFrame,
.stSelectbox, .stSlider, .stTextInput, .stButton,
.stAlert, .stCaption, .stRadio, .stCheckbox, .stTabs,
h1, h2, h3, h4, h5, h6, p, label,
[data-testid="stMetricLabel"], [data-testid="stMetricValue"],
[data-testid="stMarkdownContainer"],
[data-testid="stCaptionContainer"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}

/* ── Design tokens ── */
:root {
    --bg-primary: #1C1C1E;
    --bg-card: #2C2C2E;
    --bg-elevated: #3A3A3C;
    --text-primary: #F5F5F7;
    --text-secondary: #A1A1A6;
    --text-muted: #636366;
    --accent-gold: #C9A96E;
    --accent-gold-dim: rgba(201, 169, 110, 0.15);
    --accent-gold-glow: rgba(201, 169, 110, 0.08);
    --border-subtle: #3A3A3C;
    --border-faint: #2C2C2E;
    --success: #6BBF7A;
    --danger: #E06C6C;
    --info: #7AB4D9;
    --warning: #D4A74A;
    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
    --shadow-card: 0 2px 12px rgba(0,0,0,0.25);
    --shadow-elevated: 0 4px 20px rgba(0,0,0,0.35);
}

/* ── Preserve Material Icons for Streamlit controls ── */
[data-testid="collapsedControl"] *,
[data-testid="stSidebarCollapsedControl"] *,
[data-testid="stExpander"] summary span[data-testid="stMarkdownContainer"] ~ span,
[data-testid="stExpander"] summary svg,
[data-testid="stExpanderToggleIcon"] *,
.material-symbols-rounded,
[class*="material-symbols"],
[class*="material-icons"],
[data-testid="baseButton-headerNoPadding"] span,
button[kind="headerNoPadding"] span,
[data-testid="stExpander"] details summary span:last-child {
    font-family: 'Material Symbols Rounded', 'Material Icons Round', 'Material Icons', sans-serif !important;
    -webkit-font-feature-settings: 'liga' !important;
    font-feature-settings: 'liga' !important;
}

/* ── Page background ── */
.stApp, [data-testid="stAppViewContainer"] {
    background-color: var(--bg-primary) !important;
}

/* ── Section headings: clean gold underline ── */
.stMarkdown h2 {
    font-weight: 600 !important;
    letter-spacing: -0.02em !important;
    padding-bottom: 10px !important;
    border-bottom: 2px solid var(--accent-gold) !important;
    margin-bottom: 20px !important;
    margin-top: 40px !important;
    color: var(--text-primary) !important;
}

.stMarkdown h3 {
    font-weight: 600 !important;
    letter-spacing: -0.01em !important;
    color: var(--text-primary) !important;
    margin-top: 24px !important;
}

/* ── Metric cards ── */
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

/* ── Sidebar refinement ── */
[data-testid="stSidebar"] {
    background-color: #1C1C1E !important;
    border-right: 1px solid var(--border-subtle) !important;
}

[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    font-weight: 600 !important;
    color: var(--text-primary) !important;
    letter-spacing: -0.01em !important;
    border-bottom: none !important;
    font-size: 14px !important;
}

/* ── Buttons ── */
.stButton > button[kind="primary"],
.stButton > button[data-testid="stBaseButton-primary"] {
    background: linear-gradient(135deg, #C9A96E, #B8944F) !important;
    color: #1C1C1E !important;
    border: none !important;
    border-radius: var(--radius-sm) !important;
    font-weight: 600 !important;
    letter-spacing: 0.02em !important;
    transition: all 0.2s ease !important;
    box-shadow: 0 2px 8px rgba(201,169,110,0.25) !important;
}

.stButton > button[kind="primary"]:hover,
.stButton > button[data-testid="stBaseButton-primary"]:hover {
    background: linear-gradient(135deg, #D4B97E, #C9A96E) !important;
    box-shadow: 0 4px 16px rgba(201,169,110,0.35) !important;
}

/* ── Expanders ── */
[data-testid="stExpander"] {
    border: 1px solid var(--border-subtle) !important;
    border-radius: var(--radius-md) !important;
    background: var(--bg-card) !important;
    box-shadow: var(--shadow-card) !important;
}

[data-testid="stExpander"] summary {
    font-weight: 600 !important;
    color: var(--text-primary) !important;
}

/* ── Info/Warning/Success/Error alerts ── */
[data-testid="stAlert"] {
    border-radius: var(--radius-md) !important;
    border-width: 1px !important;
}

/* ── Dataframes ── */
[data-testid="stDataFrame"] {
    border-radius: var(--radius-md) !important;
    overflow: hidden !important;
}

/* ── Horizontal dividers ── */
hr {
    border-color: var(--border-subtle) !important;
    opacity: 0.5 !important;
}

/* ── Swiss card utility (used in inline HTML) ── */
.swiss-card {
    background: #2C2C2E;
    border: 1px solid #3A3A3C;
    border-radius: 12px;
    padding: 24px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.25);
}

.swiss-card-accent {
    background: #2C2C2E;
    border: 1px solid #C9A96E;
    border-radius: 12px;
    padding: 24px;
    box-shadow: 0 2px 12px rgba(201,169,110,0.12);
}

.swiss-label {
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #A1A1A6;
}

.swiss-value {
    font-size: 36px;
    font-weight: 700;
    color: #F5F5F7;
    line-height: 1.1;
}

.swiss-accent {
    color: #C9A96E;
}

.swiss-muted {
    color: #A1A1A6;
    font-size: 13px;
}

.swiss-tag {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
}
</style>
""", unsafe_allow_html=True)

# ── Constants ────────────────────────────────────────────────────

REGIME_LABELS = {
    MarketRegime.LOW_VOL_BULLISH: ("Low Vol · Bullish", "●"),
    MarketRegime.LOW_VOL_NEUTRAL: ("Low Vol · Neutral", "◐"),
    MarketRegime.RANGE_BOUND_HV: ("Range-Bound · High Vol", "◑"),
    MarketRegime.HIGH_VOL_BEARISH: ("High Vol · Bearish", "●"),
    MarketRegime.TRENDING_BEARISH: ("Trending · Bearish", "▼"),
}


STRATEGY_DESCRIPTIONS = {
    "buy_call": {
        "name": "Buy Call (Scalp)",
        "icon": "↗",
        "desc": "Buy ATM calls on bullish breakout after 60-min opening range. Intraday scalp — close by EOD.",
        "color": "#6BBF7A",
    },
    "buy_put": {
        "name": "Buy Put (Scalp)",
        "icon": "↘",
        "desc": "Buy ATM puts on bearish breakout after 60-min opening range. Intraday scalp — close by EOD.",
        "color": "#E06C6C",
    },
}


# ══════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════

# ── Browser notification helper ────────────────────────────────────

def send_browser_notification(title: str, body: str, urgency: str = "normal"):
    """Inject JavaScript to send a browser push notification + audio alert.

    Requires user to grant notification permission on first trigger.
    urgency: "critical" plays a louder repeating beep.
    """
    import streamlit.components.v1 as components

    beep_js = """
    // Audio alert — two short beeps
    (function() {
        try {
            var ctx = new (window.AudioContext || window.webkitAudioContext)();
            function beep(freq, startTime, duration) {
                var osc = ctx.createOscillator();
                var gain = ctx.createGain();
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.frequency.value = freq;
                gain.gain.value = 0.3;
                osc.start(startTime);
                osc.stop(startTime + duration);
            }
            beep(880, ctx.currentTime, 0.15);
            beep(880, ctx.currentTime + 0.2, 0.15);
    """ + ("""
            // Critical: add a third louder beep
            beep(1100, ctx.currentTime + 0.45, 0.25);
    """ if urgency == "critical" else "") + """
        } catch(e) { console.log("Audio alert failed:", e); }
    })();
    """

    # Escape for JS string
    safe_title = title.replace("'", "\\'").replace("\n", " ")
    safe_body = body.replace("'", "\\'").replace("\n", " ")

    notification_js = f"""
    <script>
    (function() {{
        {beep_js}

        // Request notification permission and send
        if ('Notification' in window) {{
            if (Notification.permission === 'granted') {{
                new Notification('{safe_title}', {{
                    body: '{safe_body}',
                    icon: 'https://img.icons8.com/3d-fluency/94/money-bag.png',
                    requireInteraction: true
                }});
            }} else if (Notification.permission !== 'denied') {{
                Notification.requestPermission().then(function(permission) {{
                    if (permission === 'granted') {{
                        new Notification('{safe_title}', {{
                            body: '{safe_body}',
                            icon: 'https://img.icons8.com/3d-fluency/94/money-bag.png',
                            requireInteraction: true
                        }});
                    }}
                }});
            }}
        }}
    }})();
    </script>
    """
    components.html(notification_js, height=0, width=0)


def request_notification_permission():
    """Inject JS to request browser notification permission on page load."""
    import streamlit.components.v1 as components
    components.html("""
    <script>
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }
    </script>
    """, height=0, width=0)

# ── Mock data for offline / demo mode ─────────────────────────────

MOCK_INDICATORS: dict[str, dict] = {
    "AAPL": {
        "symbol": "AAPL", "current_price": 190.0, "vix": 15.0, "rsi_14": 55.0,
        "sma_20": 188.0, "sma_50": 185.0, "sma_200": 175.0,
        "bb_upper": 195.0, "bb_middle": 188.0, "bb_lower": 181.0,
        "macd": 1.5, "macd_signal": 1.0, "macd_histogram": 0.5, "atr_14": 3.2,
        "volume": 55_000_000, "volume_sma_20": 50_000_000,
        "next_earnings_date": "2026-07-25", "days_to_earnings": 100,
    },
    "SPY": {
        "symbol": "SPY", "current_price": 440.0, "vix": 25.0, "rsi_14": 50.0,
        "sma_20": 438.0, "sma_50": 435.0, "sma_200": 420.0,
        "bb_upper": 450.0, "bb_middle": 438.0, "bb_lower": 426.0,
        "macd": 0.3, "macd_signal": 0.2, "macd_histogram": 0.1, "atr_14": 6.5,
        "volume": 70_000_000, "volume_sma_20": 65_000_000,
        "next_earnings_date": None, "days_to_earnings": None,
    },
    "TSLA": {
        "symbol": "TSLA", "current_price": 165.0, "vix": 35.0, "rsi_14": 72.0,
        "sma_20": 170.0, "sma_50": 178.0, "sma_200": 180.0,
        "bb_upper": 180.0, "bb_middle": 170.0, "bb_lower": 160.0,
        "macd": -2.0, "macd_signal": -1.0, "macd_histogram": -1.0, "atr_14": 5.5,
        "volume": 90_000_000, "volume_sma_20": 75_000_000,
        "next_earnings_date": "2026-07-20", "days_to_earnings": 95,
    },
    "MSFT": {
        "symbol": "MSFT", "current_price": 410.0, "vix": 12.0, "rsi_14": 35.0,
        "sma_20": 408.0, "sma_50": 412.0, "sma_200": 390.0,
        "bb_upper": 420.0, "bb_middle": 408.0, "bb_lower": 396.0,
        "macd": 0.2, "macd_signal": 0.1, "macd_histogram": 0.1, "atr_14": 5.0,
        "volume": 30_000_000, "volume_sma_20": 35_000_000,
        "next_earnings_date": "2026-07-22", "days_to_earnings": 97,
    },
}


def _mock_indicators(symbol: str, timeframe: str = "daily") -> dict:
    """Return mock indicators — use AAPL data as fallback for unknown symbols."""
    data = MOCK_INDICATORS.get(symbol, MOCK_INDICATORS["AAPL"]).copy()
    data["symbol"] = symbol
    data["timestamp"] = datetime.now(timezone.utc).isoformat()
    data["timeframe"] = timeframe

    # Adjust indicators slightly per timeframe to simulate different resolutions
    if timeframe in ("intraday", "15min"):
        # Intraday: tighter bands, more volatile RSI, smaller ATR, lower per-bar volume
        data["atr_14"] = round(data["atr_14"] * 0.15, 2)     # 15-min ATR is ~15% of daily
        data["bb_upper"] = round(data["bb_middle"] + (data["bb_upper"] - data["bb_middle"]) * 0.4, 2)
        data["bb_lower"] = round(data["bb_middle"] - (data["bb_middle"] - data["bb_lower"]) * 0.4, 2)
        data["volume"] = int(data["volume"] * 0.04)           # ~1/26 of daily volume per 15-min bar
        data["volume_sma_20"] = int(data["volume_sma_20"] * 0.04)
    elif timeframe == "1hour":
        # Hourly: somewhat tighter
        data["atr_14"] = round(data["atr_14"] * 0.35, 2)
        data["bb_upper"] = round(data["bb_middle"] + (data["bb_upper"] - data["bb_middle"]) * 0.6, 2)
        data["bb_lower"] = round(data["bb_middle"] - (data["bb_middle"] - data["bb_lower"]) * 0.6, 2)
        data["volume"] = int(data["volume"] * 0.15)           # ~1/6.5 of daily per hourly bar
        data["volume_sma_20"] = int(data["volume_sma_20"] * 0.15)
    elif timeframe == "weekly":
        # Weekly: wider bands, larger ATR, higher aggregate volume
        data["atr_14"] = round(data["atr_14"] * 2.5, 2)
        data["bb_upper"] = round(data["bb_middle"] + (data["bb_upper"] - data["bb_middle"]) * 1.8, 2)
        data["bb_lower"] = round(data["bb_middle"] - (data["bb_middle"] - data["bb_lower"]) * 1.8, 2)
        data["volume"] = int(data["volume"] * 5)
        data["volume_sma_20"] = int(data["volume_sma_20"] * 5)

    return data


def _mock_price_history(symbol: str) -> pd.DataFrame:
    """Generate synthetic 6-month OHLCV data for chart display."""
    data = MOCK_INDICATORS.get(symbol, MOCK_INDICATORS["AAPL"])
    base_price = data["current_price"]
    n_days = 130
    dates = pd.bdate_range(end=date.today(), periods=n_days)
    np.random.seed(hash(symbol) % 2**31)
    returns = np.random.normal(0.0003, 0.015, n_days)
    prices = base_price * np.exp(np.cumsum(returns) - np.cumsum(returns)[-1])
    # Make the last price match current_price
    prices = prices * (base_price / prices[-1])
    df = pd.DataFrame({
        "Open": prices * (1 + np.random.uniform(-0.005, 0.005, n_days)),
        "High": prices * (1 + np.random.uniform(0.002, 0.02, n_days)),
        "Low": prices * (1 - np.random.uniform(0.002, 0.02, n_days)),
        "Close": prices,
        "Volume": np.random.randint(10_000_000, 80_000_000, n_days),
    }, index=dates)
    return df


def _mock_fetch(symbol: str, timeframe: str = "daily") -> dict:
    """Build the same {indicators, regime} dict as the live fetch."""
    ind_data = _mock_indicators(symbol, timeframe)
    ind = MarketIndicators(**ind_data)
    analyzer = MarketAnalyzer()
    regime = analyzer.classify_regime(ind)
    return {"indicators": ind.model_dump(), "regime": regime.value}


# ── Live data fetchers ────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner="Fetching market data…")
def fetch_indicators(symbol: str, timeframe: str = "daily") -> dict:
    """Fetch live market indicators via the MarketAnalyzer (cached 5 min)."""
    analyzer = MarketAnalyzer()
    ind = analyzer.analyze(symbol, timeframe=timeframe)
    regime = analyzer.classify_regime(ind)
    return {"indicators": ind.model_dump(), "regime": regime.value}


@st.cache_data(ttl=120, show_spinner="Fetching intraday data for time replay…")
def fetch_indicators_at_time(symbol: str, as_of_time: str, timeframe: str = "daily") -> dict:
    """Fetch intraday 5-min data, truncate to as_of_time, and recompute indicators.

    This lets the user see what the recommendation would have been at e.g. 11:30 AM.
    """
    import math as _math

    # Parse target time
    _hh, _mm = map(int, as_of_time.split(":"))

    # Fetch 5-min intraday data — try Alpaca first (cached), fallback to yfinance
    df_5m = pd.DataFrame()
    try:
        from src.utils.alpaca_data import fetch_bars as _alpaca_fetch
        df_5m = _alpaca_fetch(symbol, days_back=5, interval="5min")
    except Exception:
        pass
    if df_5m.empty:
        df_5m = yf.download(symbol, period="5d", interval="5m", progress=False)

    df_daily = yf.download(symbol, period="6mo", interval="1d", progress=False)
    vix_df = yf.download("^VIX", period="5d", interval="1d", progress=False)

    for _df in (df_5m, df_daily, vix_df):
        if isinstance(_df.columns, pd.MultiIndex):
            _df.columns = _df.columns.get_level_values(0)

    if df_5m.empty:
        raise ValueError(f"No intraday data for {symbol}")

    df_5m.index = pd.to_datetime(df_5m.index)
    if df_5m.index.tz is None:
        df_5m.index = df_5m.index.tz_localize("UTC")
    df_5m.index = df_5m.index.tz_convert("US/Eastern")

    # Get today's (or last trading day's) bars
    from datetime import date as _date
    _today = _date.today()
    _today_bars = df_5m[df_5m.index.date == _today]
    if _today_bars.empty:
        _last_day = df_5m.index[-1].date()
        _today_bars = df_5m[df_5m.index.date == _last_day]

    # Truncate to as_of_time
    _cutoff = _today_bars.index[0].replace(hour=_hh, minute=_mm, second=0)
    _bars = _today_bars[_today_bars.index <= _cutoff]
    if _bars.empty:
        raise ValueError(f"No bars before {as_of_time} ET for {symbol}")

    _close = _bars["Close"].astype(float)
    _high = _bars["High"].astype(float)
    _low = _bars["Low"].astype(float)
    _vol = _bars["Volume"].astype(float)
    _price = float(_close.iloc[-1])

    # Extend with daily closes for longer indicators (SMA50, SMA200, Bollinger, MACD)
    _daily_close = df_daily["Close"].astype(float) if not df_daily.empty else pd.Series(dtype=float)
    _extended = pd.concat([_daily_close, _close], ignore_index=True)

    # VIX
    _vix = float(vix_df["Close"].iloc[-1]) if not vix_df.empty else 20.0

    # RSI helper
    def _rsi_calc(s, period=14):
        delta = s.diff()
        gain = delta.where(delta > 0, 0.0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
        rs = gain / (loss.replace(0, float("nan")))
        rsi = 100 - (100 / (1 + rs))
        val = rsi.iloc[-1]
        return float(val) if not pd.isna(val) else 50.0

    # RSI from pure 5-min candles (not blended with daily) — what traders see
    _rsi = _rsi_calc(_close) if len(_close) >= 15 else 50.0

    # SMAs from daily-extended series (regime-scale indicators)
    _sma_20 = float(_daily_close.iloc[-20:].mean()) if len(_daily_close) >= 20 else _price
    _sma_50 = float(_daily_close.iloc[-50:].mean()) if len(_daily_close) >= 50 else _sma_20
    _sma_200 = float(_daily_close.iloc[-200:].mean()) if len(_daily_close) >= 200 else _sma_50

    # Bollinger Bands from daily closes
    _bb_series = _daily_close.iloc[-20:] if len(_daily_close) >= 20 else _extended.iloc[-20:]
    _bb_mid = float(_bb_series.mean())
    _bb_std = float(_bb_series.std()) if len(_bb_series) > 1 else 1.0
    _bb_upper = _bb_mid + 2 * _bb_std
    _bb_lower = _bb_mid - 2 * _bb_std

    # MACD from daily-extended series
    if len(_extended) >= 26:
        _ema12 = _extended.ewm(span=12, adjust=False).mean()
        _ema26 = _extended.ewm(span=26, adjust=False).mean()
        _macd_line = float((_ema12 - _ema26).iloc[-1])
        _sig = (_ema12 - _ema26).ewm(span=9, adjust=False).mean()
        _macd_signal = float(_sig.iloc[-1])
        _macd_hist = _macd_line - _macd_signal
    else:
        _macd_line = _macd_signal = _macd_hist = 0.0

    # ATR
    if len(_bars) >= 2:
        _tr_vals = []
        for _i in range(1, len(_bars)):
            _h = float(_high.iloc[_i])
            _l = float(_low.iloc[_i])
            _pc = float(_close.iloc[_i - 1])
            _tr_vals.append(max(_h - _l, abs(_h - _pc), abs(_l - _pc)))
        _atr = float(np.mean(_tr_vals[-14:])) if _tr_vals else 1.0
    else:
        _atr = 1.0

    # Volume
    _cur_vol = int(_vol.iloc[-1]) if len(_vol) > 0 else 0
    _vol_sma = float(_vol.mean()) if len(_vol) > 0 else 1.0

    ind_data = {
        "symbol": symbol,
        "timestamp": _bars.index[-1].to_pydatetime().replace(tzinfo=None),
        "current_price": _price,
        "timeframe": timeframe,
        "vix": _vix,
        "rsi_14": _rsi,
        "sma_20": _sma_20,
        "sma_50": _sma_50,
        "sma_200": _sma_200,
        "bb_upper": _bb_upper,
        "bb_middle": _bb_mid,
        "bb_lower": _bb_lower,
        "macd": _macd_line,
        "macd_signal": _macd_signal,
        "macd_histogram": _macd_hist,
        "atr_14": _atr,
        "volume": _cur_vol,
        "volume_sma_20": _vol_sma,
        "rsi_5min": _rsi,  # replay already uses 5-min bars, so rsi_14 IS the 5-min RSI
        "next_earnings_date": None,
        "days_to_earnings": None,
    }

    # Classify regime
    _ind_obj = MarketIndicators(**ind_data)
    _analyzer = MarketAnalyzer()
    _regime = _analyzer.classify_regime(_ind_obj)

    return {"indicators": ind_data, "regime": _regime.value, "bars_5m": _bars}


@st.cache_data(ttl=300, show_spinner="Downloading price history…")
def fetch_price_history(symbol: str, period: str = "6mo") -> pd.DataFrame:
    df = yf.download(symbol, period=period, interval="1d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def build_simulated_portfolio(
    symbol: str,
    shares: int,
    avg_cost: float,
    current_price: float,
    portfolio_value: float,
    cash: float,
) -> PortfolioSummary:
    """Build a PortfolioSummary from user inputs."""
    mv = shares * current_price
    return PortfolioSummary(
        account=AccountInfo(
            account_id="SIM",
            buying_power=cash,
            cash=cash,
            portfolio_value=portfolio_value,
            options_buying_power=cash * 0.5,
        ),
        stock_positions=[
            StockPosition(
                symbol=symbol.upper(),
                quantity=float(shares),
                average_cost=avg_cost,
                current_price=current_price,
                market_value=mv,
                unrealized_pnl=(current_price - avg_cost) * shares,
            )
        ]
        if shares > 0
        else [],
        total_options_allocation=0.0,
        daily_pnl=0.0,
        trades_today=0,
    )


def generate_simulated_chain(
    symbol: str, current_price: float, vix: float, dte: int = 37
) -> list[OptionContract]:
    """Generate a synthetic options chain for simulation purposes.
    
    NOTE: These are Black-Scholes estimates, NOT real market prices.
    Real prices may differ significantly, especially for 0DTE options
    where time value is near zero. Use live chain fetch when available.
    """
    contracts: list[OptionContract] = []
    exp = date.today() + timedelta(days=dte)  # actual expiration date (0DTE = today)
    actual_dte = max(dte, 1)  # min 1 day for pricing math to avoid div-by-zero
    iv_base = max(0.15, vix / 100 + 0.05)

    strike_step = 5.0 if current_price > 100 else 2.5 if current_price > 50 else 1.0
    low_strike = math.floor((current_price * 0.85) / strike_step) * strike_step
    high_strike = math.ceil((current_price * 1.15) / strike_step) * strike_step

    strike = low_strike
    while strike <= high_strike:
        for opt_type in ("call", "put"):
            moneyness = (strike - current_price) / current_price
            iv = iv_base + abs(moneyness) * 0.1  # simple smile

            T = actual_dte / 365
            d1 = (math.log(current_price / strike) + (0.04 + 0.5 * iv**2) * T) / (
                iv * math.sqrt(T)
            )
            from scipy.stats import norm

            nd1 = norm.cdf(d1)
            delta = nd1 if opt_type == "call" else nd1 - 1
            gamma = norm.pdf(d1) / (current_price * iv * math.sqrt(T))
            theta = -(current_price * norm.pdf(d1) * iv) / (2 * math.sqrt(T)) / 365
            vega_val = current_price * norm.pdf(d1) * math.sqrt(T) / 100

            if opt_type == "call":
                intrinsic = max(0, current_price - strike)
            else:
                intrinsic = max(0, strike - current_price)
            time_val = max(0.05, iv * current_price * math.sqrt(T) * 0.4)
            mid = round(intrinsic + time_val, 2)
            bid = round(mid * 0.95, 2)
            ask = round(mid * 1.05, 2)

            sym = f"{symbol}{exp.strftime('%y%m%d')}{'C' if opt_type == 'call' else 'P'}{int(strike * 1000):08d}"
            contracts.append(
                OptionContract(
                    symbol=sym,
                    underlying=symbol,
                    strike=strike,
                    expiration=exp,
                    option_type=opt_type,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    last=mid,
                    volume=int(np.random.randint(100, 5000)),
                    open_interest=int(np.random.randint(500, 20000)),
                    greeks=Greeks(
                        delta=round(delta, 4),
                        gamma=round(gamma, 4),
                        theta=round(theta, 4),
                        vega=round(vega_val, 4),
                        implied_volatility=round(iv, 4),
                    ),
                )
            )
        strike += strike_step

    return contracts


def fetch_live_chain(
    symbol: str, current_price: float, dte: int = 0
) -> list[OptionContract] | None:
    """Fetch real options chain from Yahoo Finance via yfinance.
    
    No login required — uses free public market data.
    Returns None if no data available — caller should fall back to
    generate_simulated_chain().
    """
    try:
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            return None

        target_date = date.today() + timedelta(days=dte)
        target_str = target_date.strftime("%Y-%m-%d")

        # Find the closest expiration to the target DTE
        best_exp = min(expirations, key=lambda e: abs((datetime.strptime(e, "%Y-%m-%d").date() - target_date).days))
        exp_date = datetime.strptime(best_exp, "%Y-%m-%d").date()

        chain_data = ticker.option_chain(best_exp)
        contracts: list[OptionContract] = []

        for opt_type, df in [("call", chain_data.calls), ("put", chain_data.puts)]:
            for _, row in df.iterrows():
                strike = float(row.get("strike", 0))
                if strike == 0:
                    continue
                # Only include strikes within ~15% of current price
                if abs(strike - current_price) / current_price > 0.15:
                    continue

                bid = float(row.get("bid", 0) or 0)
                ask = float(row.get("ask", 0) or 0)
                last = float(row.get("lastPrice", 0) or 0)
                # Guard against NaN in all numeric fields
                if math.isnan(bid): bid = 0.0
                if math.isnan(ask): ask = 0.0
                if math.isnan(last): last = 0.0
                mid = round((bid + ask) / 2, 2) if (bid + ask) > 0 else last
                iv = float(row.get("impliedVolatility", 0) or 0)
                if math.isnan(iv): iv = 0.0
                vol_raw = row.get("volume", 0)
                vol = int(vol_raw) if vol_raw is not None and not (isinstance(vol_raw, float) and math.isnan(vol_raw)) else 0
                oi_raw = row.get("openInterest", 0)
                oi = int(oi_raw) if oi_raw is not None and not (isinstance(oi_raw, float) and math.isnan(oi_raw)) else 0
                contract_sym = str(row.get("contractSymbol", ""))

                # yfinance doesn't provide greeks directly — estimate delta from IV
                # Simple Black-Scholes delta approximation
                T = max((exp_date - date.today()).days, 1) / 365
                try:
                    from scipy.stats import norm
                    d1 = (math.log(current_price / strike) + (0.04 + 0.5 * iv**2) * T) / (iv * math.sqrt(T)) if iv > 0 and T > 0 else 0
                    delta = round(norm.cdf(d1) if opt_type == "call" else norm.cdf(d1) - 1, 4)
                    gamma = round(norm.pdf(d1) / (current_price * iv * math.sqrt(T)), 4) if iv > 0 else 0.0
                    theta = round(-(current_price * norm.pdf(d1) * iv) / (2 * math.sqrt(T)) / 365, 4) if iv > 0 else 0.0
                    vega_val = round(current_price * norm.pdf(d1) * math.sqrt(T) / 100, 4) if iv > 0 else 0.0
                except Exception:
                    delta, gamma, theta, vega_val = 0.5 if opt_type == "call" else -0.5, 0.0, 0.0, 0.0

                contracts.append(
                    OptionContract(
                        symbol=contract_sym or f"{symbol}{exp_date.strftime('%y%m%d')}{'C' if opt_type == 'call' else 'P'}{int(strike * 1000):08d}",
                        underlying=symbol,
                        strike=strike,
                        expiration=exp_date,
                        option_type=opt_type,
                        bid=bid,
                        ask=ask,
                        mid=mid,
                        last=last,
                        volume=vol,
                        open_interest=oi,
                        greeks=Greeks(
                            delta=delta,
                            gamma=gamma,
                            theta=theta,
                            vega=vega_val,
                            implied_volatility=round(iv, 4),
                        ),
                    )
                )

        if contracts:
            return contracts
        return None

    except Exception as exc:
        import logging as _log
        _log.warning("Live yfinance chain fetch failed: %s — falling back to synthetic", exc)
        return None


def build_pnl_chart(order: OptionOrder, current_price: float) -> go.Figure:
    """Build a P&L at expiration chart for the simulated order."""
    low = current_price * 0.80
    high = current_price * 1.20
    prices = np.linspace(low, high, 200)
    pnl = np.zeros_like(prices)

    for leg in order.legs:
        for i, px in enumerate(prices):
            if leg.option_type == "call":
                intrinsic = max(0, px - leg.strike)
            else:
                intrinsic = max(0, leg.strike - px)

            if "sell" in leg.action:
                pnl[i] += (order.limit_price - intrinsic) * 100 * leg.quantity if len(order.legs) == 1 else 0
            else:
                pnl[i] += (intrinsic - order.limit_price) * 100 * leg.quantity if len(order.legs) == 1 else 0

    # For multi-leg, compute properly
    if len(order.legs) > 1:
        pnl = np.zeros_like(prices)
        for leg in order.legs:
            for i, px in enumerate(prices):
                if leg.option_type == "call":
                    intrinsic = max(0, px - leg.strike)
                else:
                    intrinsic = max(0, leg.strike - px)
                sign = -1 if "sell" in leg.action else 1
                pnl[i] += sign * intrinsic * 100 * leg.quantity
        # Add/subtract net premium
        if order.limit_price:
            net_credit = order.limit_price * 100
            if any("sell" in l.action for l in order.legs):
                pnl += net_credit
            else:
                pnl -= net_credit

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=prices,
            y=pnl,
            fill="tozeroy",
            fillcolor="rgba(107, 191, 122, 0.10)",
            line=dict(color="#6BBF7A", width=2),
            name="P&L",
        )
    )
    fig.add_hline(y=0, line_dash="dash", line_color="#3A3A3C")
    fig.add_vline(x=current_price, line_dash="dot", line_color="#C9A96E", annotation_text="Current")

    for leg in order.legs:
        fig.add_vline(
            x=leg.strike,
            line_dash="dot",
            line_color="#E06C6C" if "sell" in leg.action else "#6BBF7A",
            annotation_text=f"{'S' if 'sell' in leg.action else 'B'} {leg.strike}",
        )

    fig.update_layout(
        title=dict(text="P&L at Expiration", font=dict(family="Inter", size=16, color="#F5F5F7")),
        xaxis_title="Stock Price at Expiration ($)",
        yaxis_title="Profit / Loss ($)",
        template="plotly_dark",
        paper_bgcolor="#1C1C1E",
        plot_bgcolor="#2C2C2E",
        font=dict(family="Inter", color="#A1A1A6"),
        height=400,
        margin=dict(l=40, r=40, t=50, b=40),
        xaxis=dict(gridcolor="#3A3A3C", zerolinecolor="#3A3A3C"),
        yaxis=dict(gridcolor="#3A3A3C", zerolinecolor="#3A3A3C"),
    )
    return fig


def build_price_chart(df: pd.DataFrame, indicators: dict) -> go.Figure:
    """Candlestick chart with Bollinger Bands and SMAs."""
    fig = go.Figure()

    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="Price",
            increasing_line_color="#6BBF7A",
            decreasing_line_color="#E06C6C",
        )
    )

    # SMAs
    close = df["Close"].astype(float)
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()

    fig.add_trace(go.Scatter(x=df.index, y=sma20, name="SMA 20", line=dict(color="#C9A96E", width=1)))
    fig.add_trace(go.Scatter(x=df.index, y=sma50, name="SMA 50", line=dict(color="#7AB4D9", width=1)))

    # Bollinger
    bb_mid = sma20
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    fig.add_trace(
        go.Scatter(x=df.index, y=bb_upper, name="BB Upper", line=dict(color="rgba(99,99,102,0.4)", dash="dash", width=1))
    )
    fig.add_trace(
        go.Scatter(x=df.index, y=bb_lower, name="BB Lower", line=dict(color="rgba(99,99,102,0.4)", dash="dash", width=1),
                   fill="tonexty", fillcolor="rgba(99,99,102,0.04)")
    )

    fig.update_layout(
        title=dict(text=f"{indicators.get('symbol', '')} — Price & Indicators", font=dict(family="Inter", size=16, color="#F5F5F7")),
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        paper_bgcolor="#1C1C1E",
        plot_bgcolor="#2C2C2E",
        font=dict(family="Inter", color="#A1A1A6"),
        height=450,
        margin=dict(l=40, r=40, t=50, b=40),
        xaxis=dict(gridcolor="#3A3A3C", zerolinecolor="#3A3A3C"),
        yaxis=dict(gridcolor="#3A3A3C", zerolinecolor="#3A3A3C"),
    )
    return fig


# ═════════════════════════════════════════════════════════════���════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════

st.sidebar.markdown(
    '<div style="text-align:center; padding: 12px 0 4px;">'
    '<span style="font-size: 28px; font-weight: 700; letter-spacing: -0.03em; color: #F5F5F7;">Options</span>'
    '<span style="font-size: 28px; font-weight: 300; letter-spacing: -0.03em; color: #C9A96E;"> Agent</span>'
    '</div>',
    unsafe_allow_html=True,
)
st.sidebar.markdown('<div style="text-align:center; font-size:11px; color:#A1A1A6; letter-spacing:0.08em; text-transform:uppercase; margin-bottom:16px;">Simulation Dashboard</div>', unsafe_allow_html=True)
st.sidebar.markdown("---")

# ── Data Mode toggle ──
mock_mode = st.sidebar.toggle(
    "🧪 Mock Mode (offline)",
    value=False,
    help="Use bundled sample data instead of live Yahoo Finance. No internet needed.",
)
if mock_mode:
    st.sidebar.caption("📦 Using mock data — AAPL, SPY, TSLA, MSFT available")

symbol = st.sidebar.text_input("Stock Symbol", value="SPY", max_chars=10).upper().strip()

st.sidebar.markdown("### Analysis Timeframe")
timeframe = st.sidebar.select_slider(
    "Timeframe",
    options=["intraday", "daily", "weekly"],
    value="intraday",
    format_func=lambda x: {
        "intraday": "⚡ Intraday (5-min)",
        "daily": "📅 Daily (Swing)",
        "weekly": "📆 Weekly (Position)",
    }[x],
)
TIMEFRAME_DESCRIPTIONS = {
    "intraday": "Scalping / day-trading entries — 5-min signals, daily regime",
    "daily": "Swing / position entries — standard for 14–60 DTE options",
    "weekly": "Long-term position entries — ideal for 60–365 DTE LEAPS",
}
st.sidebar.caption(f"📊 {TIMEFRAME_DESCRIPTIONS[timeframe]}")

# Hardcoded portfolio defaults (not shown in UI — only used internally for eligibility)
portfolio_value = 100_000
cash = 50_000
shares = 0
avg_cost = 0.01

st.sidebar.markdown("### Risk Parameters")
max_pos_pct = st.sidebar.slider("Max Position Size (%)", 1, 20, 5) / 100
max_loss_pct = st.sidebar.slider("Max Loss per Trade (%)", 1, 10, 2) / 100
min_dte = st.sidebar.slider("Min Days to Expiration", 0, 365, 0)
max_dte = st.sidebar.slider("Max Days to Expiration", 0, 365, 0)

if max_dte < min_dte:
    st.sidebar.warning("⚠️ Max DTE must be ≥ Min DTE. Using Min DTE as Max.")
    max_dte = min_dte

st.sidebar.markdown("### Choppiness Filter")
max_chop_score = st.sidebar.slider(
    "Max Chop Score",
    min_value=0, max_value=10, value=5,
    help=(
        "Filter out sweet spot triggers on choppy days. "
        "Lower = stricter (fewer but higher-conviction trades). "
        "**Golden default: 5** — matches the live agent and 1-yr backtest "
        "(PF 1.81, Sharpe 1.98). Loosen to 7 for moderate, 10 to disable."
    ),
)
st.sidebar.caption(
    f"{'🌊 Strict — highest conviction only' if max_chop_score <= 5 else '⚠️ Moderate — balanced' if max_chop_score <= 7 else '📊 Relaxed — more opportunities' if max_chop_score <= 9 else '🔓 Disabled'}"
)

st.sidebar.markdown("### Strategy Selection")
strategy_mode = st.sidebar.radio(
    "Strategy mode",
    options=["auto", "manual"],
    format_func=lambda x: "🤖 Auto (regime-based)" if x == "auto" else "🎛️ Manual (choose your own)",
    horizontal=True,
)

manual_strategy = None
if strategy_mode == "manual":
    manual_strategy = st.sidebar.selectbox(
        "Select strategy",
        options=["buy_call", "buy_put"],
        format_func=lambda x: f"{STRATEGY_DESCRIPTIONS[x]['icon']}  {STRATEGY_DESCRIPTIONS[x]['name']}",
    )

st.sidebar.markdown("### Time Replay")
_time_options = ["Now"] + [f"{h}:{m:02d}" for h in range(9, 16) for m in (30, 45, 0, 15) if (h > 9 or m >= 30) and (h < 16 or m == 0)]
# Clean up: generate proper 15-min slots 9:30 → 16:00
_time_options = ["Now"]
for _h in range(9, 16):
    for _m in [0, 15, 30, 45]:
        if _h == 9 and _m < 30:
            continue
        _time_options.append(f"{_h}:{_m:02d}")
_time_options.append("16:00")

replay_time = st.sidebar.select_slider(
    "View recommendation as-of",
    options=_time_options,
    value="Now",
    help="Slide to replay what the signals would have looked like at a specific time today. 'Now' uses the latest data.",
)
if replay_time != "Now":
    st.sidebar.caption(f"⏪ Replaying signals as of **{replay_time} ET**")
else:
    st.sidebar.caption("🔴 Live — using latest available data")

st.sidebar.markdown("---")
analyze_btn = st.sidebar.button("Analyze & Simulate", type="primary", use_container_width=True)

# ── Auto-refresh for intraday monitoring ─────────────────────────
st.sidebar.markdown("### Auto-Monitor")
_auto_refresh_enabled = st.sidebar.toggle(
    "Enable auto-refresh",
    value=False,
    help="Automatically re-analyze at a regular interval. Works best in Live mode (Mock off, Replay = Now).",
)

_refresh_interval_min = st.sidebar.select_slider(
    "Refresh interval",
    options=[1, 2, 3, 5, 10, 15, 30],
    value=5,
    format_func=lambda x: f"{x} min",
    disabled=not _auto_refresh_enabled,
)

_auto_refresh_active = _auto_refresh_enabled and not mock_mode and replay_time == "Now"

if _auto_refresh_active:
    _refresh_count = st_autorefresh(
        interval=_refresh_interval_min * 60 * 1000, limit=None, key="intraday_monitor",
    )
    st.sidebar.markdown(
        f"**Status:** 🟢 Active — refreshing every **{_refresh_interval_min} min**  \n"
        "🔔 Browser alerts enabled for high-quality signals."
    )
    # On auto-refresh ticks (count > 0), auto-analyze without clicking
    if _refresh_count and _refresh_count > 0 and "result" in st.session_state:
        # Clear data cache so we get fresh candle data
        fetch_indicators.clear()
        analyze_btn = True  # force re-analyze on refresh
elif _auto_refresh_enabled:
    st.sidebar.caption("⚠️ Auto-monitor requires **Live mode** (disable Mock, set replay to Now)")

# Request notification permission early when auto-monitor is on
if _auto_refresh_active:
    request_notification_permission()


# ══════════════════════════════════════════════════════════════════
#  MAIN PAGE
# ══════════════════════════════════════════════════════════════════

st.markdown(
    '<h1 style="font-weight: 700; letter-spacing: -0.03em; margin-bottom: 0;">'
    'Options Trading Agent</h1>',
    unsafe_allow_html=True,
)
st.caption("Analyze market conditions · View strategy recommendations · Simulate trades")

if not analyze_btn and "result" not in st.session_state:
    st.info("👈 Enter a symbol and click **Analyze & Simulate** to get started.")
    st.stop()

# ── Run analysis ─────────────────────────────────────────────────

if analyze_btn:
    with st.spinner(f"{'Loading mock data' if mock_mode else 'Analyzing'} {symbol} ({timeframe})…"):
        try:
            if mock_mode:
                raw = _mock_fetch(symbol, timeframe)
                hist = _mock_price_history(symbol)
            elif replay_time != "Now":
                raw = fetch_indicators_at_time(symbol, replay_time, timeframe)
                hist = fetch_price_history(symbol)
            else:
                raw = fetch_indicators(symbol, timeframe)
                hist = fetch_price_history(symbol)
            st.session_state["result"] = raw
            st.session_state["hist"] = hist
            st.session_state["symbol"] = symbol
            st.session_state["replay_time"] = replay_time
            st.session_state["portfolio_cfg"] = {
                "portfolio_value": portfolio_value,
                "cash": cash,
                "shares": shares,
                "avg_cost": avg_cost,
            }
            st.session_state["risk_cfg"] = {
                "max_pos_pct": max_pos_pct,
                "max_loss_pct": max_loss_pct,
                "min_dte": min_dte,
                "max_dte": max_dte,
            }
            st.session_state["strategy_mode"] = strategy_mode
            st.session_state["manual_strategy"] = manual_strategy
            st.session_state["timeframe"] = timeframe
        except Exception as exc:
            st.error(f"Error fetching data for **{symbol}**: {exc}")
            st.stop()

# ── Retrieve from session state ──────────────────────────────────

raw = st.session_state.get("result")
hist = st.session_state.get("hist")
sym = st.session_state.get("symbol", symbol)
pcfg = st.session_state.get("portfolio_cfg", {})
rcfg = st.session_state.get("risk_cfg", {})
saved_strategy_mode = st.session_state.get("strategy_mode", "auto")
saved_manual_strategy = st.session_state.get("manual_strategy", None)
saved_timeframe = st.session_state.get("timeframe", "daily")
saved_replay_time = st.session_state.get("replay_time", "Now")

if raw is None:
    st.stop()

if saved_replay_time != "Now":
    st.warning(
        f"⏪ **TIME REPLAY MODE** — Showing signals as of **{saved_replay_time} ET** today. "
        f"This is NOT live data. Slide back to **Now** for real-time analysis."
    )

ind_dict = raw["indicators"]
regime = MarketRegime(raw["regime"])
regime_label, regime_icon = REGIME_LABELS[regime]
regime_strategy = REGIME_STRATEGY_MAP[regime]

# ── Smart auto-selection: quality scores + opening range breakout ──
# Uses both daily indicators (6 signals) AND the 60-min opening range
# breakout direction to pick the best strategy.
#
# In replay mode, pass the pre-fetched time-sliced 5-min bars directly
# to the OpeningRangeAnalyzer so it computes the REAL opening range from
# actual candle data (not synthesized from daily indicators).

_is_replay = saved_replay_time != "Now"
_analysis_mock = True if _is_replay else mock_mode
_replay_bars = raw.get("bars_5m") if _is_replay else None

indicators_for_or = MarketIndicators(**ind_dict)
_ora = OpeningRangeAnalyzer()
_or_result = _ora.analyze(indicators_for_or, mock=_analysis_mock, bars_5m=_replay_bars)
_or_direction = _or_result.breakout_direction.value  # "bullish", "bearish", "neutral"
_or_momentum = _or_result.momentum_score  # -100 to +100
_or_confirmed = _or_result.breakout_confirmed  # |momentum| >= 40

# Recent 30-min momentum analysis
_rma = RecentMomentumAnalyzer()
_recent = _rma.analyze(indicators_for_or, mock=_analysis_mock, bars_5m=_replay_bars)
_recent_dir = _recent.direction  # "bullish", "bearish", "neutral"
_recent_momentum = _recent.momentum_score

# Choppiness analysis (from 5-min bars when available)
if _replay_bars is not None and not _replay_bars.empty:
    _chop_result = compute_choppiness(_replay_bars)
else:
    # Try to get bars from live yfinance data for choppiness
    try:
        import yfinance as _yf_chop
        _chop_df = _yf_chop.download(ind_dict["symbol"], period="5d", interval="5m", progress=False)
        if not _chop_df.empty:
            if isinstance(_chop_df.columns, pd.MultiIndex):
                _chop_df.columns = _chop_df.columns.get_level_values(0)
            _chop_df.index = pd.to_datetime(_chop_df.index)
            if _chop_df.index.tz is None:
                _chop_df.index = _chop_df.index.tz_localize("UTC")
            _chop_df.index = _chop_df.index.tz_convert("US/Eastern")
            from datetime import date as _dt_date
            _today_chop = _chop_df[_chop_df.index.date == _dt_date.today()]
            if _today_chop.empty:
                _today_chop = _chop_df[_chop_df.index.date == _chop_df.index[-1].date()]
            _chop_result = compute_choppiness(_today_chop)
        else:
            _chop_result = None
    except Exception:
        _chop_result = None

def _compute_quality_score(ind: dict, direction: str, or_direction: str, or_momentum: int,
                           or_confirmed: bool, recent_dir: str, recent_momentum: int) -> int:
    """Compute quality score (0-11) — delegates to shared quality_scorer."""
    return compute_quality_score(
        direction=direction,
        current_price=ind["current_price"],
        sma_20=ind["sma_20"],
        sma_50=ind["sma_50"],
        vix=ind["vix"],
        volume=ind.get("volume", 0),
        volume_sma_20=ind.get("volume_sma_20", 0),
        or_direction=or_direction,
        or_momentum=or_momentum,
        or_confirmed=or_confirmed,
        recent_dir=recent_dir,
        recent_momentum=recent_momentum,
        zlema_trend=ind.get("zlema_trend"),
        vpvr_level_broken=ind.get("vpvr_level_broken", False),
    ).score

# ── Compute ZLEMA trend for quality scoring ──
# If indicators already have zlema_trend (live mode), use it; otherwise compute from replay bars
if not ind_dict.get("zlema_trend") and _replay_bars is not None and len(_replay_bars) >= 21:
    _close_for_zlema = _replay_bars["Close"].astype(float)
    _lag_f = (8 - 1) // 2
    _lag_s = (21 - 1) // 2
    _comp_f = 2 * _close_for_zlema - _close_for_zlema.shift(_lag_f)
    _comp_s = 2 * _close_for_zlema - _close_for_zlema.shift(_lag_s)
    _zf = float(_comp_f.ewm(span=8, adjust=False).mean().iloc[-1])
    _zs = float(_comp_s.ewm(span=21, adjust=False).mean().iloc[-1])
    if _zf > _zs * 1.0002:
        ind_dict["zlema_trend"] = "bullish"
    elif _zf < _zs * 0.9998:
        ind_dict["zlema_trend"] = "bearish"
    else:
        ind_dict["zlema_trend"] = "neutral"

# ── Compute VPVR level break for quality scoring ──
if _replay_bars is not None and len(_replay_bars) >= 12:
    _vpvr_detector = MomentumCascadeDetector()
    _vpvr_bars = _replay_bars.iloc[:-6]
    _vpvr_atr = ind_dict.get("atr_14", 1.0)
    _vpvr_levels = _vpvr_detector._find_sr_levels(_vpvr_bars, _vpvr_atr)
    _vpvr_price = ind_dict["current_price"]
    _vpvr_broken = any(
        _vpvr_price > lvl + _vpvr_atr * 0.02 or _vpvr_price < lvl - _vpvr_atr * 0.02
        for lvl in _vpvr_levels
    )
    ind_dict["vpvr_level_broken"] = _vpvr_broken

call_quality = _compute_quality_score(ind_dict, "buy_call", _or_direction, _or_momentum, _or_confirmed, _recent_dir, _recent_momentum)
put_quality = _compute_quality_score(ind_dict, "buy_put", _or_direction, _or_momentum, _or_confirmed, _recent_dir, _recent_momentum)

# ── Momentum Cascade Detection (explosive move potential) ──
_cascade_detector = MomentumCascadeDetector()
_best_quality = max(call_quality, put_quality)
_best_dir_momentum = _recent_momentum  # use recent momentum for direction context
_cascade = _cascade_detector.analyze(
    indicators_for_or,
    quality_score=_best_quality,
    or_momentum=_or_momentum,
    recent_momentum=_recent_momentum,
    bars_5m=_replay_bars,
)

# ── Browser notification for high-quality signals (15-min auto-monitor) ──
if _auto_refresh_active:
    _notify_quality = max(call_quality, put_quality)
    _notify_direction = "Buy Put" if put_quality > call_quality else "Buy Call"
    _notify_price = ind_dict.get("current_price", 0)
    _notify_symbol = ind_dict.get("symbol", "")

    # Track last notification to avoid repeating the same alert
    _prev_alert = st.session_state.get("_last_alert_key", "")
    _alert_key = f"{_notify_symbol}_{_notify_quality}_{_cascade.explosion_score}_{datetime.now(timezone.utc).strftime('%H%M')}"

    if _alert_key != _prev_alert:
        if 4 <= _notify_quality <= 8 and _cascade.explosion_score >= 3:
            # Check eligibility (regime guard) before notifying
            _sweet_strategy_key = "buy_put" if put_quality > call_quality else "buy_call"
            _sweet_rsi = ind_dict.get("rsi_5min") or ind_dict.get("rsi_14", 50)
            # Inline regime check (same logic as strategy eligibility)
            if _sweet_strategy_key == "buy_put":
                _sweet_eligible = not (regime == MarketRegime.LOW_VOL_BULLISH and _sweet_rsi <= 70)
                _sweet_eligible = _sweet_eligible and _sweet_rsi >= 20
            else:
                _sweet_eligible = not (regime in (MarketRegime.TRENDING_BEARISH, MarketRegime.HIGH_VOL_BEARISH) and _sweet_rsi >= 30)
                _sweet_eligible = _sweet_eligible and _sweet_rsi <= 80
            if _sweet_eligible:
                send_browser_notification(
                    title=f"🎯 {_notify_symbol} SWEET SPOT — {_notify_direction}",
                    body=(
                        f"Quality {_notify_quality}/13 (optimal) + Explosion {_cascade.explosion_score}/10 | "
                        f"${_notify_price:.2f} | Best R:R entry zone"
                    ),
                    urgency="critical" if _cascade.explosion_score >= 7 else "normal",
                )
                st.session_state["_last_alert_key"] = _alert_key

# Pick the strategy with higher quality; tie goes to regime-based pick
if call_quality > put_quality:
    auto_strategy_name = "buy_call"
elif put_quality > call_quality:
    auto_strategy_name = "buy_put"
else:
    auto_strategy_name = regime_strategy  # tie → follow regime

# Determine selected strategy: manual override or auto (quality-based)
if saved_strategy_mode == "manual" and saved_manual_strategy:
    selected_strategy_name = saved_manual_strategy
else:
    selected_strategy_name = auto_strategy_name

strat_info = STRATEGY_DESCRIPTIONS[selected_strategy_name]

# Reconstruct objects
indicators = MarketIndicators(**ind_dict)
current_price = indicators.current_price
portfolio = build_simulated_portfolio(
    sym,
    pcfg.get("shares", shares),
    pcfg.get("avg_cost", avg_cost),
    current_price,
    pcfg.get("portfolio_value", portfolio_value),
    pcfg.get("cash", cash),
)


# ══════════════════════════════════════════════════════════════════
#  SECTION 1: Market Overview
# ══════════════════════════════════════════════════════════════════

st.markdown("## Market Overview")

_tf_labels = {"intraday": "⚡ Intraday", "15min": "⚡ Intraday", "1hour": "⚡ Intraday", "daily": "📅 Daily", "weekly": "📆 Weekly"}
_tf_desc = "Regime from daily bars · Trade signals from 5-min bars" if saved_timeframe in ("intraday", "15min", "1hour") else f"All indicators from {saved_timeframe} bars"
st.caption(f"Timeframe: **{_tf_labels.get(saved_timeframe, saved_timeframe)}** — {_tf_desc}")

cols = st.columns(6)
cols[0].metric("Price", f"${current_price:.2f}")
cols[1].metric("VIX", f"{indicators.vix:.1f}", delta=f"{'High' if indicators.vix > 25 else 'Low'}", delta_color="inverse")
_display_rsi = indicators.rsi_5min if indicators.rsi_5min is not None else indicators.rsi_14
_rsi_label = "RSI (5m)" if indicators.rsi_5min is not None else "RSI (14)"
cols[2].metric(_rsi_label, f"{_display_rsi:.1f}", delta="Overbought" if _display_rsi > 70 else ("Oversold" if _display_rsi < 30 else "Neutral"), delta_color="off")
cols[3].metric("SMA 50", f"${indicators.sma_50:.2f}")
cols[4].metric("MACD Hist", f"{indicators.macd_histogram:.3f}", delta="Bullish" if indicators.macd_histogram > 0 else "Bearish", delta_color="normal")
cols[5].metric("ATR (14)", f"${indicators.atr_14:.2f}")

# Price chart
if hist is not None and not hist.empty:
    st.plotly_chart(build_price_chart(hist, ind_dict), use_container_width=True)

# Detailed indicator table
with st.expander("📊 All Technical Indicators", expanded=False):
    indicator_data = {
        "Indicator": [
            "Current Price", "VIX", "RSI (14)",
            "SMA 20", "SMA 50", "SMA 200",
            "Bollinger Upper", "Bollinger Middle", "Bollinger Lower",
            "MACD", "MACD Signal", "MACD Histogram",
            "ATR (14)", "Next Earnings", "Days to Earnings",
        ],
        "Value": [
            f"${current_price:.2f}",
            f"{indicators.vix:.2f}",
            f"{indicators.rsi_14:.2f}",
            f"${indicators.sma_20:.2f}",
            f"${indicators.sma_50:.2f}",
            f"${indicators.sma_200:.2f}",
            f"${indicators.bb_upper:.2f}",
            f"${indicators.bb_middle:.2f}",
            f"${indicators.bb_lower:.2f}",
            f"{indicators.macd:.4f}",
            f"{indicators.macd_signal:.4f}",
            f"{indicators.macd_histogram:.4f}",
            f"${indicators.atr_14:.2f}",
            str(indicators.next_earnings_date or "N/A"),
            str(indicators.days_to_earnings or "N/A"),
        ],
    }
    st.dataframe(pd.DataFrame(indicator_data), hide_index=True, use_container_width=True)

# ══════════════════════════════════════════════════════════════════
#  SECTION 3: Strategy Recommendation
# ══════════════════════════════════════════════════════════════════

st.markdown("## Strategy Selection")

if saved_strategy_mode == "manual":
    st.info(
        f"🎛️ **Manual mode** — You selected **{STRATEGY_DESCRIPTIONS[selected_strategy_name]['icon']} "
        f"{STRATEGY_DESCRIPTIONS[selected_strategy_name]['name']}**.  "
        f"(Auto pick: {STRATEGY_DESCRIPTIONS[auto_strategy_name]['icon']} "
        f"{STRATEGY_DESCRIPTIONS[auto_strategy_name]['name']} — "
        f"Call quality {call_quality}/11, Put quality {put_quality}/11)"
    )
else:
    call_label = "🟢" if call_quality >= 6 else "🔵" if call_quality >= 3 else "🟡"
    put_label = "🟢" if put_quality >= 6 else "🔵" if put_quality >= 3 else "🟡"
    or_emoji = "🟢" if _or_direction == "bullish" else "🔴" if _or_direction == "bearish" else "⚪"
    rc_emoji = "🟢" if _recent_dir == "bullish" else "🔴" if _recent_dir == "bearish" else "⚪"
    _chop_emoji = "🌊" if _chop_result and _chop_result.chop_score > max_chop_score else "✅" if _chop_result and _chop_result.chop_score <= 4 else "⚠️" if _chop_result else ""
    _chop_text = f"  \n{_chop_emoji} Choppiness: **{_chop_result.chop_score}/10**" if _chop_result else ""
    st.info(
        f"🤖 **Auto mode** — Selected **{STRATEGY_DESCRIPTIONS[selected_strategy_name]['icon']} "
        f"{STRATEGY_DESCRIPTIONS[selected_strategy_name]['name']}** based on signal quality.  \n"
        f"🚀 Buy Call: {call_label} **{call_quality}/11** &nbsp;|&nbsp; "
        f"💥 Buy Put: {put_label} **{put_quality}/11**  \n"
        f"{or_emoji} Opening-range anchored signal: **{_or_direction.upper()}** ({_or_momentum:+d}) &nbsp;|&nbsp; "
        f"{rc_emoji} Recent 30-min: **{_recent_dir.upper()}** ({_recent_momentum:+d})"
        f"{_chop_text}"
    )

# ── Momentum Cascade Alert (always visible) ──
_cascade_color = "#E06C6C" if _cascade.explosion_score >= 7 else "#C9A96E" if _cascade.explosion_score >= 4 else "#636366"
_cascade_border = "2px solid" if _cascade.explosion_score >= 7 else "1px solid"
_cascade_bg = "#2C2C2E"
_otm_html = f'<div style="font-size: 12px; color: #C9A96E; margin-top: 8px;">Consider <b>{_cascade.recommended_strike_offset} strike(s) OTM</b> for higher leverage</div>' if _cascade.recommended_strike_offset > 0 else ''
_cascade_flags = (
    f"{'Momentum accelerating · ' if _cascade.acceleration_detected else ''}"
    f"{'Volume climax · ' if _cascade.volume_climax else ''}"
    f"{'Multi-level cascade · ' if _cascade.cascade_breakdown else ''}"
)
_cascade_flags = _cascade_flags.rstrip(' · ')
if not _cascade_flags.strip():
    _cascade_flags = "No explosive signals — normal conditions"
_data_badge = f' <span style="font-size: 10px; color: #636366; margin-left: 8px;">({_cascade.data_source})</span>'
st.markdown(
f"""<div style="background: #2C2C2E; border: 1px solid #3A3A3C; border-radius: 12px; padding: 24px; box-shadow: 0 2px 12px rgba(0,0,0,0.25); border-color: {_cascade_color};">
<div style="display: flex; justify-content: space-between; align-items: baseline;">
<div style="font-size: 15px; font-weight: 700; color: {_cascade_color}; letter-spacing: -0.01em;">
{_cascade.urgency} Explosion Potential{_data_badge}
</div>
<div style="font-size: 24px; font-weight: 800; color: {_cascade_color};">{_cascade.explosion_score}/10</div>
</div>
<div style="font-size: 13px; color: #A1A1A6; margin-top: 8px;">{_cascade_flags}</div>
{_otm_html}
</div>""",
    unsafe_allow_html=True,
)
with st.expander("🔥 Cascade Signal Details", expanded=_cascade.explosion_score >= 7):
    for sig in _cascade.signals:
        if sig["score"] > 0:
            st.markdown(f"✅ **{sig['name']}** (+{sig['score']}) — {sig['desc']}")
        else:
            st.markdown(f"⚪ {sig['name']} — {sig['desc']}")

# ── Sweet Spot Indicator ──
# Backtest-validated: Q 4–7 has the best risk/reward (not too early, not too late)
# Combined with cascade ≥ 4 → highest probability explosive entries
_best_q = max(call_quality, put_quality)
_sweet_spot_quality = 4 <= _best_q <= 7
_sweet_spot_cascade = _cascade.explosion_score >= 4
_chop_ok = _chop_result is None or _chop_result.chop_score <= max_chop_score
_sweet_spot_active = _sweet_spot_quality and _sweet_spot_cascade and _chop_ok
_sweet_spot_prime = _sweet_spot_quality and _cascade.explosion_score >= 7 and _chop_ok
_chop_blocked = _sweet_spot_quality and _sweet_spot_cascade and not _chop_ok

if _sweet_spot_prime:
    _ss_color = "#6BBF7A"
    _ss_border = "2px solid"
    _ss_bg = "#2C2C2E"
    _ss_icon = "◆"
    _ss_label = "PRIME SWEET SPOT"
    _chop_badge = f" · Chop {_chop_result.chop_score}/10" if _chop_result else ""
    _ss_desc = (
        f"Quality {_best_q}/13 (optimal) + Explosion {_cascade.explosion_score}/10{_chop_badge} — "
        f"<b>Highest probability entry.</b> Catching the move early with explosive momentum confirming."
    )
elif _sweet_spot_active:
    _ss_color = "#C9A96E"
    _ss_border = "1px solid"
    _ss_bg = "#2C2C2E"
    _ss_icon = "◆"
    _ss_label = "SWEET SPOT"
    _chop_badge = f" · Chop {_chop_result.chop_score}/10" if _chop_result else ""
    _ss_desc = (
        f"Quality {_best_q}/13 (optimal) + Explosion {_cascade.explosion_score}/10{_chop_badge} — "
        f"Good entry zone. Monitor for cascade acceleration."
    )
elif _chop_blocked:
    _ss_color = "#D4A74A"
    _ss_border = "1px dashed"
    _ss_bg = "#2C2C2E"
    _ss_icon = "⊘"
    _ss_label = "SWEET SPOT BLOCKED — CHOPPY"
    _ss_desc = (
        f"Quality {_best_q}/13 + Explosion {_cascade.explosion_score}/10 would trigger, "
        f"but <b>choppiness is {_chop_result.chop_score}/10</b> (max: {max_chop_score}). "
        f"Market is range-bound — wait for choppiness to decrease."
    )
elif _sweet_spot_quality:
    _ss_color = "#7AB4D9"
    _ss_border = "1px solid"
    _ss_bg = "#2C2C2E"
    _ss_icon = "◇"
    _ss_label = "QUALITY ZONE"
    _ss_desc = (
        f"Quality {_best_q}/13 is optimal (4–7). "
        f"Explosion only {_cascade.explosion_score}/10 — wait for cascade signals."
    )
else:
    _ss_color = None

if _ss_color:
    _ss_direction = "Buy Put" if put_quality > call_quality else "Buy Call"
    st.markdown(
f"""<div style="background: #2C2C2E; border: 1px solid #3A3A3C; border-radius: 12px; padding: 24px; box-shadow: 0 2px 12px rgba(0,0,0,0.25); border: {_ss_border} {_ss_color};">
<div style="display: flex; justify-content: space-between; align-items: center;">
<div>
<span style="font-size: 14px; font-weight: 700; color: {_ss_color}; letter-spacing: 0.04em;">{_ss_icon} {_ss_label}</span>
<span style="font-size: 13px; color: #A1A1A6; margin-left: 12px;">{_ss_direction}</span>
</div>
</div>
<div style="font-size: 13px; color: #A1A1A6; margin-top: 8px; line-height: 1.5;">{_ss_desc}</div>
<div style="font-size: 11px; color: #636366; margin-top: 6px;">
Backtest: Q 4–7 with cascade ≥ 4, chop ≤ 5 → 1-yr SPY: 63.6% WR, PF 1.81, Sharpe 1.98
</div>
</div>""",
        unsafe_allow_html=True,
    )

    # ── Entry / Stop / Target plan (matches live agent formulas) ──
    _rh = float(_or_result.range_high)
    _rl = float(_or_result.range_low)
    _rw = _rh - _rl
    if _rw > 0:
        if _cascade.explosion_score >= 8:
            _tmult = 1.5
        elif _cascade.explosion_score >= 6:
            _tmult = 1.25
        else:
            _tmult = 1.0
        _is_call = _ss_direction == "Buy Call"
        _stop_px = (_rh + _rl) / 2
        if _is_call:
            _entry_px = _rh + _rw * 0.10
            _risk = _entry_px - _stop_px
            _target_px = _entry_px + _risk * _tmult
            _to_entry = _entry_px - current_price
        else:
            _entry_px = _rl - _rw * 0.10
            _risk = _stop_px - _entry_px
            _target_px = _entry_px - _risk * _tmult
            _to_entry = current_price - _entry_px
        _reward = _risk * _tmult
        _to_entry_pct = (_to_entry / current_price) * 100
        _entry_status = "✅ Triggered" if _to_entry <= 0 else f"{_to_entry:+.2f} ({_to_entry_pct:+.2f}%) away"

        ec1, ec2, ec3, ec4 = st.columns(4)
        ec1.metric("Entry", f"${_entry_px:.2f}", _entry_status, delta_color="off")
        ec2.metric("Stop (range mid)", f"${_stop_px:.2f}", f"-${_risk:.2f} risk", delta_color="inverse")
        ec3.metric(f"Target ({_tmult:.2f}R)", f"${_target_px:.2f}", f"+${_reward:.2f} reward", delta_color="normal")
        ec4.metric("R:R", f"1 : {_tmult:.2f}", f"Cascade {_cascade.explosion_score}/10")

        st.caption(
            f"Plan from OR range ${_rl:.2f}–${_rh:.2f} (width ${_rw:.2f}). "
            f"Entry = range edge ± 10% buffer · Stop = midpoint · Target multiplier scales with cascade "
            f"(≥8→1.5R, ≥6→1.25R, else 1.0R). "
            f"Live agent monitors GainzAlgoV2 reversal (RSI 65/35, body 0.5) for early exit."
        )
    else:
        st.caption("⏳ Opening range still forming — entry/stop/target plan will appear once the 9:30–10:30 ET range is set.")

scol1, scol2 = st.columns(2)
for col, (sname, sinfo) in zip([scol1, scol2], STRATEGY_DESCRIPTIONS.items()):
    is_selected = sname == selected_strategy_name
    is_auto_pick = sname == auto_strategy_name
    if is_selected:
        badge = "● SELECTED"
    elif is_auto_pick and saved_strategy_mode == "manual":
        badge = "AUTO PICK"
    else:
        badge = ""
    with col:
        with st.container(border=True):
            if badge:
                c1, c2 = st.columns([3, 1])
                c1.markdown(f"**{sinfo['icon']} {sinfo['name']}**")
                c2.caption(badge)
            else:
                st.markdown(f"**{sinfo['icon']} {sinfo['name']}**")
            st.caption(sinfo['desc'])


# ══════════════════════════════════════════════════════════════════
#  SECTION 4: Simulated Order Construction
# ══════════════════════════════════════════════════════════════════

st.markdown("## Simulated Trade")

ALL_STRATEGY_KEYS = ["buy_call", "buy_put"]

# Allow user to override strategy
override_strategy = st.selectbox(
    "Strategy to simulate (change to try a different one):",
    options=ALL_STRATEGY_KEYS,
    index=ALL_STRATEGY_KEYS.index(
        selected_strategy_name if selected_strategy_name in ALL_STRATEGY_KEYS
        else "buy_call"
    ),
    format_func=lambda x: f"{STRATEGY_DESCRIPTIONS[x]['icon']}  {STRATEGY_DESCRIPTIONS[x]['name']}",
)

strategy_map = {
    "buy_call": BuyCallStrategy(),
    "buy_put": BuyPutStrategy(),
}
chosen_strategy = strategy_map[override_strategy]
chosen_regime_map = {
    "buy_call": MarketRegime.LOW_VOL_BULLISH,
    "buy_put": MarketRegime.HIGH_VOL_BEARISH,
}

# Eligibility check — use the actual detected regime for buy_call/buy_put
# so the "oversold bounce" / "overbought reversal" paths can trigger
if override_strategy in ("buy_call", "buy_put"):
    eligibility_regime = regime  # use the real market regime
else:
    eligibility_regime = chosen_regime_map[override_strategy]

eligible = chosen_strategy.evaluate_eligibility(
    eligibility_regime, portfolio, indicators
)

if not eligible:
    # Build strategy-specific reason — show only the ACTUAL blocking condition
    _elig_rsi = indicators.rsi_5min if indicators.rsi_5min is not None else indicators.rsi_14
    _rsi_source = "5-min RSI" if indicators.rsi_5min is not None else f"{indicators.timeframe} RSI"
    _block_reasons = []

    if override_strategy == "buy_call":
        if _elig_rsi > 80:
            _block_reasons.append(f"🛑 {_rsi_source} is {_elig_rsi:.1f} — **RSI > 80** (overbought, overextended)")
        if eligibility_regime in (MarketRegime.TRENDING_BEARISH, MarketRegime.HIGH_VOL_BEARISH):
            if _elig_rsi >= 30:
                _block_reasons.append(f"🛑 Market regime is **{regime_label}** — calls blocked in bearish regimes (RSI {_elig_rsi:.1f} is not < 30 for oversold bounce exception)")
        if indicators.current_price < indicators.sma_20 * 0.985 and indicators.sma_20 < indicators.sma_50:
            _pct = (1 - indicators.current_price / indicators.sma_20) * 100
            _block_reasons.append(f"🛑 Regime guard: price is {_pct:.1f}% below SMA20 with SMA20 < SMA50 — active sell-off")

    elif override_strategy == "buy_put":
        if _elig_rsi < 20:
            _block_reasons.append(f"🛑 {_rsi_source} is {_elig_rsi:.1f} — **RSI < 20** (extremely oversold, bounce likely)")
        if eligibility_regime in (MarketRegime.LOW_VOL_BULLISH,):
            if _elig_rsi <= 70:
                _block_reasons.append(f"🛑 Market regime is **{regime_label}** — puts blocked in bullish regimes (RSI {_elig_rsi:.1f} is not > 70 for overbought reversal exception)")
        if (indicators.current_price < indicators.sma_20 * 0.97
                and indicators.current_price < indicators.sma_50 * 0.98):
            _pct = (1 - indicators.current_price / indicators.sma_20) * 100
            _block_reasons.append(f"🛑 Regime guard: price is {_pct:.1f}% below SMA20 — extended sell-off, V-reversal bounce likely")

    if not _block_reasons:
        _block_reasons.append(f"- {_rsi_source} is {_elig_rsi:.1f}, Market regime is **{regime_label}**")

    specific_reasons = "\n".join(_block_reasons)
    st.warning(
        f"⚠️ **{STRATEGY_DESCRIPTIONS[override_strategy]['name']}** is not eligible with your current simulated portfolio.\n\n"
        f"**Possible reasons:**\n{specific_reasons}\n\n"
        f"Adjust your portfolio in the sidebar and re-run."
    )
else:
    # Generate chain & order — use DTE range from sidebar
    is_scalp_strategy = override_strategy in ("buy_call", "buy_put")
    if is_scalp_strategy:
        dte_min = rcfg.get("min_dte", min_dte)
        dte_max = min(rcfg.get("max_dte", max_dte), 7)  # cap scalps at 7 DTE

        # Try live chain first (when not in mock mode and not in replay mode)
        chain = None
        live_chain_used = False
        if not mock_mode and not _is_replay:
            for dte_val in range(dte_min, dte_max + 1):
                live = fetch_live_chain(sym, current_price, dte=dte_val)
                if live:
                    chain = live if chain is None else chain + live
                    live_chain_used = True

        # Fall back to synthetic chain
        if not chain:
            chain = []
            for dte_val in range(dte_min, dte_max + 1):
                chain.extend(generate_simulated_chain(sym, current_price, indicators.vix, dte=dte_val))
    else:
        chain = generate_simulated_chain(sym, current_price, indicators.vix, dte=(rcfg.get("min_dte", min_dte) + rcfg.get("max_dte", max_dte)) // 2)
        live_chain_used = False
    try:
        # In replay mode, force the strategy's internal analyzers to use mock
        # (synthesized from time-sliced indicators) instead of fetching live data.
        if _is_replay:
            _orig_or_analyze = OpeningRangeAnalyzer.analyze
            _orig_rm_analyze = RecentMomentumAnalyzer.analyze
            OpeningRangeAnalyzer.analyze = lambda self, ind, mock=False: _orig_or_analyze(self, ind, mock=True)
            RecentMomentumAnalyzer.analyze = lambda self, ind, mock=False: _orig_rm_analyze(self, ind, mock=True)

        try:
            order = chosen_strategy.construct_order(sym, chain, portfolio, indicators)
        finally:
            if _is_replay:
                OpeningRangeAnalyzer.analyze = _orig_or_analyze
                RecentMomentumAnalyzer.analyze = _orig_rm_analyze

        # Display order legs
        st.markdown(f"### {STRATEGY_DESCRIPTIONS[override_strategy]['icon']}  Order Details")

        # Show chain data source
        if is_scalp_strategy:
            if live_chain_used:
                st.success("📡 **Live options data** — real bid/ask prices from Yahoo Finance (no login required)")
            else:
                st.warning(
                    "⚠️ **Synthetic options data** — prices are Black-Scholes estimates, NOT real market prices. "
                    "Real prices may differ significantly (especially 0DTE). "
                    "Turn off Mock Mode to fetch live options chain from Yahoo Finance."
                )

        leg_rows = []
        for leg in order.legs:
            leg_rows.append({
                "Action": leg.action.replace("_", " ").title(),
                "Type": leg.option_type.upper(),
                "Strike": f"${leg.strike:.2f}",
                "Expiration": str(leg.expiration),
                "DTE": (leg.expiration - date.today()).days,
                "Qty": leg.quantity,
            })
        st.dataframe(pd.DataFrame(leg_rows), hide_index=True, use_container_width=True)

        # Key metrics
        mcol1, mcol2, mcol3, mcol4 = st.columns(4)
        mcol1.metric("Max Profit", f"${order.max_profit:,.2f}", delta="", delta_color="off")
        mcol2.metric("Max Loss", f"${order.max_loss:,.2f}", delta="", delta_color="off")
        mcol3.metric("Risk/Reward", f"{order.risk_reward_ratio:.3f}")
        mcol4.metric("Limit Price", f"${order.limit_price:.2f}" if order.limit_price else "Market")

        # ── Entry Status Alert (for breakout strategies) ──
        if order.breakout_price is not None and override_strategy in ("buy_call", "buy_put"):
            bp = order.breakout_price

            # Fetch real-time price (not cached) for breakout comparison
            try:
                _rt = yf.Ticker(sym)
                _rt_price = _rt.fast_info.get("lastPrice", None) or _rt.fast_info.get("last_price", None)
                if _rt_price and _rt_price > 0:
                    live_price = float(_rt_price)
                else:
                    live_price = current_price  # fallback to cached
            except Exception:
                live_price = current_price  # fallback to cached

            if override_strategy == "buy_call":
                triggered = live_price > bp
                distance = bp - live_price
                direction_word = "above"
            else:
                triggered = live_price < bp
                distance = live_price - bp
                direction_word = "below"

            if triggered:
                st.success(
                    f"🟢 **BREAKOUT TRIGGERED** — {sym} is ${live_price:.2f} (live), "
                    f"already {direction_word} the ${bp:.2f} trigger. Entry conditions met!"
                )
            else:
                st.error(
                    f"⏳ **WAIT — BREAKOUT NOT YET TRIGGERED**\n\n"
                    f"**Do NOT buy this option yet.** {sym} is **${live_price:.2f}** (live), "
                    f"still **${abs(distance):.2f} away** from the breakout trigger of **${bp:.2f}**.\n\n"
                    f"- Set an **alert** for {sym} {'>' if override_strategy == 'buy_call' else '<'} ${bp:.2f}\n"
                    f"- Only enter when price breaks {direction_word} ${bp:.2f}\n"
                    f"- If it never breaks out today → **skip this trade**"
                )

        # ── Rationale (formatted) ──
        _rationale_raw = order.rationale or ""
        # Split on sentence boundaries (". ") to get individual points
        _rationale_parts = [p.strip() for p in _rationale_raw.split(". ") if p.strip()]

        with st.expander("💡 **Trade Rationale**", expanded=True):
            for part in _rationale_parts:
                part_clean = part.rstrip(".")
                if part_clean.startswith("✅"):
                    st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;{part_clean}")
                elif part_clean.startswith("⚠️") or part_clean.startswith("📈") or part_clean.startswith("📉"):
                    st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;{part_clean}")
                elif part_clean.startswith("Buy "):
                    st.markdown(f"**📋 {part_clean}.**")
                elif part_clean.startswith("Breakout") or part_clean.startswith("Breakdown"):
                    st.markdown(f"**🎯 {part_clean}.**")
                elif part_clean.startswith("Range:"):
                    st.markdown(f"**📐 {part_clean}.**")
                elif part_clean.startswith("Stop:"):
                    st.markdown(f"**🛑 {part_clean}.**")
                elif part_clean.startswith("Quality:"):
                    st.markdown(f"**⭐ {part_clean}.**")
                elif part_clean.startswith("Exit:"):
                    st.markdown(f"**🚪 {part_clean}.**")
                elif "backtest" in part_clean.lower() or "optimized" in part_clean.lower():
                    st.caption(f"📊 {part_clean}.")
                else:
                    st.markdown(f"- {part_clean}.")

        # ── Breakout Levels (buy_call / buy_put) ──
        if order.breakout_price is not None and override_strategy in ("buy_call", "buy_put"):
            st.markdown("### 🎯 Exact Breakout Levels")

            dir_color = "#6BBF7A" if order.breakout_direction == "bullish" else "#E06C6C" if order.breakout_direction == "bearish" else "#636366"
            dir_emoji = "●" if order.breakout_direction in ("bullish", "bearish") else "○"
            trigger_word = "above" if override_strategy == "buy_call" else "below"

            st.markdown(
                f"""
                <div style="background: #2C2C2E; border: 1px solid #3A3A3C; border-radius: 12px; padding: 24px; box-shadow: 0 2px 12px rgba(0,0,0,0.25); border-color: {dir_color};">
                    <div style="font-size: 11px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.06em; color: #A1A1A6;">Breakout Trigger</div>
                    <div style="font-size: 20px; font-weight: 700; color: {dir_color}; margin-top: 6px;">
                        <span style="color: {dir_color};">{dir_emoji}</span> Stock breaks {trigger_word}
                        <span style="font-size: 26px; font-weight: 800;">${order.breakout_price:.2f}</span>
                    </div>
                    <div style="font-size: 12px; color: #636366; margin-top: 6px;">
                        Active Range: ${order.opening_range_low:.2f} – ${order.opening_range_high:.2f} (recent 30-min window)
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            bp1, bp2, bp3, bp4, bp5, bp6 = st.columns(6)
            bp1.metric("📍 Entry Price", f"${order.entry_price:.2f}",
                       delta=f"{'Above' if override_strategy == 'buy_call' else 'Below'} range", delta_color="off")
            bp2.metric("🛑 Stop Loss", f"${order.stop_loss_price:.2f}",
                       delta=f"Range midpoint", delta_color="inverse")
            bp3.metric("🎯 T1 (0.75R)", f"${order.profit_target_1:.2f}",
                       delta=f"+${abs(order.profit_target_1 - order.entry_price):.2f}", delta_color="normal")
            bp4.metric("🎯 T2 (1.5R)", f"${order.profit_target_2:.2f}",
                       delta=f"+${abs(order.profit_target_2 - order.entry_price):.2f}", delta_color="normal")
            bp5.metric("⏰ Time Stop", "3:00 PM ET",
                       delta="Close all", delta_color="off")
            bp6.metric("Direction", f"{order.breakout_direction.upper()}")

            # Step-by-step execution
            with st.expander("📋 How to Execute This Breakout Trade", expanded=True):
                if override_strategy == "buy_call":
                    st.markdown(f"""
**1. Wait for the 60-min opening range** (9:30–10:30 AM ET)
   - Range High: **${order.opening_range_high:.2f}** | Range Low: **${order.opening_range_low:.2f}**

**2. Entry trigger:** Stock price breaks **above ${order.breakout_price:.2f}**
   - ✅ Confirm price is **above VWAP** (strongest backtest signal — 57% win rate)
   - ✅ Confirm 60-min candle closed **near highs** (2nd strongest signal — 56% win rate)
   - Buy the **${order.legs[0].strike:.0f} CALL** expiring **{order.legs[0].expiration}** at ~**${order.limit_price:.2f}**

**3. Stop loss:** **${order.stop_loss_price:.2f}** (range midpoint — tighter than range low)
   - Backtest-optimized: tighter stop cuts losers faster (only 10-13% stop rate)

**4. Take profit (scaled exit):**
   - **Target 1 (0.75R):** Close HALF at stock price **${order.profit_target_1:.2f}** → then trail stop to breakeven
   - **Target 2 (1.5R):** Close remainder at **${order.profit_target_2:.2f}**
   - *(Backtest: T1 hits 20-43% of trades; old T2 at 2R never hit)*

**5. ⏰ Time stop: Close ALL by 3:00 PM ET**
   - Backtest finding: 70% of EOD exits are losers — don't hold hoping for a late move

**6. Backtest stats (SPY 30d):** 50% call win rate, 1.97 profit factor, avg winner $3.74
""")
                else:  # buy_put
                    st.markdown(f"""
**1. Wait for the 60-min opening range** (9:30–10:30 AM ET)
   - Range High: **${order.opening_range_high:.2f}** | Range Low: **${order.opening_range_low:.2f}**

**2. Entry trigger:** Stock price breaks **below ${order.breakout_price:.2f}**
   - ✅ Confirm price is **below VWAP** (strongest backtest signal)
   - ✅ Confirm 60-min candle closed **near lows** (2nd strongest signal)
   - Buy the **${order.legs[0].strike:.0f} PUT** expiring **{order.legs[0].expiration}** at ~**${order.limit_price:.2f}**

**3. Stop loss:** **${order.stop_loss_price:.2f}** (range midpoint — tighter than range high)
   - Backtest-optimized: tighter stop cuts losers faster

**4. Take profit (scaled exit):**
   - **Target 1 (0.75R):** Close HALF at stock price **${order.profit_target_1:.2f}** → then trail stop to breakeven
   - **Target 2 (1.5R):** Close remainder at **${order.profit_target_2:.2f}**

**5. ⏰ Time stop: Close ALL by 3:00 PM ET**
   - Backtest finding: 70% of EOD exits are losers

**6. Backtest stats (SPY 30d):** 63.6% put win rate (BEST direction), 1.97 profit factor
""")


        # ── Opening Range Analysis (Buy Call / Buy Put) ──
        if override_strategy in ("buy_call", "buy_put"):
            st.markdown("### ⚡ 60-Minute Opening Range Analysis")

            ora = OpeningRangeAnalyzer()
            orng = ora.analyze(indicators, mock=_analysis_mock)

            # Show data source badge
            if orng.data_source == "live_intraday":
                st.success(f"📡 **Live data** — computed from {orng.opening_range_bars} actual 5-min candles (9:30–10:30 ET)")
            else:
                st.info("📊 **Estimated** — synthesized from daily indicators. Turn off Mock Mode for live 5-min candle data.")

            st.markdown(orng.summary)

            # Key levels
            or1, or2, or3, or4 = st.columns(4)
            dir_color = "#00d2ff" if orng.breakout_direction == BreakoutDirection.BULLISH else "#ff6b6b" if orng.breakout_direction == BreakoutDirection.BEARISH else "#95a5a6"
            or1.metric("Range High", f"${orng.range_high:.2f}")
            or2.metric("Range Low", f"${orng.range_low:.2f}")
            or3.metric("Range Width", f"${orng.range_width:.2f}", delta=f"{orng.range_width_pct:.2f}%", delta_color="off")
            or4.metric("Momentum", f"{orng.momentum_score:+d}/100", delta=orng.breakout_direction.value.upper(), delta_color="normal" if orng.momentum_score > 0 else "inverse")

            # Entry / Exit levels
            st.markdown("#### 🎯 Scalp Levels")
            lv1, lv2, lv3, lv4, lv5 = st.columns(5)
            lv1.metric("Entry", f"${orng.entry_price:.2f}")
            lv2.metric("Stop Loss", f"${orng.stop_loss:.2f}", delta=f"-${orng.risk_per_share:.2f} risk", delta_color="inverse")
            lv3.metric("Target 1 (1:1)", f"${orng.target_1:.2f}", delta=f"+${orng.risk_per_share:.2f}", delta_color="normal")
            lv4.metric("Target 2 (2:1)", f"${orng.target_2:.2f}", delta=f"+${orng.risk_per_share * 2:.2f}", delta_color="normal")
            lv5.metric("Risk/Share", f"${orng.risk_per_share:.2f}")

            # Signals breakdown
            with st.expander("📊 Opening Range Signals", expanded=True):
                for sig in orng.signals:
                    s = sig["score"]
                    icon = "🟢" if s > 10 else "🔵" if s > 0 else "⚪" if s == 0 else "🟡" if s > -10 else "🔴"
                    st.markdown(f"{icon} **{sig['name']}** ({'+' if s > 0 else ''}{s}) — {sig['desc']}")

            if not orng.breakout_confirmed:
                st.warning("⏳ Breakout not yet confirmed. Wait for price to break the opening range with momentum before entering.")

            # ── Recent 30-Min Momentum Analysis ──
            st.markdown("### 🕐 Recent 30-Minute Momentum")

            if _recent.data_source == "live":
                st.success(f"📡 **Live data** — {_recent.candle_count} actual 5-min candles")
            else:
                st.info("📊 **Estimated** — synthesized from daily indicators. Turn off Mock Mode for live data.")

            st.markdown(_recent.summary)

            rm1, rm2, rm3, rm4 = st.columns(4)
            rc_color = "#00d2ff" if _recent.direction == "bullish" else "#ff6b6b" if _recent.direction == "bearish" else "#95a5a6"
            rm1.metric("Direction", _recent.direction.upper())
            rm2.metric("Momentum", f"{_recent.momentum_score:+d}/100")
            rm3.metric("30-min Change", f"{_recent.price_change_pct:+.2f}%")
            rm4.metric("5-min RSI", f"{_recent.rsi_5min:.1f}")

            with st.expander("📊 Recent 30-Min Signals", expanded=False):
                for sig in _recent.signals:
                    s = sig["score"]
                    icon = "🟢" if s > 10 else "🔵" if s > 0 else "⚪" if s == 0 else "🟡" if s > -10 else "🔴"
                    st.markdown(f"{icon} **{sig['name']}** ({'+' if s > 0 else ''}{s}) — {sig['desc']}")

        # P&L chart
        st.plotly_chart(build_pnl_chart(order, current_price), use_container_width=True)

        # ══════════════════════════════════════════════════════════
        #  SECTION 4.5: Exact Trade Execution Guide
        # ══════════════════════════════════════════════════════════

        st.markdown("## Trade Execution Guide")

        guide = build_execution_guide(order, indicators, timeframe=saved_timeframe)

        # Notes / overall readiness
        for note in guide.notes:
            st.markdown(note)

        # ── 4.5a: Contract Details ──
        with st.expander("📋 Exact Contracts to Trade", expanded=True):
            st.dataframe(pd.DataFrame(guide.contract_legs), hide_index=True, use_container_width=True)
            if order.limit_price:
                st.markdown(f"**Net Limit Price:** **${order.limit_price:.2f}** per contract")
            st.markdown(f"**Max Profit:** ${order.max_profit:,.2f}  ·  **Max Loss:** ${order.max_loss:,.2f}  ·  **Risk/Reward:** {order.risk_reward_ratio:.3f}")

        # ── 4.5b: Step-by-Step Brokerage Instructions ──
        with st.expander("🏦 Step-by-Step Brokerage Instructions", expanded=True):
            for step in guide.brokerage_steps:
                st.markdown(step)
            if mock_mode:
                st.caption("⚠️ Mock mode — contract symbols are synthetic. Use real chain data in live mode.")

        # ── 4.5c: Optimal Entry Conditions ──
        with st.expander("📊 Optimal Entry Conditions (Live Check)", expanded=True):
            cond_cols = st.columns([3, 2, 2, 1])
            cond_cols[0].markdown("**Condition**")
            cond_cols[1].markdown("**Current**")
            cond_cols[2].markdown("**Ideal Range**")
            cond_cols[3].markdown("**Status**")

            for cond in guide.entry_conditions:
                c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
                c1.markdown(f"**{cond.name}**")
                c2.markdown(cond.current_value)
                c3.markdown(cond.ideal_range)
                c4.markdown("✅" if cond.met else "❌")

            st.markdown("---")
            pct_met = guide.entry_conditions_met / guide.entry_conditions_total if guide.entry_conditions_total > 0 else 0
            bar_color = "#2ecc71" if pct_met >= 0.8 else "#3498db" if pct_met >= 0.6 else "#f39c12" if pct_met >= 0.4 else "#e74c3c"
            st.markdown(
                f"**Entry Readiness: {guide.entry_conditions_met}/{guide.entry_conditions_total} conditions met**"
            )
            st.progress(pct_met, text=f"{pct_met:.0%}")

            if guide.optimal_entry_price:
                st.markdown(f"💡 **Optimal entry price for {sym}:** ~**${guide.optimal_entry_price:.2f}** — "
                            f"current price is **${current_price:.2f}** "
                            f"({'at target ✅' if abs(current_price - guide.optimal_entry_price) <= indicators.atr_14 * 0.5 else 'wait for price to reach target'})")

        # ── 4.5d: Exit Plan & Trade Management ──
        with st.expander("🚪 Exit Plan & Trade Management", expanded=True):
            ep = guide.exit_plan

            ep1, ep2, ep3, ep4 = st.columns(4)
            ep1.metric("Take Profit", f"${ep.take_profit_price:,.2f}",
                       delta=f"{ep.take_profit_pct:.0%} of max", delta_color="off")
            ep2.metric("Stop Loss", f"${ep.stop_loss_price:,.2f}",
                       delta=f"{ep.stop_loss_pct:.0%}× premium", delta_color="off")
            ep3.metric("Roll Trigger", f"{ep.roll_trigger_dte} DTE",
                       delta="Days to expiration", delta_color="off")
            ep4.metric("Risk/Reward", f"{order.risk_reward_ratio:.3f}",
                       delta="ratio", delta_color="off")

            st.markdown("#### 📌 Management Rules")
            st.markdown(ep.adjustment_notes)

            st.markdown("#### 🔄 Rolling Conditions")
            st.markdown(ep.roll_conditions)

        # ══════════════════════════════════════════════════════════
        #  SECTION 5: Risk Validation
        # ══════════════════════════════════════════════════════════

        st.markdown("## Risk Validation")

        # Create a mock config with the sidebar parameters
        class SimRiskConfig:
            max_position_size_pct = rcfg.get("max_pos_pct", max_pos_pct)
            max_loss_pct = rcfg.get("max_loss_pct", max_loss_pct)
            max_options_allocation_pct = 0.15
            min_dte = rcfg.get("min_dte", min_dte)
            max_dte = rcfg.get("max_dte", max_dte)
            earnings_blackout_days = 7
            max_daily_trades = 3
            circuit_breaker_daily_loss_pct = 0.03

        rm = RiskManager(cfg=SimRiskConfig())
        risk = rm.validate(order, portfolio, indicators)

        if risk.approved:
            st.success("✅ **Risk Check PASSED** — This trade satisfies all risk guardrails.")
        else:
            st.error("❌ **Risk Check FAILED**")
            for reason in risk.rejection_reasons:
                st.markdown(f"- 🚫 {reason}")

        # Risk breakdown
        rcol1, rcol2, rcol3, rcol4 = st.columns(4)
        rcol1.metric("Position Size", f"{risk.position_size_pct:.2%}", delta="OK" if risk.position_size_pct <= SimRiskConfig.max_position_size_pct else "OVER", delta_color="normal" if risk.position_size_pct <= SimRiskConfig.max_position_size_pct else "inverse")
        rcol2.metric("Max Loss %", f"{risk.max_loss_pct:.2%}", delta="OK" if risk.max_loss_pct <= SimRiskConfig.max_loss_pct else "OVER", delta_color="normal" if risk.max_loss_pct <= SimRiskConfig.max_loss_pct else "inverse")
        rcol3.metric("Options Alloc", f"{risk.options_allocation_after_pct:.2%}")
        rcol4.metric("Portfolio Value", f"${portfolio.account.portfolio_value:,.0f}")

        # ══════════════════════════════════════════════════════════
        #  SECTION 6: Simulation Summary
        # ══════════════════════════════════════════════════════════

        st.markdown("## Simulation Summary")

        summary = {
            "symbol": sym,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "🟡 SIMULATION (no real trades)",
            "market_regime": regime_label,
            "recommended_strategy": STRATEGY_DESCRIPTIONS[selected_strategy_name]["name"],
            "simulated_strategy": STRATEGY_DESCRIPTIONS[override_strategy]["name"],
            "order": order.model_dump(),
            "risk_assessment": risk.model_dump(),
            "portfolio": {
                "value": portfolio.account.portfolio_value,
                "cash": portfolio.account.cash,
                "shares_held": pcfg.get("shares", shares),
            },
        }

        with st.expander("📋 Full JSON Summary", expanded=False):
            st.json(summary, expanded=False)

        # Download button
        st.download_button(
            label="📥 Download Simulation Report (JSON)",
            data=json.dumps(summary, indent=2, default=str),
            file_name=f"simulation_{sym}_{date.today().isoformat()}.json",
            mime="application/json",
        )

    except Exception as exc:
        st.error(f"Could not construct order: {exc}")


# ══════════════════════════════════════════════════════════════════
#  SECTION 1.5: Entry Point Analysis
# ══════════════════════════════════════════════════════════════════

st.markdown("## Entry Point Analysis")

entry_analyzer = EntryAnalyzer()
entry = entry_analyzer.analyze(indicators, timeframe=saved_timeframe)

# Entry score gauge
ecol1, ecol2, ecol3 = st.columns([1, 2, 1])

SIGNAL_COLORS = {
    EntrySignal.STRONG_BUY: "#6BBF7A",
    EntrySignal.BUY: "#7AB4D9",
    EntrySignal.NEUTRAL: "#636366",
    EntrySignal.WAIT: "#D4A74A",
    EntrySignal.AVOID: "#E06C6C",
}

with ecol1:
    score_color = SIGNAL_COLORS[entry.recommendation]
    st.markdown(
        f"""
        <div style="background: #2C2C2E; border: 1px solid #3A3A3C; border-radius: 12px; padding: 24px; box-shadow: 0 2px 12px rgba(0,0,0,0.25); text-align: center; border-color: {score_color}40;">
            <div style="font-size: 11px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.06em; color: #A1A1A6;">Entry Score</div>
            <div style="font-size: 48px; font-weight: 800; color: {score_color}; line-height: 1.1; margin: 8px 0;">{entry.composite_score}</div>
            <div style="font-size: 13px; color: #636366;">/ 100</div>
            <div style="font-size: 14px; font-weight: 600; margin-top: 10px; color: {score_color}; letter-spacing: 0.04em;">
                {entry.recommendation.value.replace('_', ' ').upper()}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with ecol2:
    st.markdown(entry.summary)
    if entry.support_level and entry.resistance_level:
        st.markdown(
            f"**Key Levels:**  "
            f"Support: **${entry.support_level:.2f}**  ·  "
            f"Resistance: **${entry.resistance_level:.2f}**  ·  "
            f"Current: **${current_price:.2f}**"
        )
    if entry.optimal_entry_price:
        st.markdown(f"💡 **Optimal entry price:** ~${entry.optimal_entry_price:.2f} (near support)")

with ecol3:
    # Mini score bar
    buy_signals = sum(1 for s in entry.signals if s.signal in (EntrySignal.STRONG_BUY, EntrySignal.BUY))
    neutral_signals = sum(1 for s in entry.signals if s.signal == EntrySignal.NEUTRAL)
    caution_signals = sum(1 for s in entry.signals if s.signal in (EntrySignal.WAIT, EntrySignal.AVOID))
    st.metric("Bullish Signals", f"{buy_signals}/{len(entry.signals)}")
    st.metric("Caution Signals", f"{caution_signals}/{len(entry.signals)}")

# Detailed signal breakdown
with st.expander("📊 Detailed Entry Signals", expanded=True):
    for sig in entry.signals:
        sig_color = SIGNAL_COLORS[sig.signal]
        score_prefix = "+" if sig.score > 0 else ""
        st.markdown(
            f"{'🟢' if sig.score > 5 else '🔵' if sig.score > 0 else '⚪' if sig.score == 0 else '🟡' if sig.score > -10 else '🔴'} "
            f"**{sig.name}** ({score_prefix}{sig.score}) — {sig.description}"
        )


# ══════════════════════════════════════════════════════════════════
#  SECTION 2: Regime Classification
# ══════════════════════════════════════════════════════════════════

st.markdown("## Market Regime")

rcol1, rcol2 = st.columns([1, 2])

with rcol1:
    st.markdown(
        f"""
        <div style="background: #2C2C2E; border: 1px solid #3A3A3C; border-radius: 12px; padding: 24px; box-shadow: 0 2px 12px rgba(0,0,0,0.25); text-align: center;">
            <div style="font-size: 42px; margin-bottom: 8px;">{regime_icon}</div>
            <div style="font-size: 18px; font-weight: 700; color: #F5F5F7; letter-spacing: -0.01em;">{regime_label}</div>
            <div style="font-size: 12px; color: #636366; margin-top: 6px; letter-spacing: 0.03em;">{regime.value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with rcol2:
    st.markdown("**Classification Logic:**")
    checks = {
        "VIX Level": f"{indicators.vix:.1f} → {'🔴 High (>30)' if indicators.vix > 30 else '🟡 Elevated (20-30)' if indicators.vix >= 20 else '🟢 Low (<20)'}",
        "RSI (14)": f"{indicators.rsi_14:.1f} → {'Overbought (>70)' if indicators.rsi_14 > 70 else 'Oversold (<30)' if indicators.rsi_14 < 30 else 'Neutral (30-70)'}",
        "Trend (50 vs 200 SMA)": f"{'🔻 Death Cross' if indicators.sma_50 < indicators.sma_200 else '🔺 Golden Cross'}  (SMA50={indicators.sma_50:.0f}, SMA200={indicators.sma_200:.0f})",
        "Bollinger Position": f"Price ${current_price:.0f} {'within' if indicators.bb_lower <= current_price <= indicators.bb_upper else 'outside'} bands (${indicators.bb_lower:.0f}–${indicators.bb_upper:.0f})",
        "MACD Momentum": f"{'📈 Positive' if indicators.macd_histogram > 0 else '📉 Negative'} (histogram={indicators.macd_histogram:.3f})",
    }
    for k, v in checks.items():
        st.markdown(f"- **{k}:** {v}")



# ══════════════════════════════════════════════════════════════════
#  SECTION 6.5: Live Agent Performance
# ══════════════════════════════════════════════════════════════════

st.markdown("---")
st.markdown("## Live Agent Performance")

import json as _json
from pathlib import Path as _Path
from datetime import date as _date, timedelta as _td

_journal_dir = _Path(__file__).resolve().parent.parent / "sweet_spot_journal"
_all_trades = []
if _journal_dir.exists():
    for _jf in sorted(_journal_dir.glob("*.json")):
        try:
            _d = _date.fromisoformat(_jf.stem)
        except ValueError:
            continue
        try:
            for _t in _json.loads(_jf.read_text()):
                _t["_date"] = _jf.stem
                _all_trades.append(_t)
        except Exception:
            pass

if not _all_trades:
    st.info(
        "No live agent results yet. The scheduled task `SweetSpotAgent` will populate "
        f"`{_journal_dir.name}/` after its first weekday run. Check back tomorrow."
    )
else:
    _wcol1, _wcol2, _wcol3 = st.columns([2, 2, 6])
    _window = _wcol1.selectbox("Window", ["All-time", "Last 30 days", "Last 7 days"], index=0)
    _sym_filter = _wcol2.selectbox(
        "Symbol",
        ["All"] + sorted({t.get("symbol") for t in _all_trades if t.get("symbol")}),
    )

    _today = _date.today()
    if _window == "Last 7 days":
        _cutoff = _today - _td(days=7)
    elif _window == "Last 30 days":
        _cutoff = _today - _td(days=30)
    else:
        _cutoff = None

    _trades = [
        t for t in _all_trades
        if (_cutoff is None or _date.fromisoformat(t["_date"]) >= _cutoff)
        and (_sym_filter == "All" or t.get("symbol") == _sym_filter)
    ]
    _closed = [t for t in _trades if t.get("closed") and t.get("pnl") is not None]
    _open = [t for t in _trades if not t.get("closed")]

    if not _closed:
        st.warning(
            f"{len(_trades)} trigger(s) logged in this window but none have reconciled P&L yet "
            f"({len(_open)} still open). Reconciliation runs at EOD."
        )
    else:
        _wins = [t for t in _closed if t.get("is_winner")]
        _losses = [t for t in _closed if t.get("is_winner") is False]
        _gp = sum(t["pnl"] for t in _wins)
        _gl = abs(sum(t["pnl"] for t in _losses))
        _pf = _gp / _gl if _gl > 0 else float("inf")
        _wr = len(_wins) / len(_closed) * 100
        _total = sum(t["pnl"] for t in _closed)

        _m1, _m2, _m3, _m4, _m5 = st.columns(5)
        _m1.metric("Trades", len(_closed), f"{len(_open)} open" if _open else None)
        _m2.metric("Win Rate", f"{_wr:.1f}%", f"vs 63.6% backtest", delta_color="off")
        _m3.metric("Profit Factor", f"{_pf:.2f}", f"vs 1.81 backtest", delta_color="off")
        _m4.metric("Total P&L", f"${_total:+.2f}")
        _m5.metric("Avg / Trade", f"${_total/len(_closed):+.3f}")

        # ── Cumulative P&L equity curve ──
        _sorted = sorted(_closed, key=lambda t: (t["_date"], t.get("time", "")))
        _cum = []
        _running = 0.0
        for t in _sorted:
            _running += t["pnl"]
            _cum.append({"label": f"{t['_date']} {t.get('time', '')}", "cum_pnl": _running,
                         "trade_pnl": t["pnl"], "outcome": t.get("exit_reason", "?")})

        _fig = go.Figure()
        _fig.add_trace(go.Scatter(
            x=list(range(len(_cum))),
            y=[c["cum_pnl"] for c in _cum],
            mode="lines+markers",
            line=dict(color="#6BBF7A" if _running > 0 else "#D9534F", width=2),
            marker=dict(size=6),
            hovertext=[f"{c['label']}<br>Trade: ${c['trade_pnl']:+.2f}<br>Cum: ${c['cum_pnl']:+.2f}<br>Exit: {c['outcome']}"
                       for c in _cum],
            hoverinfo="text",
            name="Cumulative P&L",
        ))
        _fig.add_hline(y=0, line_dash="dash", line_color="#636366", opacity=0.5)
        _fig.update_layout(
            title="Cumulative P&L Curve",
            xaxis_title="Trade #",
            yaxis_title="P&L ($)",
            template="plotly_dark",
            height=320,
            margin=dict(l=40, r=20, t=40, b=40),
            showlegend=False,
        )
        st.plotly_chart(_fig, use_container_width=True)

        # ── Exit-mix bar ──
        _outcomes = {}
        for t in _closed:
            r = t.get("exit_reason", "unknown")
            _outcomes[r] = _outcomes.get(r, 0) + 1
        _ec1, _ec2 = st.columns([1, 2])
        with _ec1:
            st.markdown("**Exit Mix**")
            for r, c in sorted(_outcomes.items(), key=lambda x: -x[1]):
                pct = c / len(_closed) * 100
                st.markdown(f"- `{r:<12}` **{c}** ({pct:.0f}%)")
        with _ec2:
            st.markdown("**Recent Trades** (last 15)")
            _rows = []
            for t in _sorted[-15:][::-1]:
                _dlabel = "CALL" if "call" in t.get("direction", "") else "PUT"
                _rows.append({
                    "Date": t["_date"],
                    "Time": t.get("time", ""),
                    "Sym": t.get("symbol", ""),
                    "Dir": _dlabel,
                    "Entry": f"${t.get('actual_entry', t.get('entry', 0)):.2f}",
                    "Exit": f"${t.get('exit_price', 0):.2f}",
                    "P&L": f"${t['pnl']:+.2f}",
                    "Exit Reason": t.get("exit_reason", "?"),
                    "✓": "✅" if t.get("is_winner") else "❌",
                })
            st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True)

# ══════════════════════════════════════════════════════════════════
#  SECTION 7: Strategy Comparison
# ══════════════════════════════════════════════════════════════════

st.markdown("---")
st.markdown("## Strategy Reference")

comp_data = {
    "": ["↗ Buy Call (Scalp)", "↘ Buy Put (Scalp)", "Covered Call", "Naked Put", "Naked Call", "Iron Condor", "Protective Put"],
    "Market Regime": ["Any (bullish breakout)", "Any (bearish breakout)", "Low Vol · Bullish", "Low Vol · Neutral", "High Vol · Bearish", "Range-Bound · High Vol", "Trending Bearish"],
    "Direction": ["Strong Bullish", "Strong Bearish", "Mildly Bullish", "Bullish/Neutral", "Bearish/Neutral", "Neutral (range)", "Bearish hedge"],
    "Legs": ["Buy 1 ATM call", "Buy 1 ATM put", "Sell 1 OTM call", "Sell 1 OTM put", "Sell 1 OTM call", "4-leg spread", "Buy 1 ATM put"],
    "Max Profit": ["Unlimited (up)", "Unlimited (down)", "Premium", "Premium", "Premium", "Net credit", "Unlimited (down)"],
    "Max Loss": ["Premium paid", "Premium paid", "Stock → 0", "Strike×100 − prem", "⚠️ UNLIMITED", "Spread width", "Premium paid"],
    "Ideal VIX": ["Any", "Any", "< 20", "< 20", "20 – 40+", "20 – 35", "> 30"],
    "Ideal RSI": ["> 50 (breakout)", "< 50 (breakdown)", "40 – 65", "40 – 60", "> 60 (overbought)", "40 – 60", "< 30 or death cross"],
    "Timeframe": ["⚡ Intraday", "⚡ Intraday", "📅 Daily/Swing", "📅 Daily/Swing", "📅 Daily/Swing", "📅 Daily/Swing", "📅 Daily/Swing"],
    "Shares?": ["❌ Cash only", "❌ Cash only", "✅ ≥100", "❌ Cash only", "❌ Margin", "❌ No", "✅ Any"],
}
st.dataframe(pd.DataFrame(comp_data), hide_index=True, use_container_width=True)

st.markdown("---")
st.caption("Simulation Only — No real trades are placed. "
           + ("Running in Mock Mode with sample data." if mock_mode
              else "Using live market data from Yahoo Finance.")
           + " Option chains are synthetic unless live mode is active.")

