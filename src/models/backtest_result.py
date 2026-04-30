"""Backtest result models for intraday scalping strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class TradeResult:
    """Result of a single simulated intraday trade."""
    trade_date: date
    symbol: str
    direction: str                    # "call", "put", or "skip"
    entry_price: float
    exit_price: float
    stop_loss: float
    target_1: float
    target_2: float
    range_high: float
    range_low: float
    exit_reason: str                  # "stop", "target_1", "target_2", "eod", "no_entry"
    pnl_dollars: float               # per-share P&L
    pnl_pct: float                   # P&L as % of entry price
    momentum_score: int
    signal_scores: dict[str, int] = field(default_factory=dict)
    entry_time: Optional[str] = None  # HH:MM when entry triggered
    exit_time: Optional[str] = None   # HH:MM when exit triggered
    vwap_at_entry: float = 0.0
    volume_at_entry: int = 0
    is_winner: bool = False


@dataclass
class SignalAccuracy:
    """Accuracy stats for a single signal."""
    signal_name: str
    times_active: int                 # how many trades had this signal bullish/bearish
    wins_when_active: int
    avg_pnl_when_active: float
    win_rate: float                   # wins / active


@dataclass
class BacktestReport:
    """Aggregate backtest results."""
    symbol: str
    period: str
    total_days: int
    total_trades: int
    trades_taken: int                 # trades where entry triggered (not skipped)
    wins: int
    losses: int
    win_rate: float
    avg_pnl_per_trade: float
    total_pnl: float
    max_win: float
    max_loss: float
    avg_winner: float
    avg_loser: float
    profit_factor: float              # gross profit / gross loss
    call_trades: int
    put_trades: int
    call_win_rate: float
    put_win_rate: float
    exit_reasons: dict[str, int] = field(default_factory=dict)
    signal_accuracy: list[SignalAccuracy] = field(default_factory=list)
    trades: list[TradeResult] = field(default_factory=list)

