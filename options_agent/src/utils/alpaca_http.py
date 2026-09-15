"""Put a time limit on every Alpaca HTTP call.

alpaca-py's RESTClient takes no `timeout` parameter and passes none to the
requests Session it owns, so every call can block forever. On 2026-09-14 a
0DTE chain lookup on the live account hung for 304 seconds and returned
normally — no exception, so no warning, no ops alert, nothing. The agent's
scan loop is single-threaded, so it swallowed a whole 5-minute bar: the
session never scanned 13:15, missed the entry shadow took, and the position
state that followed diverged for the rest of the day.

Bounding it at each construction site would work until someone adds a
fourteenth one, so this patches RESTClient itself, once, at import of
install_default_timeouts(). Every client built afterwards — trading, stock
data, option data, in our code or inside the SDK — inherits the limit.

The values are deliberately well inside one 5-min bar. A read timeout is a
per-response-gap limit, not a total-call budget, so a paginated fetch can
still outlast it; callers that loop (get_0dte_chain) also carry their own
page cap and elapsed-time guard.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT = float(os.environ.get("ALPACA_CONNECT_TIMEOUT", "5"))
DEFAULT_READ_TIMEOUT = float(os.environ.get("ALPACA_READ_TIMEOUT", "20"))

_INSTALLED = False


def bound_session(client, connect: float | None = None, read: float | None = None):
    """Give one already-built client's session a default timeout. Idempotent."""
    session = getattr(client, "_session", None)
    if session is None or getattr(session, "_oat_timeout_bound", False):
        return client
    timeout = (connect or DEFAULT_CONNECT_TIMEOUT, read or DEFAULT_READ_TIMEOUT)
    original = session.request

    def request(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original(method, url, **kwargs)

    session.request = request
    session._oat_timeout_bound = True
    return client


def install_default_timeouts(connect: float | None = None, read: float | None = None) -> bool:
    """Patch alpaca-py's RESTClient so every client it builds is time-bounded.

    Returns True if the patch was installed, False if it was already in place or
    the SDK layout changed. Safe to call more than once; never raises — a
    missing time limit must not itself stop the agent from starting.
    """
    global _INSTALLED
    if _INSTALLED:
        return False
    try:
        from alpaca.common.rest import RESTClient
    except Exception as exc:  # pragma: no cover - SDK layout changed
        logger.warning("Could not bound Alpaca HTTP timeouts (%s) — calls stay unbounded", exc)
        return False

    original_init = RESTClient.__init__

    def __init__(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            bound_session(self, connect, read)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Timeout bind failed for %s: %s", type(self).__name__, exc)

    RESTClient.__init__ = __init__
    _INSTALLED = True
    logger.info("Alpaca HTTP timeouts bounded: connect=%.0fs read=%.0fs",
                connect or DEFAULT_CONNECT_TIMEOUT, read or DEFAULT_READ_TIMEOUT)
    return True
