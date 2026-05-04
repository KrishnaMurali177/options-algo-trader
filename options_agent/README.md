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
         ├── Choppiness Filter    (Kaufman CI + reversal rate + bar range + direction stability)
         ├── Strategy Selector    (auto-pick Buy Call vs Buy Put by quality score)
         ├── Risk Manager         (7 guardrails + circuit breaker)
         └── Streamlit Dashboard  (simulation UI with configurable chop filter slider)
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
6. Choppiness Filter    → Block triggers on choppy/whipsaw days (configurable threshold)
7. Strategy Construction→ Entry/stop/target levels from the active range
8. Risk Manager         → Validate against 7 guardrails before execution
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
- 🌊 **Filter choppy markets** with configurable choppiness threshold slider
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
│       ├── quality_scorer.py   # Shared 11-point quality scorer
│       └── choppiness.py       # Choppiness detection & direction stability
├── mcp_server/                 # Custom Robinhood MCP server (robin_stocks)
├── scripts/                    # CLI runners & backtest
│   ├── scan_sweet_spot_today.py # Sweet spot scanner with choppiness guardrails
│   ├── backtest_sweet_spot.py  # Sweet spot backtester with chop filter
│   ├── backtest.py             # General intraday backtester
│   ├── run_agent.py            # CLI agent runner
│   └── run_scheduled.py        # Scheduled execution (9:35, 12:00, 3:30 ET)
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

### Phase 5 — Choppiness Filter

The **Choppiness Filter** (`src/utils/choppiness.py`) prevents false sweet spot triggers on range-bound, whipsaw days where breakout signals are unreliable.

#### Choppiness Score $C \in [0, 10]$

$$C = \min\!\Big(10,\; C_{CI} + C_{rev} + C_{bar} + C_{streak}\Big)$$

| Component | Max | Detection Method |
|-----------|:---:|------------------|
| **Kaufman CI** ($C_{CI}$) | +3 | $CI = 1 - \frac{\|P_{last} - P_{first}\|}{\sum \|ΔP_i\|}$ — CI ≥ 0.70 → +3, ≥ 0.60 → +2, ≥ 0.55 → +1 |
| **Direction Reversals** ($C_{rev}$) | +3 | % of bars reversing prior bar direction — ≥ 60% → +3, ≥ 50% → +2, ≥ 45% → +1 |
| **Bar Range Ratio** ($C_{bar}$) | +2 | $\frac{\text{day range}}{\text{avg bar range}}$ — < 6 → +2, < 10 → +1 |
| **Max Streak** ($C_{streak}$) | +2 | Longest consecutive same-direction bars — ≤ 2 → +2, < 3 → +1 |

**Choppiness verdict:**

$$\text{Verdict} = \begin{cases} \text{🌊 EXTREMELY CHOPPY} & C \geq 8 \\ \text{🌊 CHOPPY} & 6 \leq C < 8 \\ \text{⚠️ MIXED} & 4 \leq C < 6 \\ \text{✅ TRENDING} & C < 4 \end{cases}$$

#### Direction Stability Gate

In addition to the choppiness score, the sweet spot scanner requires **direction stability** — the signal direction (BUY CALL / BUY PUT) must be consistent for ≥ 2 consecutive evaluation windows before a trigger fires. This prevents the "11:00 PUT → 11:15 CALL → 11:30 CALL" flip-flop pattern common on choppy days.

#### Dashboard Integration

The choppiness filter is exposed as a **configurable slider** in the sidebar:
- **0–5:** Strict — highest conviction only (1yr backtest: **91% WR**, PF 18.6)
- **6–7:** Moderate — balanced (79% WR, PF 5.9)
- **8–9:** Relaxed — more opportunities
- **10:** Disabled — all triggers pass

When a sweet spot trigger is blocked by choppiness, the dashboard shows an orange **"🌊🚫 SWEET SPOT BLOCKED — CHOPPY"** indicator explaining why.

#### Live Scanner Usage

