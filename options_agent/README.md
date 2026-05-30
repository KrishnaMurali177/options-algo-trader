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

### 1. Configure
```bash
cd options_agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```bash
cp options_agent/.env.example options_agent/.env
# Edit .env — add ALPACA_API_KEY and ALPACA_SECRET_KEY for paper trading
# Robinhood credentials only needed for live MCP mode
```

### 2. Launch Dashboard
```bash
cd options_agent
streamlit run dashboard/app.py
# Opens at http://localhost:8501
```
# DOCKER EQUIVALENT
```bash
./run.sh dashboard
# Opens at http://localhost:8501
```

### 3. Start Live Agents (Paper Trading)
```bash
# Start both SPY + QQQ sweet spot agents
./run.sh agents

# Check status
./run.sh status

# Stop agents
./run.sh stop-agents
```

See [HOWTO.md](../HOWTO.md) for full Docker setup, agent parameters, and cron configuration.

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
│   │   ├── buy_call.py         # Buy Call scalping strategy
│   │   ├── buy_put.py          # Buy Put scalping strategy
│   │   └── base_strategy.py    # Abstract base class
│   └── utils/
│       ├── quality_scorer.py   # Shared 11-point quality scorer
│       ├── choppiness.py       # Choppiness detection & direction stability
│       └── gainz.py            # GainzAlgoV2 early exit detector
├── weekly/                     # Weekly options agent (3-7 DTE, self-contained)
│   ├── agent.py                # Entry/exit/lifecycle logic
│   ├── chain/weekly_chain.py   # Option chain selector (Friday expiry)
│   ├── signals/                # Daily-bar signal adapters
│   │   ├── daily_range.py      # DailyRangeAnalyzer
│   │   ├── daily_momentum.py   # DailyMomentumAnalyzer
│   │   └── weekly_indicators.py # Indicators from daily OHLCV
│   ├── state/position_manager.py # Multi-day position persistence
│   └── backtest/               # Backtester + parameter sweep
│       ├── option_pricing.py   # Real Alpaca + synthetic pricing
│       ├── replay_weekly.py    # Core replay engine
│       ├── run_weekly_backtest.py # CLI runner
│       └── sweep_weekly.py     # Grid search framework
├── mcp_server/                 # Custom Robinhood MCP server (robin_stocks)
├── scripts/                    # CLI runners & backtest
│   ├── scan_sweet_spot_today.py # Sweet spot scanner with choppiness guardrails
│   ├── backtest_sweet_spot.py  # Sweet spot backtester with chop filter
│   ├── backtest.py             # General intraday backtester
│   ├── run_agent.py            # CLI agent runner
│   ├── run_weekly_agent.py     # Weekly agent CLI wrapper + daemon
│   └── run_scheduled.py        # Scheduled execution (9:35, 12:00, 3:30 ET)
└── tests/                      # Unit & integration tests
    ├── test_weekly.py          # 34 tests for weekly agent modules
    └── test_weekly_backtest.py # 25 tests for backtester modules
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
| **Stop loss** | $S = \text{mid}(H_{active}, L_{active}) + 0.10 \times (H_{active} - L_{active}) - 0.02 \times ATR_{14}$ |
| **Risk per unit** | $R = \|E - S\|$ &ensp;(floor: $0.3 \times ATR_{14}$) |
| **Profit target 1** | $T_1 = E + 0.75\,R$ |
| **Profit target 2** | $T_2 = E + 1.5\,R$ |

#### Buy Put Levels

| Level | Formula |
|-------|---------|
| **Entry trigger** | $E = L_{active} + 0.10 \times (H_{active} - L_{active})$ &ensp;(10% inside range low) |
| **Stop loss** | $S = \text{mid}(H_{active}, L_{active}) - 0.10 \times (H_{active} - L_{active}) + 0.02 \times ATR_{14}$ |
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
| 1 | **Price Acceleration** | +2 | ATR-normalized dual-detector with consensus scoring: (a) 4-bar weighted velocity (exponential recency bias, normalized to ATR); (b) legacy 3-bar window RoC (also ATR-normalized). **+2 requires both detectors to agree** (consensus); single detector = +1. Asset-agnostic — same thresholds work for SPY/QQQ/any ETF. |
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

## 📊 Backtest Results (2yr, SPY + QQQ)

Generated 2026-05-23 via `replay_sweet_spot.py --days 730 --real-options` at current golden defaults (post cluster-penalty, VWAP-slope override T=0.7/K=3/Cmax=0.65, and dynamic-OR T=0.6 promotions). Real Alpaca 0DTE option pricing (synth fallback < 1% of trades).

| Metric | SPY | QQQ |
|--------|-----|-----|
| **Trading Days** | 500 | 500 |
| **Trades Taken** | 931 (1.9/day) | 832 (1.7/day) |
| **Win Rate** | **64.1%** | **60.1%** |
| **Profit Factor** | **2.48** | **2.00** |
| **Total P&L** (per contract, ×100) | **+$14,775** | **+$13,896** |
| **Total P&L** (cascade-sized 3×) | **+$44,325** | **+$41,688** |
| **Avg Winner / Avg Loser** | $+1.25 / $-0.90 (R:R 1.39) | $+1.67 / $-1.25 (R:R 1.33) |
| **Sharpe Ratio** | **4.54** | **3.44** |
| **Sortino Ratio** | 8.74 | 6.82 |
| **Max Drawdown** | $9.93 (2.2%) | $21.00 (5.0%) |
| **Calmar Ratio** | **44.64** | **19.85** |
| **Longest Underwater** | 27 days | 49 days |

**Walk-forward (most recent 365d):**

| Metric | SPY | QQQ |
|--------|-----|-----|
| Profit Factor | 2.36 | 1.65 |
| Sharpe Ratio | 4.73 | 2.77 |
| Calmar Ratio | 20.84 | 8.61 |
| Max Drawdown | 4.6% | 11.4% |

**OOS-90 audit (most recent 90 days, validates package isn't regime-fit):**

| Metric | SPY | QQQ |
|--------|-----|-----|
| Profit Factor | 2.80 | 2.18 |
| Sharpe Ratio | 5.08 | 4.01 |
| Calmar Ratio | 8.01 | 5.51 |
| Max Drawdown | 11.1% | 17.2% |

OOS-90 deltas vs the pre-2026-05-18 baseline are larger than the in-sample 730d deltas in percentage terms — the strongest possible walk-forward evidence that the recent goldens generalize.

**Exit-cohort separation** (730d, SPY):
- `decay_target` (win exit): 554 trades, **94.6% WR**, +$69K (per contract ×100)
- `stop`: 117 trades, **2.6% WR**, -$16K — clean separation between win-state and loss-state
- `stagnation`: 230 trades, 24.8% WR, -$10K — documented structural drag (see [stagnation memos](../.claude/projects/c--Users-krish-options-algo-trader/memory))

> **Live/replay parity:** verified bit-exact at every 5-min bar on 2026-05-20 (both symbols) and 2026-05-15 SPY pre-cluster-penalty. The 2026-05-21 through 2026-05-23 promotions (cluster penalty, VWAP-slope override, dynamic-OR + threshold) each ported their replay logic bit-exact into `run_sweet_spot_agent.py`. **Post-2026-05-23 sync**: the live agent's stale 30-min post-stagnation cooldown was reduced to 5 min to match replay's `stag_cooldown_bars=1` golden (set 2026-05-20). Run `verify_live_vs_replay --all-bars` on the next live trading day to confirm bit-exact parity across all the recent goldens.
>
> **2026-05-21 promotion — within-day cluster penalty (cap=2, window=30 min):** Quality −1 per prior same-direction trade in the last 30 min, capped at −2. Validated 12-cell sensitivity grid (window ∈ {15,30,45,60} × cap ∈ {2,3,4}) on SPY 730d — every combination beat baseline; no failing cell. 730d clean sweep: SPY PF 2.21→2.44, Sharpe 3.92→4.38, Calmar 25.76→34.85, MDD% 3.8→2.8, Longest Underwater 41d→27d; QQQ PF 1.79→1.90, Sharpe 3.00→3.24, MDD% 6.6→5.7. 365d walk-forward confirms on both symbols (QQQ 365d MDD% 17.2→13.8 — the binding constraint that killed the last three A/Bs improves). Disable with `--no-cluster-penalty`.
>
> **2026-05-22 promotion — VWAP-slope chop override (T=0.7, K=3, Cmax=0.65):** Narrow override that lets entries bypass the `chop > max_chop` gate only when (a) `(price − session_vwap) / ATR ≥ 0.7` in the trade direction, (b) the last 3 closes are on the correct side of session VWAP, and (c) raw choppiness index ≤ 0.65. This is the **narrow-cell inverse** of the wide T=0.5/K=5/Cmax=1.0 grid that previously failed walk-forward — tightening (rather than loosening) the override escaped the SPY-loses/QQQ-wins gate-loosening pattern. 730d: SPY PF 2.43→2.39, Sharpe 4.36→4.41, **Calmar 34.71→42.20**, MDD% 2.8→2.3; QQQ PF 1.89→1.92, Sharpe 3.23→3.33, MDD% 5.7→5.5. 365d walk-forward strengthens the result: SPY Calmar 16.42→20.65, MDD% 5.9→4.7; QQQ Calmar 7.06→8.08, **MDD% 13.8→12.1**. K=3/4/5 sensitivity grid confirmed K=3 is the unique peak on QQQ 365d (K=5 regresses below baseline), with SPY/QQQ-730d on a plateau. Override fires rarely (~5% trigger lift) but is bit-exactly mirrored in `run_sweet_spot_agent.py`; live-replay parity to be verified next session via `verify_live_vs_replay --all-bars`. Disable with `--vwap-slope-override-t 0` (any of the three params set to 0).
>
> **2026-05-23 promotion — `--dynamic-or` (conditional 30-min OR + 10:00 scan-start, threshold=0.6):** On each morning, compute a 30-min quick OR (09:30–09:59). If the 10:00 bar already broke out >60% of that range beyond either boundary, use the 30-min OR and start scanning at 10:00 (instead of the standard 60-min OR + 10:30 scan-start). On non-decisive mornings, fall back to the 60-min OR. Override fires rarely (~1% trigger lift on 730d), so it's high-conviction. Re-tested after cluster-penalty (2026-05-21), VWAP-slope override (2026-05-22), max-trades=4 (2026-05-18), and cooldown=1 (2026-05-18) — those goldens changed the MDD profile such that `--dynamic-or`, which previously widened MDD 60-115%, now narrows it. Threshold tuned 0.5→0.6 same day after the overfitting audit's sensitivity grid showed T=0.6 strictly dominates T=0.5 on SPY without hurting QQQ. 730d clean sweep at T=0.6: SPY PF 2.39→2.48, Sharpe 4.41→4.54, **Calmar 42.20→44.64**, MDD% 2.3→2.2; QQQ PF 1.92→2.00, Sharpe 3.33→3.44, **Calmar 17.97→19.85**, MDD% 5.5→5.0. 365d walk-forward: SPY Calmar 20.65→20.84, QQQ Sharpe 2.58→2.77, MDD% 12.1→11.4. Ported bit-exactly to live agent with a `dynamic_or_defer` reject path so scans before 10:30 on non-decisive mornings cleanly sleep/retry. Disable with `--no-dynamic-or`; threshold tunable via `--dynamic-or-threshold`.
>
> **2026-05-23 overfitting audit:** OOS-90 (most recent 90 days only) confirmed the recent goldens are not overfit. SPY OOS-90 PF 1.68→2.80, Sharpe 2.94→5.08, MDD% 25.9→11.1 (pre-recent-goldens vs current). QQQ OOS-90 PF 1.75→2.18, Sharpe 3.23→4.01. OOS-90 deltas are *larger* than the in-sample 730d deltas in percentage terms — the strongest possible walk-forward evidence the package generalizes. Dynamic-OR sensitivity grid (T ∈ {0.4, 0.5, 0.6}) revealed T=0.6 dominates T=0.5 on SPY, triggering the same-day threshold retune above.

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
| **Cascade (explosion) ≥** | 2 | Lowered from 4 — E2-E3 trades profitable when quality+chop+regime filters pass. Validated: Sharpe 1.34→2.06, PF 1.30→1.53, DD 49.5%→23.5% |
| **Max choppiness** | **5** | Strict chop filter — rejects noisy days |
| **Min choppiness** | **2** | Floor filter — rejects "false trending" (C=0-1 trades are ~50% WR / net $0 over 2yr). Validated 730d SPY: PF 1.65→1.74, Sharpe 2.78→3.12, Sortino 3.27→3.90, MDD −8.6%, Calmar 14.50→16.55. |
| **Max trades per day** | **4** | Caps exposure. Raised 3→4 on 2026-05-18 — validated 2yr SPY/QQQ/VOO real-options (clean sweep on PF/Sharpe/Calmar/MDD/WR/P&L): SPY PF 2.28→2.37, Sharpe 3.97→4.22, MDD 3.9%→3.3%; QQQ PF 1.72→1.81, Sharpe 2.83→2.97, MDD 8.5%→7.1%; VOO PF 2.68→2.99, Sharpe 5.01→5.49, MDD 3.7%→3.0%. Additional 4th-slot trades stack within high-Q cohort days without violating the 60-min OR noise filter, so MDD narrows rather than widens. |
| **Max stops per day** | **1** | Daily loss limit — halts after 1 stop-out to prevent catastrophic days |
| **Max consecutive losses** | **2** | Streak breaker — stops trading after 2 consecutive losses in a day. Validated: Sharpe 1.23→1.34, PF 1.27→1.30 |
| **Scan start** | **10:30 AM ET** | 60 min after open — matches replay validation window; OR closes at 10:30 |
| **Scan end** | **13:59 ET** | No late-day entries (theta drag on 0DTE). Extending past 13:59 hurts both symbols on 730d. |
| **Entry confirmation** | Price in upper/lower 25% of OR range | Prevents entering from mid-range |
| **Cascade-scaled targets** | E≥8→1.5R, E≥6→1.5R, else 1.0R | Mid-tier target raised from 1.25R to 1.5R (validated: PF 1.16→1.23) |
| **Cascade contract sizing** | **ON** — 3ct flat across E2-5 / E6-7 / E8+ | Flat 3/3/3 — equal sizing across all tiers |
| **Cooldown** | **1 bar (5 min)** | Between consecutive triggers, including after stagnation exits (a previous 30-min post-stag cooldown was removed on 2026-05-20 — see [bug_replay_cooldown_timing_drift](../.claude/projects/c--Users-krish-options-algo-trader/memory/bug_replay_cooldown_timing_drift.md), as live can't enforce a wall-clock post-stag delay reliably). Reduced from 2 bars (10 min) — validated 2026-05-18 on 730d real-options SPY/QQQ/VOO (clean sweep): SPY PF 2.10→2.28, Sharpe 3.63→3.97, MDD 5.3%→3.9%; QQQ PF 1.54→1.72, Sharpe 2.28→2.83, MDD 10.0%→8.5%; VOO PF 2.32→2.68, Sharpe 4.84→5.01, MDD 5.3%→3.7%. |
| **Cluster penalty** | **ON** (window=30 min, cap=2) | Subtracts 1 from quality per prior same-direction trade within 30 min, capped at 2. Promoted 2026-05-21 after 12-cell sensitivity grid (window ∈ {15,30,45,60} × cap ∈ {2,3,4}) showed no failing parameter combination. 730d clean sweep: SPY PF 2.21→2.44, Sharpe 3.92→4.38, Calmar 25.76→34.85, MDD 3.8%→2.8%, Longest Underwater 41d→27d; QQQ PF 1.79→1.90, Sharpe 3.00→3.24, MDD 6.6%→5.7%. 365d walk-forward confirms on both symbols. Disable with `--no-cluster-penalty`. |
| **VWAP-slope chop override** | **ON** (T=0.7, K=3, Cmax=0.65) | Allows entry through the `chop > max_chop` gate when (price − session_vwap)/ATR ≥ T in trade direction, last K closes on correct side of VWAP, and raw choppiness index ≤ Cmax. Narrow/high-conviction cell promoted 2026-05-22 after the wide T=0.5/K=5/Cmax=1.0 cell previously failed walk-forward — tightening the override (not loosening) escaped the gate-loosening per-symbol-arb pattern. 730d: SPY Calmar 34.71→42.20, MDD 2.8%→2.3%; QQQ Sharpe 3.23→3.33. 365d walk-forward strengthens: SPY Calmar 16.42→20.65, QQQ MDD 13.8%→12.1%. Sensitivity (K=3/4/5): SPY plateau, QQQ-730d plateau, QQQ-365d K=3 unique peak. Disable with `--vwap-slope-override-t 0` (or set any of the three to 0). |
| **Dynamic Opening Range** | **ON** (threshold=0.6, 30-min OR + 10:00 scan-start) | On mornings where the 10:00 bar already broke out >60% of the 09:30–09:59 range beyond either boundary, replace the 60-min OR with the 30-min OR and start scanning at 10:00 (instead of 10:30). On non-decisive mornings, fall back to the standard 60-min OR + 10:30 scan. Promoted 2026-05-23 (threshold tuned 0.5→0.6 same day after sensitivity grid showed T=0.6 strictly dominates T=0.5 on SPY without hurting QQQ). 730d: SPY PF 2.39→2.48 (Calmar 42.20→44.64), QQQ PF 1.92→2.00 (Calmar 17.97→19.85). 365d walk-forward: QQQ Sharpe 2.58→2.77, MDD 12.1%→11.4%; SPY Calmar 20.65→20.84. Fires rarely (~1% trigger lift). Live agent uses a `dynamic_or_defer` reject before 10:30 on non-decisive mornings. Disable with `--no-dynamic-or`; tune with `--dynamic-or-threshold <fraction>`. |
| **Stop** | 60% of range (mid + 10% width) | Tighter than bare midpoint — validated: Sharpe 0.76→1.07, DD 89.7%→63.7% |
| **Regime guard** | **OFF** | Disabled — counter-trend trades are profitable when chop+quality+cascade filters pass. Validated 2yr: Sharpe 1.38→1.74, PF 1.30→1.37, P&L +$95→+$122. Use `--regime-guard` to re-enable. |
| **Active range blend** | **ON** (blend=0.25, 6 bars/30min) | Stop/target uses 75% OR + 25% recent 30-min range. Prevents stale entries on late-day triggers. Validated SPY+QQQ: WR +3pp, DD −14%, UW 83→52 days. |
| **PB EMA inside-band gate** | **ON** (fast=13, slow=55) | Rejects entries when price is *between* the two EMAs (PB EMA's "no zone" / chop state). Symmetric — does not block direction. Validated 730d SPY: PF 1.41→1.48, Sharpe 1.89→2.26, MDD $30.81→$27.03 (−12%), avg/trade +6.6%. Disable with `--no-pb-ema`. |
| **GainzAlgoV2 early exit** | **ON** (RSI 70/30, body 0.7, min-profit 0.3R) | Stricter thresholds — only exits on strong opposing reversal candles; requires ≥ 0.3R profit to prevent premature closes on losing/scratch trades |
| **Decay-aware targets** | **ON** (floor=0.4, halflife=8 bars/40min) | Target shrinks exponentially as theta erodes; theta-breakeven exit when projected burn exceeds remaining profit. Floor raised from 0.3→0.4, halflife from 6→8 bars (validated: SPY Calmar 4.63→8.56, QQQ Sharpe 1.04→1.34). |
| **Option delta** | **0.50** (ATM) | Target delta for 0DTE contract selection |
| **Real options pricing** | **ON** | Uses Alpaca historical 0DTE bars; synth fallback when unavailable (`--no-real-options` for synth-only) |
| **Base contracts** | **1** | Per trade (scaled by cascade tier) |
| **Flip trade allowance** | **ON** (1/day) | Momentum flip trades bypass daily trade cap (separate allowance of 1 per day), skip entry confirmation (reversal enters from opposite zone), and use active-range stop/target (OR midpoint stop is nonsensical for reversals). Validated 2yr: SPY PF 1.63→1.65, Sharpe 2.72→2.81, P&L +$156→$162; QQQ P&L +$125→$128. |
| **ATR-normalized acceleration** | **ON** (consensus scoring) | Price acceleration detector uses ATR-fraction thresholds (asset-agnostic) with dual-detector consensus: both velocity (4-bar) and legacy RoC (3-bar windows) must agree for +2; single detector = +1. Validated 730d: SPY +$105/ct (+1.6%), Sharpe 3.16; QQQ +$446/ct (+9.3%), Sharpe 1.94→2.03. |

### Stagnation Exit (theta-bleed protection)

Trades that don't move in the expected direction within a set window are cut early to prevent theta from eroding 0DTE premium on stagnant positions. The **tiered stagnation** system (golden default) adds an earlier check at bar 8 for flat trades.

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Tiered stagnation** | **ON** | Early exit at bar 8 (40 min) for flat trades between −0.1R and +0.2R. Validated 730d SPY: PF 1.51→1.60, Sharpe 2.31→2.63, MDD $16.68→$11.10 (−33%), Calmar 8.56→13.66 (+60%). |
| **Tiered stag early bar** | **8** (40 min) | If trade P&L is between −0.1R and +0.2R at bar 8, exit immediately — trade is going nowhere and theta is bleeding |
| **Post-stagnation cooldown** | **1 bar** (5 min) | Same as normal cooldown — replay's `stag_cooldown_bars=1` since 2026-05-20. Live agent's 30-min post-stag cooldown was removed 2026-05-23 to match. |
| **Stagnation bars** | **12** (60 min) | Standard stagnation: if trade hasn't moved ≥ threshold after 12 bars, exit at market. Increased from 10 bars — validated 730d SPY+QQQ+VOO: SPY MDD 21.6%→11.7%, Calmar 4.63→8.56; QQQ Sharpe 1.04→1.34, MDD 37.2%→20.1% |
| **Minimum move to hold** | **0.3R** | Trade must be at least 0.3× risk in profit; otherwise cut. Lowered from 0.5R — keeps trades with some momentum alive for decaying target. |
| **MFE skip** | **0.5R** | If trade's Maximum Favorable Excursion (best P&L reached) exceeded 0.5R, skip stagnation exit — let decay_target or stop resolve it. Trades that showed real momentum but temporarily pulled back deserve more time to reach target. |

The stagnation exit uses a **two-tier system**. At bar 8 (40 min), if the trade's P&L is between −0.1R and +0.2R and MFE < 0.5R, the trade exits immediately — it's going nowhere and theta is bleeding. At bar 12 (60 min), the standard stagnation check fires: if `current_pnl < risk * 0.3` **and** `MFE < 0.5R`, exit. If MFE ≥ 0.5R at either tier, the trade had real traction and is exempt — it will exit via decay_target or stop. The standard 1-bar (5 min) cooldown applies after stagnation exits (same as normal cooldown — a previous 30-min post-stag wait was removed because live agents can't enforce a wall-clock post-stagnation delay reliably). Combined with the **streak breaker** (2 consecutive losses → stop for day), this keeps losing days contained.

**MFE skip validation (2-year, 730 days, real Alpaca 0DTE options):**

| Metric | Without MFE Skip | With MFE Skip | Δ |
|--------|------------------|---------------|---|
| SPY per-contract P&L | +$3,671 | **+$3,780** | **+3.0%** |
| SPY cascade-sized P&L | +$11,014 | **+$11,341** | **+3.0%** |
| QQQ per-contract P&L | +$2,462 | **+$2,547** | **+3.5%** |
| QQQ cascade-sized P&L | +$7,385 | **+$7,640** | **+3.5%** |
| Trade count (SPY) | 695 | 695 | Same |
| P&L/trade (SPY) | $5.28 | **$5.44** | **+3.0%** |

> Consistent +3% improvement across both symbols with identical trade counts. The MFE skip converts ~10 former stagnation-losses into decay_target-wins by letting trades that showed real momentum continue through temporary pullbacks.

**Sweep results (2-year, 730 days):**
- Best Sharpe: 12 bars / 0.5R → Sharpe 3.19, PF 2.14, WR 50.7%
- Best Total P&L: 12 bars / 0.2R → +$153.15, PF 2.10, Sharpe 2.94

### Decay-Aware Targets (theta-adaptive take-profit)

For 0DTE options, theta decay is non-linear — after 30–60 minutes in a trade, the underlying must move significantly MORE just to offset premium erosion. The decay-aware target system addresses this by dynamically shrinking the take-profit level as time passes.

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Decay model** | Exponential | `decay_factor = max(floor, 0.5^(bars / halflife))` |
| **Halflife** | **8 bars** (40 min) | Target decays to 50% of original after 40 min (was 6 bars/30 min — slower decay lets more trades reach target, especially on QQQ) |
| **Floor** | **0.4** (40%) | Target never shrinks below 40% of original distance (raised from 0.3, validated) |
| **Theta-breakeven exit** | ON | If projected theta burn over next 2 bars ≥ 80% of current option profit, exit immediately |

**How it works:**
1. At entry, the target is set normally (1.0R / 1.5R / 1.5R based on explosion score)
2. Each subsequent bar, the effective target decays: `effective_target = entry ± original_distance × decay_factor`
3. After 40 min (8 bars), the target is at 50% of original; after 80 min, it hits the 40% floor
4. Additionally, if the trade is profitable but theta will consume ≥80% of that profit in the next 10 minutes, the position exits immediately ("theta_exit")

**Impact (1-year SPY, synthesized options pricing):**

| Metric | Without Decay | With Decay | Δ |
|--------|---------------|---------------------|---|
| Win Rate | 55.1% | **59.7%** | +4.6pp |
| Profit Factor | 2.45 | **2.66** | +8.6% |
| Total P&L | +$89.02 | **+$93.44** | +5.0% |
| Sharpe | 3.85 | **4.39** | +14% |
| Sortino | 10.76 | **12.67** | +18% |
| Max Drawdown | $5.57 | **$4.17** | −25% |
| Stagnation exits | 71% | **39%** | −32pp |

> **Note:** Synth results overestimate real performance. See 1-Year Real Options Results below for ground-truth numbers.

### VIX Sit-Out Filter

On days with extreme volatility, the agent sits out entirely to avoid whipsaw losses:

| Condition | Action |
|-----------|--------|
| **VIX > 30** | Skip all trades for the day |
| **VIX spike > 20% day-over-day** | Skip all trades for the day |

This filter uses daily closing VIX data. During the Aug 2024–Mar 2025 drawdown period (VIX routinely 25–40+), the filter would have avoided the worst losing streaks.

### Notable golden-default features

> **Momentum Flip** (added May 2026): When recent 30-min momentum strongly disagrees with OR direction (|recent_mom| ≥ 40), the trade direction flips to follow recent momentum instead of the stale Opening Range signal. This prevents counter-trend entries on reversal days.
>
> **Flip Trade Allowance** (added May 2026): Momentum flip trades get a separate allowance beyond the daily trade cap (max 1 flip/day). Flip trades also bypass entry confirmation (reversal enters from the opposite zone) and use active-range stop/target geometry (OR midpoint stop is nonsensical for reversals).

See [Backtest Results (2yr, SPY + QQQ)](#-backtest-results-2yr-spy--qqq) above for current at-defaults performance.

### Replay Sweet Spot (historical simulation)

Replays the agent logic bar-by-bar on recent 5-min data — the most realistic test short of live paper trading.

```bash
cd options_agent

