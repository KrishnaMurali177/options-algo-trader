# Options Trading Agent via MCP — Design Document

> **Author:** Auto-generated  
> **Date:** April 15, 2026  
> **Status:** Draft  
> **Version:** 1.0

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [MCP Server Selection: Robinhood vs Fidelity](#3-mcp-server-selection-robinhood-vs-fidelity)
4. [Market Trend Analysis Module](#4-market-trend-analysis-module)
5. [Three Options Strategies](#5-three-options-strategies)
6. [Agent Orchestrator (LLM Reasoning Loop)](#6-agent-orchestrator-llm-reasoning-loop)
7. [Risk Management Module](#7-risk-management-module)
8. [Technology Stack](#8-technology-stack)
9. [Project Structure](#9-project-structure)
10. [Step-by-Step Implementation Guide](#10-step-by-step-implementation-guide)
11. [Security Considerations](#11-security-considerations)
12. [Testing Strategy](#12-testing-strategy)
13. [Deployment & Operations](#13-deployment--operations)
14. [Appendix: Key Data Models](#14-appendix-key-data-models)

---

## 1. Executive Summary

This document describes the design of an **AI-powered Options Trading Agent** that:

1. Connects to a brokerage (Robinhood, primary; Fidelity, fallback) via **MCP (Model Context Protocol)** — Anthropic's open standard for tool-use between AI agents and external services.
2. Analyzes current market conditions using technical indicators (VIX, RSI, SMA, Bollinger Bands, MACD, ATR).
3. Classifies the market into a **regime** (bullish/low-vol, range-bound/high-vol, bearish/high-vol).
4. Selects one of **three options strategies** — Covered Call, Iron Condor, or Protective Put — based on the detected regime.
5. Constructs and (optionally) executes the trade through the brokerage MCP server.
6. Enforces strict **risk management** guardrails at every step.

The agent uses an LLM (Claude or GPT-4o) as its reasoning engine, with the MCP protocol providing structured tool access to the brokerage.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER / CLI / Dashboard                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      AGENT ORCHESTRATOR                         │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │ LLM Adapter  │  │  Market      │  │  Strategy Selector    │ │
│  │ (Claude/GPT) │  │  Analyzer    │  │  • Covered Call       │ │
│  │              │  │  • VIX/RSI   │  │  • Iron Condor        │ │
│  │              │  │  • SMA/MACD  │  │  • Protective Put     │ │
│  │              │  │  • Bollinger │  │                       │ │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬───────────┘ │
│         │                 │                      │              │
│         └─────────┬───────┴──────────────────────┘              │
│                   │                                             │
│         ┌─────────▼─────────┐                                   │
│         │  Risk Manager     │                                   │
│         │  • Position limits│                                   │
│         │  • Greeks bounds  │                                   │
│         │  • Circuit breaker│                                   │
│         └─────────┬─────────┘                                   │
└───────────────────┼─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                     MCP CLIENT LAYER                            │
│         (Anthropic MCP Python SDK — stdio transport)            │
└──────────────────────────────┬──────────────────────────────────┘
                               │ MCP Protocol (JSON-RPC over stdio)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     MCP SERVER (robinhood-mcp)                  │
│  Exposed Tools:                                                 │
│  • get_account_info      • get_positions                        │
│  • get_options_chain     • get_option_quote                     │
│  • place_option_order    • cancel_order                         │
│  • get_market_data       • get_fundamentals                     │
└──────────────────────────────┬──────────────────────────────────┘
                               │ robin_stocks API calls
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                   ROBINHOOD BROKERAGE API                       │
│                  (REST — unofficial but stable)                  │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow (Single Execution Cycle)

```
1. Scheduler triggers agent (e.g., 9:35 AM ET)
2. Agent calls Market Analyzer → fetches OHLCV + VIX → computes indicators → returns MarketRegime
3. Agent calls MCP Server → get_positions, get_account_info → returns Portfolio state
4. Agent sends {MarketRegime + Portfolio} to LLM → LLM confirms regime, selects strategy
5. Agent calls Strategy.construct_order() → produces OptionsOrder object
6. Agent calls RiskManager.validate(order, portfolio) → PASS/REJECT
7. If PASS + live mode: Agent calls MCP Server → place_option_order
8. Agent logs result → returns summary to user
```

---

## 3. MCP Server Selection: Robinhood vs Fidelity

### What is MCP?

**Model Context Protocol (MCP)** is an open standard created by Anthropic (released Nov 2024) that provides a universal way for AI agents to interact with external tools and data sources. An MCP server exposes "tools" (functions the agent can call) and "resources" (data the agent can read) through a standardized JSON-RPC protocol over stdio or SSE transport.

### Comparison

| Criteria | Robinhood | Fidelity |
|---|---|---|
| **MCP Server Exists?** | ✅ Yes — `robinhood-mcp` (community, GitHub) | ❌ No known MCP server |
| **Underlying API Library** | `robin_stocks` — mature Python wrapper for Robinhood's REST API | `fidelity-api` — unofficial, Selenium-based browser automation |
| **Options Trading Support** | ✅ Full: chains, Greeks, multi-leg orders | ⚠️ Limited, screen-scraping |
| **Authentication** | Username + password + TOTP (MFA) | Username + password + security questions (fragile) |
| **API Reliability** | Moderate — unofficial but widely used, stable for years | Low — breaks with UI changes |
| **Real-time Data** | ✅ Quotes, chains, positions | ⚠️ Delayed, depends on scraping |
| **Community Support** | Large (`robin_stocks` has 3K+ GitHub stars) | Small |

### ✅ Recommendation: **Robinhood** (Primary)

Robinhood is the clear choice due to its available MCP server, mature API wrapper, and full options trading support. Fidelity can be added as a Phase 2 fallback by building a custom MCP server wrapping browser automation.

### Robinhood MCP Server — Available Tools

The `robinhood-mcp` server (or a custom one built with `robin_stocks`) exposes these MCP tools:

| Tool Name | Description | Parameters |
|---|---|---|
| `get_account_info` | Account balance, buying power, options level | None |
| `get_positions` | Current stock and option positions | `account_type: stock\|options` |
| `get_options_chain` | Full options chain for a symbol | `symbol, expiration_date, option_type` |
| `get_option_quote` | Single option contract quote + Greeks | `symbol, strike, expiration, type` |
| `place_option_order` | Place a single or multi-leg options order | `legs[], quantity, price, order_type, duration` |
| `cancel_order` | Cancel an open order | `order_id` |
| `get_market_data` | OHLCV candles for a symbol | `symbol, interval, span` |
| `get_fundamentals` | Company fundamentals and earnings | `symbol` |

---

## 4. Market Trend Analysis Module

### 4.1 Data Sources

| Data | Source | Library |
|---|---|---|
| Historical OHLCV (daily) | Yahoo Finance / Robinhood | `yfinance` / `robin_stocks` |
| VIX (CBOE Volatility Index) | Yahoo Finance (`^VIX`) | `yfinance` |
| Earnings Calendar | Yahoo Finance / EarningsWhispers | `yfinance` |
| Options Greeks | Robinhood options chain | MCP `get_option_quote` |

### 4.2 Technical Indicators Computed

| Indicator | Library | Parameters | Purpose |
|---|---|---|---|
| **VIX Level** | `yfinance` | Current close of `^VIX` | Volatility regime classification |
| **RSI (Relative Strength Index)** | `pandas_ta` | Period=14 | Overbought/oversold detection |
| **SMA (Simple Moving Averages)** | `pandas_ta` | 20, 50, 200-day | Trend direction, cross signals |
| **Bollinger Bands** | `pandas_ta` | Period=20, StdDev=2 | Range-bound detection |
| **MACD** | `pandas_ta` | 12/26/9 | Momentum confirmation |
| **ATR (Average True Range)** | `pandas_ta` | Period=14 | Position sizing / volatility measure |

### 4.3 Market Regime Classification

The analyzer outputs one of five regimes. The agent maps these to strategy selections:

```python
class MarketRegime(str, Enum):
    LOW_VOL_BULLISH    = "low_vol_bullish"      # VIX<20, RSI 40-65, price>50-SMA
    LOW_VOL_NEUTRAL    = "low_vol_neutral"       # VIX<20, RSI 40-60, price near 50-SMA
    RANGE_BOUND_HV     = "range_bound_high_vol"  # VIX 20-35, RSI 40-60, within Bollinger
    HIGH_VOL_BEARISH   = "high_vol_bearish"      # VIX>30, RSI>70 or death cross
    TRENDING_BEARISH   = "trending_bearish"       # 50-SMA < 200-SMA, MACD negative
```

### 4.4 Regime → Strategy Mapping

| Market Regime | Selected Strategy | Rationale |
|---|---|---|
| `LOW_VOL_BULLISH` / `LOW_VOL_NEUTRAL` | **Covered Call** | Generate income in calm, upward/flat market |
| `RANGE_BOUND_HV` | **Iron Condor** | Profit from range-bound movement when IV is elevated |
| `HIGH_VOL_BEARISH` / `TRENDING_BEARISH` | **Protective Put** | Hedge downside risk in falling/volatile market |

---

## 5. Three Options Strategies

### 5.1 Strategy 1: Covered Call

**When Selected:** Low volatility, bullish or neutral market  

**Prerequisites:**
- Portfolio holds ≥100 shares of the underlying stock  
- VIX < 20  
- RSI between 40–65  
- Price above 50-day SMA  

**Trade Construction:**
```
SELL 1 OTM Call
  - Strike: 1–2 strikes above current price
  - Expiration: 30–45 DTE
  - Delta target: 0.25–0.35 (probability of being exercised ~25-35%)
```

**Profit/Loss Profile:**
- **Max Profit:** Premium received + (strike - current price) × 100
- **Max Loss:** Unlimited downside on the stock (mitigated by owning shares)
- **Breakeven:** Current stock price - premium received

**Exit Rules:**
- Close at 50% profit (buy back call at 50% of premium collected)
- Close at 7 DTE if not already closed (avoid gamma risk)
- Roll up and out if stock moves >5% toward strike

---

### 5.2 Strategy 2: Iron Condor

**When Selected:** Range-bound market with elevated implied volatility  

**Prerequisites:**
- VIX between 20–35  
- RSI between 40–60  
- Price within 20-day Bollinger Bands  
- No earnings announcement within 14 days  

**Trade Construction:**
```
SELL 1 OTM Put   (lower strike, delta ~-0.15 to -0.20)
BUY  1 OTM Put   (lower strike - 1 or 2 width)
SELL 1 OTM Call  (upper strike, delta ~0.15 to 0.20)
BUY  1 OTM Call  (upper strike + 1 or 2 width)

Expiration: 30–45 DTE
Wing width: $1–$5 depending on stock price
```

**Profit/Loss Profile:**
- **Max Profit:** Net premium received (all 4 legs)
- **Max Loss:** Width of wider spread - net premium
- **Breakeven:** Lower short strike - net premium / Upper short strike + net premium

**Exit Rules:**
- Close at 50% of max profit
- Close if either short strike is breached (tested)
- Close at 14 DTE regardless of P&L
- Close entire position if any leg moves to 200% of premium collected

---

### 5.3 Strategy 3: Protective Put

**When Selected:** High volatility, bearish signals detected  

**Prerequisites:**
- VIX > 30  
- RSI > 70 (overbought before expected reversal) OR death cross (50-SMA < 200-SMA)  
- Portfolio has long stock exposure that needs hedging  

**Trade Construction:**
```
BUY 1 ATM or slightly OTM Put
  - Strike: at-the-money or 1 strike below current price
  - Expiration: 30–60 DTE
  - Delta target: -0.40 to -0.50
```

**Profit/Loss Profile:**
- **Max Profit:** Unlimited (as stock drops to 0 minus premium paid)
- **Max Loss:** Premium paid for the put
- **Breakeven:** Current stock price - premium paid

**Exit Rules:**
- Close if stock drops 10%+ (take profit on the hedge)
- Close if VIX drops below 20 (crisis resolved)
- Roll out if approaching expiration and still holding stock
- Close at 7 DTE if not already closed

---

## 6. Agent Orchestrator (LLM Reasoning Loop)

### 6.1 Agent Loop Design

The agent follows a **ReAct (Reasoning + Acting)** pattern:

```
LOOP:
  1. OBSERVE   → Gather market data + portfolio state
  2. THINK     → LLM analyzes regime, selects strategy, explains rationale
  3. ACT       → Construct order, validate risk, execute or log
  4. REFLECT   → Log outcome, update state, check for follow-up actions
```

### 6.2 LLM System Prompt (Simplified)

```
You are an options trading assistant. You have access to the following tools 
via MCP:
- get_account_info, get_positions, get_options_chain, get_option_quote,
  place_option_order, cancel_order, get_market_data

Your workflow:
1. Analyze the provided market indicators and classify the market regime.
2. Given the regime and current portfolio, select ONE of three strategies:
   Covered Call, Iron Condor, or Protective Put.
3. Explain your reasoning for the selection.
4. Construct the specific trade with exact strikes, expirations, and quantities.
5. Confirm the trade satisfies all risk management rules before execution.

Risk rules you MUST enforce:
- Max 5% of portfolio per trade
- Max 2% portfolio loss per trade
- No trades under 14 DTE
- No trades within 7 days of earnings
- Daily trade limit: 3 new positions
- Circuit breaker: halt if daily P&L < -3%
```

### 6.3 LLM Integration Options

| Option | Pros | Cons |
|---|---|---|
| **Claude 3.5+ (Anthropic API)** | Native MCP tool-use, best for agentic loops | Requires Anthropic API key |
| **GPT-4o (Azure OpenAI)** | Reuse existing AppGenie proxy (`AzureOpenAIProxy`) | Need to wrap MCP tools as OpenAI function calls |
| **Claude via Amazon Bedrock** | Enterprise deployment, existing AWS infra | Added complexity |

**Recommendation:** Use **Claude 3.5 Sonnet** via Anthropic API for its native MCP tool-use support. Alternatively, reuse the existing `AzureOpenAIProxy` with GPT-4o function calling and a thin adapter layer.

---

## 7. Risk Management Module

### 7.1 Pre-Trade Checks

| Check | Rule | Action if Failed |
|---|---|---|
| Position Size | Single trade ≤ 5% of portfolio value | Reject trade |
| Max Loss | Max loss on trade ≤ 2% of portfolio | Reject trade |
| Total Options Allocation | All options positions ≤ 15% of portfolio | Reject trade |
| DTE Minimum | Expiration ≥ 14 DTE | Reject trade |
| Earnings Blackout | No earnings within 7 days of expiration | Reject trade |
| Daily Trade Count | ≤ 3 new positions per day | Reject trade |
| Duplicate Position | No duplicate strategy on same underlying | Reject trade |

### 7.2 Portfolio-Level Monitoring

| Metric | Threshold | Action |
|---|---|---|
| Net Portfolio Delta | \|delta\| > 0.5 per $10K | Alert + suggest hedge |
| Daily P&L | < -3% of portfolio value | **CIRCUIT BREAKER** — halt all trading |
| Weekly P&L | < -5% of portfolio value | Reduce position sizes by 50% |
| Theta Exposure | > 0.1% of portfolio per day | Alert |

### 7.3 Post-Trade Management

- **Profit Target:** Close at 50% of max profit (configurable)
- **Stop-Loss:** Close at 200% of premium collected (short options) or 50% max loss
- **Time-Based Exit:** Close at 7 DTE regardless
- **Gamma Guardrail:** No new trades under 14 DTE

---

## 8. Technology Stack

| Component | Technology | Version |
|---|---|---|
| **Language** | Python | 3.11+ |
| **AI Agent / LLM** | Claude 3.5 Sonnet (Anthropic) or GPT-4o (Azure OpenAI) | Latest |
| **MCP SDK** | `mcp` — Anthropic's official Python SDK | `>=1.0` |
| **MCP Server** | `robinhood-mcp` (community) or custom server | Latest |
| **Brokerage Library** | `robin_stocks` | `>=3.0` |
| **Market Data** | `yfinance` | `>=0.2` |
| **Technical Analysis** | `pandas-ta` | `>=0.3` |
| **Data Processing** | `pandas`, `numpy` | Latest |
| **Data Models** | `pydantic` | `>=2.0` |
| **Configuration** | `pydantic-settings`, `.env` | Latest |
| **Scheduling** | `APScheduler` | `>=3.10` |
| **Logging** | `structlog` | `>=24.0` |
| **Testing** | `pytest`, `pytest-asyncio` | Latest |
| **API (optional dashboard)** | `FastAPI` + `uvicorn` | Latest |

### Dependencies (`requirements.txt`)

```
# MCP & Agent
mcp>=1.0.0
anthropic>=0.40.0

# Brokerage
robin_stocks>=3.0.0

# Market Data & Analysis
yfinance>=0.2.40
pandas>=2.2.0
pandas-ta>=0.3.14b
numpy>=1.26.0

# Data Models & Config
pydantic>=2.8.0
pydantic-settings>=2.4.0
python-dotenv>=1.0.0

# Scheduling
apscheduler>=3.10.0

# Logging
structlog>=24.4.0

# Web / Dashboard (optional)
fastapi>=0.115.0
uvicorn>=0.30.0

# Testing
pytest>=8.0.0
pytest-asyncio>=0.24.0
```

---

## 9. Project Structure

```
options_agent/
├── pyproject.toml                  # Project metadata + dependencies
├── requirements.txt                # Pip dependencies
├── .env.example                    # Template for environment variables
├── README.md                       # Setup and usage instructions
│
├── config/
│   ├── __init__.py
│   └── settings.py                 # Pydantic Settings — all configurable params
│
├── src/
│   ├── __init__.py
│   ├── agent.py                    # Main orchestrator: LLM + MCP + strategy loop
│   ├── mcp_client.py               # MCP client wrapper — stdio connection to robinhood-mcp
│   ├── market_analyzer.py          # Technical indicators + regime classification
│   ├── risk_manager.py             # Pre-trade validation, portfolio monitoring, circuit breaker
│   ├── llm_adapter.py              # LLM abstraction (Claude or GPT-4o)
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── market_data.py          # Pydantic: OHLCV, MarketIndicators, MarketRegime
│   │   ├── options.py              # Pydantic: OptionContract, OptionOrder, Greeks
│   │   └── portfolio.py            # Pydantic: Position, AccountInfo, PortfolioSummary
│   │
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── base_strategy.py        # ABC: evaluate_eligibility, construct_order, calculate_risk
│   │   ├── covered_call.py         # Covered Call implementation
│   │   ├── iron_condor.py          # Iron Condor implementation
│   │   └── protective_put.py       # Protective Put implementation
│   │
│   └── utils/
│       ├── __init__.py
│       ├── greeks.py               # Black-Scholes Greeks calculation helpers
│       └── date_utils.py           # DTE calculation, earnings calendar, market hours
│
├── mcp_server/
│   ├── __init__.py
│   ├── robinhood_mcp_server.py     # Custom MCP server wrapping robin_stocks (if not using community)
│   └── tools.py                    # MCP tool definitions (get_options_chain, place_order, etc.)
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # Shared fixtures
│   ├── test_market_analyzer.py
│   ├── test_strategies.py
│   ├── test_risk_manager.py
│   ├── test_mcp_client.py
│   ├── test_agent.py
│   └── fixtures/
│       ├── mock_options_chain.json
│       ├── mock_portfolio.json
│       └── mock_market_data.json
│
└── scripts/
    ├── run_agent.py                # CLI entry point: `python scripts/run_agent.py`
    ├── run_scheduled.py            # Scheduled execution (APScheduler)
    └── backtest.py                 # Historical backtesting harness
```

---

## 10. Step-by-Step Implementation Guide

### Phase 1: Foundation (Days 1–3)

#### Step 1: Project Setup
```bash
cd /Users/kmural14/PycharmProjects/AppGenie
mkdir -p options_agent/{config,src/{models,strategies,utils},mcp_server,tests/fixtures,scripts}

# Create virtual environment
cd options_agent
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

#### Step 2: Configure Environment Variables
Create `.env` file with:
```env
# Robinhood Credentials
ROBINHOOD_USERNAME=your_email@example.com
ROBINHOOD_PASSWORD=your_password
ROBINHOOD_TOTP_SECRET=your_totp_base32_secret

# LLM Configuration
ANTHROPIC_API_KEY=sk-ant-xxxxx
# OR for Azure OpenAI:
# AZURE_OPENAI_ENDPOINT=https://...
# AZURE_OPENAI_API_KEY=...
# AZURE_OPENAI_DEPLOYMENT=gpt-4o

# Agent Configuration
DRY_RUN=true                          # IMPORTANT: Start in dry-run mode
MAX_POSITION_SIZE_PCT=0.05            # 5% per trade
MAX_LOSS_PCT=0.02                     # 2% max loss per trade
MAX_OPTIONS_ALLOCATION_PCT=0.15       # 15% total options allocation
MIN_DTE=14                            # Minimum days to expiration
EARNINGS_BLACKOUT_DAYS=7
MAX_DAILY_TRADES=3
CIRCUIT_BREAKER_DAILY_LOSS_PCT=0.03   # -3% triggers halt

# Schedule (Eastern Time)
MARKET_SCAN_TIMES=09:35,12:00,15:30
```

#### Step 3: Implement Pydantic Settings (`config/settings.py`)
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Robinhood
    robinhood_username: str
    robinhood_password: str
    robinhood_totp_secret: str
    
    # LLM
    anthropic_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    llm_provider: str = "anthropic"  # or "azure_openai"
    
    # Risk Parameters
    dry_run: bool = True
    max_position_size_pct: float = 0.05
    max_loss_pct: float = 0.02
    max_options_allocation_pct: float = 0.15
    min_dte: int = 14
    earnings_blackout_days: int = 7
    max_daily_trades: int = 3
    circuit_breaker_daily_loss_pct: float = 0.03
    
    class Config:
        env_file = ".env"
```

#### Step 4: Set Up Robinhood MCP Server

**Option A: Use existing `robinhood-mcp`**
```bash
# Clone the community MCP server
git clone https://github.com/anamgiri/robinhood-mcp.git ../robinhood-mcp
cd ../robinhood-mcp
pip install -e .
```

**Option B: Build a custom MCP server** (recommended for full control)
Implement `mcp_server/robinhood_mcp_server.py` using the `mcp` SDK and `robin_stocks`:
```python
from mcp.server import Server
from mcp.types import Tool
import robin_stocks.robinhood as rh

server = Server("robinhood-trading")

@server.tool()
async def get_options_chain(symbol: str, expiration: str, option_type: str = "call"):
    """Get options chain for a symbol."""
    chain = rh.options.find_options_by_expiration(
        symbol, expirationDate=expiration, optionType=option_type
    )
    return chain
# ... more tools
```

### Phase 2: Market Analysis (Days 4–5)

#### Step 5: Implement Market Analyzer (`src/market_analyzer.py`)
```python
import yfinance as yf
import pandas_ta as ta
from src.models.market_data import MarketIndicators, MarketRegime

class MarketAnalyzer:
    def analyze(self, symbol: str) -> MarketIndicators:
        df = yf.download(symbol, period="1y", interval="1d")
        vix = yf.download("^VIX", period="5d")["Close"].iloc[-1]
        
        df.ta.rsi(length=14, append=True)
        df.ta.sma(length=20, append=True)
        df.ta.sma(length=50, append=True)
        df.ta.sma(length=200, append=True)
        df.ta.bbands(length=20, std=2, append=True)
        df.ta.macd(append=True)
        df.ta.atr(length=14, append=True)
        
        return MarketIndicators(
            vix=vix, rsi=df["RSI_14"].iloc[-1],
            sma_20=df["SMA_20"].iloc[-1], sma_50=df["SMA_50"].iloc[-1],
            sma_200=df["SMA_200"].iloc[-1], ...
        )
    
    def classify_regime(self, indicators: MarketIndicators) -> MarketRegime:
        # Classification logic based on rules in Section 4.3
        ...
```

#### Step 6: Implement Data Models (`src/models/`)
Define Pydantic models for `MarketIndicators`, `MarketRegime`, `OptionContract`, `OptionOrder`, `PortfolioSummary`, etc.

### Phase 3: Strategies (Days 6–8)

#### Step 7: Implement Base Strategy (`src/strategies/base_strategy.py`)
```python
from abc import ABC, abstractmethod

class BaseStrategy(ABC):
    @abstractmethod
    def evaluate_eligibility(self, regime, portfolio, indicators) -> bool: ...
    
    @abstractmethod
    def construct_order(self, symbol, chain, portfolio, indicators) -> OptionOrder: ...
    
    @abstractmethod
    def calculate_risk(self, order, portfolio) -> RiskAssessment: ...
```

#### Step 8: Implement Three Strategies
- `covered_call.py`: Sell OTM call on existing long position
- `iron_condor.py`: 4-leg spread using chain delta targeting
- `protective_put.py`: Buy ATM put to hedge long exposure

### Phase 4: Agent & Risk (Days 9–12)

#### Step 9: Implement Risk Manager (`src/risk_manager.py`)
All pre-trade checks, circuit breaker, position monitoring.

#### Step 10: Implement LLM Adapter (`src/llm_adapter.py`)
Abstract interface for Claude/GPT that sends market data + portfolio and receives strategy recommendation.

#### Step 11: Implement Agent Orchestrator (`src/agent.py`)
The main loop: analyze → classify → select strategy → construct order → validate risk → execute/log.

### Phase 5: Testing & Deployment (Days 13–15)

#### Step 12: Write Tests
- Unit tests for each strategy's math
- Mock MCP server tests
- Integration test for full agent cycle with fixtures

#### Step 13: Dry-Run Testing
```bash
# Run agent in dry-run mode
python scripts/run_agent.py --symbol AAPL --dry-run
```

#### Step 14: Scheduled Execution
```bash
# Run scheduled agent
python scripts/run_scheduled.py
```

---

## 11. Security Considerations

| Concern | Mitigation |
|---|---|
| **Credential Storage** | All secrets in `.env` or AWS Secrets Manager; never commit to git |
| **MCP Transport** | Use **stdio** transport (local process), NOT SSE over network |
| **TOTP/MFA** | Store Robinhood TOTP base32 secret; generate codes programmatically via `pyotp` |
| **Dry-Run Default** | `DRY_RUN=true` by default; must explicitly enable live trading |
| **Order Confirmation** | Log all orders with full detail before execution; optional human-in-the-loop approval |
| **Audit Trail** | Persist every decision (regime, strategy, order, result) to JSON log file |
| **API Rate Limits** | Respect Robinhood's rate limits (~1 req/sec); exponential backoff on 429s |
| **Git Ignore** | `.env`, credentials, and logs excluded via `.gitignore` |
| **Paper Trading** | Use Robinhood paper/simulated trading before any real money |
| **PDT Rules** | Enforce Pattern Day Trader limits (<$25K accounts: max 3 day trades in 5 rolling days) |

---

## 12. Testing Strategy

### 12.1 Unit Tests
```
tests/test_market_analyzer.py    — Indicator calculations against known values
tests/test_strategies.py         — Order construction, P&L math, eligibility rules
tests/test_risk_manager.py       — Position limits, circuit breaker, blackout logic
```

### 12.2 Integration Tests (Mocked MCP)
```python
# tests/test_agent.py
async def test_agent_selects_covered_call_in_low_vol():
    # Mock MCP responses with fixture data
    # Assert agent selects CoveredCall and constructs correct order
```

### 12.3 Backtesting
```python
# scripts/backtest.py
# Replay historical data through the agent
# Compare strategy selections and P&L against actual market outcomes
```

### 12.4 End-to-End (Dry-Run)
```bash
# Connect to real Robinhood MCP in dry-run mode
python scripts/run_agent.py --symbol SPY --dry-run --verbose
```

---

## 13. Deployment & Operations

### Local Development
```bash
cd options_agent
source .venv/bin/activate
python scripts/run_agent.py --symbol AAPL --dry-run
```

### Scheduled Production (Local Machine / EC2)
```bash
# Cron or APScheduler runs at market hours (ET)
python scripts/run_scheduled.py
# Scans at 9:35 AM, 12:00 PM, 3:30 PM ET
```

### Monitoring Dashboard (Optional, Phase 2)
Reuse the existing AppGenie FastAPI pattern to build a web dashboard:
- Current positions and P&L
- Today's trades and decisions
- Market regime history
- Risk dashboard (portfolio Greeks, allocation)

### Future Enhancements
- **Additional Strategies:** Bull call spread, bear put spread, calendar spread, straddle
- **Fidelity MCP Server:** Build custom MCP server using browser automation
- **Multi-Symbol Scanning:** Scan watchlist of 10–20 symbols
- **ML Regime Classification:** Replace rule-based regime classification with a trained model
- **Slack/Discord Alerts:** Notify on trade executions and circuit breaker triggers
- **Portfolio Rebalancing:** Auto-rebalance options allocation quarterly

---

## 14. Appendix: Key Data Models

### MarketIndicators
```python
class MarketIndicators(BaseModel):
    symbol: str
    timestamp: datetime
    current_price: float
    vix: float
    rsi_14: float
    sma_20: float
    sma_50: float
    sma_200: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    macd: float
    macd_signal: float
    macd_histogram: float
    atr_14: float
    next_earnings_date: Optional[date]
    days_to_earnings: Optional[int]
```

### MarketRegime
```python
class MarketRegime(str, Enum):
    LOW_VOL_BULLISH = "low_vol_bullish"
    LOW_VOL_NEUTRAL = "low_vol_neutral"
    RANGE_BOUND_HV = "range_bound_high_vol"
    HIGH_VOL_BEARISH = "high_vol_bearish"
    TRENDING_BEARISH = "trending_bearish"
```

### OptionOrder
```python
class OptionLeg(BaseModel):
    symbol: str
    strike: float
    expiration: date
    option_type: Literal["call", "put"]
    action: Literal["buy_to_open", "sell_to_open", "buy_to_close", "sell_to_close"]
    quantity: int

class OptionOrder(BaseModel):
    strategy_name: str
    legs: list[OptionLeg]
    order_type: Literal["limit", "market"]
    limit_price: Optional[float]
    duration: Literal["gfd", "gtc"]  # good for day, good till cancelled
    max_loss: float
    max_profit: float
    risk_reward_ratio: float
```

### RiskAssessment
```python
class RiskAssessment(BaseModel):
    approved: bool
    rejection_reasons: list[str]
    position_size_pct: float
    max_loss_pct: float
    portfolio_delta_after: float
    options_allocation_after_pct: float
```

---

## 15. Sweet Spot Live Agent — Workflow & Decision Logic

> **Added:** May 5, 2026 | **Script:** `scripts/run_sweet_spot_agent.py`

### 15.1 Daily Workflow

```
┌─────────────────────────────────────────────────────────────┐
│                    DAILY STARTUP                              │
│  • Wait for market open (9:30 ET)                           │
│  • Initialize: trades_today=0, stops_today=0                │
│  • Load/create journal file (sweet_spot_journal/YYYY-MM-DD) │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              WAIT FOR SCAN WINDOW                             │
│  • Wait until 10:30 ET (scan_start_min=60 after open)       │
│  • Opening Range forms during 9:30–10:29 (12 bars × 5min)  │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│           MAIN SCAN LOOP (every 5 minutes)                   │
│                                                              │
│  ┌─ CHECK DAILY LIMITS ─────────────────────────────────┐   │
│  │  • trades_today >= 3? → stop scanning, monitor only   │   │
│  │  • stops_today >= 1? → HALT (daily loss limit)        │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─ CHECK TIME CUTOFF ──────────────────────────────────┐   │
│  │  • now.hour >= 14 (2:00 PM)? → no new entries         │   │
│  │    → monitor open positions only until they close      │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─ CHECK COOLDOWN ─────────────────────────────────────┐   │
│  │  • Last trigger < 15 min ago? → skip                  │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─ EVALUATE SWEET SPOT ────────────────────────────────┐   │
│  │  (see §15.2 Decision Logic below)                     │   │
│  │  ALL PASS? → Return trigger dict → EXECUTE TRADE      │   │
│  │  ANY FAIL? → Return None → sleep 5 min → loop         │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                              │
└──────────────────────┬──────────────────────────────────────┘
                       │ trigger found
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              EXECUTE TRADE                                    │
│                                                              │
│  1. Compute execution levels:                               │
│     • Entry = current price                                 │
│     • Stop = (range_high + range_low) / 2 (midpoint)        │
│     • Risk = |entry - stop|                                 │
│     • Target = entry ± risk × mult                          │
│       (E≥8→1.5R, E≥6→1.25R, else 1.0R)                     │
│                                                              │
│  2. Determine contract count:                               │
│     • Cascade sizing: 3 contracts flat (all tiers)          │
│                                                              │
│  3. Place order via Alpaca:                                 │
│     • 0DTE ATM option (delta ≈ 0.50)                        │
│     • Bracket order: stop + limit (target)                  │
│                                                              │
│  4. Log to journal, trades_today += 1                       │
│                                                              │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│           POSITION MONITORING (every 5 min)                  │
│                                                              │
│  ┌─ GAINZ ALGO V2 (early exit) ────────────────────────┐   │
│  │  Check last completed 5-min bar:                      │   │
│  │  • CALL + RSI ≥ 70 + bearish body ≥ 70% → SELL       │   │
│  │  • PUT + RSI ≤ 30 + bullish body ≥ 70% → BUY back    │   │
│  │  → close_position() immediately, cancel bracket       │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─ BRACKET MONITORING ─────────────────────────────────┐   │
│  │  • Target hit? → WIN (bracket limit filled)           │   │
│  │  • Stop hit? → LOSS → stops_today += 1                │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─ TIME STOP ──────────────────────────────────────────┐   │
│  │  • 3:30 PM ET: close any remaining positions          │   │
│  │  • Buffer: 15 min before broker's 3:45 forced close   │   │
│  │  • 0DTE expires at 4:00 PM (SPY/QQQ/IWM/DIA)         │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                              │
│  Continue until all positions closed                         │
│                                                              │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    END OF DAY                                 │
│  • Save final journal                                       │
│  • Log daily summary (trades, P&L, outcomes)                │
│  • If --daemon: sleep until next trading day 9:25 AM        │
└─────────────────────────────────────────────────────────────┘
```

### 15.2 Decision Logic (Entry Criteria)

All 10 conditions must pass for a trigger to fire:

```
ENTRY CRITERIA (ALL must pass):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

 #  Gate                    Threshold              Source
─── ─────────────────────── ────────────────────── ──────────────────────────
 1  Time window             10:30 AM – 2:00 PM ET  Clock check
 2  OR Momentum direction   |M| ≥ 25              OpeningRangeAnalyzer
 3  Regime guard            Not counter-trend       SMA20 vs SMA50 + RSI
 4  Quality score           3 ≤ Q ≤ 7              compute_quality_score()
 5  Explosion score         E ≥ 4                  MomentumCascadeDetector
 6  Choppiness              C ≤ 5                  compute_choppiness()
 7  Entry confirmation      Price in upper/lower    OR range high/low ± 25%
                            25% of OR range
 8  Cooldown                ≥ 15 min since last     Timestamp delta
 9  Daily trade cap         < 3 trades today        Counter
10  Daily loss cap          < 1 stop-out today      Counter
```

**Signal evaluation pipeline (executed in order):**

1. **MarketAnalyzer.analyze(symbol, "15min")**
   - Fetches live 5-min bars from yfinance
   - Computes: RSI-14, SMA-20/50/200, MACD, ATR-14, Bollinger Bands, ZLEMA-8/21, VIX

2. **OpeningRangeAnalyzer.analyze(indicators)**
   - Uses 9:30–10:29 bars (opening range)
   - 7 weighted signals → momentum score M ∈ [−100, +100]
   - Direction: M ≥ 25 → CALL, M ≤ −25 → PUT, else SKIP

3. **RecentMomentumAnalyzer.analyze(indicators)**
   - Last 30 min of 5-min bars
   - Outputs: direction (bullish/bearish/neutral) + momentum score

4. **Regime Guard**
   - PUT blocked if SMA20 > SMA50 AND RSI ≤ 70
   - CALL blocked if SMA20 < SMA50 AND RSI ≥ 30
   - Exception: extreme RSI allows counter-trend (oversold bounce / overbought reversal)

5. **compute_quality_score()**
   - 13 possible points (11 signals + 2 penalties)
   - Sweet spot range: 3–7 (not too low = weak, not too high = chasing)

6. **MomentumCascadeDetector.analyze()**
   - 6 signals → explosion score E ∈ [0, 10]
   - Requires E ≥ 4 (minimum institutional momentum)

7. **compute_choppiness(bars)**
   - 4 components: Kaufman CI + reversal rate + bar range ratio + max streak
   - Score C ∈ [0, 10]; requires C ≤ 5 (strict = trending day only)

8. **Entry Confirmation**
   - CALL: price must be in upper 25% of OR range (near breakout)
   - PUT: price must be in lower 25% of OR range (near breakdown)
   - Prevents mid-range entries with no directional edge

### 15.3 Exit Logic

```
EXIT LOGIC (first condition to trigger wins):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Priority  Exit Type        Condition                          Action
────────  ───────────────  ─────────────────────────────────  ──────────────────────
   1      TARGET HIT       Price reaches entry ± R×mult       Bracket limit fills
   2      STOP HIT         Price reaches range midpoint       Bracket stop fills
   3      GAINZ EXIT       RSI extreme + strong opposing      Market close immediately
                           candle (body ≥ 70% of range)
   4      TIME STOP        3:30 PM ET reached                 Market close at bar close
   5      EOD / BROKER     3:45 PM broker forced close        Automatic (safety net)
```

**GainzAlgoV2 thresholds (golden defaults):**
- RSI overbought: 70 (triggers SELL for CALL positions)
- RSI oversold: 30 (triggers BUY for PUT positions)
- Min body ratio: 0.7 (candle body must be ≥ 70% of high-low range)
- Both conditions must be true simultaneously on a single completed 5-min bar

### 15.4 Replay vs Live Agent Differences

| Aspect | Replay (backtest) | Live Agent |
|--------|-------------------|------------|
| Data source | Alpaca historical 5-min bars | yfinance live 5-min bars |
| VIX | Historical daily lookup | Live daily VIX |
| Order execution | Simulated walk-forward | Real Alpaca paper/live bracket orders |
| Time stop | Checks bar timestamp ≥ 15:30 | Broker closes 0DTE at 3:45; agent exits ~3:30 |
| Gainz exit | Precomputed RSI series | Live bar RSI computation |
| Position tracking | Instant fill assumed | Real fill monitoring via Alpaca API |
| Slippage | Configurable (default $0) | Real market bid-ask spread |
| Multi-day SMA | 4 prior days prepended | MarketAnalyzer fetches sufficient history |

### 15.5 Golden Parameters Summary

| Parameter | Value | Validated By |
|-----------|-------|--------------|
| Scan window | 10:30–14:00 ET | 2-yr replay: later entries lose money |
| Quality range | 3–7 | Q < 3 = insufficient signal; Q > 7 = chasing |
| Min explosion | 4 | E < 4 = no institutional momentum |
| Max choppiness | 5 | C > 5 = whipsaw environment |
| Max trades/day | 3 | 4+ trades/day historically net negative |
| Max stops/day | 1 | Prevents catastrophic multi-stop days |
| Cooldown | 15 min (3 bars) | Avoids duplicate entries on same signal |
| Stop level | Range midpoint | Balanced: not too tight, not too loose |
| Target multiplier | 1.0R / 1.25R / 1.5R | Scaled by explosion strength |
| Cascade sizing | 3/3/3 flat | Middle tier (E6-7) underperforms; flat avoids bias |
| Time stop | 3:30 PM ET | 15-min buffer before broker's 3:45 forced close |
| Gainz RSI | 70/30, body 0.7 | Stricter = fewer premature exits |
| Option delta | 0.50 (ATM) | Maximum gamma exposure for 0DTE |

---

*End of Design Document*

