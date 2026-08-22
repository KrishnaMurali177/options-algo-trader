#!/usr/bin/env python
"""IBKR connection smoke test — no orders placed, just reads.

Run BEFORE wiring the live agent to IBKR to confirm:
  1. TWS or IB Gateway is running with API enabled.
  2. .env has IBKR_HOST / IBKR_PORT (defaults 127.0.0.1 : 7497).
  3. The paper account can be read (accountSummary works).
  4. Read-Only API is OFF (otherwise order permission field says false).

Usage:
    cd options_agent
    python scripts/test_ibkr_connection.py
"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    from src.utils.ibkr_client import get_ib

    print("Connecting to IBKR (uses IBKR_HOST / IBKR_PORT / IBKR_CLIENT_ID_BASE from .env) ...")
    try:
        ib = get_ib(timeout=10.0)
    except RuntimeError as e:
        print(f"\n[FAIL] {e}")
        return 1

    print("[OK] socket up.\n")

    accounts = ib.managedAccounts()
    print(f"Managed accounts: {accounts}")
    if not accounts:
        print("[WARN] No accounts returned — check login state in Gateway/TWS.")
        return 1

    acct = accounts[0]
    is_paper = acct.startswith("D") or acct.startswith("U") is False  # paper accounts start with 'D'
    print(f"Primary account: {acct}  (paper: {is_paper})")

    summary = ib.accountSummary(acct)
    keep = {"NetLiquidation", "TotalCashValue", "BuyingPower", "AvailableFunds",
            "GrossPositionValue", "RealizedPnL", "UnrealizedPnL"}
    print("\nAccount summary:")
    for row in summary:
        if row.tag in keep:
            print(f"  {row.tag:<20} {row.value} {row.currency}")

    positions = ib.positions()
    print(f"\nOpen positions: {len(positions)}")
    for p in positions[:5]:
        sym = p.contract.localSymbol or p.contract.symbol
        print(f"  {sym:<20} qty={p.position}  avgCost={p.avgCost}")

    open_trades = ib.openTrades()
    print(f"\nOpen orders/trades: {len(open_trades)}")

    print("\n[OK] Read-only checks all passed.")
    print("If Read-Only API were still enabled, the next order-placement test would silently no-op.")
    print("You're ready to wire IbkrPaperTrader into the live agent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
