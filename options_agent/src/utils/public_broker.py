"""Public.com execution backend — mirrors the AlpacaPaperTrader interface.

Places 0DTE single-leg option orders on a Public.com brokerage account via the official
`publicdotcom-py` SDK (`public_api_sdk`). Selected by `BROKER=public` (see `broker.py`).

Return shapes intentionally match `AlpacaPaperTrader` (src/utils/alpaca_paper.py) so the
agent, EOD reconciliation, and journal code work unchanged.

IMPORTANT — Public's API is real-money only (no paper/sandbox). Until a run is verified
against a real account, keep `PUBLIC_DRY_RUN=true` (default): entries/exits are routed
through Public's *preflight* endpoint (validated + logged, NOT placed).

Env:
  PUBLIC_API_SECRET_KEY   API secret key from public.com settings (required)
  PUBLIC_ACCOUNT_NUMBER   Brokerage account number (required for portfolio/order calls)
  PUBLIC_DRY_RUN          "true" (default) = preflight only; "false" = place real orders

Option *quotes* still come from Alpaca option data (data layer is unchanged), so
`get_option_quote` needs ALPACA_API_KEY/SECRET like the rest of the monitoring path.

Response-field mappings marked `# CONFIRM` should be validated against a live account —
the SDK's response object attribute names are documented loosely and may need adjustment.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger(__name__)

# Transient errors worth an in-line retry (network blips, rate limits, 5xx). A
# ValidationError / AuthenticationError / NotFoundError is NOT transient — fail
# fast. Classified by SDK exception class name (avoids importing the lazy SDK).
_TRANSIENT_EXC = {"RateLimitError", "ServerError"}
_TRANSIENT_MSG = (
    "timed out", "timeout", "connection", "temporarily unavailable", "max retries",
    "reset", "bad gateway", "service unavailable", "502", "503", "504",
)


def _is_transient(exc: Exception) -> bool:
    if type(exc).__name__ in _TRANSIENT_EXC:
        return True
    m = str(exc).lower()
    return any(s in m for s in _TRANSIENT_MSG)


def _status_name(order) -> str:
    """OrderStatus enum name (e.g. 'FILLED') regardless of str/enum rendering."""
    st = getattr(order, "status", None)
    return getattr(st, "name", str(st or "")).upper()


def _nested_float(obj, *path, default: float = 0.0) -> float:
    """Walk nested attributes (obj.a.b.c) and float the leaf; default on any miss.
    Public wraps money/prices in sub-objects (cost_basis.unit_cost,
    last_price.last_price, buying_power.options_buying_power, *_gain.gain_value)."""
    for p in path:
        obj = getattr(obj, p, None)
        if obj is None:
            return default
    try:
        return float(obj)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class PublicTrader:
    """Real-money 0DTE option execution on Public.com."""

    def __init__(self):
        # Lazy import so the module loads even when the SDK isn't installed / Public
        # isn't the selected broker.
        from public_api_sdk import (
            PublicApiClient,
            PublicApiClientConfiguration,
        )
        from public_api_sdk.auth_config import ApiKeyAuthConfig

        api_key = os.environ.get("PUBLIC_API_SECRET_KEY", "")
        self.account_number = os.environ.get("PUBLIC_ACCOUNT_NUMBER", "")
        if not api_key:
            raise RuntimeError("PUBLIC_API_SECRET_KEY not set in .env.public")
        if not self.account_number:
            raise RuntimeError("PUBLIC_ACCOUNT_NUMBER not set in .env.public")

        self.dry_run = _env_bool("PUBLIC_DRY_RUN", True)
        self.client = PublicApiClient(
            ApiKeyAuthConfig(api_secret_key=api_key),
            config=PublicApiClientConfiguration(default_account_number=self.account_number),
        )
        self._verify_account()

    def _verify_account(self):
        """Confirm the account is reachable and log balances + the safety mode."""
        portfolio = self.client.get_portfolio(account_id=self.account_number)
        logger.info(
            "Public.com account %s: $%s equity, $%s buying power — mode=%s",
            self.account_number,
            getattr(portfolio, "equity", "?"),
            getattr(portfolio, "buying_power", "?"),
            "DRY-RUN (preflight only)" if self.dry_run else "LIVE REAL-MONEY",
        )
        self.portfolio = portfolio

    # ── Order-request builders ────────────────────────────────────────────────
    def _single_leg_option_order(self, occ_symbol: str, side, qty: int, limit_price, tif,
                                 order_id: str | None = None):
        """Build a single-instrument OrderRequest for a 0DTE option (buy/sell to
        open/close), placed via `place_order`.

        NOTE: single-leg options use OrderRequest/place_order — NOT
        MultilegOrderRequest, which the SDK rejects for <2 legs. `order_id` is the
        client-supplied RFC-4122 UUID idempotency key; pass a stable value so a
        retried submit reuses the same id (dedup) instead of creating a 2nd order.
        """
        from public_api_sdk import (
            OrderRequest, OrderInstrument, InstrumentType, OrderType,
            OrderExpirationRequest, TimeInForce, OpenCloseIndicator,
        )
        open_close = (
            OpenCloseIndicator.OPEN
            if side_is_buy(side) else OpenCloseIndicator.CLOSE
        )
        return OrderRequest(
            order_id=order_id or str(uuid.uuid4()),
            instrument=OrderInstrument(symbol=occ_symbol, type=InstrumentType.OPTION),
            order_side=side,
            order_type=OrderType.LIMIT,
            expiration=OrderExpirationRequest(
                time_in_force=TimeInForce.DAY if tif == "day" else TimeInForce.GTC),
            quantity=qty,
            limit_price=Decimal(str(round(limit_price, 2))),
            open_close_indicator=open_close,
        )

    def _preflight_option(self, occ_symbol: str, side, qty: int, limit_price):
        """Validate an order without placing it (dry-run path)."""
        from public_api_sdk import (
            PreflightRequest, OrderInstrument, InstrumentType,
            OrderType, OrderExpirationRequest, TimeInForce,
        )
        req = PreflightRequest(
            instrument=OrderInstrument(symbol=occ_symbol, type=InstrumentType.OPTION),
            order_side=side,
            order_type=OrderType.LIMIT,
            expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
            quantity=qty,
            limit_price=Decimal(str(round(limit_price, 2))),
        )
        return self.client.perform_preflight_calculation(req)

    # ── Resilience helpers (Phase 1/2 hardening) ──────────────────────────────
    def _get_order_safe(self, order_id: str):
        """get_order(order_id) or None (never raises)."""
        try:
            return self.client.get_order(order_id=order_id, account_id=self.account_number)
        except Exception:
            return None

    def _place_with_retry(self, order_request, order_id: str, desc: str) -> str:
        """Submit an order idempotently, retrying transient errors.

        The request carries `order_id` (client idempotency key). On a transient
        failure we first check whether the order already landed (get_order) before
        resubmitting, so a lost response never creates a second order. Returns the
        broker order_id. Non-transient errors and the final attempt re-raise.
        """
        for i in range(1, 4):
            try:
                resp = self.client.place_order(order_request, account_id=self.account_number)
                return str(getattr(resp, "order_id", order_id))
            except Exception as e:
                if self._get_order_safe(order_id) is not None:
                    logger.info("%s: order %s already landed — not resubmitting (idempotent)",
                                desc, order_id)
                    return order_id
                if not _is_transient(e) or i == 3:
                    raise
                logger.warning("%s attempt %d/3 transient error: %s — retrying in 2s", desc, i, e)
                time.sleep(2.0)

    def _wait_for_fill(self, order_id: str, timeout: float, poll: float = 1.0):
        """Poll get_order until terminal. Returns the filled Order, or None if the
        order died (cancelled/rejected/expired) or the window elapsed unfilled.

        Public closes are resting LIMIT orders, so 'placed' != 'closed'. This is
        how the caller learns the close actually happened. (Hand-rolled rather than
        NewOrder.wait_for_fill so the idempotent-recovery path — which only has an
        order_id — shares one code path.)
        """
        deadline = time.time() + timeout
        while True:
            o = self._get_order_safe(order_id)
            if o is not None:
                st = _status_name(o)
                if st == "FILLED":
                    return o
                if st in ("CANCELLED", "REJECTED", "EXPIRED", "QUEUED_CANCELLED"):
                    return None
                # NEW / PARTIALLY_FILLED / PENDING_* → keep waiting
            if time.time() >= deadline:
                return None
            time.sleep(poll)

    def _order_is_for_symbol(self, order, occ_symbol: str) -> bool:
        inst = getattr(order, "instrument", None)
        if inst is not None and getattr(inst, "symbol", None) == occ_symbol:
            return True
        for leg in getattr(order, "legs", []) or []:
            li = getattr(leg, "instrument", None)
            if li is not None and getattr(li, "symbol", None) == occ_symbol:
                return True
        return False

    def _cancel_open_orders_for(self, occ_symbol: str) -> None:
        """Cancel any still-open order on this OCC symbol before placing a new one,
        so a retry / safety-net re-close never stacks multiple resting SELLs."""
        try:
            pf = self.client.get_portfolio(account_id=self.account_number)
        except Exception as e:
            logger.debug("cancel-before-replace: portfolio fetch failed: %s", e)
            return
        terminal = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED", "QUEUED_CANCELLED", "REPLACED"}
        for o in getattr(pf, "orders", []) or []:
            if _status_name(o) in terminal or not self._order_is_for_symbol(o, occ_symbol):
                continue
            oid = getattr(o, "order_id", None)
            if not oid:
                continue
            try:
                self.client.cancel_order(order_id=str(oid), account_id=self.account_number)
                logger.info("Cancelled resting order %s on %s before re-close", oid, occ_symbol)
            except Exception as e:
                logger.warning("cancel_order(%s) failed: %s", oid, e)

    # ── Interface: options entry/exit ─────────────────────────────────────────
    def place_options_trade(
        self,
        occ_symbol: str,
        direction: str,
        qty: int = 1,
        limit_price: float | None = None,
        time_in_force: str = "day",
    ) -> dict:
        """Buy-to-open a 0DTE option on Public. Mirrors AlpacaPaperTrader.place_options_trade.

        Public options orders require a limit price; if none is passed we derive a
        marketable limit from the current Alpaca mid (+ a small buffer for buys).
        """
        from public_api_sdk import OrderSide

        if limit_price is None:
            mid = self.get_option_quote(occ_symbol)
            # Marketable buy limit: cross the spread slightly to get filled.
            limit_price = round((mid or 0.10) * 1.05, 2) if mid else 0.10
        side = OrderSide.BUY

        if self.dry_run:
            pf = self._preflight_option(occ_symbol, side, qty, limit_price)
            logger.info(
                "[DRY-RUN] Public preflight BUY %d %s @ $%.2f — order_value=%s commission=%s",
                qty, occ_symbol, limit_price,
                getattr(pf, "order_value", "?"), getattr(pf, "estimated_commission", "?"),
            )
            return {
                "order_id": f"dryrun-{uuid.uuid4()}",
                "occ_symbol": occ_symbol, "side": "buy", "qty": qty,
                "type": "limit", "limit_price": limit_price,
                "status": "preflight_dryrun",
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            }

        order_id = str(uuid.uuid4())  # Public requires an RFC-4122 UUID idempotency key
        req = self._single_leg_option_order(occ_symbol, side, qty, limit_price,
                                            time_in_force, order_id=order_id)
        oid = self._place_with_retry(req, order_id, desc=f"place_options_trade({occ_symbol})")
        logger.info("Public options order placed: BUY %d %s @ $%.2f (id=%s)",
                    qty, occ_symbol, limit_price, oid)
        return {
            "order_id": oid, "occ_symbol": occ_symbol, "side": "buy", "qty": qty,
            "type": "limit", "limit_price": limit_price,
            "status": str(getattr(resp, "status", "submitted")),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }

    def close_options_position(self, occ_symbol: str, fill_timeout: float = 15.0) -> dict | None:
        """Sell-to-close an open option position, returning a truthy dict ONLY when
        the close is confirmed filled (mirrors the Alpaca 'truthy = closed' contract
        the agent relies on).

        Unlike Alpaca's idempotent market close, Public has no close primitive: we
        place a marketable-limit SELL, which may rest unfilled. So we
          1. cancel any prior open order on this symbol (no stacked SELLs),
          2. place an idempotent SELL,
          3. poll for the fill; if it doesn't fill within `fill_timeout`, cancel it
             and return None so the agent's pending_close retries next loop.
        """
        from public_api_sdk import OrderSide

        held = next((p for p in self.get_positions() if p["symbol"] == occ_symbol), None)
        if not held:
            logger.warning("close_options_position(%s): no open Public position", occ_symbol)
            return None
        qty = abs(int(float(held["qty"])))
        mid = self.get_option_quote(occ_symbol)
        # Marketable sell limit: cross down slightly to exit.
        limit_price = round((mid or 0.05) * 0.95, 2) if mid else 0.05

        if self.dry_run:
            self._preflight_option(occ_symbol, OrderSide.SELL, qty, limit_price)
            logger.info("[DRY-RUN] Public preflight SELL %d %s @ $%.2f (close)",
                        qty, occ_symbol, limit_price)
            return {"order_id": f"dryrun-{uuid.uuid4()}", "occ_symbol": occ_symbol,
                    "status": "preflight_dryrun"}

        # 1) No stacking: cancel any resting order on this symbol first.
        self._cancel_open_orders_for(occ_symbol)

        # 2) Place the SELL idempotently.
        order_id = str(uuid.uuid4())  # Public requires an RFC-4122 UUID idempotency key
        req = self._single_leg_option_order(occ_symbol, OrderSide.SELL, qty, limit_price,
                                            "day", order_id=order_id)
        try:
            self._place_with_retry(req, order_id, desc=f"close {occ_symbol}")
        except Exception as e:
            logger.warning("close_options_position(%s) placement failed: %s", occ_symbol, e)
            return None

        # 3) Poll-to-fill: truthy only when actually closed.
        filled = self._wait_for_fill(order_id, timeout=fill_timeout)
        if filled is None:
            try:
                self.client.cancel_order(order_id=order_id, account_id=self.account_number)
            except Exception:
                pass
            logger.warning("close_options_position(%s): SELL %s did not fill in %.0fs — "
                           "cancelled, position still OPEN", occ_symbol, order_id, fill_timeout)
            return None
        avg = float(getattr(filled, "average_price", 0) or 0)
        logger.info("Public option position closed: %s (order=%s avg=$%.2f)", occ_symbol, order_id, avg)
        return {"order_id": order_id, "occ_symbol": occ_symbol,
                "status": _status_name(filled), "fill_price": avg}

    def close_all(self):
        """Flatten every open option position (cancels open orders first)."""
        self.cancel_open_orders()
        for p in self.get_positions():
            self.close_options_position(p["symbol"])
        logger.info("Public: close-all requested (%s)",
                    "dry-run" if self.dry_run else "live")

    # ── Interface: account / positions ────────────────────────────────────────
    def get_positions(self) -> list[dict]:
        """Open positions in AlpacaPaperTrader's dict shape."""
        portfolio = self.client.get_portfolio(account_id=self.account_number)
        result = []
        for p in getattr(portfolio, "positions", []) or []:
            inst = getattr(p, "instrument", None)
            symbol = getattr(inst, "symbol", None) or "?"
            qty = _nested_float(p, "quantity")
            # Nested sub-objects, confirmed against a live account 2026-07-11:
            # cost_basis.unit_cost, last_price.last_price, instrument_gain.gain_*.
            result.append({
                "symbol": symbol,
                "qty": qty,
                "side": "long" if qty >= 0 else "short",
                "entry_price": _nested_float(p, "cost_basis", "unit_cost"),
                "current_price": _nested_float(p, "last_price", "last_price"),
                "unrealized_pnl": _nested_float(p, "instrument_gain", "gain_value"),
                "unrealized_pnl_pct": _nested_float(p, "instrument_gain", "gain_percentage"),
            })
        return result

    def get_today_pnl(self) -> dict:
        """Account balances in AlpacaPaperTrader's dict shape.

        Public's Portfolio.equity is a list of {type,value} buckets, buying_power is
        a nested object, and there is no previous_close_equity — so day P&L is summed
        from per-position position_daily_gain (confirmed against a live account
        2026-07-11; misses realized-and-closed-today).
        """
        portfolio = self.client.get_portfolio(account_id=self.account_number)
        equity = sum(_nested_float(e, "value") for e in (getattr(portfolio, "equity", []) or []))
        buying_power = _nested_float(portfolio, "buying_power", "options_buying_power")
        today_pnl = sum(_nested_float(p, "position_daily_gain", "gain_value")
                        for p in getattr(portfolio, "positions", []) or [])
        return {
            "equity": equity,
            "buying_power": buying_power,
            "today_pnl": today_pnl,
            "today_pnl_pct": (today_pnl / equity * 100) if equity else 0.0,
        }

    # ── Interface: order reconciliation ───────────────────────────────────────
    def get_order_outcome(self, order_id: str) -> dict | None:
        """Entry fill for a placed order (options are monitored/closed manually — no bracket)."""
        if str(order_id).startswith("dryrun-"):
            return {"actual_entry": None, "entry_filled_at": None,
                    "exit_price": None, "exit_time": None, "exit_reason": "open"}
        try:
            o = self.client.get_order(order_id=order_id, account_id=self.account_number)
        except Exception as e:
            logger.warning("get_order_outcome(%s) failed: %s", order_id, e)
            return None
        filled = getattr(o, "average_price", None)  # confirmed field (v0.1.17)
        return {
            "actual_entry": float(filled) if filled else None,
            "entry_filled_at": str(getattr(o, "closed_at", "") or "") or None,
            "exit_price": None, "exit_time": None,
            "exit_reason": "open",
        }

    def get_fill_price(self, order_id: str) -> dict | None:
        """Fill price/time for a single order (used for close-position reconciliation)."""
        if str(order_id).startswith("dryrun-"):
            return None
        try:
            o = self.client.get_order(order_id=order_id, account_id=self.account_number)
        except Exception as e:
            logger.warning("get_fill_price(%s) failed: %s", order_id, e)
            return None
        filled = getattr(o, "average_price", None)  # confirmed field (v0.1.17)
        if not filled:
            return None
        return {"price": float(filled), "filled_at": str(getattr(o, "closed_at", "") or "")}

    def get_orders(self, status: str = "all", limit: int = 20) -> list[dict]:
        """Current orders from the live portfolio (Portfolio.orders — confirmed shape).
        status='open' filters out terminal orders."""
        terminal = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED", "QUEUED_CANCELLED", "REPLACED"}
        try:
            pf = self.client.get_portfolio(account_id=self.account_number)
        except Exception as e:
            logger.debug("get_orders failed: %s", e)
            return []
        out = []
        for o in (getattr(pf, "orders", None) or []):
            st = _status_name(o)
            if status == "open" and st in terminal:
                continue
            inst = getattr(o, "instrument", None)
            sym = getattr(inst, "symbol", None)
            if not sym:
                legs = getattr(o, "legs", []) or []
                sym = getattr(getattr(legs[0], "instrument", None), "symbol", "?") if legs else "?"
            out.append({
                "id": str(getattr(o, "order_id", "")),
                "symbol": sym,
                "side": str(getattr(o, "side", "")),
                "status": st,
                "filled_price": getattr(o, "average_price", None),
            })
            if len(out) >= limit:
                break
        return out

    def cancel_open_orders(self):
        """Cancel all open orders (best-effort)."""
        for o in self.get_orders(status="open", limit=100):
            oid = o.get("id")
            if oid:
                try:
                    self.client.cancel_order(order_id=oid, account_id=self.account_number)
                except Exception as e:
                    logger.warning("cancel_order(%s) failed: %s", oid, e)
        logger.info("Public: open orders cancel requested")

    # ── Interface: option quote (delegates to Alpaca option data) ─────────────
    def get_option_quote(self, occ_symbol: str) -> float | None:
        """Current option mid — sourced from Alpaca option data (data layer is unchanged)."""
        try:
            from alpaca.data.historical import OptionHistoricalDataClient
            from alpaca.data.requests import OptionLatestQuoteRequest

            option_client = OptionHistoricalDataClient(
                api_key=os.environ.get("ALPACA_API_KEY", ""),
                secret_key=os.environ.get("ALPACA_SECRET_KEY", ""),
            )
            quotes = option_client.get_option_latest_quote(
                OptionLatestQuoteRequest(symbol_or_symbols=[occ_symbol])
            )
            q = quotes.get(occ_symbol)
            if q and q.bid_price and q.ask_price:
                return (float(q.bid_price) + float(q.ask_price)) / 2
        except Exception as e:
            logger.warning("get_option_quote(%s) failed: %s", occ_symbol, e)
        return None

    # ── Shares (disabled in live — matches the 2026-06-28 no-shares-fallback change) ──
    def place_sweet_spot_trade(self, *args, **kwargs):
        raise NotImplementedError(
            "PublicTrader is options-only; shares fallback is disabled in live trading.")

    def close_position(self, *args, **kwargs):
        raise NotImplementedError(
            "PublicTrader is options-only; shares fallback is disabled in live trading.")


def side_is_buy(side) -> bool:
    """True when an SDK OrderSide enum represents a BUY (name-based, enum-agnostic)."""
    return str(getattr(side, "name", side)).upper() == "BUY"
