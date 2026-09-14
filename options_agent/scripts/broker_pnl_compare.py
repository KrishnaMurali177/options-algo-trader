#!/usr/bin/env python3
"""Realized P&L straight from Alpaca, for two accounts side by side.

Journal-derived P&L is not trustworthy for this comparison: the live agent
records an exit price on only some trades, so any journal total silently
covers a biased subset. This asks the broker instead.

Realized cash for an option contract is the net signed cashflow of its fills
(sells +, buys -, x100). Each account's total is cross-checked against the
change in account equity over the same window, which catches fees and any
exercised share legs that the fills alone miss.

Accounts are given as label:path-to-env-file. Nothing from those files is
printed except the account number's last 4 digits.

Usage (Docker, on the box):
  docker-compose --profile live run --rm \
    -v $PWD/options_agent/.env.live:/app/.env.live:ro \
    -v $PWD/options_agent/.env.shadow:/app/.env.shadow:ro \
    agent-spy python scripts/broker_pnl_compare.py --since 2026-08-19 \
      --account live:/app/.env.live --account shadow:/app/.env.shadow
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RULE = "=" * 88


def _client(env_file: str):
    """Fresh TradingClient from one env file, without leaking the creds."""
    from dotenv import dotenv_values
    from alpaca.trading.client import TradingClient

    vals = dotenv_values(env_file)
    key = vals.get("ALPACA_API_KEY", "")
    sec = vals.get("ALPACA_SECRET_KEY", "")
    paper = str(vals.get("ALPACA_PAPER", "true")).strip().lower() not in ("false", "0", "no")
    if not key or not sec:
        raise SystemExit(f"{env_file}: missing ALPACA_API_KEY / ALPACA_SECRET_KEY")
    return TradingClient(key, sec, paper=paper), paper


def _orders(client, since: date, until: date) -> list[dict]:
    """All filled orders in [since, until], paging backwards through the API."""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    out, seen = [], set()
    cursor = datetime.combine(until + timedelta(days=1), datetime.min.time(), timezone.utc)
    floor = datetime.combine(since, datetime.min.time(), timezone.utc)
    while cursor > floor:
        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500,
                               after=floor, until=cursor, direction="desc")
        batch = client.get_orders(req)
        if not batch:
            break
        fresh = 0
        for o in batch:
            if str(o.id) in seen:
                continue
            seen.add(str(o.id))
            fresh += 1
            if o.filled_avg_price is None or o.filled_at is None:
                continue
            out.append({
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": int(float(o.qty)),
                "price": float(o.filled_avg_price),
                "filled_at": o.filled_at,
                "day": o.filled_at.astimezone().strftime("%Y-%m-%d"),
            })
        if fresh == 0:
            break
        cursor = min(o.submitted_at for o in batch)
    return out


def _underlying(sym: str) -> str:
    """OCC = root + YYMMDD + C/P + 8-digit strike; root is all but the last 15."""
    return sym[:-15] if len(sym) > 15 else sym


def _equity_series(client, since: date, until: date) -> dict[str, float]:
    """Daily account equity, for the account-truth cross-check."""
    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        h = client.get_portfolio_history(
            GetPortfolioHistoryRequest(start=since - timedelta(days=4),
                                       end=until, timeframe="1D"))
        return {datetime.fromtimestamp(t, timezone.utc).astimezone().strftime("%Y-%m-%d"): float(e)
                for t, e in zip(h.timestamp, h.equity) if e is not None}
    except Exception as exc:
        print(f"    (portfolio history unavailable: {exc})")
        return {}


def analyze(label: str, env_file: str, since: date, until: date, symbol: str | None):
    client, paper = _client(env_file)
    acct = client.get_account()
    orders = _orders(client, since, until)
    if symbol:
        orders = [o for o in orders if _underlying(o["symbol"]) == symbol]

    by_contract: dict[str, float] = defaultdict(float)
    by_contract_qty: dict[str, int] = defaultdict(int)
    by_und: dict[str, float] = defaultdict(float)
    by_day: dict[str, float] = defaultdict(float)
    buys = sells = 0
    for o in orders:
        mult = 100 if len(o["symbol"]) > 6 else 1
        signed = (o["price"] if o["side"] == "sell" else -o["price"]) * o["qty"] * mult
        by_contract[o["symbol"]] += signed
        by_contract_qty[o["symbol"]] = max(by_contract_qty[o["symbol"]], o["qty"])
        by_und[_underlying(o["symbol"])] += signed
        by_day[o["day"]] += signed
        buys += o["side"] == "buy"
        sells += o["side"] == "sell"

    eq = _equity_series(client, since, until)
    days = sorted(eq)
    eq_delta = (eq[days[-1]] - eq[days[0]]) if len(days) >= 2 else None

    print(f"\n  {label.upper()}  ({'paper' if paper else 'REAL MONEY'}, "
          f"acct …{str(acct.account_number)[-4:]})")
    print(f"    equity now ${float(acct.equity):,.2f}   fills {len(orders)} "
          f"({buys} buys / {sells} sells)   contracts {len(by_contract)}")
    if eq_delta is not None:
        print(f"    equity {days[0]} ${eq[days[0]]:,.2f} → {days[-1]} ${eq[days[-1]]:,.2f}"
              f"   Δ ${eq_delta:,.2f}")
    total = sum(by_und.values())
    print(f"    realized from fills  ${total:,.2f}")
    if eq_delta is not None:
        print(f"    fees / unmatched legs (equity Δ − fills)  ${eq_delta - total:,.2f}")
    for und, cash in sorted(by_und.items(), key=lambda kv: kv[1]):
        n = sum(1 for s in by_contract if _underlying(s) == und)
        qty = sum(by_contract_qty[s] for s in by_contract if _underlying(s) == und)
        per_ct = cash / qty if qty else float("nan")
        print(f"      {und:6} ${cash:>10,.2f}   {n:>3} contracts, {qty:>3} ct total"
              f"   ${per_ct:>8,.2f}/ct")
    return {"label": label, "by_day": by_day, "by_und": by_und, "paper": paper,
            "by_contract": by_contract, "by_contract_qty": by_contract_qty,
            "equity": eq, "total": total, "eq_delta": eq_delta}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default=None)
    ap.add_argument("--symbol", default=None, help="Restrict to one underlying, e.g. SPY")
    ap.add_argument("--account", action="append", required=True,
                    metavar="LABEL:ENVFILE", help="Repeatable, e.g. live:/app/.env.live")
    ap.add_argument("--daily", action="store_true", help="Print the per-day series")
    args = ap.parse_args()

    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until) if args.until else date.today()

    print(f"\n{RULE}")
    print(f"BROKER-TRUTH P&L  {since} → {until}"
          f"{'  ·  ' + args.symbol if args.symbol else ''}")
    print(RULE)

    results = []
    for spec in args.account:
        label, _, env_file = spec.partition(":")
        if not env_file:
            raise SystemExit(f"--account needs LABEL:ENVFILE, got {spec!r}")
        results.append(analyze(label, env_file, since, until, args.symbol))

    if args.daily and results:
        alldays = sorted({d for r in results for d in r["by_day"]})
        print(f"\n  {'date':12}" + "".join(f"{r['label']:>14}" for r in results))
        print("  " + "-" * (12 + 14 * len(results)))
        for d in alldays:
            print(f"  {d:12}" + "".join(f"{r['by_day'].get(d, 0.0):>14,.2f}" for r in results))
        print("  " + "-" * (12 + 14 * len(results)))
        print(f"  {'TOTAL':12}" + "".join(f"{sum(r['by_day'].values()):>14,.2f}" for r in results))

    print(f"\n{RULE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