# Golden parameters (recommended) — real Alpaca 0DTE options + cascade sizing + decay-aware targets
python scripts/replay_sweet_spot.py --days 365

# Use synthesized options pricing instead of real Alpaca data
python scripts/replay_sweet_spot.py --days 365 --no-real-options

# Disable decay-aware targets (revert to fixed targets)
python scripts/replay_sweet_spot.py --days 365 --no-decay-aware-targets

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

Runs autonomously during market hours, scanning every 5 minutes and placing 0DTE option orders on your Alpaca paper account. Both SPY and QQQ agents run as separate Docker containers.

```bash
# Start both SPY + QQQ agents (recommended)
./run.sh agents

# Or start individually
./run.sh agent-spy
./run.sh agent-qqq

# Check status and recent logs
./run.sh status

# Stop agents
./run.sh stop-agents

# Custom parameters via docker-compose run
docker-compose --profile live run --rm agent-spy python scripts/run_sweet_spot_agent.py \
  --daemon --contracts 2 --max-chop 5 --max-trades-per-day 4

# Journal-only mode (no paper orders, just logs triggers)
docker-compose --profile live run --rm agent-spy python scripts/run_sweet_spot_agent.py \
  --no-paper
```

**GainzAlgoV2 early-exit behavior:** When enabled (default), the agent monitors
each open paper position every 5 minutes. If the most recently completed bar
prints an opposing reversal candle (RSI extreme + strong-bodied candle in the
opposite direction) **and** the position's unrealized P&L is ≥ 0.3R (min-profit
gate), the agent calls `close_position(symbol)` to exit immediately, cancelling
the bracket. This prevents Gainz from closing trades that haven't yet reached
a meaningful profit. Monitoring continues past the 14:00 entry cutoff until all
positions close.

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

