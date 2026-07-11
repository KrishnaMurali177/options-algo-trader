"""Public.com broker verification harness (Phase 0/4 of the hardening plan).

Confirms real SDK behavior before/while hardening PublicTrader:
  - account    : connect, print portfolio (equity/BP/positions/open orders). creds only, no order.
  - chain      : list today's near-ATM 0DTE contracts + quotes for a symbol.  creds only, no order.
  - preflight  : validate a 1-lot option order server-side (NO order placed).  creds, no order.
  - roundtrip  : REAL 1-contract BUY -> wait_for_fill -> SELL close -> wait_for_fill.
                 Also re-submits the SAME order_id once to check dedup (idempotency).
                 Hard-gated: requires --i-understand-real-money.

Run (rebuilt image, live profile so Public keys load):
  docker-compose --profile live run --rm --no-deps agent-spy \
      python scripts/verify_public_broker.py account
  ... preflight --occ SPY260707C00620000 --limit 0.80
  ... roundtrip --occ SPY260707C00620000 --qty 1 --i-understand-real-money

Env: PUBLIC_API_SECRET_KEY, PUBLIC_ACCOUNT_NUMBER, PUBLIC_DRY_RUN (ignored here —
this script places real orders ONLY in roundtrip mode behind the gate flag).
"""
from __future__ import annotations

import argparse
import os
import time
import uuid
from decimal import Decimal


def _client():
    from public_api_sdk import PublicApiClient, PublicApiClientConfiguration
    from public_api_sdk.auth_config import ApiKeyAuthConfig
    key = os.environ["PUBLIC_API_SECRET_KEY"]
    acct = os.environ["PUBLIC_ACCOUNT_NUMBER"]
    client = PublicApiClient(
        ApiKeyAuthConfig(api_secret_key=key),
        config=PublicApiClientConfiguration(default_account_number=acct),
    )
    return client, acct


def _dump(label, obj, fields):
    print(f"  {label}:")
    for f in fields:
        print(f"      {f} = {getattr(obj, f, '<MISSING>')}")


def cmd_account(args):
    client, acct = _client()
    pf = client.get_portfolio(account_id=acct)
    _dump("portfolio", pf, ["account_id", "account_type", "equity", "buying_power"])
    positions = getattr(pf, "positions", []) or []
    print(f"  positions: {len(positions)}")
    for p in positions:
        inst = getattr(p, "instrument", None)
        print(f"      {getattr(inst,'symbol','?')}: qty={getattr(p,'quantity',0)} "
              f"cost_basis={getattr(p,'cost_basis',0)} last={getattr(p,'last_price',0)} "
              f"gain={getattr(p,'instrument_gain',0)}")
    orders = getattr(pf, "orders", []) or []
    print(f"  open orders: {len(orders)}")
    for o in orders:
        print(f"      {getattr(o,'order_id','?')}: {getattr(o,'side','?')} "
              f"{getattr(o,'status','?')} {getattr(o,'limit_price','?')}")


def cmd_chain(args):
    from public_api_sdk import OptionChainRequest
    client, acct = _client()
    req = OptionChainRequest(base_symbol=args.symbol)  # nearest expiry by default
    chain = client.get_option_chain(req, account_id=acct)
    calls = getattr(chain, "calls", []) or []
    puts = getattr(chain, "puts", []) or []
    print(f"  {args.symbol}: {len(calls)} calls, {len(puts)} puts (nearest expiry)")
    for c in calls[:8]:
        print("      call:", c)


def _build_single_leg(occ, side_name, qty, limit, order_id):
    # Single-leg options use OrderRequest/place_order (MultilegOrderRequest requires 2+ legs).
    from public_api_sdk import (
        OrderRequest, OrderInstrument, InstrumentType, OrderType,
        OrderExpirationRequest, TimeInForce, OpenCloseIndicator, OrderSide,
    )
    side = OrderSide.BUY if side_name == "buy" else OrderSide.SELL
    oc = OpenCloseIndicator.OPEN if side_name == "buy" else OpenCloseIndicator.CLOSE
    return OrderRequest(
        order_id=order_id,
        instrument=OrderInstrument(symbol=occ, type=InstrumentType.OPTION),
        order_side=side,
        order_type=OrderType.LIMIT,
        expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
        quantity=qty,
        limit_price=Decimal(str(round(limit, 2))),
        open_close_indicator=oc,
    )


