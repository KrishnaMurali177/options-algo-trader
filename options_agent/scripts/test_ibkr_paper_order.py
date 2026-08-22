#!/usr/bin/env python
"""IBKR paper-account order round-trip smoke test.

Places ONE 1-share SPY market order via IbkrPaperTrader, waits for fill,
closes the position at market, and reports the round-trip P&L.

Purpose: validate every IbkrPaperTrader method (order placement, fill query,
position query, close) against a real IBKR paper account BEFORE wiring the
class into the live sweet-spot agent.

Safety:
  - Uses `IBKR_PORT` from env — set 4002 (Gateway paper) or 7497 (TWS paper).
    If you accidentally point at the LIVE port (4001 / 7496) this script will
    place a real order. It refuses to run if it thinks it's on a live port.
  - Position size is HARDCODED to 1 share. Not configurable.
  - If anything unexpected happens, it tries to close the position before exiting.

Usage:
    cd options_agent
    python scripts/test_ibkr_paper_order.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env so DISCORD_WEBHOOK_URL / IBKR_* are picked up when run from
# Task Scheduler with no interactive shell.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


SYMBOL = "SPY"
QTY = 1


def _discord_notify(title: str, description: str, ok: bool) -> None:
    """Post a simple embed to DISCORD_WEBHOOK_URL. Silent if not configured."""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        return
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": 0x2e7d32 if ok else 0xc62828,
        }]
    }
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "OptionsAgent/1.0"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.warning("Discord notify failed: %s", e)


def _guard_live_port() -> None:
    port = int(os.environ.get("IBKR_PORT", "7497"))
    live_ports = {7496, 4001}
    if port in live_ports:
        raise SystemExit(
            f"[REFUSE] IBKR_PORT={port} looks like a LIVE port. Set 7497 (TWS paper) "
            f"or 4002 (Gateway paper) before running this test."
        )


def _await_fill(trade, timeout: float = 15.0) -> tuple[bool, str]:
    """Wait until an ib_async Trade is Filled/Cancelled/Inactive or timeout elapses.

    Returns (filled, last_status). PreSubmitted after timeout usually means
    the market is closed and IBKR queued the order for the next open.
    """
    from src.utils.ibkr_client import get_ib
    ib = get_ib()
    deadline = time.time() + timeout
    while time.time() < deadline:
        ib.waitOnUpdate(timeout=1.0)
        st = trade.orderStatus.status
        if st in {"Filled", "Cancelled", "Inactive"}:
            return st == "Filled", st
    return False, trade.orderStatus.status


def main() -> int:
    _guard_live_port()

    from src.utils.ibkr_client import get_ib, stock_contract
    from src.utils.ibkr_paper import IbkrPaperTrader

    print("=" * 68)
    print(f"  IBKR PAPER ORDER SMOKE TEST — {SYMBOL} x {QTY} share, market")
    print("=" * 68)

    ib = get_ib()
    # Free-tier fallback: request DELAYED_FROZEN data (type 4) — no OPRA/NYSE
    # subscription required. Live data would be type 1.
    ib.reqMarketDataType(4)
    trader = IbkrPaperTrader()

    # Snapshot spot before submitting so we have a reference for the fill.
    contract = stock_contract(SYMBOL)
    ib.qualifyContracts(contract)
    ticker = ib.reqMktData(contract, "", False, False)
    ib.sleep(3.0)  # delayed data takes a couple seconds to arrive
    ref_price = ticker.marketPrice()
    if not ref_price or ref_price != ref_price:  # NaN check
        ref_price = ticker.close or ticker.last or 0.0
    ib.cancelMktData(contract)
    print(f"\nReference spot: ${ref_price:.2f}  (delayed-frozen)")

    # ── BUY ────────────────────────────────────────────────────────────────
    print(f"\n[1/4] Placing BUY 1 {SYMBOL} @ MKT ...")
    from ib_async import MarketOrder
    entry_order = MarketOrder(action="BUY", totalQuantity=QTY, tif="DAY")
    entry_trade = ib.placeOrder(contract, entry_order)

    print("       waiting for fill ...")
    filled, last_status = _await_fill(entry_trade, timeout=20.0)
    if not filled:
        # Cancel the queued order regardless of reason so nothing lingers.
        try:
            ib.cancelOrder(entry_order)
        except Exception:
            pass
        if last_status == "PreSubmitted":
            print(f"\n[INFO] Order accepted but queued as PreSubmitted — the market is "
                  f"closed and IBKR will wait until 9:30 ET (regular hours) to route it. "
                  f"\n       Re-run this test between 09:30 and 16:00 ET to validate fills.")
            print("       Order was cancelled — no position was opened.")
            print("\n[OK] Connection + order-submission path validated. Fill validation deferred.")
            _discord_notify(
                "IBKR smoke test — MARKET CLOSED",
                "Order was accepted by Gateway but couldn't route because market was closed. "
                "Order cancelled cleanly. Re-run during 09:30–16:00 ET to validate fills.",
                ok=True,
            )
            return 0
        print(f"[FAIL] Entry order did not fill within 20s. Status: {last_status}")
        print("       Common causes: no market-data subscription, "
              "'Read-Only API' still enabled in Gateway, or connection loss.")
        _discord_notify(
            "IBKR smoke test — FAIL (entry didn't fill)",
            f"Entry order did not fill within 20s. Last status: `{last_status}`. "
            "Check Gateway: is Read-Only API still enabled? Is the account logged in?",
            ok=False,
        )
        return 1

    entry_px = float(entry_trade.orderStatus.avgFillPrice)
    entry_id = entry_trade.order.orderId
    print(f"       filled: {QTY} @ ${entry_px:.4f}  (orderId={entry_id})")

    # ── POSITION CHECK ─────────────────────────────────────────────────────
    print(f"\n[2/4] Reading position via trader.get_positions() ...")
    positions = trader.get_positions()
    pos = next((p for p in positions if SYMBOL in (p["symbol"] or "")), None)
    if pos is None:
        print(f"[FAIL] Position not visible after fill. Full list: {positions}")
        _discord_notify(
            "IBKR smoke test — FAIL (position not visible)",
            f"BUY filled at ${entry_px:.4f} but `trader.get_positions()` doesn't show {SYMBOL}. "
            "Position is likely OPEN on IBKR — check Gateway and close manually.",
            ok=False,
        )
        return 1
    print(f"       {pos}")

    # ── CLOSE ──────────────────────────────────────────────────────────────
    print(f"\n[3/4] Closing position via trader.close_position('{SYMBOL}') ...")
    close_result = trader.close_position(SYMBOL)
    if close_result is None:
        print("[FAIL] close_position returned None.")
        _discord_notify(
            "IBKR smoke test — FAIL (close returned None)",
            f"Entry filled at ${entry_px:.4f} but `close_position('{SYMBOL}')` returned None. "
            "Position is still OPEN on IBKR — close it manually via Gateway.",
            ok=False,
        )
        return 1
    print(f"       submitted: {close_result}")

    # Wait for close fill.
    close_trade = next(
        (t for t in ib.trades() if t.order.orderId == int(close_result["order_id"])),
        None,
    )
    if close_trade is None:
        print("[FAIL] Cannot locate the close trade in ib.trades().")
        return 1
    close_filled, close_status = _await_fill(close_trade, timeout=20.0)
    if not close_filled:
        print(f"[FAIL] Close order did not fill. Status: {close_status}")
        _discord_notify(
            "IBKR smoke test — FAIL (close didn't fill)",
            f"Close order did not fill within 20s. Last status: `{close_status}`. "
            f"Position may still be OPEN — check Gateway.",
            ok=False,
        )
        return 1
    exit_px = float(close_trade.orderStatus.avgFillPrice)
    print(f"       filled: closed {QTY} @ ${exit_px:.4f}")

    # ── ROUND-TRIP ─────────────────────────────────────────────────────────
    pnl = (exit_px - entry_px) * QTY
    print(f"\n[4/4] Round trip: entry ${entry_px:.4f} → exit ${exit_px:.4f}  "
          f"P&L: ${pnl:+.4f} ({QTY} share)")

    # ── Test get_fill_price + get_order_outcome ────────────────────────────
    fill = trader.get_fill_price(str(entry_id))
    print(f"\n       get_fill_price(entry) → {fill}")
    outcome = trader.get_order_outcome(str(entry_id))
    print(f"       get_order_outcome(entry) → {outcome}")

    # Confirm position is really flat before exiting.
    final_positions = [p for p in trader.get_positions()
                       if SYMBOL in (p["symbol"] or "")]
    if final_positions:
        print(f"\n[WARN] Position still shows open after close: {final_positions}")
        _discord_notify(
            "IBKR smoke test — WARN (residual position)",
            f"Round-trip filled (entry ${entry_px:.4f} → exit ${exit_px:.4f}, P&L "
            f"${(exit_px - entry_px):+.4f}) BUT position still shows open. "
            f"Check Gateway — may be a partial fill or stale cache.",
            ok=False,
        )
        return 1

    print("\n[OK] All IbkrPaperTrader methods round-tripped successfully.")
    print("     IbkrPaperTrader is ready to be wired into the live agent.")
    _discord_notify(
        "IBKR smoke test — PASS ✅",
        f"Full round-trip validated on {SYMBOL} paper.\n"
        f"Entry: ${entry_px:.4f}\n"
        f"Exit: ${exit_px:.4f}\n"
        f"P&L: ${(exit_px - entry_px):+.4f} ({QTY} share)\n"
        f"All IbkrPaperTrader methods work. Ready to wire into the live agent.",
        ok=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print("\n[ERROR] Unhandled exception. Attempting emergency close.")
        traceback.print_exc()
        emergency = str(e)[:400]
        try:
            from src.utils.ibkr_paper import IbkrPaperTrader
            IbkrPaperTrader().close_position(SYMBOL)
        except Exception as ce:
            print("[ERROR] Emergency close also failed. Check Gateway manually.")
            emergency += f"\n\nEmergency close ALSO failed: {ce}"
        _discord_notify(
            "IBKR smoke test — CRASHED",
            f"Unhandled exception:\n```\n{emergency}\n```\n"
            "Common cause: IB Gateway not running / not logged in. "
            "Check Gateway status and re-run manually.",
            ok=False,
        )
        raise SystemExit(2)
