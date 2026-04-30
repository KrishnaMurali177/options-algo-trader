"""Pydantic models for option contracts, orders, and Greeks."""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Greeks(BaseModel):
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    implied_volatility: float = 0.0


class OptionContract(BaseModel):
    symbol: str
    underlying: str
    strike: float
    expiration: date
    option_type: Literal["call", "put"]
    bid: float
    ask: float
    mid: float = 0.0
    last: float = 0.0
    volume: int = 0
    open_interest: int = 0
    greeks: Greeks = Field(default_factory=Greeks)


class OptionLeg(BaseModel):
    symbol: str
    strike: float
    expiration: date
    option_type: Literal["call", "put"]
    action: Literal["buy_to_open", "sell_to_open", "buy_to_close", "sell_to_close"]
    quantity: int = 1


class OptionOrder(BaseModel):
    strategy_name: str
    underlying: str
    legs: list[OptionLeg]
    order_type: Literal["limit", "market"] = "limit"
    limit_price: Optional[float] = None
    duration: Literal["gfd", "gtc"] = "gfd"
    max_loss: float
    max_profit: float
    risk_reward_ratio: float
    rationale: str = ""

    # ── Breakout / Scalp Levels (populated by buy_call / buy_put) ──
    breakout_price: Optional[float] = Field(default=None, description="Price level that triggers the breakout entry")
    opening_range_high: Optional[float] = Field(default=None, description="30-min opening range high")
    opening_range_low: Optional[float] = Field(default=None, description="30-min opening range low")
    entry_price: Optional[float] = Field(default=None, description="Recommended stock entry price for the breakout")
    stop_loss_price: Optional[float] = Field(default=None, description="Stock-level stop loss")
    profit_target_1: Optional[float] = Field(default=None, description="First profit target (1:1 R:R)")
    profit_target_2: Optional[float] = Field(default=None, description="Second profit target (2:1 R:R)")
    breakout_direction: Optional[str] = Field(default=None, description="bullish / bearish / neutral")

