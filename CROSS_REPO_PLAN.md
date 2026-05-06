# Cross-Repo Integration Plan — options_analyzer → options_agent

## Context
The `options_analyzer` repo is a mature 4-layer trading system (FastAPI + React, Tastytrade, GARCH, Monte Carlo, 16 test files, ~14.7K lines). The `options_agent` repo is a lighter MCP-based agent (Robinhood, Streamlit, 11-point scoring). Many capabilities in `options_analyzer` solve open problems in `options_agent`. This plan identifies what to port, adapt, or share — without merging the repos.

### Guiding Principles
- **Port logic, not frameworks.** options_analyzer uses FastAPI/Tastytrade; options_agent uses MCP/Robinhood. Extract the algorithms, not the plumbing.
- **Don't duplicate what already works.** If options_analyzer already solves a problem well, import or adapt — don't reinvent.
- **Keep repos independent.** Shared code goes into a `shared/` package or is copied with attribution, not cross-imported.

---

## Phase A: Contract Liquidity Filter (from `scanner/contract_filter.py`)

**What options_analyzer has:**
`src/scanner/contract_filter.py` — `filter_contracts()` applies DTE range, moneyness (0.85–1.15), min OI (100), max bid-ask spread % (15%), delta range (0.15–0.50), and IV sanity checks. Battle-tested with real chain data.