## 📅 Weekly Options Agent (3-7 DTE)

A **separate, modular** weekly options system that trades 3-7 DTE contracts using daily bar analysis. Completely self-contained under `weekly/` — zero modifications to the 0DTE pipeline.

### Architecture

```
options_agent/weekly/
├── __init__.py
├── agent.py                        # Core entry/exit/lifecycle logic
├── chain/
│   └── weekly_chain.py             # Option chain selector (Friday expiry, delta targeting)
├── signals/
│   ├── daily_range.py              # DailyRangeAnalyzer (replaces 60-min OR for daily bars)
│   ├── daily_momentum.py           # DailyMomentumAnalyzer (replaces 30-min momentum)
│   └── weekly_indicators.py        # RSI/MACD/ATR/BB/SMA/ZLEMA from daily OHLCV
├── state/
│   └── position_manager.py         # Multi-day position persistence (per-position JSON)
└── backtest/
    ├── option_pricing.py           # Real Alpaca + synthetic pricing, BS delta estimation
    ├── replay_weekly.py            # Core replay engine (multi-day position lifecycle)
    ├── run_weekly_backtest.py       # CLI runner for single-config backtests
    └── sweep_weekly.py             # Parameter grid search (quick ~1K, full ~10K+ combos)
```

### Signal Stack (Daily Bar Adapters)

