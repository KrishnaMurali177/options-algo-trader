# Critical Fixes Plan — Options Trading Agent

## Context
The options trading agent handles real capital. Five critical issues compromise safety, correctness, and auditability. Each phase is independently committable.

---

## Phase 1: Custom Exceptions + Explicit Fallback Handling

**Problem:** `opening_range.py`, `recent_momentum.py`, `momentum_cascade.py` catch broad `Exception`, silently swallowing programming bugs (TypeError, AttributeError) alongside genuine data fetch failures.

**Files to modify:**
- `src/exceptions.py` (NEW) — custom exception classes
- `src/opening_range.py:92-99` — narrow catch to `DataFetchError`
- `src/recent_momentum.py:63-70` — same
- `src/momentum_cascade.py:73-79` — same

**Changes:**
1. Create `src/exceptions.py` with:
   - `DataFetchError(Exception)` — raised when yfinance/MCP data unavailable
   - `InsufficientDataError(DataFetchError)` — raised when bars/candles too few
2. In each analyzer's `_analyze_live()`, replace `ValueError` raises with `DataFetchError` / `InsufficientDataError`
3. In each analyzer's `analyze()`, change `except Exception` → `except DataFetchError`
4. Programming bugs (TypeError, KeyError, etc.) will now propagate and crash visibly

**Test strategy:** Existing tests should still pass. Add test that a `TypeError` inside `_analyze_live` propagates (not caught).

---

## Phase 2: Portfolio Value Validation

**Problem:** `risk_manager.py:29` defaults `portfolio_value` to `1.0` if None/zero, making all percentage-based checks meaningless.

**Files to modify:**
- `src/risk_manager.py:29` — raise error instead of fallback
- `src/exceptions.py` — add `PortfolioDataError`

**Changes:**
1. Add `PortfolioDataError(Exception)` to `src/exceptions.py`
2. Replace `pv = portfolio.account.portfolio_value or 1.0` with:
   ```python
   pv = portfolio.account.portfolio_value
   if not pv or pv <= 0:
       raise PortfolioDataError(
           f"Invalid portfolio value: {pv}. Cannot perform risk checks."
       )
   ```

**Test strategy:** Update `tests/test_risk_manager.py` — add test for zero/None portfolio_value raising `PortfolioDataError`. Existing tests already set valid portfolio values, so they pass unchanged.

---

## Phase 3: Order Confirmation Gate for Live Trading

**Problem:** `agent.py:125-131` executes orders immediately in live mode with no human-in-the-loop.

**Files to modify:**
- `config/settings.py` — add `require_confirmation` setting
- `src/agent.py` — add confirmation gate + `confirm_and_execute()` method

**Changes:**
1. Add to Settings: `require_confirmation: bool = Field(default=True, description="Require explicit confirmation before live execution")`
2. In `agent.py` Step 6, when `dry_run=False` and `require_confirmation=True`:
   - Store pending order in `self._pending_order`
   - Return summary with `status="awaiting_confirmation"` and full order details
   - Do NOT execute
3. Add `async def confirm_and_execute(self) -> dict`:
   - Checks `self._pending_order` exists
   - Connects to MCP, places order, returns execution result
   - Clears `self._pending_order`
4. When `require_confirmation=False`, execute immediately (existing behavior)

**Test strategy:** Update `tests/test_agent.py` — test that live mode with confirmation returns "awaiting_confirmation" status. Test `confirm_and_execute` places the order.

---

## Phase 4: Portfolio Delta Computation

**Problem:** `risk_manager.py:85` hardcodes `portfolio_delta_after=0.0`. No Greek exposure tracking.

**Files to modify:**
- `src/models/portfolio.py` — add `delta` field to `OptionPosition`
- `src/risk_manager.py` — compute net delta
- `src/agent.py` — pass delta when building OptionPosition from MCP data

**Changes:**
1. Add `delta: float = 0.0` to `OptionPosition` model
2. In `agent.py:_build_portfolio()`, populate `delta` from MCP position data: `delta=float(p.get("delta", 0))`
3. In `risk_manager.py`, compute:
   ```python
   current_delta = sum(
       pos.delta * pos.quantity * 100 for pos in portfolio.option_positions
   )
   order_delta = sum(
       leg.quantity * 100 * self._leg_delta(leg, order)
       for leg in order.legs
   )
   portfolio_delta_after = current_delta + order_delta
   ```
4. Add helper `_leg_delta()` that extracts delta from the order context
5. Pass `order_delta` into validate as optional param (since Greeks come from the chain, not the order model)

**Refined approach:** Since `OptionOrder` doesn't carry Greeks, add an optional `greeks: Optional[Greeks]` field to `OptionLeg`. Strategies already have access to Greeks during construction — populate them.

**Test strategy:** Update `tests/test_risk_manager.py` with positions that have delta values, verify `portfolio_delta_after` is computed correctly.

---

## Phase 5: Persistent Audit Trail

**Problem:** No durable record of agent decisions, orders, or outcomes. Only stdout logs.

**Files to create:**
- `src/audit.py` (NEW) — audit logger

**Files to modify:**
- `src/agent.py` — integrate audit logging at end of `run()`
- `docker-compose.yml` — add volume mount for `audit_logs/`
- `.gitignore` — add `audit_logs/`

**Changes:**
1. Create `src/audit.py`:
   - `AuditLogger` class with `log(summary: dict)` method
   - Writes JSON-lines to `audit_logs/YYYY-MM-DD.jsonl`
   - Creates directory if not exists
   - Each line: full agent summary (timestamp, symbol, regime, strategy, order, risk, execution result)
2. In `agent.py`, instantiate `AuditLogger` and call `self.audit.log(summary)` in the `finally` block (logs every run, success or failure)
3. Add `audit_logs/` volume mount in docker-compose.yml
4. Add `audit_logs/` to `.gitignore`

**Test strategy:** Unit test that `AuditLogger.log()` creates file and writes valid JSON lines.

---

## Execution Order

Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5

Each phase: implement → run tests → update CHANGELOG.md → commit.

## Verification

After all phases:
1. `python -m pytest tests/` — all tests pass
2. `python scripts/run_agent.py SPY --dry-run --verbose` — agent runs with new error handling, audit log created
3. Check `audit_logs/` for valid JSONL output
4. Verify synthesized data fallback only catches `DataFetchError`
5. Verify `portfolio_value=0` raises `PortfolioDataError`
