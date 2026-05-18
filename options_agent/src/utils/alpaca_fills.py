"""Fetch actual Alpaca fill prices for trade journal entries."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def enrich_trades_with_fills(trades: list[dict]) -> None:
    """Add actual_entry and actual_exit fill prices from Alpaca to each trade.

    Mutates trade dicts in place. Skips trades that already have both fields
    or that lack order IDs.
    """
    needs_enrichment = [
        t for t in trades
        if t.get("order_id") and t.get("closed")
    ]
    if not needs_enrichment:
        return

    try:
        from src.utils.alpaca_paper import AlpacaPaperTrader
        trader = AlpacaPaperTrader()
    except Exception as e:
        logger.warning("Could not connect to Alpaca for fill prices: %s", e)
        return

    for t in needs_enrichment:
        if not t.get("actual_entry"):
            entry_fill = trader.get_fill_price(t["order_id"])
            if entry_fill:
                t["actual_entry"] = entry_fill["price"]

        close_id = t.get("close_order_id")
        if close_id and not t.get("actual_exit"):
            exit_fill = trader.get_fill_price(close_id)
            if exit_fill:
                t["actual_exit"] = exit_fill["price"]
