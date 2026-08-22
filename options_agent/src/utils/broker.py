"""Broker abstraction — routes data / chain / execution calls to Alpaca or IBKR
based on a process-wide selection.

Design goals:
  - Callers (run_sweet_spot_agent.py, dashboard, replay scripts) import
    broker.* and never touch alpaca_* or ibkr_* modules directly.
  - Broker choice is a single startup decision. `set_broker("ibkr")` once,
    every subsequent call routes there.
  - Symbol-aware client-id offset for IBKR so concurrent per-symbol daemons
    don't collide on the same clientId.

Usage:
  from src.utils import broker
  broker.set_broker("ibkr", symbol="SPY")     # in each daemon's startup
  df = broker.fetch_bars("SPY", days_back=5)  # routes to ibkr_data.fetch_bars
  chain = broker.get_dte_chain("SPY", dte=0, option_type="call", ...)
  trader = broker.make_trader()               # IbkrPaperTrader
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Process-wide broker selection. Set by set_broker(); default preserves
# existing Alpaca behavior for callers that don't opt in.
_BROKER: str = os.environ.get("BROKER", "alpaca").lower()
_SYMBOL: str = ""

# Per-symbol clientId offset table (IBKR only). Concurrent daemons need
# unique IDs; extend this map as new symbols come online.
_CLIENT_ID_OFFSETS: dict[str, int] = {
    "SPY": 0,
    "QQQ": 1,
    "IWM": 2,
    "DIA": 3,
    "VOO": 4,
}


def set_broker(name: str, symbol: str = "") -> None:
    """Select which broker backs subsequent calls. Idempotent.

    For IBKR: also publishes IBKR_CLIENT_ID_OFFSET so get_ib()'s FIRST call
    in this process picks the symbol-specific clientId. Prevents 4 concurrent
    daemons from all colliding on clientId=IBKR_CLIENT_ID_BASE.
    """
    global _BROKER, _SYMBOL
    name = name.lower()
    if name not in ("alpaca", "ibkr"):
        raise ValueError(f"broker must be 'alpaca' or 'ibkr', got {name!r}")
    _BROKER = name
    _SYMBOL = symbol.upper()
    if name == "ibkr":
        os.environ["IBKR_CLIENT_ID_OFFSET"] = str(_client_id_offset())
    logger.info("broker set: %s (symbol=%s, ibkr_offset=%s)",
                _BROKER, _SYMBOL or "<unspecified>",
                os.environ.get("IBKR_CLIENT_ID_OFFSET", "n/a"))


def current_broker() -> str:
    return _BROKER


def _client_id_offset() -> int:
    """IBKR clientId offset for the current symbol. Default 0 for unknown symbols."""
    return _CLIENT_ID_OFFSETS.get(_SYMBOL, 0)


# ── Data ─────────────────────────────────────────────────────────────────────

def fetch_bars(symbol: str, days_back: int = 365, interval: str = "5min",
               force_refresh: bool = False):
    if _BROKER == "ibkr":
        from src.utils.ibkr_data import fetch_bars as _f
        return _f(symbol, days_back=days_back, interval=interval, force_refresh=force_refresh)
    from src.utils.alpaca_data import fetch_bars as _f
    return _f(symbol, days_back=days_back, interval=interval, force_refresh=force_refresh)


def is_trading_day(date=None) -> bool:
    if _BROKER == "ibkr":
        from src.utils.ibkr_data import is_trading_day as _f
        return _f(date)
    from src.utils.alpaca_data import is_trading_day as _f
    return _f(date)


# ── Options chain (live selection at trigger time) ───────────────────────────

def get_dte_chain(symbol: str, dte: int = 0, option_type: str = "call",
                  target_delta: float = 0.50, delta_tolerance: float = 0.15,
                  spot_price: float | None = None) -> dict | None:
    if _BROKER == "ibkr":
        from src.utils.ibkr_data import get_dte_chain as _f
        return _f(symbol, dte=dte, option_type=option_type,
                  target_delta=target_delta, delta_tolerance=delta_tolerance,
                  spot_price=spot_price)
    from src.utils.alpaca_data import get_dte_chain as _f
    return _f(symbol, dte=dte, option_type=option_type,
              target_delta=target_delta, delta_tolerance=delta_tolerance,
              spot_price=spot_price)


# ── Execution ────────────────────────────────────────────────────────────────

def make_trader():
    """Build a paper trader for the current broker.

    IBKR: reuses this process's IB() singleton (established by first data/
    chain call), which was clientId-offset by set_broker() via env var. So
    all 4 concurrent per-symbol daemons trade on distinct IBKR connections.
    """
    if _BROKER == "ibkr":
        from src.utils.ibkr_paper import IbkrPaperTrader
        return IbkrPaperTrader(client_id_offset=0)
    from src.utils.alpaca_paper import AlpacaPaperTrader
    return AlpacaPaperTrader()


def enrich_trades_with_fills(trades: list[dict], trader: Any = None) -> None:
    """Fill in actual_entry / actual_exit from broker's fill lookup. Mutates in place.

    Broker-agnostic — takes any trader that exposes get_fill_price(order_id).
    If trader is None (e.g. journal-only mode), this is a no-op.
    """
    if trader is None:
        return
    for t in trades:
        if not (t.get("order_id") and t.get("closed")):
            continue
        if not t.get("actual_entry"):
            entry_fill = trader.get_fill_price(t["order_id"])
            if entry_fill:
                t["actual_entry"] = entry_fill["price"]
        close_id = t.get("close_order_id")
        if close_id and not t.get("actual_exit"):
            exit_fill = trader.get_fill_price(close_id)
            if exit_fill:
                t["actual_exit"] = exit_fill["price"]


# ── Reality-check: broker's actual order count vs journal claims ─────────────

def count_orders_today(trader: Any) -> int:
    """Return the number of orders submitted to the broker today.

    Used to sanity-check the journal against broker reality before EOD reports.
    If journal claims 8 trades but broker shows 0, we know we're about to
    publish phantom P&L and should warn instead.

    For IBKR: ib.trades() is per-session, and since daemons launch daily,
    all entries are today's. Count all (non-cancelled/non-inactive counted
    as intent-to-trade; cancelled ones too since they hit the broker).

    For Alpaca: filter by submitted_at prefix against today's ISO date.
    """
    if trader is None:
        return 0
    try:
        orders = trader.get_orders(status="all", limit=200)
    except Exception as e:
        logger.warning("count_orders_today failed: %s", e)
        return -1  # sentinel: fetch failed, don't trust the delta

    if _BROKER == "ibkr":
        # ib.trades() is per-session; all entries came from today's daemon.
        return len(orders)

    # Alpaca path: filter by submitted_at
    from datetime import date
    today_prefix = date.today().isoformat()
    return sum(
        1 for o in orders
        if str(o.get("submitted_at", "") or "").startswith(today_prefix)
    )


# ── Position lookup abstraction (replaces trader.client.get_all_positions()) ──

def get_option_positions_map(trader: Any) -> dict:
    """Return {occ_symbol_compressed: {"qty": int, "avg_price": float}}.

    Both traders expose get_positions() with a symbol field that we normalize
    to OCC-compressed form (no whitespace).
    """
    out: dict[str, dict] = {}
    try:
        positions = trader.get_positions()
    except Exception as e:
        logger.warning("get_option_positions_map failed: %s", e)
        return out
    for p in positions or []:
        raw_sym = str(p.get("symbol", "") or "").replace(" ", "")
        if not raw_sym:
            continue
        try:
            qty = int(float(p.get("qty", 0) or 0))
        except (TypeError, ValueError):
            qty = 0
        if qty == 0:
            continue
        out[raw_sym] = {
            "qty": qty,
            "avg_price": p.get("entry_price"),
            "symbol": raw_sym,
        }
    return out