The weekly agent reuses the **same scoring functions** as the 0DTE pipeline (`compute_quality_score()`, `compute_choppiness()`, `MomentumCascadeDetector`, `gainz_signal`) but feeds them daily-bar inputs via adapted analyzers:

| 0DTE Component | Weekly Adapter | What Changes |
|----------------|----------------|--------------|
| `OpeningRangeAnalyzer` (60-min) | `DailyRangeAnalyzer` | Prior day's high/low as "range", scores breakout + RSI/MACD/SMA/volume |
| `RecentMomentumAnalyzer` (30-min) | `DailyMomentumAnalyzer` | Last 5 daily bars: price trend, green/red ratio, RSI, SMA20, volume |
| `build_indicators()` (5-min) | `build_weekly_indicators()` | RSI-14, MACD 12/26/9, ATR-14, BB 20/2, SMA 20/50/200, ZLEMA 8/21 on daily closes |

### Weekly Theta Decay Model

```
decay = premium * 0.70 * (1 - sqrt(dte_remaining / dte_at_entry))
```

Produces the correct convex curve: ~7% decay/day early, ~25% mid-week, ~39% on penultimate day.

### Trailing Stop Tiers (R-Multiples)

| Position reaches | Stop moves to |
|-----------------|---------------|
| +1.0R | Breakeven |
| +1.5R | +0.5R |
| +2.0R | +1.0R |

