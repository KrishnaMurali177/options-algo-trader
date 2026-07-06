"""Execution-broker selector for the sweet-spot agent.

The agent drives a single `trader` object through a fixed set of methods
(`place_options_trade`, `close_options_position`, `get_positions`, `get_today_pnl`,
`get_order_outcome`, `get_fill_price`, `get_option_quote`, `cancel_open_orders`, …).
This factory chooses which broker backend fulfils those calls.

Only ORDER EXECUTION is switched here. Market data, the 0DTE options chain, and OCC
symbol resolution always stay on Alpaca (`alpaca_data.py`, `alpaca_options.py`) — OCC
symbols are standardized, so each broker just receives the same contract id.

Select via the BROKER env var:
  BROKER=alpaca (default) → AlpacaPaperTrader   (paper or real per ALPACA_PAPER)
  BROKER=public           → PublicTrader        (Public.com, real-money;
                                                 PUBLIC_DRY_RUN=true preflights only)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def get_trader():
    """Instantiate and return the configured execution broker.

    Both backends expose the same method surface, so callers are broker-agnostic.
    Raises ValueError on an unknown BROKER value.
    """
    broker = os.environ.get("BROKER", "alpaca").strip().lower()

    if broker == "alpaca":
        from src.utils.alpaca_paper import AlpacaPaperTrader
        return AlpacaPaperTrader()

    if broker == "public":
        from src.utils.public_broker import PublicTrader
        logger.info("Broker backend: PUBLIC.COM (execution)")
        return PublicTrader()

    raise ValueError(f"Unknown BROKER={broker!r} — expected 'alpaca' or 'public'")
