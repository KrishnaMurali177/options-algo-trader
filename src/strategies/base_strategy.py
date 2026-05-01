"""Abstract base class for all options strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models.market_data import MarketIndicators, MarketRegime
from src.models.options import OptionContract, OptionOrder
from src.models.portfolio import PortfolioSummary


class BaseStrategy(ABC):
    """Every strategy must implement these three methods."""

    name: str = "base"

    @abstractmethod
    def evaluate_eligibility(
        self,
        regime: MarketRegime,
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> bool:
        """Return True if the strategy is eligible given market + portfolio state."""
        ...

    @abstractmethod
    def construct_order(
        self,
        symbol: str,
        chain: list[OptionContract],
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> OptionOrder:
        """Build a concrete OptionOrder from the options chain."""
        ...

    @abstractmethod
    def calculate_max_loss(self, order: OptionOrder) -> float:
        """Return the max dollar loss for this order."""
        ...

    # Convenience: select contracts by delta target
    @staticmethod
    def _find_by_delta(
        contracts: list[OptionContract],
        target_delta: float,
        tolerance: float = 0.05,
    ) -> OptionContract | None:
        best = None
        best_diff = float("inf")
        for c in contracts:
            diff = abs(abs(c.greeks.delta) - abs(target_delta))
            if diff < best_diff:
                best_diff = diff
                best = c
        if best and best_diff <= tolerance:
            return best
        # Fallback: return closest regardless
        return best

    @staticmethod
    def _filter_by_dte(contracts: list[OptionContract], min_dte: int, max_dte: int):
        from datetime import date as _date

        today = _date.today()
        return [
            c
            for c in contracts
            if min_dte <= (c.expiration - today).days <= max_dte
        ]

