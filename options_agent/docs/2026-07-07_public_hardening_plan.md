# Public.com broker hardening — plan

**Date:** 2026-07-07
**Branch:** feature/mag7-single-name-agents
**Scope:** Make `src/utils/public_broker.py` (PublicTrader) network-resilient and
order-safe, on par with the Alpaca hardening committed 2026-07-06
(`37a1fbe`, `ea345ae`, `19fb113`). This is item **C** from that work.

## Why Public needs its own design (not a cherry-pick of the Alpaca fix)

Public's order flow is fundamentally different from Alpaca's:

| | Alpaca | Public |
|---|---|---|
| Entry | `submit_order` (market ok) | `place_multileg_order`, **LIMIT only**, client-supplied `order_id` (uuid) |
| Close | `close_position(occ)` — one **idempotent** call, ~instant market fill | **No close primitive.** Place a new marketable-limit SELL (`mid × 0.95`) |
| "Success" means | position closed | order **accepted** — a resting limit that **may never fill** |
| Retry safety | full-close is idempotent | resubmit = a **second order** (stacking risk) |

Two consequences:
1. **`close_options_position` returning truthy ≠ closed.** Our agent marks the
   trade closed and stops monitoring on a truthy result. For Public, truthy =
   order *placed*, not *filled*. `_verify_closes_against_broker` would catch an
   unfilled position, but its re-close would place **another** resting SELL →
   stacked orders.
2. **Entry/close retries aren't idempotent.** Public generates a fresh `order_id`
   uuid *inside* each build call, so a network-blip retry submits a new order.
   Public *does* let the client supply `order_id`, so it's well-suited to
   idempotency once we generate it once and reuse it — pending confirmation that
   Public treats it as a dedup key.

## Decisions (locked 2026-07-07)
- **Close semantics:** poll-to-fill. `close_options_position` places the SELL,
  polls for the fill over a bounded window, returns truthy ONLY on confirmed
  fill (else cancels the resting order and returns None). Preserves the
  broker-agnostic "truthy = closed" contract; reuses pending_close + safety nets.
- **Verification:** real funded account available; a single 1-contract real
  round trip is permitted to confirm response fields + idempotency. No real
  order until explicitly greenlit; DRY-RUN preflight + SDK introspection first.

## Current unknowns (all `# CONFIRM` in public_broker.py)
The SDK (`publicdotcom-py` / `public_api_sdk`) is in requirements.txt but **not
installed in the running image** — Public can't even load today. Unverified:
- `place_multileg_order` return object shape (`order_id`? `status`?)
- `get_order` fields: `filled_price` / `average_price`, `filled_at`, `status`
  enum values
- Whether request `order_id` is an **idempotency/dedup key**
- `cancel_order` signature + how to list open orders (`get_history`? shape?)
- Portfolio/position fields (`average_cost`, `current_price`, `unrealized_pnl`,
  `previous_close_equity`)

## Phases

### Phase 0 — Ground truth (blocking)
- [x] Rebuild image so `public_api_sdk` installs. (v0.1.17, image rebuilt 2026-07-07)
- [x] Introspect SDK models/methods; resolve every `# CONFIRM`. (see findings below)
- [~] Confirm request-`order_id` idempotency semantics. (client-suppliable everywhere;
      dedup-on-resubmit still to confirm via real order)
- [ ] DRY-RUN verification harness (preflight paths) — needs market hours + creds.
- [ ] (Greenlit) one real 1-contract 0DTE round trip → lock down field names.

### Phase 1 — Idempotent placement + transient retry (Public-local)
- [ ] Local `_is_transient` + retry helper (no Alpaca import).
- [ ] `place_options_trade`: generate `order_id` once, thread into
      `_single_leg_option_order` (new param), reuse across retries; on transient
      failure look up via `get_order(order_id)` before resubmitting.

### Phase 2 — Poll-to-fill close + cancel-before-replace (core fix)
- [ ] `close_options_position`: cancel existing open order for the symbol →
      place idempotent marketable-limit SELL → poll `get_order` for fill over a
      bounded window (re-cross more aggressively if needed) → filled: return
      truthy with real fill; not filled: cancel + return None.
- [ ] Config knobs: poll timeout, interval, re-cross step.

### Phase 3 — Agent-contract validation
- [ ] Verify pending_close, "already gone" guard, and
      `_verify_closes_against_broker` behave under Public (re-close inherits
      cancel+poll → no stacking). Confirm reconcile field lookups (Phase 0).

