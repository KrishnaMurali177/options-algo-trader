"""Fetch actual Alpaca fill prices for trade journal entries."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def enrich_trades_with_fills(trades: list[dict]) -> None:
    """Add actual_entry and actual_exit fill prices from Alpaca to each trade.

    Mutates trade dicts in place. Also runs a broker-truth reconcile: a trade the
    journal still marks *open* may have been closed outside the agent (e.g.
    manually on the broker website). If its option is no longer an open position,
    the real sell-to-close fill is pulled from Alpaca and the trade is stamped
    closed (exit_reason 'closed_externally') so the report reflects reality
    instead of a phantom open position.
    """
    executed = [
        t for t in trades
        if t.get("order_id") and t.get("trade_mode") == "0dte_option"
    ]
    if not executed:
        return

    try:
        from src.utils.alpaca_paper import AlpacaPaperTrader
        trader = AlpacaPaperTrader()
    except Exception as e:
        logger.warning("Could not connect to Alpaca for fill prices: %s", e)
        return

    # Snapshot which options are still genuinely open on the broker, so we can
    # tell a real open position from one that was closed outside the agent.
    try:
        live_syms = {p["symbol"] for p in trader.get_positions()}
    except Exception as e:
        logger.warning("Could not fetch live positions for reconcile: %s", e)
        live_syms = None

    for t in executed:
        # Entry fill (always look up the real buy-to-open price).
        if not t.get("actual_entry"):
            entry_fill = trader.get_fill_price(t["order_id"])
            if entry_fill:
                t["actual_entry"] = entry_fill["price"]

        if t.get("closed") is True:
            # Already closed by the agent — fetch the recorded close fill.
            close_id = t.get("close_order_id")
            if close_id and not t.get("actual_exit"):
                exit_fill = trader.get_fill_price(close_id)
                if exit_fill:
                    t["actual_exit"] = exit_fill["price"]
        else:
            # Journal says open — verify against the broker. If the option is no
            # longer held, it was closed externally; pull the real sell fill.
            occ = t.get("occ_symbol")
            if occ and live_syms is not None and occ not in live_syms:
                fill = trader.get_closing_fill(occ)
                if fill:
                    t["actual_exit"] = fill["price"]
                    t["exit_time"] = fill["time"]
                    t["close_order_id"] = fill["order_id"]
                    t["exit_reason"] = "closed_externally"
                    t["closed"] = True
                    logger.info("Reconciled %s from broker: closed externally @ $%.2f",
                                occ, fill["price"])
