"""Protective Put strategy — buy puts to hedge long stock exposure in bearish/volatile markets."""

from __future__ import annotations

import logging

from src.models.market_data import MarketIndicators, MarketRegime
from src.models.options import OptionContract, OptionLeg, OptionOrder
from src.models.portfolio import PortfolioSummary
from src.strategies.base_strategy import BaseStrategy
from config.settings import settings

logger = logging.getLogger(__name__)


class ProtectivePutStrategy(BaseStrategy):
    name = "protective_put"

    def evaluate_eligibility(
        self,
        regime: MarketRegime,
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> bool:
        if regime not in (MarketRegime.HIGH_VOL_BEARISH, MarketRegime.TRENDING_BEARISH):
            return False

        # Must have long stock exposure to hedge
        for pos in portfolio.stock_positions:
            if pos.symbol.upper() == indicators.symbol.upper() and pos.quantity > 0:
                return True

        logger.info("Protective put ineligible: no long position in %s", indicators.symbol)
        return False

    def construct_order(
        self,
        symbol: str,
        chain: list[OptionContract],
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> OptionOrder:
        puts = [c for c in chain if c.option_type == "put"]
        puts = self._filter_by_dte(puts, settings.min_dte, settings.max_dte)

        if not puts:
            raise ValueError(f"No suitable put contracts for {symbol}")

        # ATM or slightly OTM put: delta ~-0.45
        target_put = self._find_by_delta(puts, target_delta=0.45)
        if target_put is None:
            raise ValueError("No put near delta -0.45")

        premium_cost = target_put.mid * 100
        # Shares we are hedging
        shares = 0
        for pos in portfolio.stock_positions:
            if pos.symbol.upper() == symbol.upper():
                shares = int(pos.quantity)
                break
        contracts_needed = max(1, shares // 100)

        total_cost = premium_cost * contracts_needed
        max_profit = (indicators.current_price - target_put.strike) * 100 * contracts_needed  # stock drops to strike
        # Actually for a protective put, the "profit" on the put is unlimited downside protection
        # Max loss on the put itself is the premium paid

        leg = OptionLeg(
            symbol=target_put.symbol,
            strike=target_put.strike,
            expiration=target_put.expiration,
            option_type="put",
            action="buy_to_open",
            quantity=contracts_needed,
        )

        return OptionOrder(
            strategy_name=self.name,
            underlying=symbol,
            legs=[leg],
            order_type="limit",
            limit_price=target_put.mid,
            duration="gfd",
            max_loss=total_cost,  # premium paid
            max_profit=target_put.strike * 100 * contracts_needed - total_cost,  # stock → 0
            risk_reward_ratio=round(
                (target_put.strike * 100 * contracts_needed - total_cost) / total_cost, 3
            ) if total_cost > 0 else 0,
            rationale=(
                f"Buy {contracts_needed}x {target_put.strike} put expiring {target_put.expiration} "
                f"(delta {target_put.greeks.delta:.2f}) for ${target_put.mid:.2f} to hedge "
                f"{shares} shares of {symbol}. Regime: {MarketRegime.HIGH_VOL_BEARISH.value}."
            ),
        )

    def calculate_max_loss(self, order: OptionOrder) -> float:
        return order.max_loss

