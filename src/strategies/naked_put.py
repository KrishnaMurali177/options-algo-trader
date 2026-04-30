"""Naked Put (Cash-Secured Put) — sell OTM puts to generate income without owning shares.

This is a bullish/neutral strategy: you sell a put and collect premium, profiting
if the stock stays above the strike. You must have enough cash to cover assignment
(hence "cash-secured"). It's ideal when you're willing to buy the stock at a
lower price and want to get paid while waiting.

Risk: If the stock drops below the strike, you're obligated to buy at the strike
(max loss = strike × 100 − premium received).
"""

from __future__ import annotations

import logging

from src.models.market_data import MarketIndicators, MarketRegime
from src.models.options import OptionContract, OptionLeg, OptionOrder
from src.models.portfolio import PortfolioSummary
from src.strategies.base_strategy import BaseStrategy
from config.settings import settings

logger = logging.getLogger(__name__)


class NakedPutStrategy(BaseStrategy):
    name = "naked_put"

    # ── Eligibility ──────────────────────────────────────────────

    def evaluate_eligibility(
        self,
        regime: MarketRegime,
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> bool:
        # Works in low-vol bullish/neutral — when you expect the stock to hold or rise
        if regime not in (
            MarketRegime.LOW_VOL_BULLISH,
            MarketRegime.LOW_VOL_NEUTRAL,
            MarketRegime.RANGE_BOUND_HV,
        ):
            return False

        # Must have enough cash to cover assignment (strike × 100)
        # We'll check against a rough OTM put strike (~95% of current price)
        estimated_strike = indicators.current_price * 0.95
        cash_needed = estimated_strike * 100
        available_cash = portfolio.account.buying_power
        if available_cash < cash_needed:
            logger.info(
                "Naked put ineligible: need ~$%.0f cash to secure, have $%.0f",
                cash_needed, available_cash,
            )
            return False

        return True

    # ── Order Construction ───────────────────────────────────────

    def construct_order(
        self,
        symbol: str,
        chain: list[OptionContract],
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> OptionOrder:
        # Filter: puts, within configured DTE range
        puts = [c for c in chain if c.option_type == "put"]
        puts = self._filter_by_dte(puts, settings.min_dte, settings.max_dte)
        if not puts:
            raise ValueError(f"No suitable put contracts for {symbol}")

        # Sell OTM put with delta ~-0.25 (conservative) to ~-0.30
        target = self._find_by_delta(puts, target_delta=0.25)
        if target is None:
            raise ValueError("No put near delta -0.25")

        premium = target.mid * 100  # per contract
        cash_to_secure = target.strike * 100
        max_loss = cash_to_secure - premium  # stock goes to $0
        max_profit = premium  # stock stays above strike

        leg = OptionLeg(
            symbol=target.symbol,
            strike=target.strike,
            expiration=target.expiration,
            option_type="put",
            action="sell_to_open",
            quantity=1,
        )

        return OptionOrder(
            strategy_name=self.name,
            underlying=symbol,
            legs=[leg],
            order_type="limit",
            limit_price=target.mid,
            duration="gfd",
            max_loss=max_loss,
            max_profit=max_profit,
            risk_reward_ratio=round(max_profit / max_loss, 3) if max_loss > 0 else 0,
            rationale=(
                f"Sell {target.strike} put expiring {target.expiration} "
                f"(delta {target.greeks.delta:.2f}) for ${target.mid:.2f} premium. "
                f"Cash secured: ${cash_to_secure:,.0f}. "
                f"Profit if {symbol} stays above ${target.strike:.2f}."
            ),
        )

    # ── Risk Calc ────────────────────────────────────────────────

    def calculate_max_loss(self, order: OptionOrder) -> float:
        return order.max_loss

