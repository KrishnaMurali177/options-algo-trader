"""Tests for the three options strategies."""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from src.models.market_data import MarketRegime
from src.strategies.covered_call import CoveredCallStrategy
from src.strategies.iron_condor import IronCondorStrategy
from src.strategies.protective_put import ProtectivePutStrategy


# ── Covered Call ──────────────────────────────────────────────────

class TestCoveredCall:
    strategy = CoveredCallStrategy()

    def test_eligible_low_vol_bullish(self, low_vol_bullish_indicators, sample_portfolio):
        assert self.strategy.evaluate_eligibility(
            MarketRegime.LOW_VOL_BULLISH, sample_portfolio, low_vol_bullish_indicators
        )

    def test_ineligible_wrong_regime(self, high_vol_bearish_indicators, sample_portfolio):
        assert not self.strategy.evaluate_eligibility(
            MarketRegime.HIGH_VOL_BEARISH, sample_portfolio, high_vol_bearish_indicators
        )

    def test_ineligible_no_shares(self, low_vol_bullish_indicators, sample_portfolio):
        sample_portfolio.stock_positions = []  # no stock
        assert not self.strategy.evaluate_eligibility(
            MarketRegime.LOW_VOL_BULLISH, sample_portfolio, low_vol_bullish_indicators
        )

    def test_construct_order(self, low_vol_bullish_indicators, sample_portfolio, sample_call_chain):
        order = self.strategy.construct_order(
            "AAPL", sample_call_chain, sample_portfolio, low_vol_bullish_indicators
        )
        assert order.strategy_name == "covered_call"
        assert len(order.legs) == 1
        assert order.legs[0].action == "sell_to_open"
        assert order.legs[0].option_type == "call"
        assert order.max_profit > 0


# ── Iron Condor ───────────────────────────────────────────────────

class TestIronCondor:
    strategy = IronCondorStrategy()

    def test_eligible_range_bound(self, range_bound_hv_indicators, sample_portfolio):
        assert self.strategy.evaluate_eligibility(
            MarketRegime.RANGE_BOUND_HV, sample_portfolio, range_bound_hv_indicators
        )

    def test_ineligible_wrong_regime(self, low_vol_bullish_indicators, sample_portfolio):
        assert not self.strategy.evaluate_eligibility(
            MarketRegime.LOW_VOL_BULLISH, sample_portfolio, low_vol_bullish_indicators
        )

    def test_ineligible_earnings_soon(self, range_bound_hv_indicators, sample_portfolio):
        range_bound_hv_indicators.days_to_earnings = 5
        assert not self.strategy.evaluate_eligibility(
            MarketRegime.RANGE_BOUND_HV, sample_portfolio, range_bound_hv_indicators
        )


# ── Protective Put ────────────────────────────────────────────────

class TestProtectivePut:
    strategy = ProtectivePutStrategy()

    def test_eligible_high_vol_bearish(self, high_vol_bearish_indicators, sample_portfolio):
        assert self.strategy.evaluate_eligibility(
            MarketRegime.HIGH_VOL_BEARISH, sample_portfolio, high_vol_bearish_indicators
        )

    def test_ineligible_no_stock(self, high_vol_bearish_indicators, sample_portfolio):
        sample_portfolio.stock_positions = []
        assert not self.strategy.evaluate_eligibility(
            MarketRegime.HIGH_VOL_BEARISH, sample_portfolio, high_vol_bearish_indicators
        )

    def test_construct_order(self, high_vol_bearish_indicators, sample_portfolio, sample_put_chain):
        order = self.strategy.construct_order(
            "AAPL", sample_put_chain, sample_portfolio, high_vol_bearish_indicators
        )
        assert order.strategy_name == "protective_put"
        assert len(order.legs) == 1
        assert order.legs[0].action == "buy_to_open"
        assert order.legs[0].option_type == "put"
        assert order.max_loss > 0  # premium paid

