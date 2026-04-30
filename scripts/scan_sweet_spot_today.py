#!/usr/bin/env python
"""Scan a day's 5-min bars for sweet spot triggers (no guardrails).

Usage:
    python scripts/scan_sweet_spot_today.py                     # SPY today
    python scripts/scan_sweet_spot_today.py --date 2026-04-28   # SPY on specific date
    python scripts/scan_sweet_spot_today.py -s QQQ --date 2026-04-25
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date, datetime, timezone, timedelta
from src.utils.quality_scorer import compute_quality_score
from src.opening_range import OpeningRangeAnalyzer
from src.recent_momentum import RecentMomentumAnalyzer
from src.momentum_cascade import MomentumCascadeDetector
from src.models.market_data import MarketIndicators

parser = argparse.ArgumentParser(description="Scan sweet spot triggers for a given day")
parser.add_argument("--symbol", "-s", default="SPY")
parser.add_argument("--date", "-d", default=None, help="Date to scan (YYYY-MM-DD). Default: today")
args = parser.parse_args()

symbol = args.symbol
target_date = date.fromisoformat(args.date) if args.date else date.today()

# Fetch 5-min data (need to cover the target date)
days_ago = (date.today() - target_date).days
if days_ago > 58:
    print(f"ERROR: 5-min data only available for last ~60 days. {target_date} is {days_ago} days ago.")
    sys.exit(1)

period = f"{max(days_ago + 3, 5)}d"
df = yf.download(symbol, period=period, interval='5m', progress=False)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
df.index = pd.to_datetime(df.index)
if df.index.tz is None:
    df.index = df.index.tz_localize('UTC')
df.index = df.index.tz_convert('US/Eastern')

today_bars = df[df.index.date == target_date]
if today_bars.empty:
    available = sorted(set(df.index.date))
    print(f"ERROR: No data for {target_date}. Available dates: {available[-5:]}")
    sys.exit(1)

df_daily = yf.download(symbol, period='6mo', interval='1d', progress=False)
if isinstance(df_daily.columns, pd.MultiIndex):
    df_daily.columns = df_daily.columns.get_level_values(0)
daily_close = df_daily['Close'].astype(float)
# Use only daily closes up to target_date for accurate SMAs
daily_close = daily_close[daily_close.index <= pd.Timestamp(target_date)]
sma_20 = float(daily_close.iloc[-20:].mean()) if len(daily_close) >= 20 else float(daily_close.mean())
sma_50 = float(daily_close.iloc[-50:].mean()) if len(daily_close) >= 50 else sma_20

vix_df = yf.download('^VIX', period='6mo', interval='1d', progress=False)
if isinstance(vix_df.columns, pd.MultiIndex):
    vix_df.columns = vix_df.columns.get_level_values(0)
# Get VIX for target date or nearest prior
vix_series = vix_df['Close'].astype(float)
vix_on_date = vix_series[vix_series.index <= pd.Timestamp(target_date)]
vix = float(vix_on_date.iloc[-1]) if not vix_on_date.empty else 20.0

print(f"Symbol: {symbol}")
print(f"Date: {target_date}")
print(f"SMA20=${sma_20:.2f}  SMA50=${sma_50:.2f}  VIX={vix:.1f}")
print(f"Bars: {len(today_bars)}")
print()

ora = OpeningRangeAnalyzer()
rma = RecentMomentumAnalyzer()
cascade_det = MomentumCascadeDetector()

sweet_spots = []

print(f"{'Time':<6} {'Price':>7} {'Direction':<10} {'Qcall':>5} {'Qput':>5} {'Best':>4} {'Sweet':>8} {'Expl':>4}")
print("-" * 70)

for i in range(12, len(today_bars), 3):
    bars_up_to = today_bars.iloc[:i+1]
    close = bars_up_to['Close'].astype(float)
    price = float(close.iloc[-1])
    vol = int(bars_up_to['Volume'].iloc[-1])
    vol_avg = float(bars_up_to['Volume'].mean())
    t = bars_up_to.index[-1].strftime('%H:%M')

    ind = MarketIndicators(
        symbol=symbol, timestamp=datetime.now(timezone.utc),
        current_price=price, timeframe='15min',
        vix=vix, rsi_14=50.0, sma_20=sma_20, sma_50=sma_50, sma_200=sma_50,
        bb_upper=price+2, bb_middle=price, bb_lower=price-2,
        macd=0, macd_signal=0, macd_histogram=0, atr_14=1.0,
        volume=vol, volume_sma_20=vol_avg,
    )

    or_result = ora.analyze(ind, mock=True, bars_5m=bars_up_to)
    recent = rma.analyze(ind, mock=True, bars_5m=bars_up_to)

    or_dir = or_result.breakout_direction.value
    or_mom = or_result.momentum_score
    or_conf = abs(or_mom) >= 40
    rec_dir = recent.direction
    rec_mom = recent.momentum_score

    zlema_trend = None
    if len(close) >= 21:
        lag_f = (8-1)//2
        lag_s = (21-1)//2
        comp_f = 2*close - close.shift(lag_f)
        comp_s = 2*close - close.shift(lag_s)
        zf = float(comp_f.ewm(span=8, adjust=False).mean().iloc[-1])
        zs = float(comp_s.ewm(span=21, adjust=False).mean().iloc[-1])
        if zf > zs * 1.0002: zlema_trend = 'bullish'
        elif zf < zs * 0.9998: zlema_trend = 'bearish'
        else: zlema_trend = 'neutral'

    q_call = compute_quality_score(
        direction='buy_call', current_price=price, sma_20=sma_20, sma_50=sma_50,
        vix=vix, volume=vol, volume_sma_20=vol_avg,
        or_direction=or_dir, or_momentum=or_mom, or_confirmed=or_conf,
        recent_dir=rec_dir, recent_momentum=rec_mom, zlema_trend=zlema_trend,
    )
    q_put = compute_quality_score(
        direction='buy_put', current_price=price, sma_20=sma_20, sma_50=sma_50,
        vix=vix, volume=vol, volume_sma_20=vol_avg,
        or_direction=or_dir, or_momentum=or_mom, or_confirmed=or_conf,
        recent_dir=rec_dir, recent_momentum=rec_mom, zlema_trend=zlema_trend,
    )

    best_q = max(q_call.score, q_put.score)
    best_dir = 'BUY PUT' if q_put.score > q_call.score else 'BUY CALL'

    cascade = cascade_det.analyze(ind, quality_score=best_q, or_momentum=or_mom,
                                   recent_momentum=rec_mom, bars_5m=bars_up_to)
    expl = cascade.explosion_score

    is_sweet = 4 <= best_q <= 7 and expl >= 4
    marker = '🎯' if is_sweet else ''
    if is_sweet:
        sweet_spots.append((t, price, best_dir, best_q, expl))

    print(f"{t:<6} ${price:.2f} {best_dir:<10} {q_call.score:>5} {q_put.score:>5} {best_q:>4}   {marker:<6} E={expl}")

print()
print("=" * 70)
print("SWEET SPOT TRIGGERS (without guardrails):")
print("=" * 70)
if sweet_spots:
    for t, p, d, q, e in sweet_spots:
        print(f"  {t} ET  ${p:.2f}  {d}  Quality={q}/13  Explosion={e}/10")
else:
    print("  No sweet spots triggered today.")

