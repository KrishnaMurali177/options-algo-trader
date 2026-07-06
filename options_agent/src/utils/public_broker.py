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
import uuid
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger(__name__)


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
    def _single_leg_option_order(self, occ_symbol: str, side, qty: int, limit_price, tif):
        """Build a one-leg MultilegOrderRequest for an option (buy/sell to open/close)."""
        from public_api_sdk import (
            MultilegOrderRequest, OrderLegRequest, LegInstrument,
            LegInstrumentType, OrderType, OrderExpirationRequest, TimeInForce,
            OpenCloseIndicator,
        )
        open_close = (
            OpenCloseIndicator.OPEN
            if side_is_buy(side) else OpenCloseIndicator.CLOSE
        )
        expiration = OrderExpirationRequest(
            time_in_force=TimeInForce.DAY if tif == "day" else TimeInForce.GTC
        )
        return MultilegOrderRequest(
            order_id=str(uuid.uuid4()),
            quantity=qty,
            type=OrderType.LIMIT,               # Public options orders use LIMIT
            limit_price=Decimal(str(round(limit_price, 2))),
            expiration=expiration,
            legs=[OrderLegRequest(
                instrument=LegInstrument(symbol=occ_symbol, type=LegInstrumentType.OPTION),
                side=side,
                open_close_indicator=open_close,
                ratio_quantity=1,
            )],
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

        order = self._single_leg_option_order(occ_symbol, side, qty, limit_price, time_in_force)
        resp = self.client.place_multileg_order(order)
        oid = str(getattr(resp, "order_id", order.order_id))  # CONFIRM response field
        logger.info("Public options order placed: BUY %d %s @ $%.2f (id=%s)",
                    qty, occ_symbol, limit_price, oid)
        return {
            "order_id": oid, "occ_symbol": occ_symbol, "side": "buy", "qty": qty,
            "type": "limit", "limit_price": limit_price,
            "status": str(getattr(resp, "status", "submitted")),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }

    def close_options_position(self, occ_symbol: str) -> dict | None:
        """Sell-to-close an open option position. Mirrors the Alpaca method."""
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

        try:
            order = self._single_leg_option_order(occ_symbol, OrderSide.SELL, qty, limit_price, "day")
            resp = self.client.place_multileg_order(order)
        except Exception as e:
            logger.warning("close_options_position(%s) failed: %s", occ_symbol, e)
            return None
        oid = str(getattr(resp, "order_id", order.order_id))  # CONFIRM
        logger.info("Public option position closed: %s (order=%s)", occ_symbol, oid)
        return {"order_id": oid, "occ_symbol": occ_symbol,
                "status": str(getattr(resp, "status", "submitted"))}

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
            symbol = getattr(inst, "symbol", None) or getattr(p, "symbol", "?")
            qty = float(getattr(p, "quantity", 0) or 0)
            entry = float(getattr(p, "average_cost", 0) or getattr(p, "cost_basis", 0) or 0)  # CONFIRM
            cur = float(getattr(p, "current_price", 0) or getattr(p, "last_price", 0) or 0)    # CONFIRM
            upl = float(getattr(p, "unrealized_pnl", 0) or 0)                                  # CONFIRM
            result.append({
                "symbol": symbol,
                "qty": qty,
                "side": "long" if qty >= 0 else "short",
                "entry_price": entry,
                "current_price": cur,
                "unrealized_pnl": upl,
                "unrealized_pnl_pct": (upl / (abs(entry) * abs(qty)) * 100) if entry and qty else 0.0,
            })
        return result

    def get_today_pnl(self) -> dict:
        """Account balances in AlpacaPaperTrader's dict shape."""
        portfolio = self.client.get_portfolio(account_id=self.account_number)
        equity = float(getattr(portfolio, "equity", 0) or 0)
        last_equity = float(getattr(portfolio, "previous_close_equity", 0) or 0)  # CONFIRM
        today_pnl = (equity - last_equity) if last_equity else 0.0
        return {
            "equity": equity,
            "buying_power": float(getattr(portfolio, "buying_power", 0) or 0),
            "today_pnl": today_pnl,
            "today_pnl_pct": (today_pnl / last_equity * 100) if last_equity else 0.0,
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
        filled = getattr(o, "filled_price", None) or getattr(o, "average_price", None)  # CONFIRM
        return {
            "actual_entry": float(filled) if filled else None,
            "entry_filled_at": str(getattr(o, "filled_at", "") or "") or None,
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
        filled = getattr(o, "filled_price", None) or getattr(o, "average_price", None)  # CONFIRM
        if not filled:
            return None
        return {"price": float(filled), "filled_at": str(getattr(o, "filled_at", "") or "")}

    def get_orders(self, status: str = "all", limit: int = 20) -> list[dict]:
        """Best-effort recent orders. Public's history shape needs live confirmation."""
        try:
            hist = self.client.get_history(account_id=self.account_number)  # CONFIRM method/shape
        except Exception as e:
            logger.debug("get_orders failed: %s", e)
            return []
        out = []
        for o in (getattr(hist, "orders", None) or [])[:limit]:
            out.append({
                "id": str(getattr(o, "order_id", "")),
                "symbol": getattr(o, "symbol", "?"),
                "side": str(getattr(o, "side", "")),
                "status": str(getattr(o, "status", "")),
                "filled_price": getattr(o, "filled_price", None),
            })
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
