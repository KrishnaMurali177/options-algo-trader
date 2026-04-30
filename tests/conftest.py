"""Shared test fixtures."""

import os
import sys
from datetime import date, datetime

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.market_data import MarketIndicators, MarketRegime
from src.models.options import Greeks, OptionContract, OptionLeg, OptionOrder
from src.models.portfolio import (
    AccountInfo,
    OptionPosition,
    PortfolioSummary,
    StockPosition,
)


@pytest.fixture
def low_vol_bullish_indicators() -> MarketIndicators:
    return MarketIndicators(
        symbol="AAPL",
        timestamp=datetime.utcnow(),
        current_price=190.0,
        vix=15.0,
        rsi_14=55.0,
        sma_20=188.0,
        sma_50=185.0,
        sma_200=175.0,
        bb_upper=195.0,
        bb_middle=188.0,
        bb_lower=181.0,
        macd=1.5,
        macd_signal=1.0,
        macd_histogram=0.5,
        atr_14=3.2,
        next_earnings_date=date(2026, 7, 25),
        days_to_earnings=100,
    )


@pytest.fixture
def range_bound_hv_indicators() -> MarketIndicators:
    return MarketIndicators(
        symbol="SPY",
        timestamp=datetime.utcnow(),
        current_price=440.0,
        vix=25.0,
        rsi_14=50.0,
        sma_20=438.0,
        sma_50=435.0,
        sma_200=420.0,
        bb_upper=450.0,
        bb_middle=438.0,
        bb_lower=426.0,
        macd=0.3,
        macd_signal=0.2,
        macd_histogram=0.1,
        atr_14=6.5,
        next_earnings_date=None,
        days_to_earnings=None,
    )


@pytest.fixture
def high_vol_bearish_indicators() -> MarketIndicators:
    return MarketIndicators(
        symbol="AAPL",
        timestamp=datetime.utcnow(),
        current_price=165.0,
        vix=35.0,
        rsi_14=72.0,
        sma_20=170.0,
        sma_50=178.0,
        sma_200=180.0,
        bb_upper=180.0,
        bb_middle=170.0,
        bb_lower=160.0,
        macd=-2.0,
        macd_signal=-1.0,
        macd_histogram=-1.0,
        atr_14=5.5,
        next_earnings_date=date(2026, 7, 25),
        days_to_earnings=100,
    )


@pytest.fixture
def sample_portfolio() -> PortfolioSummary:
    return PortfolioSummary(
        account=AccountInfo(
            account_id="TEST123",
            buying_power=50000.0,
            cash=50000.0,
            portfolio_value=100000.0,
            options_buying_power=25000.0,
        ),
        stock_positions=[
            StockPosition(
                symbol="AAPL",
                quantity=200,
                average_cost=150.0,
                current_price=190.0,
                market_value=38000.0,
                unrealized_pnl=8000.0,
            ),
        ],
        option_positions=[],
        total_options_allocation=0.0,
        daily_pnl=200.0,
        trades_today=0,
    )


@pytest.fixture
def sample_call_chain() -> list[OptionContract]:
    """Realistic call option chain for AAPL ~190, 35 DTE."""
    base_exp = date(2026, 5, 15)
    return [
        OptionContract(
            symbol="AAPL260515C00195000", underlying="AAPL",
            strike=195.0, expiration=base_exp, option_type="call",
            bid=3.20, ask=3.50, last=3.35, volume=1200, open_interest=5400,
            greeks=Greeks(delta=0.35, gamma=0.04, theta=-0.05, vega=0.12, implied_volatility=0.25),
        ),
        OptionContract(
            symbol="AAPL260515C00200000", underlying="AAPL",
            strike=200.0, expiration=base_exp, option_type="call",
            bid=1.80, ask=2.10, last=1.95, volume=800, open_interest=3200,
            greeks=Greeks(delta=0.25, gamma=0.03, theta=-0.04, vega=0.10, implied_volatility=0.24),
        ),
        OptionContract(
            symbol="AAPL260515C00205000", underlying="AAPL",
            strike=205.0, expiration=base_exp, option_type="call",
            bid=0.90, ask=1.10, last=1.00, volume=500, open_interest=2000,
            greeks=Greeks(delta=0.15, gamma=0.02, theta=-0.03, vega=0.08, implied_volatility=0.23),
        ),
    ]


@pytest.fixture
def sample_put_chain() -> list[OptionContract]:
    """Realistic put chain for AAPL ~190, 35 DTE."""
    base_exp = date(2026, 5, 15)
    return [
        OptionContract(
            symbol="AAPL260515P00185000", underlying="AAPL",
            strike=185.0, expiration=base_exp, option_type="put",
            bid=2.80, ask=3.10, last=2.95, volume=900, open_interest=4100,
            greeks=Greeks(delta=-0.35, gamma=0.04, theta=-0.05, vega=0.12, implied_volatility=0.26),
        ),
        OptionContract(
            symbol="AAPL260515P00190000", underlying="AAPL",
            strike=190.0, expiration=base_exp, option_type="put",
            bid=4.50, ask=4.80, last=4.65, volume=1500, open_interest=6000,
            greeks=Greeks(delta=-0.48, gamma=0.05, theta=-0.06, vega=0.14, implied_volatility=0.27),
        ),
        OptionContract(
            symbol="AAPL260515P00180000", underlying="AAPL",
            strike=180.0, expiration=base_exp, option_type="put",
            bid=1.40, ask=1.70, last=1.55, volume=600, open_interest=2800,
            greeks=Greeks(delta=-0.20, gamma=0.03, theta=-0.03, vega=0.09, implied_volatility=0.25),
        ),
    ]