### Phase 4 — End-to-end test
- [ ] DRY-RUN preflight round trip through the agent.
- [ ] One real 1-contract live round trip (market hours) — validate journal +
      report vs the A/B reconcile fixes.
- [ ] Roll out behind `PUBLIC_DRY_RUN` + 1-contract cap.

## Phase 0 findings (2026-07-07) — SDK introspected, unknowns resolved

`public_api_sdk` **0.1.17** installed cleanly after image rebuild. The SDK is far
more capable than `public_broker.py` currently assumes — it has built-in
fill-waiting and idempotency, which simplifies the design.

**Game-changers**
- `place_multileg_order(...)` returns a **`NewOrder`** with:
  `wait_for_fill(timeout, on_partial_fill, polling_interval=1.0) -> Order`,
  `wait_for_terminal_status(timeout)`, `wait_for_status(...)`, `get_status()`,
  `get_details() -> Order`, `cancel()`. → **poll-to-fill is built in** (Phase 2
  uses `wait_for_fill`, not a hand-rolled loop).
- **`order_id` is a client-supplied field on every order request/method** →
  idempotency key is native. (Real-order test still confirms dedup-on-resubmit.)
- `Portfolio.orders` lists current orders directly → cancel-before-replace needs
  no `get_history`. `cancel_and_replace_order(CancelAndReplaceRequest)` exists
  for re-crossing a resting limit (has its own `request_id`).

**Exceptions** (`public_api_sdk.exceptions`): `APIError` (base) → `RateLimitError`,
`ServerError`, `AuthenticationError`, `NotFoundError`, `ValidationError`; plus
`WaitTimeoutError` (from `wait_for_*`). Transient-retry set = `RateLimitError`,
`ServerError`, network/`ConnectionError`. Fail-fast = `ValidationError`,
`AuthenticationError`, `NotFoundError`. `WaitTimeoutError` = fill timeout (handled
by close, not the submit-retry).

**Corrected field mappings** (fixes the `# CONFIRM` guesses; apply in Phase 1):
- `Order` (get_order / wait_for_fill result): fill price = **`average_price`**
  (not `filled_price`), fill qty = **`filled_quantity`**, terminal time =
  **`closed_at`**, plus `status` (OrderStatus enum), `reject_reason`.
- `PortfolioPosition`: entry = **`cost_basis`** (÷ `quantity` for per-share),
  current px = **`last_price`**, unrealized P&L = **`instrument_gain`**, value =
  `current_value`. (Guessed `average_cost`/`current_price`/`unrealized_pnl` are wrong.)
- `Portfolio`: `equity`, `buying_power`, `positions`, **`orders`**. **No
  `previous_close_equity`** → `get_today_pnl` can't compute day P&L that way
  (approximate via Σ `position_daily_gain`, or leave equity-only).
- `PreflightResponse`: `order_value`, `estimated_commission`, `estimated_cost`,
  `estimated_proceeds`, `buying_power_requirement`… (dry-run logging is correct).
- `get_history` → `HistoryResponsePage.transactions` (not `.orders`); the current
  `get_orders` using `hist.orders` is broken — replace with `Portfolio.orders`.

`OrderStatus`: FILLED, PARTIALLY_FILLED, NEW, CANCELLED, REJECTED, EXPIRED,
PENDING_CANCEL, PENDING_REPLACE, REPLACED, QUEUED_CANCELLED, UNKNOWN.

**Design deltas from these findings**
- Phase 2 close = place SELL (idempotent order_id) → `NewOrder.wait_for_fill(
  timeout≈12s)`; on `WaitTimeoutError` → `cancel()` → return None. Re-cross via
  `cancel_and_replace_order` if we want a more aggressive second attempt.
- Phase 1 retry keys on the SDK exception types above (not string matching).

**Still pending (needs market hours + account):** live preflight round trip, then
the greenlit 1-contract real order to confirm idempotency + `closed_at`/
`average_price` on a real fill. Harness: `scripts/verify_public_broker.py`.

## Risks / notes
- No paper sandbox — every real test costs real money → 1-contract, DRY-RUN
  default, market-hours only.
- Poll-to-fill blocks that agent's loop ~12s; fine at 5-min cadence, other
  symbols are separate containers.
- 0DTE marketable limits should fill fast; poll window + re-cross is the safety
  valve for wide spreads near close.
