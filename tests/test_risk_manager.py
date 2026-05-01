"""Tests for the RiskManager."""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from unittest.mock import MagicMock

import pytest
from src.models.options import OptionLeg, OptionOrder
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

