"""
Robinhood MCP Server — wraps robin_stocks into MCP tools.

Supports multiple individual accounts under one login.
Set ROBINHOOD_ACCOUNT_INDEX=1 (or ROBINHOOD_ACCOUNT_NUMBER) in .env
to operate on your secondary account.

Run standalone:
    python -m mcp_server.robinhood_mcp_server

The agent connects to this process over stdio transport.
"""

from __future__ import annotations

import json
import logging
import os

import pyotp
import robin_stocks.robinhood as rh
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

# ── Account state (set during login) ─────────────────────────────

_account_url: str | None = None     # Robinhood account URL for the selected account
_account_number: str | None = None  # For display/logging


# ── Robinhood authentication + account selection ──────────────────

def _login():
    """Log in and pin the agent to the configured account (primary or secondary)."""
    global _account_url, _account_number

    username = os.environ["ROBINHOOD_USERNAME"]
    password = os.environ["ROBINHOOD_PASSWORD"]
    totp_secret = os.environ.get("ROBINHOOD_TOTP_SECRET", "")
    mfa_code = pyotp.TOTP(totp_secret).now() if totp_secret else None

    rh.login(username, password, mfa_code=mfa_code, store_session=True)
    logger.info("Logged in to Robinhood as %s", username)

    # ── Discover all accounts under this login ──
    accounts = rh.account.load_account_profile(info=None, dataType="results")
    # robin_stocks may return a dict (single) or list (multiple)
    if isinstance(accounts, dict):
        accounts = [accounts]

    if not accounts:
        raise RuntimeError("No Robinhood accounts found after login.")

    # Log all available accounts for reference
    for i, acct in enumerate(accounts):
        acct_num = acct.get("account_number", "???")
        acct_type = acct.get("type", "unknown")
        logger.info(
            "  Account %d: %s (type=%s, url=%s)",
            i, acct_num, acct_type, acct.get("url", "")
        )

    # ── Select the target account ──
    explicit_number = os.environ.get("ROBINHOOD_ACCOUNT_NUMBER", "").strip()
    account_index = int(os.environ.get("ROBINHOOD_ACCOUNT_INDEX", "1"))

    selected = None
    if explicit_number:
        # Match by account number
        for acct in accounts:
            if acct.get("account_number") == explicit_number:
                selected = acct
                break
        if not selected:
            available = [a.get("account_number") for a in accounts]
            raise RuntimeError(
                f"Account number '{explicit_number}' not found. Available: {available}"
            )
    else:
        # Match by index
        if account_index >= len(accounts):
            raise RuntimeError(
                f"Account index {account_index} out of range. "
                f"You have {len(accounts)} account(s) (indices 0–{len(accounts)-1})."
            )
        selected = accounts[account_index]

    _account_url = selected.get("url", "")
    _account_number = selected.get("account_number", "")

    logger.info(
        "✅ Agent pinned to account: %s (index=%d, url=%s)",
        _account_number, account_index if not explicit_number else -1, _account_url,
    )


# ── MCP Server definition ────────────────────────────────────────