**What options_agent lacks:**
Strategies (`buy_call.py`, `buy_put.py`) accept whatever contract matches the target delta — no OI, spread, or IV filtering. (Issue #9 from audit.)

**Plan:**
1. Port `filter_contracts()` logic into a new `src/utils/contract_filter.py` in options_agent
2. Adapt to options_agent's `OptionContract` model (field names differ slightly)
3. Apply filter before `_find_by_delta()` in both buy strategies
4. Add settings: `min_open_interest`, `max_bid_ask_spread_pct` to `config/settings.py`
5. This **replaces Phase 7** from REMAINING_FIXES_PLAN.md with a proven implementation

**Source files to reference:**
- `options_analyzer/src/scanner/contract_filter.py`
- `options_analyzer/src/config.py` (CHAIN_SCANNER_CONFIG filter defaults)

---

## Phase B: Shared Indicator Engine (from `bias_detector.py`)

**What options_analyzer has:**
`src/bias_detector.py` has clean implementations of `_ema()`, `_rsi()`, `_macd()`, `_atr()` — all as standalone methods. Also has GARCH vol in `src/monte_carlo/garch_vol.py` and ZLEMA-like concepts.

**What options_agent lacks:**
RSI computed in 5 files, MACD in 3, ATR in 2. Each copy is slightly different. (Issue #11 from audit.)

**Plan:**
1. Create `src/utils/indicators.py` in options_agent with functions ported from options_analyzer's `bias_detector.py`:
   - `rsi(close, period)`, `macd(close)`, `atr(high, low, close, period)`, `ema(close, period)`, `zlema(close, period)`
2. Replace all 5 duplicate RSI implementations, 3 MACD, 2 ATR with imports
3. This **replaces Phase 8** from REMAINING_FIXES_PLAN.md

**Source files to reference:**
- `options_analyzer/src/bias_detector.py` (lines with `_ema`, `_rsi`, `_macd`, `_atr`)
- `options_analyzer/src/monte_carlo/garch_vol.py` (for future GARCH integration)

---

## Phase C: Exit Rules + Position Management (from `trade_generator.py` + `portfolio.py`)

**What options_analyzer has:**
- `src/trade_generator.py` defines per-strategy `ExitRule` dataclass: profit target %, stop loss multiplier, DTE exit, hold-to-expiry flag. Each strategy has validated exit parameters (e.g., iron condor: 50% credit target, 2x loss stop, 1 DTE exit).
- `src/portfolio.py` has a full `Position` class tracking entry price, current P&L, Greeks, and exit parameters. Portfolio-level limits (max positions, max delta/gamma/theta/vega, max risk).

**What options_agent lacks:**
Agent opens positions but never closes them. No EOD exit, stop-loss execution, partial profit-taking, or rolling logic. (Issue #6 from audit — the highest-priority functionality gap.)

**Plan:**
1. Create `src/exit_manager.py` in options_agent:
   - `ExitRule` dataclass (profit_target_pct, stop_loss_multiplier, dte_exit, hold_to_expiry) — ported from options_analyzer
   - `ExitManager` class with `check_exits(positions, current_prices) -> list[ExitSignal]`
   - Each strategy defines its own `ExitRule` (buy_call/buy_put already have profit_target_1/2 and stop_loss_price fields)
2. Create `src/position_tracker.py`:
   - Track open positions with entry price, current Greeks, P&L
   - Persist to `audit_logs/positions.jsonl` (reuse existing audit infrastructure)
3. Add exit-checking step to `agent.py` run cycle — before opening new positions, check if any existing positions should be closed
4. Wire MCP `place_option_order` for close actions (`buy_to_close`, `sell_to_close`)

**Source files to reference:**
- `options_analyzer/src/trade_generator.py` (ExitRule dataclass, per-strategy exit params)
- `options_analyzer/src/portfolio.py` (Position class, P&L tracking, Greek aggregation)

---

## Phase D: Kelly Sizing (from `sizing.py`)

**What options_analyzer has:**
`src/sizing.py` — half-Kelly position sizing with per-strategy win rates and avg win/loss from backtests. Caps at 2% portfolio risk. Includes slippage model (3% of premium, min 1 tick, reject if bid-ask > 10% of mid).

**What options_agent lacks:**
Position sizing is implicit in `max_loss` calculation within strategies. No Kelly criterion, no slippage modeling, no per-strategy win rate tracking.

**Plan:**
1. Create `src/position_sizer.py`:
   - `PositionSizer` class with `size(strategy_name, portfolio_value, contract_price) -> SizeResult`
   - `SizeResult`: contracts, capital_at_risk, risk_pct, method (kelly/fixed)
   - Half-Kelly with 2% cap (from options_analyzer)
   - Strategy stats (win_rate, avg_win, avg_loss) stored in `config/strategy_stats.json` — initially seeded from options_analyzer's backtest results, updated as options_agent accumulates trades
2. Add slippage check: reject if bid-ask > configurable % of mid
3. Integrate into `agent.py` Step 4 — after strategy constructs order, sizer determines contract count

**Source files to reference:**
- `options_analyzer/src/sizing.py` (half-Kelly formula, slippage model)
- `options_analyzer/src/portfolio.py` (risk_pct cap logic)

---

## Phase E: Retry Decorator (from resilience patterns)

**What options_analyzer has:**
Graceful degradation patterns — try/except with logging on all provider calls, timeout on API calls (10s for FlashAlpha), fallback chains (GEX → chain-based max pain). No explicit retry decorator though.

**What options_agent lacks:**
All yfinance and MCP calls fail on first error. No backoff, no throttling. (Issue #10 from audit.)

**Plan:**
1. Create `src/utils/retry.py` with `retry_on_transient` decorator:
   - Max 3 attempts, exponential backoff (1s, 2s, 4s)
   - Retry on `ConnectionError`, `TimeoutError`, `requests.exceptions.RequestException`
   - Do NOT retry on `DataFetchError` (intentional raises)
   - Use `tenacity` library (add to requirements.txt) or implement manually (~30 lines)
2. Apply to `yf.download()` calls in market_analyzer, opening_range, recent_momentum, momentum_cascade
3. Apply to MCP client methods
4. This **replaces Phase 10** from REMAINING_FIXES_PLAN.md

**Source files to reference:**
- `options_analyzer/src/scanner/providers/yfinance_provider.py` (rate limiting pattern)
- `options_analyzer/src/scanner/providers/cached_provider.py` (TTL cache pattern — consider adding)

---

## Phase F: Chain Caching (from `cached_provider.py`)

**What options_analyzer has:**
`src/scanner/providers/cached_provider.py` — TTL-based in-memory cache wrapping the chain provider. 15-min TTL for chains, 1-hour for history. Thread-safe via `threading.Lock`.

**What options_agent lacks:**
Every `yf.download()` call hits the network. Dashboard recomputes all indicators from scratch on every interaction. (Issues #17 from audit.)

**Plan:**
1. Create `src/utils/cache.py` with a simple `TTLCache` class (ported from options_analyzer's CachedProvider pattern):
   - `get(key) -> value | None`
   - `set(key, value, ttl_seconds)`
   - Thread-safe
2. Wrap `yf.download()` calls in `market_analyzer.py` with cache (TTL: 5 min for intraday, 15 min for daily)
3. In dashboard, use `@st.cache_data(ttl=300)` for indicator computations
4. This addresses issues #10 (partial) and #17

**Source files to reference:**
- `options_analyzer/src/scanner/providers/cached_provider.py`

---

## Phase G: Confluence Scoring Model (from `trade_generator.py`)

**What options_analyzer has:**
`src/trade_generator.py` uses OLS-regression-calibrated confluence scoring: edge_pct (35%), regime (15%), dealer (10%), bias (10%), skew (15%), timing (15%). Threshold: score >= 60 surfaces candidate. Weights are empirically validated from backtests.

**What options_agent has:**
11-point quality scoring in `src/utils/quality_scorer.py` with manually assigned weights. Not empirically validated.

**Plan:**
1. Don't replace the 11-point scorer yet — it works for the current strategy set (buy_call/buy_put scalps)
2. Add a `src/utils/confluence_scorer.py` for spread strategies (when added):
   - Port the weighted scoring model from options_analyzer
   - Configurable weights via `config/strategy_params.py` (ties into Phase 9 of REMAINING_FIXES_PLAN.md)
3. Add edge calculation: compare GARCH forward vol estimate vs chain IV (port from `options_analyzer/src/scanner/edge.py`)
4. This is **additive** — doesn't replace existing scoring, provides a second model for future spread strategies

**Source files to reference:**
- `options_analyzer/src/trade_generator.py` (confluence scoring, weights)
- `options_analyzer/src/scanner/edge.py` (IV-RV edge calculation)
- `options_analyzer/src/monte_carlo/garch_vol.py` (GARCH model)

---

## Phase H: Expand Strategy Library (from `strategies/`)

**What options_analyzer has:**
5 defined-risk strategies with validated backtests: iron condor, short put spread, short call spread, long call/put spread, butterfly. Each has ideal regimes, DTE ranges, IV ranges, and signal checklists. Backtest-validated Kelly stats show iron_condor and short_call_spread are **not tradeable** (negative Kelly).

**What options_agent has:**
2 strategies: buy_call, buy_put (0-3 DTE scalps). Covered call, protective put, and iron condor exist in code but resolve to buy_call/buy_put in practice (test failures confirm this).

**Plan:**
1. Port `short_put_spread` strategy (80.4% win rate, positive Kelly) as first spread strategy:
   - Adapt from `options_analyzer/src/strategies/credit_spread.py`
   - Map to options_agent's `BaseStrategy` interface
   - Use options_analyzer's exit rules (50% credit target, 2x stop, 1 DTE exit)
   - Requires: Phase C (exit management), Phase D (Kelly sizing), Phase A (liquidity filter)
2. Port `long_call_spread` (59.8% win rate) as second spread:
   - Adapt from `options_analyzer/src/strategies/debit_spread.py`
   - Momentum-driven, no edge gate — aligns with options_agent's momentum cascade approach
3. Port `butterfly` (50.6% win rate, highest avg win) as pin-play strategy:
   - Center on max pain (port max pain calc from options_analyzer)
4. Update `strategy_selector.py` with regime→strategy mappings for new strategies
5. Do NOT port iron_condor or short_call_spread (negative Kelly — options_analyzer's own backtests say don't trade them)

**Source files to reference:**
- `options_analyzer/src/strategies/credit_spread.py`
- `options_analyzer/src/strategies/debit_spread.py`
- `options_analyzer/src/strategies/butterfly.py`
- `options_analyzer/src/strategies/base.py` (StrategyDefinition ABC for reference)

---

## Deferred (Not in this plan)

These exist in options_analyzer but aren't worth porting now:

- **Monte Carlo suite** — GBM, jump-diffusion, American MC, MC Greeks. Powerful but heavy. Options_agent's scalp strategies don't need MC pricing; consider if/when adding longer-dated strategies.
- **Black-Scholes pricing engine** — Options_agent gets Greeks from the chain directly. Only needed if computing theo prices locally.
- **DXFeed streaming** — Tastytrade-specific. Options_agent uses MCP/Robinhood.
- **React frontend** — Options_agent has Streamlit. Different paradigm. Dashboard refactor is a separate effort.
- **SQLite chain storage** — Useful for historical validation but options_agent doesn't collect chains yet. Add when backtesting becomes a priority.
- **GEX/dealer positioning** — Requires FlashAlpha API key (paid). Port when options_agent adds spread strategies that benefit from dealer gamma analysis.
- **Intraday 0DTE support** — Options_agent already handles 0-3 DTE via buy_call/buy_put. options_analyzer's day classifier and move exhaustion are interesting but not critical.

---

## Execution Order

**Phase A** → **Phase B** → **Phase E** → **Phase F** → **Phase C** → **Phase D** → **Phase G** → **Phase H**

Rationale:
- A + B are foundational (filter + indicators) — everything else depends on clean contracts and shared math
- E + F (retry + cache) improve reliability before adding complexity
- C + D (exits + sizing) are prerequisites for spread strategies
- G + H (confluence scoring + new strategies) are the payoff — unlocking the spread strategy library

### Overlap with REMAINING_FIXES_PLAN.md
| REMAINING_FIXES Phase | Cross-Repo Phase | Disposition |
|---|---|---|
| Phase 6 (dead code + cascade) | — | Keep as-is, no analyzer equivalent |
| Phase 7 (liquidity filter) | **Phase A** | Replaced — use analyzer's proven filter |
| Phase 8 (indicator engine) | **Phase B** | Replaced — port analyzer's implementations |
| Phase 9 (magic numbers) | — | Keep as-is, do alongside Phase H |
| Phase 10 (retry) | **Phase E** | Replaced — covers same scope |
| Phase 11 (test coverage) | — | Keep as-is, expand tests as each phase lands |

---

## Verification

After all phases:
1. All existing tests pass (no regressions)
2. New strategies (short_put_spread, long_call_spread, butterfly) have tests
3. Liquidity filter rejects bad contracts (OI < threshold, wide spreads)
4. Exit manager closes positions at targets/stops
5. Kelly sizer produces reasonable contract counts
6. Retry decorator handles transient failures gracefully
7. Chain cache reduces yfinance API calls
8. `grep -r "def _rsi\|def _macd\|def _atr" src/` returns only `src/utils/indicators.py`
