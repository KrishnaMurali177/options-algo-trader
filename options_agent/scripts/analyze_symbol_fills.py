"""Ad-hoc: pull ACTUAL Alpaca orders for given underlyings and report per-trade
stats (mean/max/min/std return, win rate, total cash). Separates filled option
round-trips from unfilled (canceled/rejected/expired) triggers so we can tell
"Discord trigger fired" from "money actually changed hands".

Paginates the full order history (the get_orders wrapper caps at 500, no cursor).

Run (Docker, paper account = default .env):
  docker-compose --profile live run --rm agent-spy \
    python scripts/analyze_symbol_fills.py MSFT AAPL
"""
from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _und(sym: str) -> str:
    """OCC root = everything before the trailing YYMMDD(6)+C/P(1)+strike(8)=15."""
    return sym[:-15] if len(sym) > 15 else sym


def fetch_all_orders(trader, page: int = 500):
    """Full order history via cursor pagination on submitted_at (oldest kept)."""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    out = []
    seen = set()
    until = None
    while True:
        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=page,
                               direction="desc", until=until)
        batch = trader.client.get_orders(req)
        if not batch:
            break
        new = 0
        for o in batch:
            if str(o.id) in seen:
                continue
            seen.add(str(o.id))
            out.append(o)
            new += 1
        until = batch[-1].submitted_at
        if new == 0 or len(batch) < page:
            break
    return out


def main() -> None:
    syms = [s.upper() for s in sys.argv[1:]] or ["MSFT", "AAPL"]

    from src.utils.alpaca_paper import AlpacaPaperTrader
    trader = AlpacaPaperTrader()
    acct = "PAPER" if trader.paper else "LIVE-REAL-MONEY"

    orders = fetch_all_orders(trader)
    print(f"Account: {acct} · {len(orders)} total orders in history\n")

    for target in syms:
        # Options for this underlying only (OCC len > 15 with matching root).
        opt = [o for o in orders if len(o.symbol) > 15 and _und(o.symbol) == target]
        filled = [o for o in opt if o.status.value == "filled" and o.filled_avg_price]
        unfilled = [o for o in opt if o.status.value != "filled"]

        print(f"=== {target} ===")
        print(f"  option orders: {len(opt)}  "
              f"(filled {len(filled)}, unfilled {len(unfilled)})")
        # Unfilled breakdown — this is where "Discord trigger but no trade" lives.
        by_status = defaultdict(int)
        for o in unfilled:
            by_status[o.status.value] += 1
        if by_status:
            print("  unfilled by status: "
                  + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))

        if not filled:
            print("  → NO filled option trades. Nothing changed hands.\n")
            continue

        # Round-trip per OCC contract: net signed cash + per-contract return %.
        buys = defaultdict(list)   # occ -> list of (price, qty)
        sells = defaultdict(list)
        for o in filled:
            px, qty = float(o.filled_avg_price), int(float(o.qty))
            (buys if o.side.value == "buy" else sells)[o.symbol].append((px, qty))

        rets, cashes = [], []
        wins = 0
        for occ in set(buys) | set(sells):
            bq = sum(q for _, q in buys[occ])
            sq = sum(q for _, q in sells[occ])
            buy_cash = sum(p * q for p, q in buys[occ])   # $ paid (per-share)
            sell_cash = sum(p * q for p, q in sells[occ])
            cash = (sell_cash - buy_cash) * 100
            cashes.append(cash)
            if cash > 0:
                wins += 1
            # Return % needs a complete round trip and a cost basis.
            if bq > 0 and sq > 0 and buy_cash > 0:
                avg_buy = buy_cash / bq
                avg_sell = sell_cash / sq
                rets.append((avg_sell - avg_buy) / avg_buy * 100)

        n = len(cashes)
        total = sum(cashes)
        print(f"  round-trip contracts: {n}  ({wins}W/{n-wins}L, "
              f"win rate {wins/n*100:.0f}%)")
        print(f"  total realized cash: {'+' if total>=0 else ''}${total:,.0f}")
        print(f"  cash/contract: mean ${statistics.mean(cashes):,.0f}  "
              f"max +${max(cashes):,.0f}  min ${min(cashes):,.0f}")
        if rets:
            print(f"  return %/contract (n={len(rets)}): "
                  f"mean {statistics.mean(rets):+.1f}%  "
                  f"max {max(rets):+.1f}%  min {min(rets):+.1f}%  "
                  f"std {statistics.pstdev(rets):.1f}%")
        print()


if __name__ == "__main__":
    main()
