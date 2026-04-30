# Options Trading Agent via MCP

A **self-contained** options trading agent that connects to Robinhood via **MCP (Model Context Protocol)**, analyzes market conditions using technical indicators, and algorithmically selects and executes one of two intraday scalping strategies — **no external LLM or AI API required**.

## 🏗 Architecture

```
User ↔ Options Agent (algorithmic) ↔ MCP Client ↔ MCP Server ↔ Robinhood API
         │
         ├── Market Analyzer      (VIX, RSI, SMA, Bollinger, MACD, ATR, ZLEMA)
         ├── Opening Range Analyzer (60-min breakout direction + 7 weighted signals)
         ├── Recent Momentum Analyzer (30-min real-time momentum snapshot)
         ├── Quality Scorer       (shared 11-point quality gate)
         ├── Momentum Cascade     (explosion detector: VPVR + ZLEMA + volume climax)
         ├── Strategy Selector    (auto-pick Buy Call vs Buy Put by quality score)
         ├── Risk Manager         (7 guardrails + circuit breaker)
         └── Streamlit Dashboard  (simulation UI)
```

## 📋 Strategies

| Strategy | Direction | When Selected | Description |
|---|---|---|---|
| **🚀 Buy Call (Scalp)** | Bullish breakout | Call quality > Put quality | Buy ATM/slightly ITM calls on bullish breakout above the active range |
| **💥 Buy Put (Scalp)** | Bearish breakout | Put quality > Call quality | Buy ATM/slightly ITM puts on bearish breakdown below the active range |

Both strategies are **intraday scalps** with defined risk (premium paid). The agent auto-selects between them based on the 11-point quality score.

### Strategy Pipeline

```
1. Market Analyzer      → Technical indicators (RSI, SMA, MACD, Bollinger, ATR, VIX, ZLEMA)
2. Opening Range (60m)  → Breakout direction + weighted momentum score M ∈ [-100, +100]
3. Recent Momentum (30m)→ Real-time directional snapshot (bullish/bearish/neutral)
4. Quality Scorer (11pt)→ Score each strategy 0-13; auto-pick the higher one
5. Momentum Cascade     → Explosion potential (VPVR levels + ZLEMA + volume climax)
6. Strategy Construction→ Entry/stop/target levels from the active range
7. Risk Manager         → Validate against 7 guardrails before execution
```

### Eligibility Rules

| Strategy | Blocked When | Exception |
|---|---|---|
| **Buy Call** | Bearish regime (trending or high-vol bearish) | Allowed if RSI < 30 (oversold bounce) |
| **Buy Call** | RSI > 80 (overbought) | — |
| **Buy Put** | Bullish regime (low-vol bullish) | Allowed if RSI > 70 (overbought reversal) |
| **Buy Put** | RSI < 20 (extremely oversold) | — |

### Contract Selection

| Strategy | Target Delta | Tolerance | Fallback |
|---|---|---|---|
| **Buy Call** | 0.60 | ±0.15 | Closest to ATM |
| **Buy Put** | 0.55 | ±0.15 | Closest to ATM |

## 🚀 Quick Start

### 1. Setup
```bash
cd options_agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure
```bash
cp .env.example .env
# Edit .env with your Robinhood credentials (only needed for live/MCP mode)
```

### 3. Launch Simulation Dashboard (Streamlit)
```bash
cd options_agent
streamlit run dashboard/app.py
# Opens at http://localhost:8501
```

The dashboard lets you:
- 📊 **Analyze** any stock symbol with live market data and technical indicators
- 🏷️ **View** the classified market regime (bullish, range-bound, bearish)
- 🎯 **Auto-select** between Buy Call and Buy Put based on 11-point quality score
- 📋 **Inspect** constructed option orders with strike, delta, expiration
- 🎯 **See breakout triggers** from the recent 30-min range with exact entry/stop/target levels
- 📈 **Visualize** P&L at expiration charts
- ✅ **Validate** risk checks against configurable guardrails
- 📥 **Download** simulation reports as JSON
- 🔀 **Override** the recommended strategy to compare alternatives

> **No real trades are ever placed.** The dashboard uses live market data for analysis but generates synthetic option chains for order simulation.

### 4. Run (CLI Dry-Run Mode)
```bash
# Single symbol analysis
python scripts/run_agent.py --symbol AAPL --dry-run --verbose

