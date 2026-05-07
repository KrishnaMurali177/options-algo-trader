#!/usr/bin/env python
"""Scan a day's 5-min bars for sweet spot triggers — matching live agent logic exactly.

Usage:
    python scripts/scan_sweet_spot_today.py                     # SPY today
    python scripts/scan_sweet_spot_today.py --date 2026-04-28   # SPY on specific date
    python scripts/scan_sweet_spot_today.py -s QQQ --date 2026-04-25
    python scripts/scan_sweet_spot_today.py --no-regime-guard   # disable regime guard
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date, datetime, timezone, timedelta
from src.utils.quality_scorer import compute_quality_score
from src.utils.choppiness import compute_choppiness
from src.opening_range import OpeningRangeAnalyzer
from src.recent_momentum import RecentMomentumAnalyzer
from src.momentum_cascade import MomentumCascadeDetector
from src.models.market_data import MarketIndicators

parser = argparse.ArgumentParser(description="Scan sweet spot triggers for a given day (live agent logic)")
parser.add_argument("--symbol", "-s", default="SPY")
parser.add_argument("--date", "-d", default=None, help="Date to scan (YYYY-MM-DD). Default: today")
parser.add_argument("--max-chop", type=int, default=5,
                    help="Max chop score to allow triggers (default: 5, 0-10 scale)")
parser.add_argument("--no-regime-guard", action="store_true",
                    help="Disable regime guard (default: ON)")
parser.add_argument("--scan-start-min", type=int, default=60,
                    help="Minutes after open to start scanning (default: 60 = 10:30)")
args = parser.parse_args()

symbol = args.symbol
target_date = date.fromisoformat(args.date) if args.date else date.today()
regime_guard = not args.no_regime_guard

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

# Compute RSI on 5-min bars up to each evaluation point (like live agent via MarketAnalyzer)
def compute_rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])

print(f"Symbol: {symbol}")
print(f"Date: {target_date}")
print(f"SMA20=${sma_20:.2f}  SMA50=${sma_50:.2f}  VIX={vix:.1f}")
print(f"Bars: {len(today_bars)}")
print(f"Regime guard: {'ON' if regime_guard else 'OFF'}")
print(f"Scan start: {args.scan_start_min} min after open")
print(f"Max chop: {args.max_chop}")
print()

ora = OpeningRangeAnalyzer()
rma = RecentMomentumAnalyzer()
cascade_det = MomentumCascadeDetector()

sweet_spots = []
last_trigger_idx = -999  # for 15-min cooldown (3 bars = 15 min)

# Determine the bar index corresponding to scan_start_min after open
# Market open is at 9:30 ET, each bar is 5 min, so scan_start_min / 5 bars after first bar
scan_start_bar = args.scan_start_min // 5

# ── Precompute indicator series for the day (matching replay_sweet_spot.py) ──
day_close = today_bars["Close"].astype(float)
day_high = today_bars["High"].astype(float)
day_low = today_bars["Low"].astype(float)
day_vol = today_bars["Volume"].astype(float)

# RSI
day_delta = day_close.diff()
day_gain = day_delta.where(day_delta > 0, 0.0).rolling(14).mean()
day_loss = (-day_delta.where(day_delta < 0, 0.0)).rolling(14).mean()
day_rs = day_gain / day_loss.replace(0, np.nan)
day_rsi = 100 - (100 / (1 + day_rs))

# MACD
day_ema12 = day_close.ewm(span=12, adjust=False).mean()
day_ema26 = day_close.ewm(span=26, adjust=False).mean()
day_macd_line = day_ema12 - day_ema26
day_macd_signal = day_macd_line.ewm(span=9, adjust=False).mean()
day_macd_hist = day_macd_line - day_macd_signal

# ATR
day_tr = pd.concat([day_high - day_low, (day_high - day_close.shift()).abs(),
                    (day_low - day_close.shift()).abs()], axis=1).max(axis=1)
day_atr = day_tr.rolling(14).mean()

# Bollinger Bands
day_bb_mid = day_close.rolling(20).mean()
day_bb_std = day_close.rolling(20).std()

# ZLEMA
lag_fast = (8 - 1) // 2
lag_slow = (21 - 1) // 2
comp_fast = 2 * day_close - day_close.shift(lag_fast)
comp_slow = 2 * day_close - day_close.shift(lag_slow)
day_zlema_fast = comp_fast.ewm(span=8, adjust=False).mean()
day_zlema_slow = comp_slow.ewm(span=21, adjust=False).mean()

# Volume SMA
day_vol_sma = day_vol.rolling(min(20, len(day_vol))).mean()

print(f"{'Time':<6} {'Price':>7} {'Direction':<10} {'Q':>3} {'E':>3} {'C':>3} {'OR_Mom':>6} {'RSI':>5} {'Trigger':<20}")
print("-" * 80)

for i in range(12, len(today_bars)):
    # Only scan every 5 min (every bar) — live agent scans every 5 min
    # But start only after scan_start_min
    if i < scan_start_bar:
        continue

    bars_up_to = today_bars.iloc[:i+1]
    n = i + 1
    close = bars_up_to['Close'].astype(float)
    price = float(close.iloc[-1])
    vol = int(bars_up_to['Volume'].iloc[-1])
    vol_avg = float(bars_up_to['Volume'].mean())
    t = bars_up_to.index[-1].strftime('%H:%M')

    # ── Build full indicators from precomputed series (matching replay) ──
    rsi = float(day_rsi.iloc[:n].iloc[-1]) if n >= 15 and not np.isnan(day_rsi.iloc[:n].iloc[-1]) else 50.0

    # MACD
    if n >= 26:
        macd_val = float(day_macd_line.iloc[:n].iloc[-1])
        macd_sig = float(day_macd_signal.iloc[:n].iloc[-1])
        macd_hist_val = float(day_macd_hist.iloc[:n].iloc[-1])
    else:
        macd_val = macd_sig = macd_hist_val = 0.0

    # ATR
    atr_val = float(day_atr.iloc[:n].iloc[-1]) if n >= 15 and not np.isnan(day_atr.iloc[:n].iloc[-1]) else 1.0

    # Bollinger
    bb_mid_val = float(day_bb_mid.iloc[:n].iloc[-1]) if n >= 20 and not np.isnan(day_bb_mid.iloc[:n].iloc[-1]) else price
    bb_std_val = float(day_bb_std.iloc[:n].iloc[-1]) if n >= 20 and not np.isnan(day_bb_std.iloc[:n].iloc[-1]) else 0.0
    bb_upper = bb_mid_val + 2 * bb_std_val
    bb_lower = bb_mid_val - 2 * bb_std_val

    # ZLEMA
    if n >= 21:
        zf = float(day_zlema_fast.iloc[:n].iloc[-1])
        zs = float(day_zlema_slow.iloc[:n].iloc[-1])
        if zf > zs * 1.0002:
            zlema_trend = "bullish"
        elif zf < zs * 0.9998:
            zlema_trend = "bearish"
        else:
            zlema_trend = "neutral"
    else:
        zf = zs = price
        zlema_trend = "neutral"

    # Volume SMA
    vol_sma_val = float(day_vol_sma.iloc[:n].iloc[-1]) if not np.isnan(day_vol_sma.iloc[:n].iloc[-1]) else float(vol)

    ind = MarketIndicators(
        symbol=symbol, timestamp=datetime.now(timezone.utc),
        current_price=price, timeframe='15min',
        vix=vix, rsi_14=rsi, rsi_5min=rsi,
        sma_20=sma_20, sma_50=sma_50, sma_200=sma_50,
        bb_upper=bb_upper, bb_middle=bb_mid_val, bb_lower=bb_lower,
        macd=macd_val, macd_signal=macd_sig, macd_histogram=macd_hist_val,
        atr_14=atr_val,
        volume=vol, volume_sma_20=vol_sma_val,
        zlema_fast=zf, zlema_slow=zs, zlema_trend=zlema_trend,
    )

    or_result = ora.analyze(ind, mock=True, bars_5m=bars_up_to)
    recent = rma.analyze(ind, mock=True, bars_5m=bars_up_to)

    or_dir = or_result.breakout_direction.value if or_result else "neutral"
    or_mom = or_result.momentum_score if or_result else 0
    rec_dir = recent.direction if recent else "neutral"
    rec_mom = recent.momentum_score if recent else 0

    # ── Direction from OR momentum (live agent logic) ──
    if or_mom >= 25:
        direction = "buy_call"
    elif or_mom <= -25:
        direction = "buy_put"
    else:
        # No direction — skip
        print(f"{t:<6} ${price:.2f} {'—':<10} {'—':>3} {'—':>3} {'—':>3} {or_mom:>+6} {rsi:>5.1f}")
        continue

    # ── Regime guard (live agent logic) ──
    if regime_guard:
        bullish_regime = sma_20 > sma_50
        bearish_regime = sma_20 < sma_50
        if direction == "buy_put" and bullish_regime and rsi <= 70:
            print(f"{t:<6} ${price:.2f} {direction:<10} {'—':>3} {'—':>3} {'—':>3} {or_mom:>+6} {rsi:>5.1f}  regime_blocked")
            continue
        if direction == "buy_call" and bearish_regime and rsi >= 30:
            print(f"{t:<6} ${price:.2f} {direction:<10} {'—':>3} {'—':>3} {'—':>3} {or_mom:>+6} {rsi:>5.1f}  regime_blocked")
            continue

    # ── Quality score (now passes zlema_trend, matching replay/live agent) ──
    quality_result = compute_quality_score(
        direction=direction,
        current_price=price,
        sma_20=sma_20,
        sma_50=sma_50,
        vix=vix,
        volume=1.0,
        volume_sma_20=1.0,
        or_direction=or_dir,
        or_momentum=or_mom,
        or_confirmed=abs(or_mom) >= 40,
        recent_dir=rec_dir,
        recent_momentum=rec_mom,
        zlema_trend=zlema_trend,
    )
    quality = quality_result.score

    # ── Cascade / Explosion ──
    cascade = cascade_det.analyze(ind, quality_score=quality, or_momentum=or_mom,
                                   recent_momentum=rec_mom, bars_5m=bars_up_to)
    expl = cascade.explosion_score

    # ── Choppiness ──
    chop = compute_choppiness(bars_up_to)
    chop_sc = chop.chop_score

    # ── Sweet Spot criteria (live agent: 3 <= Q <= 7, E >= 2, chop <= max) ──
    is_sweet = 3 <= quality <= 7 and expl >= 2 and chop_sc <= args.max_chop

    # ── Entry confirmation: price in upper/lower 25% of OR range ──
    if is_sweet and or_result:
        range_high = or_result.range_high
        range_low = or_result.range_low
        range_width = range_high - range_low
        if range_width > 0:
            breakout_threshold = range_width * 0.25
            if direction == "buy_call" and price < (range_high - breakout_threshold):
                is_sweet = False
                marker = "🚫 (not in upper 25%)"
            elif direction == "buy_put" and price > (range_low + breakout_threshold):
                is_sweet = False
                marker = "🚫 (not in lower 25%)"
            else:
                marker = ""
        else:
            marker = ""
    else:
        marker = ""

    if is_sweet:
        # 15-min cooldown (3 bars)
        if i - last_trigger_idx < 3:
            marker = '⏳ (cooldown)'
        else:
            marker = '🎯'
            last_trigger_idx = i

            # Compute entry/stop/target like agent
            range_high = or_result.range_high if or_result else price
            range_low = or_result.range_low if or_result else price
            range_width = range_high - range_low

            if expl >= 8:
                target_mult = 1.5
            elif expl >= 6:
                target_mult = 1.5  # was 1.25
            else:
                target_mult = 1.0

            if direction == "buy_call":
                entry = range_high + range_width * 0.10
                mid = (range_high + range_low) / 2
                stop = mid + 0.10 * (range_high - range_low)  # Tighter: 60% of range
                risk = entry - stop
                target = entry + risk * target_mult
            else:
                entry = range_low - range_width * 0.10
                mid = (range_high + range_low) / 2
                stop = mid - 0.10 * (range_high - range_low)  # Tighter: 60% of range
                risk = stop - entry
                target = entry - risk * target_mult

            sweet_spots.append((t, price, direction, quality, expl, chop_sc,
                                round(entry, 2), round(stop, 2), round(target, 2), target_mult))
    elif not marker and not (3 <= quality <= 7 and expl >= 4):
        marker = ""

    print(f"{t:<6} ${price:.2f} {direction:<10} {quality:>3} {expl:>3} {chop_sc:>3} {or_mom:>+6} {rsi:>5.1f}  {marker}")

print()
print("=" * 80)

# Print choppiness summary for the full day
full_chop = compute_choppiness(today_bars)
print(f"DAY CHOPPINESS: {full_chop.summary}")
print()

print("SWEET SPOT TRIGGERS (live agent logic):")
print("=" * 80)
if sweet_spots:
    for t, p, d, q, e, c, entry, stop, target, tm in sweet_spots:
        dir_label = "CALL" if "call" in d else "PUT"
        print(f"  {t} ET  ${p:.2f}  {dir_label}  Q={q}/13  E={e}/10  C={c}/10  "
              f"Entry=${entry}  Stop=${stop}  Target=${target} ({tm:.2f}R)")
else:
    print("  No sweet spots triggered today.")

print()
print(f"Summary: {len(sweet_spots)} trigger(s)")
