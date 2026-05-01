"""Pydantic models for portfolio positions and account info."""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class StockPosition(BaseModel):
    symbol: str
    quantity: float
    average_cost: float
    current_price: float
    market_value: float
    unrealized_pnl: float


class OptionPosition(BaseModel):
    symbol: str
    underlying: str
    strike: float
    expiration: date
    option_type: Literal["call", "put"]
    quantity: int
    average_cost: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    delta: float = 0.0


class AccountInfo(BaseModel):
    account_id: str = ""
    buying_power: float = 0.0
    cash: float = 0.0
    portfolio_value: float = 0.0
    options_buying_power: float = 0.0


class PortfolioSummary(BaseModel):
    account: AccountInfo
    stock_positions: list[StockPosition] = Field(default_factory=list)
    option_positions: list[OptionPosition] = Field(default_factory=list)
    total_options_allocation: float = 0.0
    daily_pnl: float = 0.0
    trades_today: int = 0


class RiskAssessment(BaseModel):
    """Result of risk manager validation."""
    approved: bool
    rejection_reasons: list[str] = Field(default_factory=list)
    position_size_pct: float = 0.0
    max_loss_pct: float = 0.0
    portfolio_delta_after: float = 0.0
    options_allocation_after_pct: float = 0.0

