"""Iron Condor strategy — sell OTM put + call spreads in range-bound high-vol markets."""

from __future__ import annotations

import logging

from src.models.market_data import MarketIndicators, MarketRegime
from src.models.options import OptionContract, OptionLeg, OptionOrder
from src.models.portfolio import PortfolioSummary
from src.strategies.base_strategy import BaseStrategy
from config.settings import settings

logger = logging.getLogger(__name__)


class IronCondorStrategy(BaseStrategy):
    name = "iron_condor"

    def evaluate_eligibility(
        self,
        regime: MarketRegime,
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> bool:
        if regime != MarketRegime.RANGE_BOUND_HV:
            return False
        # Reject if earnings are imminent
        if indicators.days_to_earnings is not None and indicators.days_to_earnings < 14:
            logger.info("Iron condor ineligible: earnings in %d days", indicators.days_to_earnings)
            return False
        return True

    def construct_order(
        self,
        symbol: str,
        chain: list[OptionContract],
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> OptionOrder:
        puts = [c for c in chain if c.option_type == "put"]
        calls = [c for c in chain if c.option_type == "call"]
        puts = self._filter_by_dte(puts, settings.min_dte, settings.max_dte)
        calls = self._filter_by_dte(calls, settings.min_dte, settings.max_dte)

        if not puts or not calls:
            raise ValueError(f"Insufficient option chain data for {symbol}")

        # Short put: delta ~-0.18
        short_put = self._find_by_delta(puts, target_delta=0.18)
        # Short call: delta ~0.18
        short_call = self._find_by_delta(calls, target_delta=0.18)
        if not short_put or not short_call:
            raise ValueError("Cannot find short strikes at target delta")

        # Wing width: find next strike further OTM
        wing_width = 5.0  # default $5 width
        long_put_strike = short_put.strike - wing_width
        long_call_strike = short_call.strike + wing_width

        # Find the actual long contracts
        long_put = min(puts, key=lambda c: abs(c.strike - long_put_strike), default=None)
        long_call = min(calls, key=lambda c: abs(c.strike - long_call_strike), default=None)
        if not long_put or not long_call:
            raise ValueError("Cannot find wing contracts")

        # Use the same expiration as the short legs
        exp = short_put.expiration

        net_premium = (short_put.mid + short_call.mid - long_put.mid - long_call.mid) * 100
        put_width = (short_put.strike - long_put.strike) * 100
        call_width = (long_call.strike - short_call.strike) * 100
        max_loss = max(put_width, call_width) - net_premium

        legs = [
            OptionLeg(symbol=short_put.symbol, strike=short_put.strike, expiration=exp,
                      option_type="put", action="sell_to_open"),
            OptionLeg(symbol=long_put.symbol, strike=long_put.strike, expiration=exp,
                      option_type="put", action="buy_to_open"),
            OptionLeg(symbol=short_call.symbol, strike=short_call.strike, expiration=exp,
                      option_type="call", action="sell_to_open"),
            OptionLeg(symbol=long_call.symbol, strike=long_call.strike, expiration=exp,
                      option_type="call", action="buy_to_open"),
        ]

        return OptionOrder(
            strategy_name=self.name,
            underlying=symbol,
            legs=legs,
            order_type="limit",
            limit_price=round(net_premium / 100, 2),
            duration="gfd",
            max_loss=max_loss,
            max_profit=net_premium,
            risk_reward_ratio=round(net_premium / max_loss, 3) if max_loss > 0 else 0,
            rationale=(
                f"Iron condor on {symbol}: sell {short_put.strike}P/{short_call.strike}C, "
                f"buy {long_put.strike}P/{long_call.strike}C, exp {exp}. "
                f"Net credit ${net_premium / 100:.2f}. Regime: range_bound_high_vol."
            ),
        )

    def calculate_max_loss(self, order: OptionOrder) -> float:
        return order.max_loss

