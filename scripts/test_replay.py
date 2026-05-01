"""Quick test of the replay pipeline."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np, pandas as pd, yfinance as yf
from src.models.market_data import MarketIndicators
from src.market_analyzer import MarketAnalyzer
from src.opening_range import OpeningRangeAnalyzer
from src.recent_momentum import RecentMomentumAnalyzer
from src.utils.quality_scorer import compute_quality_score
from src.strategies.buy_call import BuyCallStrategy
from src.strategies.buy_put import BuyPutStrategy
from datetime import date

symbol, as_of = "SPY", "10:30"
hh, mm = 10, 30

df_5m = yf.download(symbol, period="5d", interval="5m", progress=False)
df_daily = yf.download(symbol, period="6mo", interval="1d", progress=False)
vix_df = yf.download("^VIX", period="5d", interval="1d", progress=False)
for d in (df_5m, df_daily, vix_df):
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)

df_5m.index = pd.to_datetime(df_5m.index)
if df_5m.index.tz is None:
    df_5m.index = df_5m.index.tz_localize("UTC")
df_5m.index = df_5m.index.tz_convert("US/Eastern")

today = date.today()
tb = df_5m[df_5m.index.date == today]
if tb.empty:
    ld = df_5m.index[-1].date()
    tb = df_5m[df_5m.index.date == ld]
    print(f"Using last trading day: {ld}")

cutoff = tb.index[0].replace(hour=hh, minute=mm, second=0)
bars = tb[tb.index <= cutoff]
print(f"Bars up to {as_of}: {len(bars)}")

close = bars["Close"].astype(float)
dc = df_daily["Close"].astype(float)
ext = pd.concat([dc, close], ignore_index=True)
price = float(close.iloc[-1])
vix = float(vix_df["Close"].iloc[-1])

ind = MarketIndicators(
    symbol=symbol, current_price=price, timeframe="daily", vix=vix,
    rsi_14=50.0, sma_20=float(ext.iloc[-20:].mean()), sma_50=float(ext.iloc[-50:].mean()),
    sma_200=float(ext.iloc[-200:].mean()), bb_upper=price + 5, bb_middle=price, bb_lower=price - 5,
    macd=0.0, macd_signal=0.0, macd_histogram=0.0, atr_14=2.0,
    volume=int(bars["Volume"].iloc[-1]), volume_sma_20=float(bars["Volume"].mean()),
)
print(f"Price at {as_of}: ${ind.current_price:.2f}")

# Test analyzers in mock mode (replay)
ora = OpeningRangeAnalyzer()
orng = ora.analyze(ind, mock=True)
print(f"OR direction: {orng.breakout_direction.value}, momentum: {orng.momentum_score}")

rma = RecentMomentumAnalyzer()
recent = rma.analyze(ind, mock=True)
print(f"Recent: {recent.direction}, momentum: {recent.momentum_score}")

q = compute_quality_score(
    direction="buy_call", current_price=ind.current_price, sma_20=ind.sma_20, sma_50=ind.sma_50,
    vix=ind.vix, volume=ind.volume, volume_sma_20=ind.volume_sma_20,
    or_direction=orng.breakout_direction.value, or_momentum=orng.momentum_score,
    or_confirmed=orng.breakout_confirmed, recent_dir=recent.direction, recent_momentum=recent.momentum_score,
)
print(f"Buy Call quality: {q.score}/11 {q.label}")
print("Full replay pipeline works!")

# Test Momentum Cascade Detector
from src.momentum_cascade import MomentumCascadeDetector
mcd = MomentumCascadeDetector()
cascade = mcd.analyze(ind, quality_score=q.score, or_momentum=orng.momentum_score, recent_momentum=recent.momentum_score)
print(f"\n--- Cascade Detector ---")
print(f"Explosion score: {cascade.explosion_score}/10  {cascade.urgency}")
print(f"Acceleration: {cascade.acceleration_detected}, Vol climax: {cascade.volume_climax}, Cascade: {cascade.cascade_breakdown}")
if cascade.recommended_strike_offset > 0:
    print(f"💡 Suggest {cascade.recommended_strike_offset} strike(s) OTM for leverage")
for sig in cascade.signals:
    prefix = "✅" if sig["score"] > 0 else "⚪"
    print(f"  {prefix} {sig['name']} (+{sig['score']}) — {sig['desc']}")