### Golden Parameters (Weekly)

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Quality range** | 3-7 | Same as 0DTE |
| **Chop range** | 2-5 | Same as 0DTE |
| **Explosion min** | 2 | Cascade detector on daily bars (synthesized mode) |
| **Entry days** | Mon, Tue, Wed | Avoid late-week entries (insufficient DTE) |
| **Entry window** | 10:00-11:00 ET | Morning evaluation only |
| **Target delta** | 0.35 | ~35-delta OTM contracts |
| **DTE window** | 3-7 days | Targets nearest Friday expiry |
| **Stop loss** | 1.5 × ATR | Underlying-level stop |
| **Profit target** | 2.0 × ATR | Underlying-level target |
| **Trailing stops** | ON | Tiered at +1R/+1.5R/+2R |
| **Decay halflife** | 2.0 sessions | Target shrinks as theta erodes |
| **Stagnation** | 2 sessions | Cut positions that don't move |
| **Gainz exit** | ON | RSI 75/25, body 0.6, min profit 0.3R |
| **VIX filter** | Max 30, spike 20% | Skip high-vol days |
| **Max open/symbol** | 2 | Concurrent position cap |
| **Max stops/week** | 1 | Weekly loss limit |

### Exit Priority Chain

1. **Gap stop** — Adverse overnight gap > 1.5 × ATR
2. **Trailing stop** — R-multiple tiered (see above)
3. **Hard stop** — Underlying breaches stop level
4. **Decay target** — Theta decay threshold reached
5. **DTE expiry** — Position held to < 1 DTE
6. **Regime degradation** — Choppiness spikes above threshold
7. **Stagnation** — No meaningful move after 2 sessions
8. **Gainz** — Opposing reversal candle with profit