# Backtest / analyze current indicators
python scripts/backtest.py --symbol SPY --period 1y --save
```

### 5. Run Tests
```bash
pytest tests/ -v
```

### 6. Scheduled Execution
```bash
python scripts/run_scheduled.py
# Scans at 9:35 AM, 12:00 PM, 3:30 PM ET (Mon-Fri)
```

### 7. Enable Live Trading (CAUTION ⚠️)
```bash
# Set DRY_RUN=false in .env, then:
python scripts/run_agent.py --symbol AAPL --live
```

## 📁 Structure

```
options_agent/
├── dashboard/
│   └── app.py                  # Streamlit simulation dashboard
├── .streamlit/config.toml      # Streamlit theme & config
├── config/settings.py          # All configuration (from .env)
├── src/
│   ├── agent.py                # Main orchestrator
│   ├── mcp_client.py           # MCP connection to Robinhood
│   ├── market_analyzer.py      # Technical indicators + regime classification
│   ├── opening_range.py        # 60-min opening range breakout analysis
│   ├── recent_momentum.py      # 30-min real-time momentum snapshot
│   ├── momentum_cascade.py     # Explosion detector (VPVR + ZLEMA + cascade)
│   ├── entry_analyzer.py       # 8-signal composite entry score (0-100)
│   ├── strategy_selector.py    # Algorithmic strategy selection
│   ├── risk_manager.py         # Trade validation & circuit breakers
│   ├── backtester.py           # Intraday replay backtester (5m / 1h)
│   ├── models/                 # Pydantic data models
│   ├── strategies/
│   │   ├── buy_call.py         # 🚀 Buy Call scalping strategy
│   │   ├── buy_put.py          # 💥 Buy Put scalping strategy
│   │   └── base_strategy.py    # Abstract base class
│   └── utils/
│       └── quality_scorer.py   # Shared 11-point quality scorer
├── mcp_server/                 # Custom Robinhood MCP server (robin_stocks)
├── scripts/                    # CLI runners & backtest
└── tests/                      # Unit & integration tests
```

---

## 📐 Algorithm — Three-Phase Signal System

The agent uses a **three-phase pipeline** to decide whether to Buy Call or Buy Put, and at what price levels. All three phases share the same logic across the dashboard, strategies, and backtester.

### Phase 1 — Weighted Momentum Score (Direction)

The **Opening Range Analyzer** computes a weighted momentum score $M \in [-100, +100]$ from 7 directional signals during the first 60 minutes (9:30–10:30 ET). Each signal contributes $\pm w_i$, clamped to $[-100, 100]$:

$$M = \text{clamp}\!\Big(\sum_{i=1}^{7} s_i \cdot w_i,\; -100,\; 100\Big)$$

where $s_i \in \{-1, 0, +1\}$ is the signal direction.

**Optimized weights** (from `scripts/test_weights.py` grid search, config `pvr_down`):

| Signal | Abbrev | Weight $w_i$ | Bullish ($s_i = +1$) | Bearish ($s_i = -1$) | Neutral ($s_i = 0$) |
|--------|--------|:---:|---|---|---|
| Price vs Range | `pvr` | **5** | Price in upper 30% of OR | Price in lower 30% of OR | Mid-range (30–70%) |
| Intraday RSI | `rsi` | **20** | RSI > 60 | RSI < 40 | 40 ≤ RSI ≤ 60 |
| Intraday MACD | `macd` | **20** | Histogram > 0.05 | Histogram < −0.05 | $\|H\| \leq 0.05$ |
| VWAP | `vwap` | **20** | $P > \text{VWAP}$ | $P < \text{VWAP}$ | *(always directional)* |
| Volume Surge | `vol` | **5** | Surge + $M > 0$ | Surge + $M < 0$ | No surge |
| OR Candle | `orc` | **10** | Body > 30% bullish | Body < −30% bearish | Indecisive body |
| VIX | `vix` | **5** | VIX > 20 + $M > 0$ | VIX > 20 + $M < 0$ | VIX ≤ 20 |

**Total weight budget:** $5 + 20 + 20 + 20 + 5 + 10 + 5 = 85$

**Direction decision:**

$$\text{Direction} = \begin{cases} \text{BUY CALL} & M \geq 25 \\ \text{BUY PUT} & M \leq -25 \\ \text{SKIP (no trade)} & -25 < M < 25 \end{cases}$$

> **Design rationale:** The `pvr_down` config downweights Price-vs-Range (pvr=5) because in-range position alone (~50% WR) has low predictive value. RSI, MACD, and VWAP (20 each) showed 55–57% WR in backtests.

### Phase 2 — 11-Point Quality Score (Confidence)

Both strategies are scored 0–13 by `compute_quality_score()` in `src/utils/quality_scorer.py`. The agent auto-picks the strategy with the higher score.

$$Q = \sum_{i=1}^{11} S_i \;-\; \text{penalties}$$

#### Opening Range Signals (60-min, 9:30–10:30 ET)

| # | Signal | Weight | Condition (Buy Call) | Condition (Buy Put) |
|---|--------|--------|----------------------|---------------------|
| 1 | **Breakout Direction Aligned** | +2 | 60-min breakout is **bullish** | 60-min breakout is **bearish** |
| 2 | *(penalty)* | −1 | Breakout is **against** direction | *(same, reversed)* |
| 3 | **Breakout Confirmed** | +1 | $\|M_{OR}\| \geq 40$ **and** $M_{OR} > 0$ | $\|M_{OR}\| \geq 40$ **and** $M_{OR} < 0$ |

#### Recent 30-Min Momentum

| # | Signal | Weight | Condition (Buy Call) | Condition (Buy Put) |
|---|--------|--------|----------------------|---------------------|
| 4 | **Recent Direction Aligned** | +2 | Recent 30-min direction is **bullish** | Recent 30-min direction is **bearish** |
| 5 | *(penalty)* | −1 | Recent momentum is **against** direction | *(same, reversed)* |

#### Daily Indicator Signals

| # | Signal | Weight | Formula | Condition |
|---|--------|--------|---------|-----------|
| 6 | **Volume Surge** | +1 | $V_{ratio} = V_{current} / SMA_{20}(V)$ | $V_{ratio} \geq 1.2$ |
| 7 | **VIX Elevated** | +1 | $VIX$ | $VIX > 18$ |
| 8 | **VWAP Confirmation** | +1 | $P$ vs $SMA_{20}$ | Buy Call: $P > SMA_{20}$; Buy Put: $P < SMA_{20}$ |
| 9 | **Trend Alignment** | +1 | $SMA_{20}$ vs $SMA_{50}$ | Buy Call: $SMA_{20} > SMA_{50}$; Buy Put: $SMA_{20} < SMA_{50}$ |

#### Momentum Acceleration Signals

| # | Signal | Weight | Formula | Condition |
|---|--------|--------|---------|-----------|
| 10 | **Dual Momentum** | +1 | $M_{OR}$ and $M_{recent}$ | Both $\geq 40$ (bullish) or both $\leq -40$ (bearish), aligned with direction |
| 11 | **Volume Climax** | +1 | $V_{ratio}$ | $V_{ratio} \geq 2.0$ — institutional participation spike |

#### Advanced Indicators

| # | Signal | Weight | Formula | Condition |
|---|--------|--------|---------|-----------|
| 12 | **ZLEMA Trend** | +1 | $ZLEMA_8$ vs $ZLEMA_{21}$ cross | Buy Call: ZLEMA bullish; Buy Put: ZLEMA bearish |
| 13 | **VPVR Level Break** | +1 | Volume Profile (VPVR) | Price broke through a High Volume Node S/R level |

**ZLEMA (Zero-Lag EMA):**
$$ZLEMA(n) = EMA\big(2 \cdot C_t - C_{t-\lfloor(n-1)/2\rfloor},\; n\big)$$

where $C_t$ is the close price. The lag compensation ($2C - C_{lag}$) removes the inherent EMA delay. Trend is determined by:

$$\text{ZLEMA Trend} = \begin{cases} \text{bullish} & ZLEMA_8 > ZLEMA_{21} \times 1.0002 \\ \text{bearish} & ZLEMA_8 < ZLEMA_{21} \times 0.9998 \\ \text{neutral} & \text{otherwise} \end{cases}$$

**VPVR (Volume Profile Visible Range):**

Distributes each bar's volume across price bins proportionally:

$$VP(b) = \sum_{i=1}^{N} V_i \cdot \frac{\text{overlap}(bar_i, bin_b)}{H_i - L_i}$$

**High Volume Nodes (HVN):** bins where $VP(b) > 1.5 \times \overline{VP}$ → strong S/R levels. A "VPVR level break" means price has moved beyond at least one HVN by $0.02 \times ATR_{14}$.

**Maximum score:** 2 + 1 + 2 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 = **13** &ensp;(penalties can reduce to 0)

**Quality label:**

$$\text{Quality} = \begin{cases} \text{🟢 HIGH} & Q \geq 7 \\ \text{🔵 MEDIUM} & 4 \leq Q < 7 \\ \text{🟡 LOW} & Q < 4 \end{cases}$$

**Dashboard display example:** `🚀 Buy Call: 🔵 6/13  |  💥 Buy Put: 🟡 3/13` → auto-selects Buy Call.

### Phase 3 — Execution Levels (Active Range)

Breakout triggers, stop losses, and profit targets are computed from the **active range** — the most recent 30-minute window (high/low). This keeps levels fresh throughout the day, unlike the stale 60-min opening range.

If the recent 30-min data is unavailable, the system falls back to the 60-min opening range.

#### Buy Call Levels

| Level | Formula |
|-------|---------|
| **Entry trigger** | $E = H_{active} - 0.10 \times (H_{active} - L_{active})$ &ensp;(10% inside range high) |
| **Stop loss** | $S = \text{mid}(H_{active}, L_{active}) - 0.02 \times ATR_{14}$ |
| **Risk per unit** | $R = \|E - S\|$ &ensp;(floor: $0.3 \times ATR_{14}$) |
| **Profit target 1** | $T_1 = E + 0.75\,R$ |
| **Profit target 2** | $T_2 = E + 1.5\,R$ |

#### Buy Put Levels

| Level | Formula |
|-------|---------|
| **Entry trigger** | $E = L_{active} + 0.10 \times (H_{active} - L_{active})$ &ensp;(10% inside range low) |
| **Stop loss** | $S = \text{mid}(H_{active}, L_{active}) + 0.02 \times ATR_{14}$ |
| **Risk per unit** | $R = \|S - E\|$ &ensp;(floor: $0.3 \times ATR_{14}$) |
| **Profit target 1** | $T_1 = E - 0.75\,R$ |
| **Profit target 2** | $T_2 = E - 1.5\,R$ |

#### Exit Rules

| Rule | Description |
|------|-------------|
| **T1 hit** | Sell half position at $T_1$ (0.75R), trail stop to breakeven |
| **T2 hit** | Sell remaining at $T_2$ (1.5R) |
| **Stop hit** | Full exit at stop loss $S$ |
| **Time stop** | Close position at 3:00 PM ET if no target/stop hit |

### Phase 4 — Momentum Cascade / Explosion Detector

The **MomentumCascadeDetector** (`src/momentum_cascade.py`) identifies setups with 5x–10x option move potential. It produces an **Explosion Score** $E \in [0, 10]$ from 6 signals:

$$E = \text{clamp}\!\Big(\sum_{i=1}^{6} e_i,\; 0,\; 10\Big)$$

| # | Signal | Max Score | Detection Method |
|---|--------|:---------:|------------------|
| 1 | **Price Acceleration** | +2 | RoC increasing over consecutive 10-min windows (same direction) |
| 2 | **Volume Climax** | +2 | Volume $\geq 2\times$ avg AND accelerating $\geq 1.3\times$ prior window |
| 3 | **VPVR Cascade** | +2 | Price broke through $\geq 3$ VPVR High Volume Nodes |
| 4 | **Quality Boost** | +2 | Quality score $\geq 8$ (elite signal alignment) |
| 5 | **Dual Momentum** | +2 | Both OR and recent momentum strongly aligned ($\|M\| \geq 40$) |
| 6 | **ZLEMA Trend** | +1 | Zero-Lag EMA crossover confirms momentum direction |

**Urgency:**

$$\text{Urgency} = \begin{cases} \text{⚡ ACT NOW} & E \geq 7 \\ \text{🔔 WATCH} & 4 \leq E < 7 \\ \text{⏳ WAIT} & E < 4 \end{cases}$$

**Strike Recommendation:** $E \geq 8$ → 2 OTM; $E \geq 6$ → 1 OTM; else ATM.

## 📊 Backtest Results (1yr, SPY + QQQ)

| Metric | SPY | QQQ | Combined |
|--------|-----|-----|----------|
| **Trading Days** | 251 | 251 | — |
| **Trades Taken** | 146 | 145 | 291 |
| **Win Rate** | **61.0%** | **54.5%** | ~57.8% |
| **Total P&L** | **+$43.54** | **+$33.04** | **+$76.57** |
| **Profit Factor** | **1.50** | **1.29** | ~1.39 |
| **Call WR** | 61.9% | 60.4% | — |
| **Put WR** | 58.5% | 42.9% | — |
| **Avg Winner** | $1.46 | $1.88 | — |
| **Avg Loser** | −$1.52 | −$1.74 | — |
| **Stop Rate** | 38.6% | 38.6% | — |
| **T1 Hit Rate** | 31.7% | 33.1% | — |
| **T2 Hit Rate** | 26.9% | 20.0% | — |

## 📖 Full Design Document

See [DESIGN_DOCUMENT.md](DESIGN_DOCUMENT.md) for the complete architecture, strategy details, risk management rules, and implementation plan.

## ⚠️ Disclaimer

This software is for educational purposes. Automated options trading involves significant financial risk. Always test in dry-run mode first. The authors are not responsible for any financial losses.
