"""Tests for MarketAnalyzer regime classification."""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.market_analyzer import MarketAnalyzer
from src.models.market_data import MarketRegime


analyzer = MarketAnalyzer()


def test_low_vol_bullish(low_vol_bullish_indicators):
    regime = analyzer.classify_regime(low_vol_bullish_indicators)
    assert regime == MarketRegime.LOW_VOL_BULLISH


def test_range_bound_hv(range_bound_hv_indicators):
    regime = analyzer.classify_regime(range_bound_hv_indicators)
    assert regime == MarketRegime.RANGE_BOUND_HV


def test_high_vol_bearish(high_vol_bearish_indicators):
    regime = analyzer.classify_regime(high_vol_bearish_indicators)
    assert regime == MarketRegime.HIGH_VOL_BEARISH


def test_trending_bearish():
    """Death cross + negative MACD histogram → trending bearish."""
    from datetime import datetime
    from src.models.market_data import MarketIndicators

    ind = MarketIndicators(
        symbol="XYZ", timestamp=datetime.utcnow(), current_price=100,
        vix=22, rsi_14=45,
        sma_20=98, sma_50=95, sma_200=105,   # 50 < 200 → death cross
        bb_upper=110, bb_middle=100, bb_lower=90,
        macd=-1.5, macd_signal=-0.5, macd_histogram=-1.0,
        atr_14=4.0,
    )
    assert analyzer.classify_regime(ind) == MarketRegime.TRENDING_BEARISH


def test_low_vol_neutral():
    """Low VIX but RSI outside bullish range → neutral."""
    from datetime import datetime
    from src.models.market_data import MarketIndicators

    ind = MarketIndicators(
        symbol="XYZ", timestamp=datetime.utcnow(), current_price=100,
        vix=12, rsi_14=35,   # RSI below 40 → not bullish
        sma_20=99, sma_50=101, sma_200=95,
        bb_upper=105, bb_middle=99, bb_lower=93,
        macd=0.2, macd_signal=0.1, macd_histogram=0.1,
        atr_14=2.0,
    )
    assert analyzer.classify_regime(ind) == MarketRegime.LOW_VOL_NEUTRAL