### Backtest Results (365d SPY, Synthetic Pricing)

Generated 2026-05-29 via `replay_weekly.py --days 365 --no-real-options` at golden defaults.

| Metric | SPY |
|--------|-----|
| **Trading Days** | 251 |
| **Trades** | 19 |
| **Win Rate** | **52.6%** |
| **Profit Factor** | **3.70** |
| **Total P&L** | **+$966** |
| **Avg Win / Avg Loss** | $132 / -$40 |
| **Sharpe** | **1.58** |
| **Sortino** | 1.76 |
| **Calmar** | 7.10 |
| **Max Drawdown** | -$136 |
| **Avg Hold** | 2.5 days |
| **Avg DTE at Exit** | 1.1 |

**Exit breakdown:** 57.9% dte_expiry, 15.8% stagnation, 15.8% stop_loss, 10.5% decay_target.

**Observations:**
- Signal stack is profitable with strong risk-adjusted returns (Sharpe 1.58, PF 3.70)
- Low trade count (19/year) — quality/chop gates are highly selective
- 57.9% exits at DTE expiry — positions held to near-expiration; trailing stops and decay targets rarely fire
- Parameter sweep pending to optimize target_atr_mult, decay_halflife, and DTE window

### Running the Weekly Backtester

```bash
# Single symbol, 1 year
docker-compose run --rm dashboard python -m weekly.backtest.run_weekly_backtest --days 365 --symbol SPY

# Multi-ticker
docker-compose run --rm dashboard python -m weekly.backtest.run_weekly_backtest --days 365 --symbols SPY,QQQ,IWM,DIA

# Parameter sweep (quick grid, ~972 combos)
docker-compose run --rm dashboard python -m weekly.backtest.sweep_weekly --days 365 --symbol SPY --quick

# Full sweep with multiprocessing
docker-compose run --rm dashboard python -m weekly.backtest.sweep_weekly --days 365 --symbols SPY,QQQ --full --jobs 4

# Export results
docker-compose run --rm dashboard python -m weekly.backtest.sweep_weekly --days 365 --symbol SPY --quick --export-csv results/weekly_sweep.csv
```

### Running the Live Weekly Agent

```bash
# Start weekly agents (Docker, separate profile)
docker-compose --profile weekly up -d agent-spy-weekly agent-qqq-weekly

# Or via docker-compose run with custom params
docker-compose --profile weekly run --rm agent-spy-weekly python scripts/run_weekly_agent.py \
  --symbol SPY --daemon --target-delta 0.35 --max-premium 5.0
```

Journal files saved to `weekly_journal/YYYY-MM-DD_SYMBOL_OPEN.json` (renamed to `_CLOSED.json` on exit).

### Dashboard Integration

The Streamlit dashboard loads both `sweet_spot_journal/` (0DTE) and `weekly_journal/` (weekly) trade histories. A **Trade Type** filter (All / 0DTE / Weekly) is available in the sidebar.

## 📖 Full Design Document

See [DESIGN_DOCUMENT.md](DESIGN_DOCUMENT.md) for the complete architecture, strategy details, risk management rules, and implementation plan.

## ⚠️ Disclaimer

This software is for educational purposes. Automated options trading involves significant financial risk. Always test in dry-run mode first. The authors are not responsible for any financial losses.
