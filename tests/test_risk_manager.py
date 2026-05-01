"""Tests for the RiskManager."""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from unittest.mock import MagicMock

import pytest
from src.exceptions import PortfolioDataError
from src.models.options import Greeks, OptionLeg, OptionOrder
from src.models.portfolio import OptionPosition
from src.risk_manager import RiskManager


def _make_order(max_loss: float = 1000, dte_days: int = 35) -> OptionOrder:
    return OptionOrder(
        strategy_name="test",
        underlying="AAPL",
        legs=[
            OptionLeg(
                symbol="AAPL260515C00195000",
                strike=195.0,
                expiration=date.today() + __import__("datetime").timedelta(days=dte_days),
                option_type="call",
                action="sell_to_open",
            )
        ],
        max_loss=max_loss,
        max_profit=500,
        risk_reward_ratio=0.5,
    )


class TestRiskManager:

    def _make_cfg(self, **overrides):
        cfg = MagicMock()
        defaults = dict(
            max_position_size_pct=0.05,
            max_loss_pct=0.02,
            max_options_allocation_pct=0.15,
            min_dte=14,
            earnings_blackout_days=7,
            max_daily_trades=3,
            circuit_breaker_daily_loss_pct=0.03,
        )
        defaults.update(overrides)
        for k, v in defaults.items():
            setattr(cfg, k, v)
        return cfg

    def test_passes_valid_order(self, sample_portfolio, low_vol_bullish_indicators):
        rm = RiskManager(cfg=self._make_cfg())
        order = _make_order(max_loss=1000)  # 1% of $100k portfolio
        result = rm.validate(order, sample_portfolio, low_vol_bullish_indicators)
        assert result.approved

    def test_rejects_large_position(self, sample_portfolio, low_vol_bullish_indicators):
        rm = RiskManager(cfg=self._make_cfg())
        order = _make_order(max_loss=10000)  # 10% — exceeds 5% limit
        result = rm.validate(order, sample_portfolio, low_vol_bullish_indicators)
        assert not result.approved
        assert any("Position size" in r for r in result.rejection_reasons)

    def test_rejects_low_dte(self, sample_portfolio, low_vol_bullish_indicators):
        rm = RiskManager(cfg=self._make_cfg())
        order = _make_order(max_loss=500, dte_days=7)  # below 14 DTE min
        result = rm.validate(order, sample_portfolio, low_vol_bullish_indicators)
        assert not result.approved
        assert any("DTE" in r for r in result.rejection_reasons)

    def test_rejects_earnings_blackout(self, sample_portfolio, low_vol_bullish_indicators):
        low_vol_bullish_indicators.days_to_earnings = 3
        rm = RiskManager(cfg=self._make_cfg())
        order = _make_order(max_loss=500)
        result = rm.validate(order, sample_portfolio, low_vol_bullish_indicators)
        assert not result.approved
        assert any("Earnings" in r for r in result.rejection_reasons)

    def test_circuit_breaker(self, sample_portfolio, low_vol_bullish_indicators):
        sample_portfolio.daily_pnl = -5000  # -5% of $100k
        rm = RiskManager(cfg=self._make_cfg())
        order = _make_order(max_loss=500)
        result = rm.validate(order, sample_portfolio, low_vol_bullish_indicators)
        assert not result.approved
        assert any("CIRCUIT BREAKER" in r for r in result.rejection_reasons)

    def test_rejects_daily_trade_limit(self, sample_portfolio, low_vol_bullish_indicators):
        sample_portfolio.trades_today = 3  # already at limit
        rm = RiskManager(cfg=self._make_cfg())
        order = _make_order(max_loss=500)
        result = rm.validate(order, sample_portfolio, low_vol_bullish_indicators)
        assert not result.approved
        assert any("Daily trade limit" in r for r in result.rejection_reasons)

    def test_raises_on_zero_portfolio_value(self, sample_portfolio, low_vol_bullish_indicators):
        sample_portfolio.account.portfolio_value = 0.0
        rm = RiskManager(cfg=self._make_cfg())
        order = _make_order(max_loss=500)
        with pytest.raises(PortfolioDataError, match="Invalid portfolio value"):
            rm.validate(order, sample_portfolio, low_vol_bullish_indicators)

    def test_raises_on_negative_portfolio_value(self, sample_portfolio, low_vol_bullish_indicators):
        sample_portfolio.account.portfolio_value = -500.0
        rm = RiskManager(cfg=self._make_cfg())
        order = _make_order(max_loss=500)
        with pytest.raises(PortfolioDataError, match="Invalid portfolio value"):
            rm.validate(order, sample_portfolio, low_vol_bullish_indicators)

    def test_portfolio_delta_computed(self, sample_portfolio, low_vol_bullish_indicators):
        sample_portfolio.option_positions = [
            OptionPosition(
                symbol="AAPL260515C00195000", underlying="AAPL",
                strike=195.0, expiration=date.today() + __import__("datetime").timedelta(days=30),
                option_type="call", quantity=2, average_cost=3.0, current_price=3.5,
                market_value=700.0, unrealized_pnl=100.0, delta=0.45,
            ),
        ]
        order = OptionOrder(
            strategy_name="test", underlying="AAPL",
            legs=[OptionLeg(
                symbol="AAPL260515C00200000", strike=200.0,
                expiration=date.today() + __import__("datetime").timedelta(days=30),
                option_type="call", action="buy_to_open", quantity=1,
                greeks=Greeks(delta=0.30),
            )],
            max_loss=500, max_profit=500, risk_reward_ratio=1.0,
        )
        rm = RiskManager(cfg=self._make_cfg())
        result = rm.validate(order, sample_portfolio, low_vol_bullish_indicators)
        # existing: 0.45 * 2 * 100 = 90, order: 0.30 * 1 * 100 = 30, total = 120
        assert result.portfolio_delta_after == pytest.approx(120.0)

    def test_portfolio_delta_sell_leg_negates(self, sample_portfolio, low_vol_bullish_indicators):
        order = OptionOrder(
            strategy_name="test", underlying="AAPL",
            legs=[OptionLeg(
                symbol="AAPL260515C00200000", strike=200.0,
                expiration=date.today() + __import__("datetime").timedelta(days=30),
                option_type="call", action="sell_to_open", quantity=1,
                greeks=Greeks(delta=0.30),
            )],
            max_loss=500, max_profit=500, risk_reward_ratio=1.0,
        )
        rm = RiskManager(cfg=self._make_cfg())
        result = rm.validate(order, sample_portfolio, low_vol_bullish_indicators)
        # no existing positions, sell leg: -0.30 * 1 * 100 = -30
        assert result.portfolio_delta_after == pytest.approx(-30.0)

