"""IBKR connection helper — shared IB() bootstrap for the ibkr_* modules.

Wraps ib_async's IB() with:
  - Env-driven config (IBKR_HOST / IBKR_PORT / IBKR_CLIENT_ID_BASE).
  - Per-process singleton connection so ibkr_data, ibkr_options, and
    ibkr_paper share one TWS/Gateway socket instead of holding N of them.
  - OCC ↔ ib_async.Option converters — the rest of the codebase talks
    OCC strings; IBKR wants Option contracts.

Setup:
  1. Install TWS (Trader Workstation) or IB Gateway from Interactive Brokers.
  2. In its settings enable "API → Enable ActiveX and Socket Clients"
     and disable "Read-Only API".
  3. Note the port:
       TWS paper    → 7497   (default)
       TWS live     → 7496
       Gateway paper → 4002
       Gateway live  → 4001
  4. Add to .env:
       IBKR_HOST=127.0.0.1
       IBKR_PORT=7497
       IBKR_CLIENT_ID_BASE=42     # Any unique-per-process int; each module
                                  # adds a small offset so replay/live/paper
                                  # can coexist on the same TWS.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_IB_SINGLETON = None  # ib_async.IB instance, lazy


def _load_env() -> tuple[str, int, int]:
    from dotenv import load_dotenv
    load_dotenv()
    host = os.environ.get("IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("IBKR_PORT", "7497"))
    client_id_base = int(os.environ.get("IBKR_CLIENT_ID_BASE", "42"))
    return host, port, client_id_base


def get_ib(client_id_offset: int = 0, timeout: float = 8.0):
    """Return a connected ib_async.IB() singleton for this process.

    Args:
        client_id_offset: Explicit offset. If 0 (default), fall back to the
            IBKR_CLIENT_ID_OFFSET env var — broker.set_broker() publishes this
            per-symbol so concurrent daemons (SPY / QQQ / IWM / DIA) all get
            unique clientIds without the caller managing them.
        timeout: Seconds to wait for the socket handshake.

    Raises RuntimeError if TWS/Gateway isn't reachable.
    """
    global _IB_SINGLETON
    with _LOCK:
        if _IB_SINGLETON is not None and _IB_SINGLETON.isConnected():
            return _IB_SINGLETON

        try:
            from ib_async import IB
        except ImportError as e:
            raise RuntimeError(
                "ib_async is not installed. Run: pip install ib_async"
            ) from e

        host, port, cid_base = _load_env()
        # Explicit arg wins; else consult env published by broker.set_broker().
        effective_offset = client_id_offset if client_id_offset else int(
            os.environ.get("IBKR_CLIENT_ID_OFFSET", "0")
        )
        ib = IB()
        client_id = cid_base + effective_offset
        try:
            ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=False)
        except Exception as e:
            raise RuntimeError(
                f"IBKR connect failed at {host}:{port} clientId={client_id}. "
                f"Is TWS or IB Gateway running with API enabled? Original error: {e}"
            ) from e

        # Fall back to DELAYED_FROZEN market data (type 4) so option quotes
        # work without an OPRA subscription. Without this, options snapshots
        # return empty bid/ask and every chain pick fails with source=no-quote.
        # Users with OPRA subs can override by setting IBKR_MARKET_DATA_TYPE=1.
        try:
            md_type = int(os.environ.get("IBKR_MARKET_DATA_TYPE", "4"))
            ib.reqMarketDataType(md_type)
            logger.info("IBKR market data type set to %d (1=live, 2=frozen, 3=delayed, 4=delayed-frozen)", md_type)
        except Exception as e:
            logger.warning("reqMarketDataType failed: %s — using default (live). "
                           "Option quotes will fail unless you have an OPRA subscription.", e)

        logger.info("IBKR connected: %s:%d clientId=%d", host, port, client_id)
        _IB_SINGLETON = ib
        return ib


def disconnect():
    """Explicit disconnect — mainly for test teardown."""
    global _IB_SINGLETON
    with _LOCK:
        if _IB_SINGLETON is not None and _IB_SINGLETON.isConnected():
            _IB_SINGLETON.disconnect()
        _IB_SINGLETON = None


# ── OCC ↔ Option contract conversion ─────────────────────────────────────────

# OCC 21-char format: {root:<6}{yy:02}{mm:02}{dd:02}{C/P}{strike*1000:08}
# Example: "SPY   260530C00550000"  → SPY, 2026-05-30, CALL, strike 550.00
_OCC_RE = re.compile(
    r"^(?P<root>[A-Z0-9]{1,6})\s*"
    r"(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<right>[CP])"
    r"(?P<strike>\d{8})$"
)


def parse_occ(occ: str) -> dict:
    """Parse an OCC symbol into its component pieces.

    Returns {root, expiration (date), right ("C"|"P"), strike (float)}.
    Raises ValueError on unparseable input.
    """
    s = occ.strip().replace(" ", "")
    # Re-pad to canonical 21-char form so the regex matches: OCC uses spaces
    # to pad the root, but a compressed form like "SPY260530C00550000" is
    # what the Alpaca side of this codebase uses.
    if len(s) < 15 or len(s) > 22:
        raise ValueError(f"OCC symbol wrong length: {occ!r}")
    # Right-anchor the trailing 15 chars: 6-digit date + 1 right + 8 strike
    tail = s[-15:]
    root = s[:-15]
    if not root or not root.isalnum():
        raise ValueError(f"OCC root invalid: {occ!r}")
    m = re.match(r"^(\d{2})(\d{2})(\d{2})([CP])(\d{8})$", tail)
    if not m:
        raise ValueError(f"OCC tail invalid: {occ!r}")
    yy, mm, dd, right, strike_i = m.groups()
    year = 2000 + int(yy)
    exp = date(year, int(mm), int(dd))
    strike = int(strike_i) / 1000.0
    return {"root": root, "expiration": exp, "right": right, "strike": strike}


def format_occ(root: str, expiration: date, right: str, strike: float) -> str:
    """Build the compressed OCC form used elsewhere in this codebase."""
    yymmdd = expiration.strftime("%y%m%d")
    strike_i = int(round(strike * 1000))
    r = "C" if right.upper().startswith("C") else "P"
    return f"{root.upper()}{yymmdd}{r}{strike_i:08d}"


def occ_to_option(occ: str, exchange: str = "SMART", currency: str = "USD"):
    """Build an ib_async.Option contract from an OCC symbol."""
    from ib_async import Option
    p = parse_occ(occ)
    return Option(
        symbol=p["root"],
        lastTradeDateOrContractMonth=p["expiration"].strftime("%Y%m%d"),
        strike=p["strike"],
        right=p["right"],
        exchange=exchange,
        currency=currency,
    )


def stock_contract(symbol: str, exchange: str = "SMART",
                   primary_exchange: str = "ARCA", currency: str = "USD"):
    """Build an ib_async.Stock contract for a US equity/ETF."""
    from ib_async import Stock
    return Stock(symbol=symbol, exchange=exchange,
                 primaryExchange=primary_exchange, currency=currency)
