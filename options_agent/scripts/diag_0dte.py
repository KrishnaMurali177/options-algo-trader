"""Verify get_0dte_chain now picks an ATM contract via strike-proximity fallback."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.utils.alpaca_data import get_0dte_chain

print("Testing get_0dte_chain SPY call (no spot passed — should auto-fetch):")
c1 = get_0dte_chain("SPY", option_type="call", target_delta=0.50)
print(f"  → {c1}\n")

print("Testing get_0dte_chain SPY put with spot_price=731.50:")
c2 = get_0dte_chain("SPY", option_type="put", target_delta=0.50, spot_price=731.50)
print(f"  → {c2}")

# Old debug below kept for reference:
import sys as _s; _s.exit(0)

from alpaca.data.historical import OptionHistoricalDataClient
from alpaca.data.requests import OptionSnapshotRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest

api_key = os.environ["ALPACA_API_KEY"]
secret_key = os.environ["ALPACA_SECRET_KEY"]
trading = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)
opt_data = OptionHistoricalDataClient(api_key=api_key, secret_key=secret_key)

today = date.today()
print(f"Today: {today}")

req = GetOptionContractsRequest(
    underlying_symbols=["SPY"],
    expiration_date=today,
    type="call",
    status="active",
)
resp = trading.get_option_contracts(req)
contracts = resp.option_contracts if resp else []
print(f"Raw 0DTE call contracts returned: {len(contracts)}")

if contracts:
    strikes = sorted([float(c.strike_price) for c in contracts])
    print(f"Strike range: {strikes[0]} → {strikes[-1]} ({len(strikes)} strikes)")
    print(f"Sample expirations: {set(str(c.expiration_date) for c in contracts[:5])}")

    occ_symbols = [c.symbol for c in contracts][:50]  # first 50 to keep it small
    snap_req = OptionSnapshotRequest(symbol_or_symbols=occ_symbols)
    snaps = opt_data.get_option_snapshot(snap_req)
    print(f"\nSnapshots returned for {len(snaps)} of {len(occ_symbols)} requested")

    have_greeks = 0
    have_quote = 0
    liquid = 0
    near_50 = 0
    for occ, snap in snaps.items():
        if snap.greeks and snap.greeks.delta is not None:
            have_greeks += 1
            if 0.35 <= abs(snap.greeks.delta) <= 0.65:
                near_50 += 1
        if snap.latest_quote:
            have_quote += 1
            bid = float(snap.latest_quote.bid_price or 0)
            ask = float(snap.latest_quote.ask_price or 0)
            mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else 0
            if mid > 0.01:
                liquid += 1
    print(f"  with greeks.delta: {have_greeks}")
    print(f"  with delta in [0.35, 0.65]: {near_50}")
    print(f"  with quote: {have_quote}")
    print(f"  with mid > $0.01: {liquid}")

    print("\nSample first 3 contracts:")
    for occ, snap in list(snaps.items())[:3]:
        d = snap.greeks.delta if snap.greeks else None
        b = float(snap.latest_quote.bid_price) if snap.latest_quote else 0
        a = float(snap.latest_quote.ask_price) if snap.latest_quote else 0
        print(f"  {occ}: delta={d}, bid={b}, ask={a}")
