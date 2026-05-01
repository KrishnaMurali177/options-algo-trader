"""Covered Call strategy — sell OTM calls on existing long stock positions."""

from __future__ import annotations

import logging
from datetime import date

from src.models.market_data import MarketIndicators, MarketRegime
from src.models.options import OptionContract, OptionLeg, OptionOrder
from src.models.portfolio import PortfolioSummary
from src.strategies.base_strategy import BaseStrategy
from config.settings import settings

logger = logging.getLogger(__name__)


class CoveredCallStrategy(BaseStrategy):
    name = "covered_call"

    # ── Eligibility ──────────────────────────────────────────────

    def evaluate_eligibility(
        self,
        regime: MarketRegime,
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> bool:
        # Must be low-vol bullish/neutral
        if regime not in (MarketRegime.LOW_VOL_BULLISH, MarketRegime.LOW_VOL_NEUTRAL):
            return False

        # Must own ≥100 shares of the underlying
        for pos in portfolio.stock_positions:
            if pos.symbol.upper() == indicators.symbol.upper() and pos.quantity >= 100:
                return True

        logger.info("Covered call ineligible: no 100-share lot for %s", indicators.symbol)
        return False

    # ── Order Construction ───────────────────────────────────────

    def construct_order(
        self,
        symbol: str,
        chain: list[OptionContract],
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> OptionOrder:
        # Filter: calls, within configured DTE range
        calls = [c for c in chain if c.option_type == "call"]
        calls = self._filter_by_dte(calls, settings.min_dte, settings.max_dte)
        if not calls:
            raise ValueError(f"No suitable call contracts for {symbol}")

        # Find OTM call with delta ~0.30
        target = self._find_by_delta(calls, target_delta=0.30)
        if target is None:
            raise ValueError("No call near delta 0.30")

        premium = target.mid * 100  # per contract
        max_profit = premium + (target.strike - indicators.current_price) * 100
        max_loss = indicators.current_price * 100 - premium  # stock drops to 0

        leg = OptionLeg(
            symbol=target.symbol,
            strike=target.strike,
            expiration=target.expiration,
            option_type="call",
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
                f"Sell {target.strike} call expiring {target.expiration} "
                f"(delta {target.greeks.delta:.2f}) for ${target.mid:.2f} premium. "
                f"Regime: {MarketRegime.LOW_VOL_BULLISH.value}."
            ),
        )

    # ── Risk Calc ────────────────────────────────────────────────

    def calculate_max_loss(self, order: OptionOrder) -> float:
        return order.max_loss

