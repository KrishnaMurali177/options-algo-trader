# Remaining Fixes Plan — Options Trading Agent

## Context
Phases 1–5 (CRITICAL_FIXES_PLAN.md) are complete. This plan covers the remaining 14 issues from the original audit, grouped into 6 phases ordered by impact. Each phase is independently committable.

### Already Fixed (Phases 1–5)
- ~~Silent data fallbacks~~ → custom exceptions + narrow catches
- ~~Dangerous portfolio_value fallback~~ → PortfolioDataError
- ~~No order confirmation~~ → require_confirmation gate
- ~~Portfolio delta never computed~~ → net delta from positions + order Greeks
- ~~No audit trail~~ → JSON-lines audit logger

---

## Phase 6: Dead Code Removal + Cascade Strike Application

**Problems:**
- `entry_analyzer.py`, `execution_guide.py`, `signal_tuner.py` are only used in the dashboard or scripts, never in the core agent flow. They add maintenance surface with no production value.
- `MomentumCascadeDetector` computes `recommended_strike_offset` in buy_call/buy_put, but the offset is only mentioned in the rationale text — never applied to actual strike selection via `_find_by_delta()`.

**Files to modify:**
- `src/strategies/buy_call.py` — apply `recommended_strike_offset` to shift target delta/strike
- `src/strategies/buy_put.py` — same
- `src/entry_analyzer.py` — remove (or move to `scripts/utils/` if dashboard needs it)
- `src/execution_guide.py` — remove (or move)
- `src/signal_tuner.py` — remove (or move)
- `dashboard/app.py` — update imports if modules are relocated

**Changes:**
1. In buy_call `construct_order()`: after cascade analysis, if `recommended_strike_offset > 0`, shift the target strike OTM by that many strikes before calling `_find_by_delta()`
2. In buy_put `construct_order()`: same logic for puts (shift strike lower)
3. Move `entry_analyzer.py`, `execution_guide.py`, `signal_tuner.py` to a `scripts/utils/` directory since they're only used by dashboard/scripts, not the agent core
4. Update dashboard imports accordingly

**Test strategy:** Add test that cascade offset > 0 results in a different strike than offset = 0. Verify dashboard still loads after import path changes.

---

## Phase 7: Option Liquidity Filtering

**Problem:** Strategies accept whatever contract matches the target delta, regardless of spread width or open interest. This risks poor fills and illiquid positions.

**Files to modify:**
- `src/strategies/buy_call.py` — add liquidity filter before strike selection
- `src/strategies/buy_put.py` — same
- `config/settings.py` — add `min_open_interest` and `max_bid_ask_spread_pct` settings

**Changes:**
1. Add to Settings:
   - `min_open_interest: int = Field(default=10, description="Minimum OI for contract eligibility")`
   - `max_bid_ask_spread_pct: float = Field(default=0.15, description="Max bid-ask spread as % of mid price")`
2. In both strategies, before `_find_by_delta()`, filter the chain:
   - Remove contracts with `open_interest < min_open_interest`
   - Remove contracts where `(ask - bid) / mid > max_bid_ask_spread_pct`
3. If no contracts survive filtering, raise `InsufficientDataError` with a clear message

**Test strategy:** Test that contracts with OI=0 or wide spreads are excluded. Test that good contracts still pass.

---

## Phase 8: Shared Indicator Engine

**Problem:** RSI is computed in 5 files, MACD in 3, ATR in 2 — each with its own static method. Any formula fix must be applied in every copy.

**Files to create:**
- `src/utils/indicators.py` — shared indicator functions

**Files to modify:**
- `src/market_analyzer.py` — replace `_rsi`, `_macd`, `_atr`, `_zlema` with imports
- `src/recent_momentum.py` — replace `_rsi` with import
- `src/opening_range.py` — replace `_rsi`, `_macd` with imports
- `src/momentum_cascade.py` — replace `_rsi` with import (if present)
- `src/backtester.py` — replace `_rsi`, `_macd` with imports
- `dashboard/app.py` — replace `_rsi_calc` with import

**Changes:**
1. Create `src/utils/indicators.py` with:
   - `rsi(close: pd.Series, period: int) -> float`
   - `macd(close: pd.Series) -> tuple[float, float, float]`
   - `atr(high, low, close: pd.Series, period: int) -> float`
   - `zlema(close: pd.Series, period: int) -> float`
2. Replace all local implementations with `from src.utils.indicators import rsi, macd, atr, zlema`
3. Remove the now-unused static methods from each file

**Test strategy:** Unit tests for each indicator function in `tests/test_indicators.py`. Run full test suite to confirm no regressions.

---

## Phase 9: Centralize Magic Numbers

**Problem:** Signal weights, delta targets, ATR multipliers, VIX thresholds, RSI gates, and time stops are hardcoded across strategies. Tuning requires editing multiple files and hoping you found every instance.

**Files to create:**
- `config/strategy_params.py` — dataclasses for strategy-specific parameters

