"""Risk Manager — validates every trade against portfolio-level guardrails."""

from __future__ import annotations

import logging
from datetime import date

from config.settings import settings
from src.models.options import OptionOrder
from src.models.portfolio import PortfolioSummary, RiskAssessment
from src.models.market_data import MarketIndicators

logger = logging.getLogger(__name__)


class RiskManager:
    """Enforces pre-trade risk checks and portfolio-level circuit breakers."""

    def __init__(self, cfg=None):
        self.cfg = cfg or settings

    def validate(
        self,
        order: OptionOrder,
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> RiskAssessment:
        reasons: list[str] = []
        pv = portfolio.account.portfolio_value or 1.0  # avoid div-by-zero

        # 1. Position size check
        position_size_pct = order.max_loss / pv
        if position_size_pct > self.cfg.max_position_size_pct:
            reasons.append(
                f"Position size {position_size_pct:.1%} exceeds max {self.cfg.max_position_size_pct:.1%}"
            )

        # 2. Max loss check
        max_loss_pct = order.max_loss / pv
        if max_loss_pct > self.cfg.max_loss_pct:
            reasons.append(
                f"Max loss {max_loss_pct:.1%} exceeds limit {self.cfg.max_loss_pct:.1%}"
            )

        # 3. Total options allocation check
        current_alloc = portfolio.total_options_allocation
        new_alloc_pct = (current_alloc + order.max_loss) / pv
        if new_alloc_pct > self.cfg.max_options_allocation_pct:
            reasons.append(
                f"Options allocation would be {new_alloc_pct:.1%}, exceeds {self.cfg.max_options_allocation_pct:.1%}"
            )

        # 4. Minimum DTE
        for leg in order.legs:
            dte = (leg.expiration - date.today()).days
            if dte < self.cfg.min_dte:
                reasons.append(f"Leg {leg.strike}{leg.option_type[0].upper()} has only {dte} DTE (min {self.cfg.min_dte})")

        # 5. Earnings blackout
        if indicators.days_to_earnings is not None:
            if indicators.days_to_earnings <= self.cfg.earnings_blackout_days:
                reasons.append(
                    f"Earnings in {indicators.days_to_earnings} days (blackout {self.cfg.earnings_blackout_days}d)"
                )

        # 6. Daily trade limit
        if portfolio.trades_today >= self.cfg.max_daily_trades:
            reasons.append(
                f"Daily trade limit reached ({portfolio.trades_today}/{self.cfg.max_daily_trades})"
            )

        # 7. Circuit breaker — daily P&L
        daily_loss_pct = abs(portfolio.daily_pnl) / pv if portfolio.daily_pnl < 0 else 0
        if daily_loss_pct >= self.cfg.circuit_breaker_daily_loss_pct:
            reasons.append(
                f"CIRCUIT BREAKER: daily loss {daily_loss_pct:.1%} >= {self.cfg.circuit_breaker_daily_loss_pct:.1%}"
            )

        approved = len(reasons) == 0
        assessment = RiskAssessment(
            approved=approved,
            rejection_reasons=reasons,
            position_size_pct=position_size_pct,
            max_loss_pct=max_loss_pct,
            portfolio_delta_after=0.0,  # TODO: compute net delta
            options_allocation_after_pct=new_alloc_pct,
        )

        if approved:
            logger.info("✅ Risk check PASSED for %s on %s", order.strategy_name, order.underlying)
        else:
            logger.warning("❌ Risk check FAILED for %s: %s", order.strategy_name, reasons)

        return assessment

