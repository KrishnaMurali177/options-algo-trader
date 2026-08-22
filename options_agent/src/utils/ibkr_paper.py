"""IBKR Paper Trading — parallel to alpaca_paper.py.

Places real paper orders via Interactive Brokers when sweet spots trigger.
Public surface mirrors AlpacaPaperTrader so run_sweet_spot_agent.py can
be broker-swapped.

Setup:
  - TWS or IB Gateway running in PAPER mode (port 7497 for TWS paper,
    4002 for Gateway paper). API enabled.
  - .env: IBKR_HOST / IBKR_PORT / IBKR_CLIENT_ID_BASE — see ibkr_client.py.

Usage:
  from src.utils.ibkr_paper import IbkrPaperTrader
  trader = IbkrPaperTrader()
  order = trader.place_sweet_spot_trade("SPY", "buy_call",
                                        entry=710.50, stop=708.00, target=712.00)
"""

from __future__ import annotations

import logging
import math
from datetime import datetime

from src.utils.ibkr_client import get_ib, occ_to_option, stock_contract

logger = logging.getLogger(__name__)


def _to_num(x, default=None):
    if x is None:
        return default
    try:
        v = float(x)
        if math.isnan(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


class IbkrPaperTrader:
    """Paper-account execution via IBKR. Mirrors AlpacaPaperTrader."""

    def __init__(self, client_id_offset: int = 1):
        # Offset by default so paper trader gets a distinct clientId from
        # ibkr_data/ibkr_options readers if they run in the same process.
        self.ib = get_ib(client_id_offset=client_id_offset)
        self._verify_account()

    def _verify_account(self):
        summary = self.ib.accountSummary()
        # Log the paper account's cash + net-liq for a quick smoke test.
        cash = next((s for s in summary if s.tag == "TotalCashValue"), None)
        nl = next((s for s in summary if s.tag == "NetLiquidation"), None)
        logger.info(
            "IBKR paper account: cash=%s equity=%s",
            cash.value if cash else "?",
            nl.value if nl else "?",
        )

    # ── Equity sweet-spot bracket ─────────────────────────────────────────

    def place_sweet_spot_trade(
        self,
        symbol: str,
        direction: str,
        qty: int = 1,
        entry: float | None = None,
        stop: float | None = None,
        target: float | None = None,
        time_in_force: str = "day",
    ) -> dict:
        """Place a paper equity bracket order for a sweet spot trigger.

        direction "buy_call"/"buy_put" maps to BUY/SELL — SELL on IBKR
        with no existing position auto-shorts (paper account supports this).
        """
        from ib_async import LimitOrder, MarketOrder

        side = "BUY" if "call" in direction else "SELL"
        tif = "DAY" if time_in_force == "day" else "GTC"

        contract = stock_contract(symbol)
        self.ib.qualifyContracts(contract)

        entry_order = (
            LimitOrder(action=side, totalQuantity=qty, lmtPrice=round(entry, 2), tif=tif)
            if entry is not None
            else MarketOrder(action=side, totalQuantity=qty, tif=tif)
        )
        entry_order.transmit = not (stop is not None and target is not None)

        placed = self.ib.placeOrder(contract, entry_order)
        legs_info = []

        # If stop and target were both provided, wire a proper OCA bracket.
        if stop is not None and target is not None:
            close_side = "SELL" if side == "BUY" else "BUY"
            tp = LimitOrder(action=close_side, totalQuantity=qty,
                            lmtPrice=round(target, 2), tif=tif)
            tp.parentId = placed.order.orderId
            tp.transmit = False
            tp.orderRef = "target"

            from ib_async import StopOrder
            sl = StopOrder(action=close_side, totalQuantity=qty,
                           stopPrice=round(stop, 2), tif=tif)
            sl.parentId = placed.order.orderId
            sl.transmit = True   # last leg transmits the whole bundle
            sl.orderRef = "stop"

            tp_trade = self.ib.placeOrder(contract, tp)
            sl_trade = self.ib.placeOrder(contract, sl)
            legs_info = [
                {"order_id": str(tp_trade.order.orderId), "type": "target"},
                {"order_id": str(sl_trade.order.orderId), "type": "stop"},
            ]

        logger.info(
            "IBKR paper order: %s %d %s @ %s (id=%s)",
            side, qty, symbol,
            entry if entry else "MKT", placed.order.orderId,
        )

        return {
            "order_id": str(placed.order.orderId),
            "symbol": symbol,
            "side": side.lower(),
            "qty": qty,
            "type": "limit" if entry is not None else "market",
            "limit_price": entry,
            "stop_price": stop,
            "target_price": target,
            "status": placed.orderStatus.status,
            "submitted_at": datetime.utcnow().isoformat(),
            "legs": legs_info,
        }

    # ── Options ───────────────────────────────────────────────────────────

    def place_options_trade(
        self,
        occ_symbol: str,
        direction: str,
        qty: int = 1,
        limit_price: float | None = None,
        time_in_force: str = "day",
    ) -> dict:
        """Buy-to-open a single option contract."""
        from ib_async import LimitOrder, MarketOrder

        tif = "DAY" if time_in_force == "day" else "GTC"
        contract = occ_to_option(occ_symbol)
        self.ib.qualifyContracts(contract)

        order = (
            LimitOrder(action="BUY", totalQuantity=qty,
                       lmtPrice=round(limit_price, 2), tif=tif)
            if limit_price is not None
            else MarketOrder(action="BUY", totalQuantity=qty, tif=tif)
        )
        placed = self.ib.placeOrder(contract, order)

        logger.info(
            "IBKR options order: BUY %d %s @ %s (id=%s)",
            qty, occ_symbol,
            f"${limit_price:.2f}" if limit_price else "MKT",
            placed.order.orderId,
        )
        return {
            "order_id": str(placed.order.orderId),
            "occ_symbol": occ_symbol,
            "side": "buy",
            "qty": qty,
            "type": "limit" if limit_price is not None else "market",
            "limit_price": limit_price,
            "status": placed.orderStatus.status,
            "submitted_at": datetime.utcnow().isoformat(),
        }

    def close_options_position(self, occ_symbol: str,
                                wait_seconds: float = 20.0) -> dict | None:
        """Sell-to-close an open option position at market.

        Waits up to `wait_seconds` for the close order to actually fill before
        returning. The 2026-08-21 paper session showed the previous "fire and
        forget" version silently orphaning a SPY 767 position for 2 hours
        (fake "closed" in the journal, real position sat losing money until
        the EOD force-close swept it). Now we block until Filled/Cancelled/
        Inactive and surface a warning if it doesn't fill in time — caller
        can then escalate or retry.
        """
        from ib_async import MarketOrder

        try:
            contract = occ_to_option(occ_symbol)
            self.ib.qualifyContracts(contract)
        except Exception as e:
            logger.warning("close_options_position(%s) qualify failed: %s", occ_symbol, e)
            return None

        pos = next(
            (p for p in self.ib.positions()
             if p.contract.conId == contract.conId and p.position != 0),
            None,
        )
        if pos is None:
            logger.warning("No open position for %s", occ_symbol)
            return None

        qty = abs(int(pos.position))
        side = "SELL" if pos.position > 0 else "BUY"
        order = MarketOrder(action=side, totalQuantity=qty, tif="DAY")
        trade = self.ib.placeOrder(contract, order)
        logger.info("IBKR option close: %s %d %s (id=%s) — waiting for fill",
                    side, qty, occ_symbol, trade.order.orderId)

        # Block until terminal status or timeout. ib.sleep releases control
        # back to the network layer between polls.
        import time as _time
        deadline = _time.time() + wait_seconds
        while _time.time() < deadline:
            self.ib.waitOnUpdate(timeout=1.0)
            st = trade.orderStatus.status
            if st in {"Filled", "Cancelled", "Inactive", "ApiCancelled"}:
                break

        st = trade.orderStatus.status
        fill_px = _to_num(trade.orderStatus.avgFillPrice)
        if st == "Filled":
            logger.info("IBKR option close FILLED: %s %d %s @ $%.4f",
                        side, qty, occ_symbol, fill_px or 0.0)
        else:
            logger.warning("IBKR option close DID NOT FILL within %.0fs: %s status=%s "
                           "(position may still be open — EOD force-close will retry)",
                           wait_seconds, occ_symbol, st)

        return {
            "order_id": str(trade.order.orderId),
            "occ_symbol": occ_symbol,
            "status": st,
            "fill_price": fill_px,
            "filled": st == "Filled",
        }

    def close_position(self, symbol: str,
                       wait_seconds: float = 15.0) -> dict | None:
        """Close a single open equity position (mirrors AlpacaPaperTrader).

        Same fill-waiting behavior as close_options_position: block until
        Filled/Cancelled or timeout, so caller knows whether the close
        actually completed.
        """
        from ib_async import MarketOrder

        contract = stock_contract(symbol)
        self.ib.qualifyContracts(contract)
        pos = next(
            (p for p in self.ib.positions()
             if p.contract.conId == contract.conId and p.position != 0),
            None,
        )
        if pos is None:
            logger.warning("No open position for %s", symbol)
            return None
        qty = abs(int(pos.position))
        side = "SELL" if pos.position > 0 else "BUY"
        order = MarketOrder(action=side, totalQuantity=qty, tif="DAY")
        trade = self.ib.placeOrder(contract, order)
        logger.info("IBKR equity close: %s %d %s (id=%s) — waiting for fill",
                    side, qty, symbol, trade.order.orderId)

        import time as _time
        deadline = _time.time() + wait_seconds
        while _time.time() < deadline:
            self.ib.waitOnUpdate(timeout=1.0)
            if trade.orderStatus.status in {"Filled", "Cancelled", "Inactive", "ApiCancelled"}:
                break

        st = trade.orderStatus.status
        fill_px = _to_num(trade.orderStatus.avgFillPrice)
        if st != "Filled":
            logger.warning("IBKR equity close did not fill in %.0fs: %s status=%s",
                           wait_seconds, symbol, st)
        return {
            "order_id": str(trade.order.orderId),
            "symbol": symbol,
            "status": st,
            "fill_price": fill_px,
            "filled": st == "Filled",
        }

    def close_all(self):
        """Close all open positions at market. Doesn't cancel resting bracket legs
        — call cancel_open_orders() first if that matters."""
        from ib_async import MarketOrder

        for p in self.ib.positions():
            if not p.position:
                continue
            qty = abs(int(p.position))
            side = "SELL" if p.position > 0 else "BUY"
            order = MarketOrder(action=side, totalQuantity=qty, tif="DAY")
            self.ib.placeOrder(p.contract, order)
        logger.info("IBKR: submitted close-all")

    # ── Quotes / positions / P&L ─────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        out = []
        for p in self.ib.positions():
            if not p.position:
                continue
            avg = _to_num(p.avgCost, 0.0)
            # avgCost for options is per share ($x100 in premium terms)
            multiplier = int(getattr(p.contract, "multiplier", "1") or "1")
            entry_price = avg / multiplier if multiplier > 1 else avg
            # For options IBKR returns localSymbol like "SPY   240502C00500000"
            # (space-padded). Strip whitespace so the string matches the
            # compressed OCC form used everywhere else in the codebase.
            raw = p.contract.localSymbol or p.contract.symbol
            sym = "".join((raw or "").split())
            out.append({
                "symbol": sym,
                "qty": p.position,
                "side": "long" if p.position > 0 else "short",
                "entry_price": entry_price,
                "current_price": None,   # requires reqMktData; skipped for speed
                "unrealized_pnl": None,
                "unrealized_pnl_pct": None,
            })
        return out

    def get_today_pnl(self) -> dict:
        summary = self.ib.accountSummary()
        cash = _to_num(next((s.value for s in summary if s.tag == "TotalCashValue"), None), 0.0)
        nl = _to_num(next((s.value for s in summary if s.tag == "NetLiquidation"), None), 0.0)
        # IBKR gives daily-P&L via account tag "RealizedPnL"/"UnrealizedPnL"
        realized = _to_num(next((s.value for s in summary if s.tag == "RealizedPnL"), None), 0.0)
        unrealized = _to_num(next((s.value for s in summary if s.tag == "UnrealizedPnL"), None), 0.0)
        return {
            "equity": nl,
            "buying_power": cash,
            "today_pnl": (realized or 0.0) + (unrealized or 0.0),
            "today_pnl_pct": None,   # IBKR doesn't hand us prior-day equity directly
        }

    def get_orders(self, status: str = "all", limit: int = 20) -> list[dict]:
        trades = self.ib.trades()  # today's trades this client saw
        out = []
        for t in trades[-limit:]:
            st = t.orderStatus.status
            if status == "open" and st in {"Filled", "Cancelled", "Inactive"}:
                continue
            if status == "closed" and st not in {"Filled", "Cancelled", "Inactive"}:
                continue
            out.append({
                "id": str(t.order.orderId),
                "symbol": t.contract.localSymbol or t.contract.symbol,
                "side": t.order.action.lower(),
                "qty": t.order.totalQuantity,
                "type": t.order.orderType,
                "status": st,
                "filled_price": _to_num(t.orderStatus.avgFillPrice),
                "submitted_at": None,
                "filled_at": None,
            })
        return out

    def get_order_outcome(self, parent_order_id: str) -> dict | None:
        """Reconstruct entry + exit fills for a bracket parent.

        Looks up the parent trade, finds child legs with orderRef "target"/"stop"
        (set in place_sweet_spot_trade), returns fill and reason.
        """
        parent_id = int(parent_order_id)
        parent = next((t for t in self.ib.trades() if t.order.orderId == parent_id), None)
        if parent is None:
            return None

        out = {
            "actual_entry": _to_num(parent.orderStatus.avgFillPrice),
            "entry_filled_at": None,
            "exit_price": None,
            "exit_time": None,
            "exit_reason": "open",
        }
        # Find child legs (parentId==parent_id)
        for t in self.ib.trades():
            if getattr(t.order, "parentId", 0) != parent_id:
                continue
            if t.orderStatus.status != "Filled":
                continue
            fill_px = _to_num(t.orderStatus.avgFillPrice)
            if fill_px is None:
                continue
            out["exit_price"] = fill_px
            ref = getattr(t.order, "orderRef", "") or ""
            if ref == "target" or t.order.orderType == "LMT":
                out["exit_reason"] = "target"
            else:
                out["exit_reason"] = "stop"
            break
        return out

    def get_fill_price(self, order_id: str) -> dict | None:
        oid = int(order_id)
        t = next((t for t in self.ib.trades() if t.order.orderId == oid), None)
        if t is None:
            return None
        px = _to_num(t.orderStatus.avgFillPrice)
        if px is None:
            return None
        return {"price": px, "time": None}

    def get_option_quote(self, occ_symbol: str) -> float | None:
        """Current mid for an option contract. Requires OPRA subscription."""
        try:
            contract = occ_to_option(occ_symbol)
            self.ib.qualifyContracts(contract)
            ticker = self.ib.reqMktData(contract, "", False, False)
            self.ib.sleep(1.0)
            bid, ask = _to_num(ticker.bid), _to_num(ticker.ask)
            self.ib.cancelMktData(contract)
            if bid and ask and bid > 0 and ask > 0:
                return (bid + ask) / 2
        except Exception as e:
            logger.warning("get_option_quote(%s) failed: %s", occ_symbol, e)
        return None

    def cancel_open_orders(self):
        for t in self.ib.openTrades():
            self.ib.cancelOrder(t.order)
        logger.info("IBKR: cancel-all sent to open orders")