**Files to modify:**
- `src/strategies/buy_call.py` — replace hardcoded values with params
- `src/strategies/buy_put.py` — same
- `src/utils/quality_scorer.py` — replace hardcoded weights with params

**Changes:**
1. Create parameter dataclasses:
   ```python
   @dataclass
   class ScalpParams:
       delta_target: float = 0.60
       delta_tolerance: float = 0.15
       rsi_oversold: float = 30
       rsi_overbought: float = 80
       atr_stop_multiplier: float = 0.02
       atr_risk_multiplier: float = 0.3
       profit_target_r1: float = 0.75
       profit_target_r2: float = 1.5
       time_stop_hour: int = 15
       time_stop_minute: int = 0
       sma20_discount_pct: float = 0.015
   ```
2. Strategies accept params in `__init__` with sensible defaults
3. Quality scorer weights become configurable

**Test strategy:** Existing tests should pass with defaults. Add test that overriding a param changes strategy behavior (e.g., different delta_target produces different strike).

---

## Phase 10: Retry + Circuit Breaker for External APIs

**Problem:** All yfinance and MCP calls fail on first error. No backoff, no throttling. A transient network blip kills the entire run.

**Files to create:**
- `src/utils/retry.py` — retry decorator with exponential backoff

**Files to modify:**
- `src/market_analyzer.py` — wrap `yf.download` calls
- `src/opening_range.py` — wrap `yf.download` in `_analyze_live`
- `src/recent_momentum.py` — same
- `src/momentum_cascade.py` — same
- `src/mcp_client.py` — wrap MCP tool calls
- `requirements.txt` — add `tenacity` (or implement manually)

**Changes:**
1. Create a `retry_on_transient` decorator:
   - Max 3 attempts, exponential backoff (1s, 2s, 4s)
   - Retry on `ConnectionError`, `TimeoutError`, `requests.exceptions.RequestException`
   - Do NOT retry on `DataFetchError` (those are intentional raises)
   - Log each retry attempt
2. Apply to all `yf.download()` call sites
3. Apply to MCP client methods (`get_account_info`, `get_positions`, `get_options_chain`, `place_option_order`)

**Test strategy:** Mock a function that fails twice then succeeds — verify retry decorator retries and eventually returns. Test that non-transient errors propagate immediately.

---

## Phase 11: Strategy Decoupling + Test Coverage

**Problem:**
- Buy Call/Put strategies instantiate their own `OpeningRangeAnalyzer` and `RecentMomentumAnalyzer` on every call, making them hard to test and tightly coupled.
- No tests exist for `opening_range.py`, `recent_momentum.py`, `momentum_cascade.py`, `backtester.py`, or `quality_scorer.py`.

**Files to modify:**
- `src/strategies/buy_call.py` — accept analyzers via `__init__`
- `src/strategies/buy_put.py` — same
- `src/strategies/base_strategy.py` — add optional analyzer slots

**Files to create:**
- `tests/test_opening_range.py`
- `tests/test_recent_momentum.py`
- `tests/test_momentum_cascade.py`
- `tests/test_quality_scorer.py`

**Changes:**
1. Strategies accept optional `opening_range_analyzer` and `momentum_analyzer` in `__init__`, defaulting to fresh instances
2. Write tests for each analysis module using synthesized/mock data:
   - `test_opening_range.py`: test synthesized mode produces valid result, test with pre-fetched bars
   - `test_recent_momentum.py`: test synthesized mode, test `_score()` with mock DataFrame
   - `test_momentum_cascade.py`: test synthesized mode, test signal scoring
   - `test_quality_scorer.py`: test score boundaries (0 and 11), test each signal contribution

**Test strategy:** All new tests use mock/synthesized data, no network calls. Run full suite to verify no regressions.

---

## Deferred (Not in this plan)

These items are valid but lower priority or require larger architectural decisions:

- **Dashboard refactor** (issue #13) — 2100-line monolith that duplicates core logic. Worth a dedicated effort once the core is stable.
- **Dashboard caching** (issue #17) — add `@st.cache_data` after dashboard refactor
- **Long function decomposition** (issue #18) — `_analyze_live` in opening_range (188 lines) and momentum_cascade (185 lines). Refactor after test coverage exists (Phase 11).
- **Monitoring/metrics** (issue #16) — Prometheus/alerting. Operational concern, not a code safety issue.
- **Phase 2 features** (issue #19) — Spread strategies, multi-symbol scanning, Slack alerts, ML regime. Feature work, not fixes.

---

## Execution Order

Phase 6 → Phase 7 → Phase 8 → Phase 9 → Phase 10 → Phase 11

Each phase: implement → run tests → update CHANGELOG.md → commit.

## Verification

After all phases:
1. `docker exec options_agent-dashboard-1 python3 -m pytest tests/` — all tests pass
2. No duplicate RSI/MACD/ATR implementations remain
3. `grep -r "TODO\|FIXME\|HACK" src/` — review remaining TODOs
4. Strategies use injected analyzers and configurable params
5. Transient API failures retry gracefully
