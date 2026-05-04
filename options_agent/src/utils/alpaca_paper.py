"""Alpaca Paper Trading — execute sweet spot trades on paper account.

Places real paper orders via Alpaca when sweet spots trigger.
Tracks fills, P&L, and outcomes automatically.

Setup:
  Requires ALPACA_API_KEY and ALPACA_SECRET_KEY in .env
  (same keys used for historical data — paper account).

Usage:
    from src.utils.alpaca_paper import AlpacaPaperTrader

    trader = AlpacaPaperTrader()
    order = trader.place_sweet_spot_trade("SPY", "buy_call", entry=710.50, stop=708.00, target=712.00)
    # Returns order ID — tracked automatically

    # Check open positions
    trader.get_positions()

    # Get today's P&L
    trader.get_today_pnl()
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class AlpacaPaperTrader:
    """Paper trade execution via Alpaca for sweet spot validation."""

    def __init__(self):
        from alpaca.trading.client import TradingClient

        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")

        if not api_key or not secret_key:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set in .env")

        # paper=True ensures we hit the paper trading endpoint
        self.client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)
        self._verify_account()

    def _verify_account(self):
        """Verify paper account is accessible."""
        account = self.client.get_account()
        logger.info(
            "Alpaca paper account: $%s buying power, $%s equity",
            account.buying_power, account.equity,
        )
        self.account = account

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
        """Place a paper trade for a sweet spot trigger.

        Args:
            symbol: Stock symbol (e.g., "SPY")
            direction: "buy_call" or "buy_put" — maps to buy/short
            qty: Number of shares (default 1 for tracking)
            entry: Limit price for entry (None = market order)
            stop: Stop loss price
            target: Take profit price
            time_in_force: "day" (close at EOD) or "gtc"

        Returns:
            Dict with order details.
        """
        from alpaca.trading.requests import (
            LimitOrderRequest,
            MarketOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

        # Map direction to buy/sell
        if "call" in direction:
            side = OrderSide.BUY
        else:
            side = OrderSide.SELL  # Short for puts

        tif = TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC

        # Build order with bracket (stop + target) if provided
        if stop and target:
            # Bracket order: entry + stop loss + take profit
            if entry:
                order_data = LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=tif,
                    limit_price=round(entry, 2),
                    order_class=OrderClass.BRACKET,
                    stop_loss=StopLossRequest(stop_price=round(stop, 2)),
                    take_profit=TakeProfitRequest(limit_price=round(target, 2)),
                )
            else:
                order_data = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=tif,
                    order_class=OrderClass.BRACKET,
                    stop_loss=StopLossRequest(stop_price=round(stop, 2)),
                    take_profit=TakeProfitRequest(limit_price=round(target, 2)),
                )
        elif entry:
            order_data = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side,
                time_in_force=tif,
                limit_price=round(entry, 2),
            )
        else:
            order_data = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side,
                time_in_force=tif,
            )

        order = self.client.submit_order(order_data)
        logger.info(
            "Paper order placed: %s %s %d %s @ %s (id=%s)",
            side.value, symbol, qty,
            "limit" if entry else "market",
            entry or "MKT", order.id,
        )

        return {
            "order_id": str(order.id),
            "symbol": symbol,
            "side": side.value,
            "qty": qty,
            "type": "limit" if entry else "market",
            "limit_price": entry,
            "stop_price": stop,
            "target_price": target,
            "status": order.status.value,
            "submitted_at": str(order.submitted_at),
        }

    def get_positions(self) -> list[dict]:
        """Get all open paper positions."""
        positions = self.client.get_all_positions()
        result = []
        for p in positions:
            result.append({
                "symbol": p.symbol,
                "qty": p.qty,
                "side": p.side.value,
                "entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "unrealized_pnl": float(p.unrealized_pl),
                "unrealized_pnl_pct": float(p.unrealized_plpc) * 100,
            })
        return result

    def get_today_pnl(self) -> dict:
        """Get today's paper trading P&L."""
        account = self.client.get_account()
        return {
            "equity": float(account.equity),
            "buying_power": float(account.buying_power),
            "today_pnl": float(account.equity) - float(account.last_equity),
            "today_pnl_pct": (float(account.equity) - float(account.last_equity)) / float(account.last_equity) * 100,
        }

    def get_orders(self, status: str = "all", limit: int = 20) -> list[dict]:
        """Get recent paper orders."""
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        status_map = {
            "all": QueryOrderStatus.ALL,
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
        }

        request = GetOrdersRequest(
            status=status_map.get(status, QueryOrderStatus.ALL),
            limit=limit,
        )
        orders = self.client.get_orders(request)
        result = []
        for o in orders:
            result.append({
                "id": str(o.id),
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": o.qty,
                "type": o.type.value,
                "status": o.status.value,
                "filled_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "submitted_at": str(o.submitted_at),
                "filled_at": str(o.filled_at) if o.filled_at else None,
            })
        return result

    def close_all(self):
        """Close all open paper positions."""
        self.client.close_all_positions(cancel_orders=True)
        logger.info("All paper positions closed")

    def close_position(self, symbol: str) -> dict | None:
        """Close a single open paper position by symbol (cancels its bracket too)."""
        try:
            order = self.client.close_position(symbol)
        except Exception as e:
            logger.warning("close_position(%s) failed: %s", symbol, e)
            return None
        logger.info("Paper position closed: %s (order=%s)", symbol, order.id)
        return {"order_id": str(order.id), "symbol": symbol, "status": order.status.value}

    def get_order_outcome(self, parent_order_id: str) -> dict | None:
        """Reconstruct the entry + exit fills for a bracket order.

        Returns a dict with actual_entry, exit_price, exit_time, exit_reason,
        or None if the order can't be fetched. exit_reason is one of:
          - 'target' (take-profit leg filled)
          - 'stop'   (stop-loss leg filled)
          - 'open'   (still open / no exit yet)
        Caller stamps 'gainz_exit' or 'eod' separately when applicable.
        """
        try:
            order = self.client.get_order_by_id(parent_order_id)
        except Exception as e:
            logger.warning("get_order_outcome(%s) failed: %s", parent_order_id, e)
            return None

        actual_entry = float(order.filled_avg_price) if order.filled_avg_price else None
        out = {
            "actual_entry": actual_entry,
            "entry_filled_at": str(order.filled_at) if order.filled_at else None,
            "exit_price": None,
            "exit_time": None,
            "exit_reason": "open",
        }

        legs = getattr(order, "legs", None) or []
        for leg in legs:
            if leg.status.value == "filled" and leg.filled_avg_price:
                out["exit_price"] = float(leg.filled_avg_price)
                out["exit_time"] = str(leg.filled_at) if leg.filled_at else None
                # take_profit = limit order; stop_loss = stop order
                out["exit_reason"] = "target" if leg.type.value == "limit" else "stop"
                break
        return out

    def get_fill_price(self, order_id: str) -> dict | None:
        """Fetch fill price/time for a single order (used for Gainz close-position fills)."""
        try:
            order = self.client.get_order_by_id(order_id)
        except Exception as e:
            logger.warning("get_fill_price(%s) failed: %s", order_id, e)
            return None
        if not order.filled_avg_price:
            return None
        return {
            "price": float(order.filled_avg_price),
            "time": str(order.filled_at) if order.filled_at else None,
        }

    def cancel_open_orders(self):
        """Cancel all open paper orders."""
        self.client.cancel_orders()
        logger.info("All open orders cancelled")