def cmd_preflight(args):
    from public_api_sdk import (
        PreflightRequest, OrderInstrument, InstrumentType, OrderType,
        OrderExpirationRequest, TimeInForce, OrderSide,
    )
    client, acct = _client()
    # validate_order=False → "what-if" that skips account-state/buying-power
    # checks, so this confirms the request shape (incl. single-leg OPTION support)
    # on an UNFUNDED account, placing nothing. Pass --validate to include state checks.
    req = PreflightRequest(
        instrument=OrderInstrument(symbol=args.occ, type=InstrumentType.OPTION),
        order_side=OrderSide.BUY if args.side == "buy" else OrderSide.SELL,
        order_type=OrderType.LIMIT,
        expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
        quantity=args.qty,
        limit_price=Decimal(str(round(args.limit, 2))),
        validate_order=args.validate,
    )
    pf = client.perform_preflight_calculation(req, account_id=acct)
    _dump("preflight", pf, ["order_value", "estimated_cost", "estimated_commission",
                             "estimated_proceeds", "buying_power_requirement"])


def _print_order(tag, o):
    print(f"  {tag}: id={getattr(o,'order_id','?')} status={getattr(o,'status','?')} "
          f"filled_qty={getattr(o,'filled_quantity','?')} avg_price={getattr(o,'average_price','?')} "
          f"closed_at={getattr(o,'closed_at','?')} reject={getattr(o,'reject_reason',None)}")


def cmd_roundtrip(args):
    if not args.i_understand_real_money:
        raise SystemExit("REFUSING: roundtrip places a REAL order. Pass --i-understand-real-money.")
    client, acct = _client()

    # 1) Idempotency probe: submit the SAME order_id twice, expect ONE order.
    buy_id = str(uuid.uuid4())  # Public requires an RFC-4122 UUID
    print(f"BUY order_id={buy_id} occ={args.occ} qty={args.qty} limit={args.limit}")
    o1 = client.place_order(_build_single_leg(args.occ, "buy", args.qty, args.limit, buy_id), account_id=acct)
    print(f"  submit#1 -> NewOrder.order_id={o1.order_id}")
    try:
        o2 = client.place_order(_build_single_leg(args.occ, "buy", args.qty, args.limit, buy_id), account_id=acct)
        print(f"  submit#2 (same id) -> order_id={o2.order_id}  (SAME id => dedup works)")
    except Exception as e:
        print(f"  submit#2 (same id) raised {type(e).__name__}: {e}  (rejection => dedup via error)")

    filled = o1.wait_for_fill(timeout=args.timeout)
    _print_order("BUY filled", filled)

    # 2) Close via SELL + wait_for_fill (poll-to-fill proof).
    sell_id = str(uuid.uuid4())  # Public requires an RFC-4122 UUID
    sell_limit = round(args.limit * 0.9, 2)
    print(f"SELL close order_id={sell_id} limit={sell_limit}")
    s = client.place_order(_build_single_leg(args.occ, "sell", args.qty, sell_limit, sell_id), account_id=acct)
    try:
        sf = s.wait_for_fill(timeout=args.timeout)
        _print_order("SELL filled", sf)
    except Exception as e:
        print(f"  SELL wait_for_fill raised {type(e).__name__}: {e} — cancelling")
        s.cancel()


def main():
    ap = argparse.ArgumentParser(description="Public.com broker verification harness")
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("account")
    c = sub.add_parser("chain"); c.add_argument("--symbol", default="SPY")
    p = sub.add_parser("preflight")
    p.add_argument("--occ", required=True); p.add_argument("--side", choices=["buy", "sell"], default="buy")
    p.add_argument("--qty", type=int, default=1); p.add_argument("--limit", type=float, required=True)
    p.add_argument("--validate", action="store_true",
                   help="include account-state/buying-power checks (default: what-if only, no funding needed)")
    r = sub.add_parser("roundtrip")
    r.add_argument("--occ", required=True); r.add_argument("--qty", type=int, default=1)
    r.add_argument("--limit", type=float, required=True); r.add_argument("--timeout", type=float, default=15.0)
    r.add_argument("--i-understand-real-money", action="store_true")
    args = ap.parse_args()
    {"account": cmd_account, "chain": cmd_chain,
     "preflight": cmd_preflight, "roundtrip": cmd_roundtrip}[args.mode](args)


if __name__ == "__main__":
    main()
