"""Realized P&L from a broker account's activities ledger, FIFO by position.

Why not just sum the fills? Two reasons, both of which silently cost money on
the dashboard:

1. **Fees.** Fills are gross. The account's equity is net.
2. **Exercised contracts.** An ITM long call at expiry does not produce a sell
   fill. It produces an exercise (the contract disappears), a share purchase at
   the strike, and later a share sale. Summing option fills books the contract
   as a total loss of premium and then ignores the share sale entirely, because
   that sale carries an equity symbol rather than an OCC one. On the paper
   account that understated P&L by $2,967.

Why not just sum the ledger's daily cash either? Because those cash flows
straddle days: shares bought at the strike on 2026-05-13 (-$142,800) and sold
on 2026-05-14 (+$142,733). Any date window that starts between them shows a
six-figure phantom gain. So this module does not track cash — it tracks
positions, and realizes P&L on the *closing* transaction, where it belongs.

On exercise the option's remaining premium rolls into the cost basis of the
delivered shares (textbook treatment), so the whole trade realizes at once when
those shares are sold, and no window can split it.

Verified against both accounts: totals reconcile to equity-minus-funding.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

# Cash movements that are deposits/withdrawals/transfers, not P&L.
FUNDING = {"JNLC", "CSD", "CSW", "ACATC", "ACATS", "JNL", "TRANS"}

# Ordering within one timestamp: the exercise must retire the option before the
# share delivery claims its basis.
_TYPE_ORDER = {"OPEXC": 0, "OPTRD": 1}


def multiplier(symbol: str) -> int:
    """OCC option symbols are 21 chars and control 100 shares."""
    return 100 if len(symbol) > 15 else 1


def _apply(lots: list, qty: float, unit_price: float, side: int) -> float:
    """Apply a trade to a signed FIFO position, returning realized P&L.

    `lots` holds [signed_qty, unit_cost] — positive long, negative short — so a
    sell with nothing behind it opens a short rather than inventing a zero cost
    basis. The paper account really does this (it sold AAPL and MSFT short on
    2026-06-25 and covered later); treating those as zero-basis sales overstated
    P&L by $925."""
    realized = 0.0
    remaining = qty
    # First close whatever is open on the other side.
    while remaining > 1e-9 and lots and (lots[0][0] > 0) != (side > 0):
        lot_qty, lot_cost = lots[0]
        available = abs(lot_qty)
        take = min(available, remaining)
        if lot_qty > 0:                       # selling out of a long
            realized += take * (unit_price - lot_cost)
        else:                                 # buying back a short
            realized += take * (lot_cost - unit_price)
        remaining -= take
        left = available - take
        if left <= 1e-9:
            lots.pop(0)
        else:
            lots[0][0] = left if lot_qty > 0 else -left
    # Whatever is left opens (or extends) a position on this side.
    if remaining > 1e-9:
        lots.append([remaining * side, unit_price])
    return realized


def _remove(lots: list, qty: float) -> float:
    """Take `qty` long units off the front FIFO and return the cost removed.

    Used when an exercise retires a contract: the premium doesn't vanish, it
    becomes part of the delivered shares' basis."""
    cost = 0.0
    remaining = qty
    while remaining > 1e-9 and lots and lots[0][0] > 0:
        lot_qty, lot_cost = lots[0]
        take = min(lot_qty, remaining)
        cost += take * lot_cost
        remaining -= take
        if lot_qty - take <= 1e-9:
            lots.pop(0)
        else:
            lots[0][0] = lot_qty - take
    return cost


def _when(a: dict):
    return (a.get("created_at") or a.get("transaction_time")
            or a.get("date") or "", _TYPE_ORDER.get(a.get("activity_type"), 2))


def _day(a: dict):
    raw = a.get("date") or (a.get("transaction_time") or "")[:10]
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def realized_by_day(activities: list) -> dict:
    """{date: realized P&L} for one account, FIFO, fees included."""
    inv: dict = defaultdict(list)      # symbol -> [[qty, unit_cost], ...]
    carried: dict = defaultdict(float)  # exercise group_id -> option basis
    out: dict = defaultdict(float)

    for a in sorted(activities, key=_when):
        kind = a.get("activity_type")
        if kind in FUNDING:
            continue
        day = _day(a)
        if day is None:
            continue

        if kind == "FEE":
            out[day] += float(a.get("net_amount") or 0)

        elif kind == "FILL":
            sym = a.get("symbol", "")
            try:
                px, qty = float(a["price"]), abs(float(a["qty"]))
            except (KeyError, TypeError, ValueError):
                continue
            unit = px * multiplier(sym)
            side = -1 if str(a.get("side", "")).startswith("sell") else 1
            out[day] += _apply(inv[sym], qty, unit, side)

        elif kind == "OPEXC":
            # The contract is retired; its premium follows the shares.
            sym = a.get("symbol", "")
            try:
                qty = abs(float(a.get("qty") or 0))
            except (TypeError, ValueError):
                continue
            carried[a.get("group_id") or sym] += _remove(inv[sym], qty)

        elif kind == "OPTRD":
            # The share leg of an exercise/assignment, priced at the strike.
            sym = a.get("symbol", "")
            try:
                px, qty = float(a["price"]), abs(float(a["qty"]))
                net = float(a.get("net_amount") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            gid = a.get("group_id") or sym
            basis = carried.pop(gid, 0.0)
            if net <= 0:      # shares delivered to us (long call exercised)
                unit = px + (basis / qty if qty else 0.0)
                out[day] += _apply(inv[sym], qty, unit, 1)
            else:             # shares taken from us (assigned / put exercised)
                out[day] += _apply(inv[sym], qty, px, -1) + basis

        else:
            # Anything else that moves cash (dividends, interest, corrections).
            out[day] += float(a.get("net_amount") or 0)

    return dict(out)


def fetch_activities(base_url: str, key: str, secret: str) -> list:
    """Page through /v2/account/activities. Read-only."""
    import requests

    hdr = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    rows, page = [], None
    while True:
        params = {"page_size": 100}
        if page:
            params["page_token"] = page
        r = requests.get(f"{base_url}/v2/account/activities", headers=hdr,
                         params=params, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        rows += batch
        page = batch[-1].get("id")
        if len(batch) < 100:
            break
    return rows
