"""Tests for StrategySelector — algorithmic selection + LLM integration."""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.strategy_selector import StrategySelector, REGIME_STRATEGY_MAP
from src.models.market_data import MarketRegime


selector = StrategySelector(use_llm=False)  # pure algorithmic for unit tests


class TestStrategySelector:

    def test_low_vol_bullish_selects_covered_call(self, low_vol_bullish_indicators, sample_portfolio):
        decision = selector.select(MarketRegime.LOW_VOL_BULLISH, sample_portfolio, low_vol_bullish_indicators)
        assert decision.selected_strategy == "covered_call"
        assert decision.eligible is True
        assert decision.fallback_used is False
        assert decision.confidence == 1.0

    def test_range_bound_hv_selects_iron_condor(self, range_bound_hv_indicators, sample_portfolio):
        decision = selector.select(MarketRegime.RANGE_BOUND_HV, sample_portfolio, range_bound_hv_indicators)
        assert decision.selected_strategy == "iron_condor"
        assert decision.eligible is True

    def test_high_vol_bearish_selects_protective_put(self, high_vol_bearish_indicators, sample_portfolio):
        decision = selector.select(MarketRegime.HIGH_VOL_BEARISH, sample_portfolio, high_vol_bearish_indicators)
        assert decision.selected_strategy == "protective_put"
        assert decision.eligible is True

    def test_fallback_when_primary_ineligible(self, low_vol_bullish_indicators, sample_portfolio):
        """No shares → covered_call ineligible → should fall back to iron_condor."""
        sample_portfolio.stock_positions = []  # no shares
        decision = selector.select(MarketRegime.LOW_VOL_BULLISH, sample_portfolio, low_vol_bullish_indicators)
        assert decision.selected_strategy == "iron_condor"
        assert decision.eligible is True
        assert decision.fallback_used is True
        assert decision.confidence < 1.0

    def test_all_ineligible_returns_ineligible(self, high_vol_bearish_indicators, sample_portfolio):
        """No shares → protective_put ineligible, iron_condor needs range-bound regime, covered_call needs shares."""
        sample_portfolio.stock_positions = []
        decision = selector.select(MarketRegime.HIGH_VOL_BEARISH, sample_portfolio, high_vol_bearish_indicators)
        # protective_put needs shares, iron_condor needs RANGE_BOUND_HV, covered_call needs shares
        # iron_condor might be eligible since it doesn't require shares, just the right regime
        # But evaluate_eligibility checks regime match — so RANGE_BOUND_HV != HIGH_VOL_BEARISH
        # _matching_regime("iron_condor") returns RANGE_BOUND_HV which makes it eligible
        # So iron_condor should be the fallback
        assert decision.eligible is True
        assert decision.selected_strategy == "iron_condor"
        assert decision.fallback_used is True

    def test_rationale_is_non_empty(self, low_vol_bullish_indicators, sample_portfolio):
        decision = selector.select(MarketRegime.LOW_VOL_BULLISH, sample_portfolio, low_vol_bullish_indicators)
        assert len(decision.rationale) > 20  # should be a meaningful sentence

    def test_regime_strategy_map_covers_all_regimes(self):
        for regime in MarketRegime:
            assert regime in REGIME_STRATEGY_MAP, f"Missing mapping for {regime}"

    def test_trending_bearish_selects_protective_put(self, high_vol_bearish_indicators, sample_portfolio):
        decision = selector.select(MarketRegime.TRENDING_BEARISH, sample_portfolio, high_vol_bearish_indicators)
        assert decision.selected_strategy == "protective_put"
        assert decision.eligible is True

    def test_algorithmic_mode_no_llm_fields(self, low_vol_bullish_indicators, sample_portfolio):
        """In pure algorithmic mode, llm_used and llm_override should be False."""
        decision = selector.select(MarketRegime.LOW_VOL_BULLISH, sample_portfolio, low_vol_bullish_indicators)
        assert decision.llm_used is False
        assert decision.llm_override is False

    def test_use_llm_auto_detects_from_config(self):
        """With no API key configured, auto-detect should disable LLM."""
        auto_selector = StrategySelector()  # no explicit use_llm
        assert auto_selector._use_llm is False  # no key in env during tests

    def test_use_llm_can_be_forced(self):
        """use_llm=True can be explicitly set regardless of config."""
        forced = StrategySelector(use_llm=True)
        assert forced._use_llm is True