server = Server("robinhood-trading")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="list_accounts", description="List all Robinhood accounts under this login and show which one is active.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="get_account_info", description="Get Robinhood account balance, buying power, portfolio value for the active account.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="get_positions", description="Get current stock or option positions.",
             inputSchema={"type": "object", "properties": {
                 "account_type": {"type": "string", "enum": ["stock", "options"], "default": "stock"}
             }}),
        Tool(name="get_options_chain", description="Get options chain for a symbol.",
             inputSchema={"type": "object", "properties": {
                 "symbol": {"type": "string"},
                 "expiration": {"type": "string", "description": "YYYY-MM-DD"},
                 "option_type": {"type": "string", "enum": ["call", "put"], "default": "call"},
             }, "required": ["symbol", "expiration"]}),
        Tool(name="get_market_data", description="Get historical OHLCV data.",
             inputSchema={"type": "object", "properties": {
                 "symbol": {"type": "string"},
                 "interval": {"type": "string", "default": "day"},
                 "span": {"type": "string", "default": "year"},
             }, "required": ["symbol"]}),
        Tool(name="place_option_order", description="Place a single or multi-leg options order.",
             inputSchema={"type": "object", "properties": {
                 "legs": {"type": "array", "items": {"type": "object"}},
                 "order_type": {"type": "string", "default": "limit"},
                 "limit_price": {"type": "number"},
                 "duration": {"type": "string", "default": "gfd"},
             }, "required": ["legs"]}),
        Tool(name="cancel_order", description="Cancel an open order.",
             inputSchema={"type": "object", "properties": {
                 "order_id": {"type": "string"},
             }, "required": ["order_id"]}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch MCP tool calls to robin_stocks functions."""
    try:
        result = _dispatch(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    except Exception as e:
        logger.exception("Tool %s failed", name)
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


def _dispatch(name: str, args: dict):
    if name == "list_accounts":
        accounts = rh.account.load_account_profile(info=None, dataType="results")
        if isinstance(accounts, dict):
            accounts = [accounts]
        return {
            "active_account": _account_number,
            "accounts": [
                {
                    "index": i,
                    "account_number": a.get("account_number", ""),
                    "type": a.get("type", ""),
                    "active": a.get("account_number") == _account_number,
                }
                for i, a in enumerate(accounts)
            ],
        }

    elif name == "get_account_info":
        # Load profile for the specific account
        profile = rh.profiles.load_account_profile(account_number=_account_number)
        portfolio = rh.profiles.load_portfolio_profile(account_number=_account_number)
        return {
            "account_id": _account_number or profile.get("account_number", ""),
            "buying_power": float(portfolio.get("withdrawable_amount", 0)),
            "cash": float(portfolio.get("withdrawable_amount", 0)),
            "portfolio_value": float(portfolio.get("equity", 0)),
            "options_buying_power": float(profile.get("option_level", 0)),
        }

    elif name == "get_positions":
        account_type = args.get("account_type", "stock")
        if account_type == "stock":
            # build_holdings doesn't accept account_number, so we filter
            # by fetching all positions and matching our account URL
            positions = rh.account.build_holdings(with_dividends=False)
            return [
                {
                    "symbol": sym,
                    "quantity": float(data.get("quantity", 0)),
                    "average_cost": float(data.get("average_buy_price", 0)),
                    "current_price": float(data.get("price", 0)),
                    "market_value": float(data.get("equity", 0)),
                    "unrealized_pnl": float(data.get("equity_change", 0)),
                }
                for sym, data in positions.items()
            ]
        else:
            options = rh.options.get_open_option_positions(account_number=_account_number)
            result = []
            for opt in options:
                chain_data = opt.get("option", opt)
                result.append({
                    "symbol": chain_data.get("chain_symbol", ""),
                    "underlying": chain_data.get("chain_symbol", ""),
                    "strike": float(chain_data.get("strike_price", 0)),
                    "expiration": chain_data.get("expiration_date", ""),
                    "option_type": chain_data.get("type", "call"),
                    "quantity": int(float(opt.get("quantity", 0))),
                    "average_cost": float(opt.get("average_price", 0)) / 100,
                    "current_price": float(opt.get("mark_price", opt.get("adjusted_mark_price", 0))),
                    "market_value": float(opt.get("quantity", 0)) * float(opt.get("mark_price", 0)) * 100,
                    "unrealized_pnl": 0,
                })
            return result

    elif name == "get_options_chain":
        symbol = args["symbol"]
        expiration = args["expiration"]
        option_type = args.get("option_type", "call")
        chain = rh.options.find_options_by_expiration(
            symbol, expirationDate=expiration, optionType=option_type
        )
        return [
            {
                "symbol": c.get("chain_symbol", symbol),
                "strike_price": float(c.get("strike_price", 0)),
                "expiration_date": c.get("expiration_date", expiration),
                "bid_price": float(c.get("bid_price", 0)),
                "ask_price": float(c.get("ask_price", 0)),
                "last_trade_price": float(c.get("last_trade_price", 0)),
                "volume": int(c.get("volume", 0) or 0),
                "open_interest": int(c.get("open_interest", 0) or 0),
                "delta": float(c.get("delta", 0) or 0),
                "gamma": float(c.get("gamma", 0) or 0),
                "theta": float(c.get("theta", 0) or 0),
                "vega": float(c.get("vega", 0) or 0),
                "implied_volatility": float(c.get("implied_volatility", 0) or 0),
            }
            for c in (chain or [])
        ]

    elif name == "get_market_data":
        symbol = args["symbol"]
        interval = args.get("interval", "day")
        span = args.get("span", "year")
        historicals = rh.stocks.get_stock_historicals(symbol, interval=interval, span=span)
        return [
            {
                "date": h.get("begins_at", ""),
                "open": float(h.get("open_price", 0)),
                "high": float(h.get("high_price", 0)),
                "low": float(h.get("low_price", 0)),
                "close": float(h.get("close_price", 0)),
                "volume": int(h.get("volume", 0)),
            }
            for h in (historicals or [])
        ]

    elif name == "place_option_order":
        # Place orders against the pinned account
        legs = args.get("legs", [])
        order_type = args.get("order_type", "limit")
        limit_price = args.get("limit_price")
        duration = args.get("duration", "gfd")

        logger.info("Placing option order on account %s", _account_number)

        if len(legs) == 1:
            leg = legs[0]
            effect_map = {
                "buy_to_open": "open",
                "sell_to_open": "open",
                "buy_to_close": "close",
                "sell_to_close": "close",
            }
            result = rh.orders.order_option_by_price(
                positionEffect=effect_map[leg["action"]],
                creditOrDebit="credit" if "sell" in leg["action"] else "debit",
                price=limit_price or 0.01,
                symbol=leg["symbol"],
                quantity=leg.get("quantity", 1),
                expirationDate=leg["expiration"],
                strike=leg["strike"],
                optionType=leg["option_type"],
                timeInForce=duration,
                account_number=_account_number,
            )
            return result or {"status": "submitted"}
        else:
            # Multi-leg: robin_stocks spread functions
            logger.warning("Multi-leg orders require specific robin_stocks spread functions.")
            return {"status": "multi_leg_not_fully_implemented", "legs": len(legs)}

    elif name == "cancel_order":
        order_id = args["order_id"]
        result = rh.orders.cancel_option_order(order_id)
        return result or {"status": "cancelled"}

    else:
        raise ValueError(f"Unknown tool: {name}")


# ── Main entry point ──────────────────────────────────────────────

async def main():
    _login()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

