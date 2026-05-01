#!/usr/bin/env python
"""Quick smoke test for EntryAnalyzer and NakedPutStrategy."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from src.entry_analyzer import EntryAnalyzer
from src.models.market_data import MarketIndicators
from src.strategies.naked_put import NakedPutStrategy
from src.strategies.naked_call import NakedCallStrategy
from src.models.portfolio import PortfolioSummary, AccountInfo, StockPosition
from src.models.market_data import MarketRegime

ea = EntryAnalyzer()

# Test 1: AAPL bullish
ind = MarketIndicators(symbol="AAPL", timestamp=datetime.now(timezone.utc), current_price=190,
    vix=15, rsi_14=55, sma_20=188, sma_50=185, sma_200=175,
    bb_upper=195, bb_middle=188, bb_lower=181, macd=1.5, macd_signal=1.0,
    macd_histogram=0.5, atr_14=3.2, days_to_earnings=100)
r = ea.analyze(ind)
print(f"AAPL: score={r.composite_score}, rec={r.recommendation.value}, support=${r.support_level}, resistance=${r.resistance_level}")
for s in r.signals:
    print(f"  {s.name:30s} {s.signal.value:12s} ({s.score:+3d})")

# Test 2: TSLA bearish
ind2 = MarketIndicators(symbol="TSLA", timestamp=datetime.now(timezone.utc), current_price=165,
    vix=35, rsi_14=72, sma_20=170, sma_50=178, sma_200=180,
    bb_upper=180, bb_middle=170, bb_lower=160, macd=-2, macd_signal=-1,
    macd_histogram=-1, atr_14=5.5, days_to_earnings=95)
r2 = ea.analyze(ind2)
print(f"\nTSLA: score={r2.composite_score}, rec={r2.recommendation.value}")

# Test 3: Naked put eligibility
np_strat = NakedPutStrategy()
port = PortfolioSummary(
    account=AccountInfo(portfolio_value=100000, cash=50000, buying_power=50000),
    stock_positions=[],
)
eligible = np_strat.evaluate_eligibility(MarketRegime.LOW_VOL_NEUTRAL, port, ind)
print(f"\nNaked put eligible (50k cash, no shares): {eligible}")

port2 = PortfolioSummary(
    account=AccountInfo(portfolio_value=10000, cash=5000, buying_power=5000),
    stock_positions=[],
)
eligible2 = np_strat.evaluate_eligibility(MarketRegime.LOW_VOL_NEUTRAL, port2, ind)
print(f"Naked put eligible (5k cash, no shares): {eligible2}")

# Test 4: Naked call eligibility
nc_strat = NakedCallStrategy()
port_margin = PortfolioSummary(
    account=AccountInfo(portfolio_value=100000, cash=50000, buying_power=50000),
    stock_positions=[],
)
nc_eligible = nc_strat.evaluate_eligibility(MarketRegime.HIGH_VOL_BEARISH, port_margin, ind2)
print(f"\nNaked call eligible (TSLA, 50k margin, VIX=35, RSI=72): {nc_eligible}")

nc_low_bp = PortfolioSummary(
    account=AccountInfo(portfolio_value=5000, cash=1000, buying_power=1000),
    stock_positions=[],
)
nc_ineligible = nc_strat.evaluate_eligibility(MarketRegime.HIGH_VOL_BEARISH, nc_low_bp, ind2)
print(f"Naked call eligible (1k margin): {nc_ineligible}")

# Test 5: Naked call entry scoring
print("\n--- Naked Call Entry Scoring ---")
# TSLA: high VIX, overbought RSI = excellent for selling calls
entry_tsla = NakedCallStrategy.score_entry(ind2)
print(f"TSLA call-sell score: {entry_tsla['score']}/100")
print(f"  Suggested strike: ${entry_tsla['suggested_strike']:.0f}")
print(f"  Resistance: ${entry_tsla['resistance']:.2f}")
for name, sc, desc in entry_tsla["signals"]:
    print(f"  {name:25s} ({sc:+3d}) {desc}")

# AAPL: low VIX, neutral RSI = poor for selling calls
entry_aapl = NakedCallStrategy.score_entry(ind)
print(f"\nAAPL call-sell score: {entry_aapl['score']}/100")
print(f"  Suggested strike: ${entry_aapl['suggested_strike']:.0f}")
for name, sc, desc in entry_aapl["signals"]:
    print(f"  {name:25s} ({sc:+3d}) {desc}")

print("\n✅ All smoke tests passed")