```bash
# Scan today with choppiness guardrails (default: max-chop=5, min-stability=2)
python scripts/scan_sweet_spot_today.py

# Scan a specific date
python scripts/scan_sweet_spot_today.py --date 2026-04-30

# Adjust thresholds
python scripts/scan_sweet_spot_today.py --max-chop 7 --min-stability 3

# Disable choppiness filter (see raw triggers)
python scripts/scan_sweet_spot_today.py --no-chop-filter
```

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

### Sweet Spot Backtest (1yr SPY, Quality 4–7 + Explosion ≥ 4)

The **sweet spot filter** selects only trades where quality is in the optimal 4–7 range (not chasing) with cascade explosion ≥ 4. Adding the **choppiness filter** dramatically improves win rate and profit factor:

| Chop Filter | Trades | Win Rate | Avg P&L | Total P&L | Profit Factor |
|-------------|--------|----------|---------|-----------|---------------|
| **Off** (≤10) | 28 | 64.3% | $0.64 | $18.01 | 3.09 |
| **≤9** | 24 | 70.8% | $0.74 | $17.85 | 3.87 |
| **≤7** (moderate) | 19 | **78.9%** | $0.86 | $16.37 | **5.92** |
| **≤6** | 12 | **91.7%** | $1.10 | $13.20 | **20.70** |
| **≤5** (strict) | 11 | **90.9%** | $1.07 | $11.78 | **18.58** |

> **Note:** The backtester evaluates once per day (at opening range close), so trade counts are lower than the live scanner which evaluates every 15 minutes throughout the day. Live trading generates ~1–2 filtered triggers per day on average.

**Recommended settings:**
- **Live trading (scanner/dashboard):** max-chop = **5** (strict, ~91% WR)
- **Backtester:** max-chop = **7** (accounts for full-day choppiness measurement skew)

## 🤖 Sweet Spot Live Agent & Replay Testing

### Golden Parameters (validated via 3-year replay testing)

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Mode** | **0DTE Options** | Trades 0DTE ATM options by default (`--shares` for legacy share trading) |
| **Quality range** | **3–7** | Loosened from 4–7 — Q3 signals that pass cascade+chop filters are profitable |
| **Cascade (explosion) ≥** | 4 | Minimum momentum cascade score |
| **Max choppiness** | **5** | Strict chop filter — rejects noisy days |
| **Max trades per day** | **3** | Caps exposure; 4+ trades/day historically loses money |
| **Max stops per day** | **1** | Daily loss limit — halts after 1 stop-out to prevent catastrophic days |
| **Scan start** | **11:00 AM ET** | 90 min after open — balanced between early-entry noise and missed setups |
| **Scan end** | 2:00 PM ET | No late-day entries (theta drag on 0DTE) |
| **Entry confirmation** | Price in upper/lower 25% of OR range | Prevents entering from mid-range |
| **Cascade-scaled targets** | E≥8→1.5R, E≥6→1.25R, else 1.0R | Stronger momentum = wider target |
| **Cascade contract sizing** | **ON** — 2ct E4-5, 4ct E6-7, 6ct E8+ | Loads up on high-conviction setups |
| **Cooldown** | 3 bars (15 min) | Between consecutive triggers |
| **Stop** | Range midpoint | (range_high + range_low) / 2 |
| **Regime guard** | ON | Blocks counter-trend trades (SMA20 vs SMA50) |
| **GainzAlgoV2 early exit** | **ON** (RSI 60/40, body 0.5) | Closes position on opposing reversal candle — faster exits for 0DTE premium preservation |
| **Option delta** | **0.50** (ATM) | Target delta for 0DTE contract selection |
| **Base contracts** | **1** | Per trade (scaled by cascade tier) |

**3-Year Replay Results (SPY, 751 days, 0DTE Options):**

| Metric | Value |
|--------|-------|
| Triggers | 506 (0.7/day) |
| Win Rate | **55.3%** |
| Profit Factor | **3.29** |
| Total P&L (cascade-sized) | +$256.36 |
| Total Option P&L (per contract, ×100) | **+$10,722** |
| Total Option P&L (cascade-sized, ×100) | **+$25,636** |
| Avg P&L/Trade | +$0.51 |
| Avg Winner | +$1.32 |
| Avg Loser | −$0.50 |
| R:R Ratio | 2.65 |
| Sharpe Ratio (annualized) | **3.74** |
| Sortino Ratio | **12.62** |
| Max Drawdown | $7.85 (3.1% of peak) |
| Calmar Ratio | **32.64** |
| Longest Underwater | 51 days |

> **Note:** Option P&L uses a delta-gamma approximation with 0DTE theta decay modeling.
> Cascade sizing applies 2/4/6 contracts for E4-5/E6-7/E8+ tiers respectively.

### Replay Sweet Spot (historical simulation)

Replays the agent logic bar-by-bar on recent 5-min data — the most realistic test short of live paper trading.

```bash
cd options_agent

# Golden parameters (recommended) — 0DTE options with cascade sizing (all defaults)
python scripts/replay_sweet_spot.py --days 365

# Disable cascade sizing (flat 1 contract per trade)
python scripts/replay_sweet_spot.py --days 365 --no-cascade-sizing

# Legacy share mode (no options P&L modeling)
python scripts/replay_sweet_spot.py --days 365 --shares

# Last 30 trading days (default, uses Alpaca 5-min data)
python scripts/replay_sweet_spot.py --days 30

# Specific symbol
python scripts/replay_sweet_spot.py --symbol QQQ --days 365

# Sweep Gainz thresholds across 25 RSI/body combos (finds the sweet zone)
python scripts/sweep_gainz_thresholds.py --days 365
```

Output includes: win rate, profit factor, total P&L, exit breakdown, and a full trade log with entry/exit times, quality/explosion/chop scores, and per-trade P&L.

### Live Sweet Spot Agent (paper trading)

Runs autonomously during market hours, scanning every 5 minutes and placing bracket orders on your Alpaca paper account.

```bash
cd options_agent

# Single day run (golden parameters — 0DTE options, cascade sizing, Gainz exit ON)
python scripts/run_sweet_spot_agent.py

# Daemon mode (restarts daily, runs Mon–Fri)
python scripts/run_sweet_spot_agent.py --daemon

# Multiple base contracts (cascade sizing will scale: 2ct base → 2/4/6 by tier)
python scripts/run_sweet_spot_agent.py --daemon --contracts 2

# Legacy share trading mode
python scripts/run_sweet_spot_agent.py --shares --qty 10

# Override defaults
python scripts/run_sweet_spot_agent.py --daemon --max-chop 5 --max-trades-per-day 3 \
  --max-stops-per-day 1 --scan-start-min 90

# Disable Gainz early exit (revert to baseline behavior)
python scripts/run_sweet_spot_agent.py --no-gainz-exit

# Tune Gainz thresholds
python scripts/run_sweet_spot_agent.py --gainz-rsi-overbought 65 --gainz-rsi-oversold 35 \
  --gainz-body-ratio 0.6

# Journal-only mode (no paper orders, just logs triggers)
python scripts/run_sweet_spot_agent.py --no-paper
```

**GainzAlgoV2 early-exit behavior:** When enabled (default), the agent monitors
each open paper position every 5 minutes. If the most recently completed bar
prints an opposing reversal candle (RSI extreme + strong-bodied candle in the
opposite direction), the agent calls `close_position(symbol)` to exit
immediately, cancelling the bracket. Monitoring continues past the 14:00
entry cutoff until all positions close.

**Requirements:**
- `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` in `.env` (free paper account)
- Logs to `logs/sweet_spot_agent.log`
- Journal files saved to `sweet_spot_journal/YYYY-MM-DD.json`

### Sweet Spot Tracker (manual observation)

For watching sweet spot triggers in real-time without placing trades:

```bash
# Live tracking during market hours
python scripts/track_sweet_spots.py --max-chop 10

# Review today's triggers at EOD
python scripts/track_sweet_spots.py --review

# Review a specific date
python scripts/track_sweet_spots.py --review-date 2026-05-01

# Show all historical results
python scripts/track_sweet_spots.py --history
```

## 📖 Full Design Document

See [DESIGN_DOCUMENT.md](DESIGN_DOCUMENT.md) for the complete architecture, strategy details, risk management rules, and implementation plan.

## ⚠️ Disclaimer

This software is for educational purposes. Automated options trading involves significant financial risk. Always test in dry-run mode first. The authors are not responsible for any financial losses.
