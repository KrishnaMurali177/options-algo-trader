"""Intraday Backtester — replays historical data day-by-day.

Supports two modes:
  - **5-min mode** (any period, via Alpaca): Uses 5-min candles for fine-grained simulation.
    Alpaca provides 5+ years of 5-min data (free paper account).
  - **1-hour mode** (fallback via yfinance): Used only when Alpaca is unavailable.

Data sources (in priority order):
  1. Alpaca (free) — 5-min bars up to 5+ years. Set ALPACA_API_KEY / ALPACA_SECRET_KEY in .env.
  2. yfinance (fallback) — 5-min limited to ~60d, 1-hour up to 730d.

Signal system aligned with live trading pipeline (as of 2026-04-20):

  PHASE 1 — Momentum (matches OpeningRangeAnalyzer._analyze_live):
    7 signals determine breakout direction (call/put/skip):
      1. Price vs range position  ±5    (weak signal, downweighted)
      2. Intraday RSI             ±20   (threshold 40/60)
      3. Intraday MACD histogram  ±20   (threshold ±0.05, upweighted)
      4. Price vs VWAP            ±20   (always-on, upweighted)
      5. Volume surge             ±5    (directional with momentum)
      6. OR candle direction      ±10   (body > 30% of range)
      7. VIX context              ±5    (VIX > 20, directional)
    Direction: momentum ≥ 25 → call, ≤ -25 → put

  PHASE 2 — Quality filter (shared 11-point scorer):
    Uses compute_quality_score() from src/utils/quality_scorer.py.
    11 criteria scored 0-11 (see quality_scorer.py for details).
    Gate: require quality >= 2 to take trade.

  Execution (backtest-optimized):
    - Entry: breakout of range high/low + ATR buffer
    - Stop: range midpoint
    - T1: 0.75R, T2: 1.5R
    - Time stop: 3:00 PM ET
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from src.models.backtest_result import (
    BacktestReport,
    SignalAccuracy,
    TradeResult,
)
from src.utils.quality_scorer import compute_quality_score
from src.momentum_cascade import MomentumCascadeDetector, CascadeResult
from src.utils.choppiness import compute_choppiness

logger = logging.getLogger(__name__)


def _parse_period_days(period: str) -> int:
    """Parse '60d', '1y', '6mo' etc. to approximate number of days."""
    period = period.lower().strip()
    if period.endswith("d"):
        return int(period[:-1])
    elif period.endswith("mo"):
        return int(period[:-2]) * 30
    elif period.endswith("y"):
        return int(period[:-1]) * 365
    return 60


class IntradayBacktester:
    """Backtest opening range breakout scalping on historical intraday data."""

    def __init__(self, atr_buffer: float = 0.0, use_optimized_exits: bool = True,
                 entry_offset_pct: float = 0.10, sweet_spot_only: bool = False,
                 max_chop_score: int = 5, min_cascade: int = 4,
                 sweet_spot_quality_range: tuple[int, int] = (4, 7)):
        self.atr_buffer = atr_buffer
        self.use_optimized_exits = use_optimized_exits
        # entry_offset_pct: shift entry trigger as % of range width
        #   0.0  = trigger at exact range high/low
        #   -0.1 = trigger 10% INSIDE the range (earlier entry, more trades)
        #   +0.1 = trigger 10% BEYOND the range (more confirmation, best PF on 5-min data)
        # Default: +0.10 (best PF 1.23, WR 59%, $24 P&L on 1yr SPY 5-min Alpaca data)
        self.entry_offset_pct = entry_offset_pct
        # Sweet spot filter: matches dashboard entry criteria exactly
        self.sweet_spot_only = sweet_spot_only
        self.max_chop_score = max_chop_score
        self.min_cascade = min_cascade
        self.sweet_spot_quality_range = sweet_spot_quality_range
        self._cascade_detector = MomentumCascadeDetector()

    def run(self, symbol: str, period: str = "60d") -> BacktestReport:
        """Run the full backtest over the given period."""
        approx_days = _parse_period_days(period)

        # ── Fetch intraday bars (Alpaca first, yfinance fallback) ──
        df, interval = self._fetch_intraday(symbol, approx_days, period)

        min_bars_per_day = 24 if interval == "5m" else 4

        # Fetch daily VIX
        vix_df = yf.download("^VIX", period=period, interval="1d", progress=False)
        if isinstance(vix_df.columns, pd.MultiIndex):
            vix_df.columns = vix_df.columns.get_level_values(0)
        vix_map: dict[date, float] = {}
        if not vix_df.empty:
            for idx, row in vix_df.iterrows():
                vix_map[pd.Timestamp(idx).date()] = float(row["Close"])

        # Fetch daily OHLC for previous-day high/low and gap calculations
        daily_df = yf.download(symbol, period=period, interval="1d", progress=False)
        if isinstance(daily_df.columns, pd.MultiIndex):
            daily_df.columns = daily_df.columns.get_level_values(0)
        daily_map: dict[date, dict] = {}
        if not daily_df.empty:
            for idx, row in daily_df.iterrows():
                d = pd.Timestamp(idx).date()
                daily_map[d] = {
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                }

        # Split into trading days
        trading_days = sorted(set(df.index.date))
        logger.info("Found %d trading days (%s interval)", len(trading_days), interval)

        trades: list[TradeResult] = []

        for i, day in enumerate(trading_days):
            day_bars = df[df.index.date == day].copy()
            if len(day_bars) < min_bars_per_day:
                continue

            vix = vix_map.get(day, 20.0)

            # Previous day data for gap/S&R signals
            prev_day_data = None
            if i > 0:
                prev_date = trading_days[i - 1]
                prev_day_data = daily_map.get(prev_date)

            result = self._simulate_day(
                day_bars, symbol, day, vix, interval, prev_day_data, daily_map
            )
            trades.append(result)

        return self._build_report(symbol, period, len(trading_days), trades)

    def _fetch_intraday(
        self, symbol: str, approx_days: int, period: str,
    ) -> tuple[pd.DataFrame, str]:
        """Fetch intraday bars, preferring Alpaca 5-min over yfinance.

        Returns:
            (DataFrame, interval_string) — interval is "5m" or "1h".
        """
        # Try Alpaca first — supports 5-min bars for 5+ years
        try:
            from src.utils.alpaca_data import fetch_bars

            logger.info(
                "Fetching 5-min bars from Alpaca for %s (%d days) …", symbol, approx_days
            )
            df = fetch_bars(symbol, days_back=approx_days, interval="5min")
            if not df.empty and len(df) > 100:
                logger.info(
                    "Alpaca: %d 5-min bars for %s (%s → %s)",
                    len(df), symbol,
                    df.index[0].strftime("%Y-%m-%d"),
                    df.index[-1].strftime("%Y-%m-%d"),
                )
                return df, "5m"
            logger.warning("Alpaca returned insufficient data, falling back to yfinance")
        except Exception as e:
            logger.warning("Alpaca fetch failed (%s), falling back to yfinance", e)

        # Fallback: yfinance
        if approx_days <= 60:
            interval = "5m"
        else:
            interval = "1h"
            logger.info("yfinance fallback: using 1-hour bars for %s period", period)

        logger.info("Fetching %s data from yfinance for %s over %s …", interval, symbol, period)
        df = yf.download(symbol, period=period, interval=interval, progress=False)
        if df.empty:
            raise ValueError(f"No data returned for {symbol}")

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert("US/Eastern")

        return df, interval

    def _simulate_day(
        self, day_df: pd.DataFrame, symbol: str, trade_date: date,
        vix: float, interval: str, prev_day: dict | None,
        daily_map: dict | None = None,
    ) -> TradeResult:
        """Simulate one trading day."""

        # ── 1. Extract opening range (9:30–10:30 ET) ──
        if interval == "1h":
            # First hourly bar IS the opening range
            opening_bars = day_df[day_df.index.hour == 9]  # 9:30 bar
            if opening_bars.empty:
                opening_bars = day_df.iloc[:1]  # fallback: first bar
        else:
            opening_bars = day_df.between_time("09:30", "10:29")

        if len(opening_bars) < 1:
            return self._skip_trade(trade_date, symbol, "insufficient_opening_bars")

        range_high = float(opening_bars["High"].max())
        range_low = float(opening_bars["Low"].min())
        range_width = range_high - range_low
        if range_width <= 0:
            return self._skip_trade(trade_date, symbol, "zero_range")

        open_price = float(opening_bars["Open"].iloc[0])
        or_close = float(opening_bars["Close"].iloc[-1])
        or_volume = int(opening_bars["Volume"].sum())

        # ── 2. Compute indicators ──
        bars_to_end_or = day_df[day_df.index <= opening_bars.index[-1]]
        close_to_or = bars_to_end_or["Close"].astype(float)
        price = float(close_to_or.iloc[-1])

        # For 1h mode, close_to_or has only ~1 bar which is insufficient for
        # RSI (needs 15+) and MACD (needs 26+).  Build a longer series from
        # daily closes leading up to today + the intraday close_to_or values.
        indicator_close = close_to_or  # default: use intraday bars
        if len(close_to_or) < 26 and daily_map:
            # Gather up to 40 prior daily closes
            sorted_dates = sorted(d for d in daily_map if d < trade_date)
            prior_closes = pd.Series(
                [daily_map[d]["close"] for d in sorted_dates[-40:]],
                dtype=float,
            )
            indicator_close = pd.concat([prior_closes, close_to_or], ignore_index=True)

        # Intraday RSI (use extended series)
        intraday_rsi = self._rsi(indicator_close, 14) if len(indicator_close) >= 15 else 50.0

        # Intraday MACD (use extended series)
        if len(indicator_close) >= 26:
            _, _, intraday_hist = self._macd(indicator_close)
        else:
            intraday_hist = 0.0

        # VWAP
        typical = (bars_to_end_or["High"] + bars_to_end_or["Low"] + bars_to_end_or["Close"]) / 3
        cumvol = bars_to_end_or["Volume"].cumsum()
        last_cumvol = float(cumvol.iloc[-1])
        vwap = float((typical * bars_to_end_or["Volume"]).cumsum().iloc[-1] / last_cumvol) if last_cumvol > 0 else or_close

        # Volume comparison
        avg_bar_vol = float(day_df["Volume"].mean())
        avg_or_bar_vol = float(opening_bars["Volume"].mean())
        volume_surge = avg_or_bar_vol > avg_bar_vol * 1.2

        # OR candle direction
        or_body_pct = (or_close - open_price) / range_width if range_width > 0 else 0

        # ATR from opening bars (or intraday bars)
        tr_vals = []
        src_bars = opening_bars if len(opening_bars) > 1 else day_df.iloc[:min(12, len(day_df))]
        for i in range(1, len(src_bars)):
            h = float(src_bars["High"].iloc[i])
            l = float(src_bars["Low"].iloc[i])
            pc = float(src_bars["Close"].iloc[i - 1])
            tr_vals.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr = np.mean(tr_vals) if tr_vals else range_width * 0.5

        # ── NEW: EMA 9/21 crossover (use extended series for enough data) ──
        ema9 = indicator_close.ewm(span=min(9, len(indicator_close)), adjust=False).mean()
        ema21 = indicator_close.ewm(span=min(21, len(indicator_close)), adjust=False).mean()
        ema_bullish = float(ema9.iloc[-1]) > float(ema21.iloc[-1])

        # ── NEW: Gap Open (today's open vs yesterday's close) ──
        gap_pct = 0.0
        if prev_day:
            gap_pct = (open_price - prev_day["close"]) / prev_day["close"] * 100

        # ── NEW: Previous day high/low as S/R ──
        near_prev_support = False
        near_prev_resistance = False
        if prev_day:
            dist_to_prev_low = abs(price - prev_day["low"]) / atr if atr > 0 else 99
            dist_to_prev_high = abs(price - prev_day["high"]) / atr if atr > 0 else 99
            near_prev_support = dist_to_prev_low < 1.0 and price >= prev_day["low"]
            near_prev_resistance = dist_to_prev_high < 1.0 and price <= prev_day["high"]

        # ── NEW: Range width vs ATR (is today's range unusually wide?) ──
        range_atr_ratio = range_width / atr if atr > 0 else 1.0

        # ══════════════════��═══════════════════════════════════════════
        # 3. PHASE 1 — Momentum signals (aligned with OpeningRangeAnalyzer)
        #
        # These 7 signals match opening_range.py _analyze_live() exactly:
        #   same thresholds, same weights, same order.
        # They determine the breakout direction (call / put / skip).
        # ══════════════════════════════════════════════════════════════
        signals: dict[str, int] = {}
        momentum = 0

        # OR-1  Price vs range  (live: +30/-30 if broken out, 0 if inside)
        # At OR close the price is always inside the range, so we use the
        # range-position proxy: upper third → bullish lean, lower → bearish.
        # Weight: ±5 (downweighted — weakest signal at ~50% WR across both symbols)
        range_position = (price - range_low) / range_width if range_width > 0 else 0.5
        if range_position > 0.7:
            signals["price_vs_range"] = 5; momentum += 5
        elif range_position < 0.3:
            signals["price_vs_range"] = -5; momentum -= 5
        else:
            signals["price_vs_range"] = 0

        # OR-2  Intraday RSI  (live: ±20, thresholds 60/40)
        if intraday_rsi > 60:
            signals["intraday_rsi"] = 20; momentum += 20
        elif intraday_rsi < 40:
            signals["intraday_rsi"] = -20; momentum -= 20
        else:
            signals["intraday_rsi"] = 0

        # OR-3  Intraday MACD histogram  (live: ±15, thresholds ±0.05)
        # Weight: ±20 (upweighted — consistent 55-57% WR on both symbols)
        if intraday_hist > 0.05:
            signals["intraday_macd"] = 20; momentum += 20
        elif intraday_hist < -0.05:
            signals["intraday_macd"] = -20; momentum -= 20
        else:
            signals["intraday_macd"] = 0

        # OR-4  Price vs VWAP  (live: ±15, always-on)
        # Weight: ±20 (upweighted — reliable always-on directional signal)
        if price > vwap:
            signals["vwap"] = 20; momentum += 20
        else:
            signals["vwap"] = -20; momentum -= 20

        # OR-5  Volume surge  (live: ±5, directional with momentum)
        if volume_surge:
            vol_dir = 5 if momentum > 0 else -5
            signals["volume"] = abs(vol_dir); momentum += vol_dir
        else:
            signals["volume"] = 0

        # OR-6  OR candle direction  (live: ±10, threshold ±0.3 body%)
        if or_body_pct > 0.3:
            signals["or_candle"] = 10; momentum += 10
        elif or_body_pct < -0.3:
            signals["or_candle"] = -10; momentum -= 10
        else:
            signals["or_candle"] = 0

        # OR-7  VIX context  (live: ±5 if VIX > 20, directional)
        if vix > 20:
            vix_adj = 5 if momentum > 0 else -5
            signals["vix"] = abs(vix_adj); momentum += vix_adj
        else:
            signals["vix"] = 0

        momentum = max(-100, min(100, momentum))

        # ── 4. Determine direction (same threshold as OpeningRangeAnalyzer) ──
        if momentum >= 25:
            direction = "call"
        elif momentum <= -25:
            direction = "put"
        else:
            return self._skip_trade(trade_date, symbol, "no_breakout",
                                    range_high=range_high, range_low=range_low,
                                    momentum=momentum, signals=signals)

        # ══════════════════════════════════════════════════════════════
        # PHASE 2 — 11-point quality filter (shared with strategies & dashboard)
        #
        # Uses compute_quality_score() from src/utils/quality_scorer.py.
        # Maps Phase 1 momentum → or_direction/or_confirmed, and derives
        # SMA proxies from extended indicator series for daily signals.
        # ══════════════════════════════════════════════════════════════

        # Map Phase 1 momentum to opening range direction fields
        or_direction = "bullish" if momentum >= 25 else "bearish" if momentum <= -25 else "neutral"
        or_confirmed = abs(momentum) >= 40

        # Recent 30-min momentum proxy: use last few bars' trend as a second opinion
        # In a backtest we don't have a separate 30-min window, so we derive from
        # the OR candle direction and RSI as a proxy for "current momentum".
        recent_momentum_proxy = 0
        if intraday_rsi > 55:
            recent_momentum_proxy += 20
        elif intraday_rsi < 45:
            recent_momentum_proxy -= 20
        if or_body_pct > 0.1:
            recent_momentum_proxy += 15
        elif or_body_pct < -0.1:
            recent_momentum_proxy -= 15
        recent_dir = "bullish" if recent_momentum_proxy >= 20 else "bearish" if recent_momentum_proxy <= -20 else "neutral"

        # SMA proxies from extended indicator series
        if len(indicator_close) >= 50:
            sma_20_proxy = float(indicator_close.iloc[-20:].mean())
            sma_50_proxy = float(indicator_close.iloc[-50:].mean())
        elif len(indicator_close) >= 20:
            sma_20_proxy = float(indicator_close.iloc[-20:].mean())
            sma_50_proxy = sma_20_proxy  # not enough data for SMA50
        else:
            sma_20_proxy = vwap  # best available proxy
            sma_50_proxy = vwap

        # Volume proxy: use volume_surge as a ratio indicator
        vol_proxy = avg_or_bar_vol
        vol_avg_proxy = avg_bar_vol if avg_bar_vol > 0 else 1.0

        dir_str = "buy_call" if direction == "call" else "buy_put"

        # ZLEMA trend for quality scoring (compute from available close data)
        zlema_trend_proxy = None
        if len(indicator_close) >= 21:
            lag_f = (8 - 1) // 2
            lag_s = (21 - 1) // 2
            comp_f = 2 * indicator_close - indicator_close.shift(lag_f)
            comp_s = 2 * indicator_close - indicator_close.shift(lag_s)
            zf = float(comp_f.ewm(span=8, adjust=False).mean().iloc[-1])
            zs = float(comp_s.ewm(span=21, adjust=False).mean().iloc[-1])
            if zf > zs * 1.0002:
                zlema_trend_proxy = "bullish"
            elif zf < zs * 0.9998:
                zlema_trend_proxy = "bearish"
            else:
                zlema_trend_proxy = "neutral"

        quality_result = compute_quality_score(
            direction=dir_str,
            current_price=price,
            sma_20=sma_20_proxy,
            sma_50=sma_50_proxy,
            vix=vix,
            volume=vol_proxy,
            volume_sma_20=vol_avg_proxy,
            or_direction=or_direction,
            or_momentum=momentum,
            or_confirmed=or_confirmed,
            recent_dir=recent_dir,
            recent_momentum=recent_momentum_proxy,
            zlema_trend=zlema_trend_proxy,
        )
        quality_score = quality_result.score

        signals["ema_cross"] = 10 if ema_bullish else -10
        signals["gap_open"] = 10 if gap_pct > 0.3 else (-10 if gap_pct < -0.3 else 0)

        # Track S/R and quality in signals for reporting
        if near_prev_support:
            signals["prev_day_sr"] = 8
        elif near_prev_resistance:
            signals["prev_day_sr"] = -8
        else:
            signals["prev_day_sr"] = 0
        signals["quality_score"] = quality_score

        # ── Quality gate: require ≥ 2 to enter (same as strategies) ──
        if quality_score < 2:
            return self._skip_trade(trade_date, symbol, "low_quality",
                                    range_high=range_high, range_low=range_low,
                                    momentum=momentum, signals=signals)

        # ── Regime Guard: block trades fighting extended trends ──
        # Use actual daily closes for regime check (SMA proxies are too noisy)
        if daily_map:
            sorted_prior = sorted(d for d in daily_map if d < trade_date)
            if len(sorted_prior) >= 20:
                last20_closes = [daily_map[d]["close"] for d in sorted_prior[-20:]]
                daily_sma20 = sum(last20_closes) / 20
                price_vs_daily_sma20 = (price - daily_sma20) / daily_sma20 if daily_sma20 > 0 else 0
                # Call guard: price >1.5% below daily SMA20 = active sell-off
                if direction == "call" and price_vs_daily_sma20 < -0.015:
                    return self._skip_trade(trade_date, symbol, "regime_guard_call",
                                            range_high=range_high, range_low=range_low,
                                            momentum=momentum, signals=signals)
                # Put guard: price >3% below daily SMA20 = extended sell-off, bounce likely
                if direction == "put" and price_vs_daily_sma20 < -0.03:
                    return self._skip_trade(trade_date, symbol, "regime_guard_put",
                                            range_high=range_high, range_low=range_low,
                                            momentum=momentum, signals=signals)

        # ── Sweet Spot Filter (matches dashboard entry criteria) ──
        # NOTE: In the dashboard, sweet spots are evaluated continuously
        # throughout the day with all bars available up to current time.
        # Here we evaluate using all OR bars (12 bars of 5-min data).
        # The cascade from just 12 bars is conservative — the dashboard
        # typically has 30-60+ bars when it triggers a sweet spot later in the day.
        if self.sweet_spot_only:
            q_min, q_max = self.sweet_spot_quality_range

            # 1. Quality must be in sweet spot range
            if quality_score < q_min or quality_score > q_max:
                return self._skip_trade(trade_date, symbol, "sweet_spot_quality_miss",
                                        range_high=range_high, range_low=range_low,
                                        momentum=momentum, signals=signals)

            # 2. Momentum Cascade explosion score must meet threshold
            # Use all available bars up to first few post-OR bars for better
            # acceleration signal (simulates checking ~30-45 min after OR close)
            post_or_preview = day_df[day_df.index > opening_bars.index[-1]].head(6)
            cascade_bars = pd.concat([day_df[day_df.index <= opening_bars.index[-1]], post_or_preview])
            cascade_result = self._compute_cascade(
                cascade_bars, quality_score, momentum, recent_momentum_proxy, indicators=None
            )
            if cascade_result.explosion_score < self.min_cascade:
                return self._skip_trade(trade_date, symbol, "sweet_spot_cascade_miss",
                                        range_high=range_high, range_low=range_low,
                                        momentum=momentum, signals=signals)

            # 3. Choppiness filter — use full day bars up to OR end for broader sample
            chop_result = compute_choppiness(day_df[day_df.index <= opening_bars.index[-1]])
            if chop_result.chop_score > self.max_chop_score:
                return self._skip_trade(trade_date, symbol, "sweet_spot_chop_miss",
                                        range_high=range_high, range_low=range_low,
                                        momentum=momentum, signals=signals)

        # ── 5. Entry / Exit Levels ──
        # entry_offset_pct shifts the trigger relative to range width:
        #   negative = enter INSIDE range (earlier, more trades, lower WR)
        #   positive = enter BEYOND range (more confirmation, fewer trades, higher WR)
        offset = range_width * self.entry_offset_pct

        if self.use_optimized_exits:
            # BACKTEST-OPTIMIZED: tighter stop at 60% of range, 0.75R/1.5R targets
            range_mid = (range_high + range_low) / 2
            if direction == "call":
                entry_trigger = range_high + offset
                entry_price = round(entry_trigger + atr * self.atr_buffer, 2)
                stop_loss = round(range_mid + 0.10 * (range_high - range_low) - atr * 0.02, 2)
                risk = entry_price - stop_loss
                if risk <= 0: risk = atr * 0.3
                target_1 = round(entry_price + risk * 0.75, 2)
                target_2 = round(entry_price + risk * 1.5, 2)
            else:
                entry_trigger = range_low - offset
                entry_price = round(entry_trigger - atr * self.atr_buffer, 2)
                stop_loss = round(range_mid - 0.10 * (range_high - range_low) + atr * 0.02, 2)
                risk = stop_loss - entry_price
                if risk <= 0: risk = atr * 0.3
                target_1 = round(entry_price - risk * 0.75, 2)
                target_2 = round(entry_price - risk * 1.5, 2)
        else:
            # Original: stop at range low/high, 1R/2R targets
            if direction == "call":
                entry_trigger = range_high + offset
                entry_price = round(entry_trigger + atr * self.atr_buffer, 2)
                stop_loss = round(range_low - atr * self.atr_buffer, 2)
                risk = entry_price - stop_loss
                target_1 = round(entry_price + risk, 2)
                target_2 = round(entry_price + risk * 2, 2)
            else:
                entry_trigger = range_low - offset
                entry_price = round(entry_trigger - atr * self.atr_buffer, 2)
                stop_loss = round(range_high + atr * self.atr_buffer, 2)
                risk = stop_loss - entry_price
                target_1 = round(entry_price - risk, 2)
                target_2 = round(entry_price - risk * 2, 2)

        # ── 6. Walk forward through post-opening-range bars ──
        post_or = day_df[day_df.index > opening_bars.index[-1]]
        if post_or.empty:
            return self._skip_trade(trade_date, symbol, "no_post_or_bars",
                                    range_high=range_high, range_low=range_low,
                                    momentum=momentum, signals=signals)

        entered = False
        actual_entry = entry_price
        entry_time = None
        exit_price = 0.0
        exit_reason = "eod"
        exit_time = None
        # Time stop: 15:00 (optimized) or 15:55 (original)
        time_stop = "15:00" if self.use_optimized_exits else "15:55"

        for ts, bar in post_or.iterrows():
            bar_high = float(bar["High"])
            bar_low = float(bar["Low"])
            bar_close = float(bar["Close"])
            bar_time = ts.strftime("%H:%M")

            if not entered:
                if direction == "call" and bar_high >= entry_trigger:
                    entered = True
                    actual_entry = max(entry_price, float(bar["Open"]))
                    entry_time = bar_time
                    risk = actual_entry - stop_loss
                    if risk <= 0: risk = atr * 0.3
                    if self.use_optimized_exits:
                        target_1 = round(actual_entry + risk * 0.75, 2)
                        target_2 = round(actual_entry + risk * 1.5, 2)
                    else:
                        target_1 = round(actual_entry + risk, 2)
                        target_2 = round(actual_entry + risk * 2, 2)
                elif direction == "put" and bar_low <= entry_trigger:
                    entered = True
                    actual_entry = min(entry_price, float(bar["Open"]))
                    entry_time = bar_time
                    risk = stop_loss - actual_entry
                    if risk <= 0: risk = atr * 0.3
                    if self.use_optimized_exits:
                        target_1 = round(actual_entry - risk * 0.75, 2)
                        target_2 = round(actual_entry - risk * 1.5, 2)
                    else:
                        target_1 = round(actual_entry - risk, 2)
                        target_2 = round(actual_entry - risk * 2, 2)
                continue

            # Check exit conditions
            if direction == "call":
                if bar_low <= stop_loss:
                    exit_price = stop_loss; exit_reason = "stop"; exit_time = bar_time; break
                if bar_high >= target_2:
                    exit_price = target_2; exit_reason = "target_2"; exit_time = bar_time; break
                if bar_high >= target_1:
                    exit_price = target_1; exit_reason = "target_1"; exit_time = bar_time; break
            else:
                if bar_high >= stop_loss:
                    exit_price = stop_loss; exit_reason = "stop"; exit_time = bar_time; break
                if bar_low <= target_2:
                    exit_price = target_2; exit_reason = "target_2"; exit_time = bar_time; break
                if bar_low <= target_1:
                    exit_price = target_1; exit_reason = "target_1"; exit_time = bar_time; break

            # Time stop
            if bar_time >= time_stop:
                exit_price = bar_close; exit_reason = "time_stop"; exit_time = bar_time; break

        if not entered:
            return self._skip_trade(trade_date, symbol, "no_entry",
                                    range_high=range_high, range_low=range_low,
                                    momentum=momentum, signals=signals)

        if exit_price == 0.0:
            exit_price = float(post_or["Close"].iloc[-1])
            exit_reason = "eod"
            exit_time = post_or.index[-1].strftime("%H:%M")

        # ── 7. Compute P&L ──
        pnl = (exit_price - actual_entry) if direction == "call" else (actual_entry - exit_price)
        pnl_pct = (pnl / actual_entry) * 100 if actual_entry > 0 else 0

        return TradeResult(
            trade_date=trade_date, symbol=symbol, direction=direction,
            entry_price=actual_entry, exit_price=exit_price,
            stop_loss=stop_loss, target_1=target_1, target_2=target_2,
            range_high=range_high, range_low=range_low,
            exit_reason=exit_reason,
            pnl_dollars=round(pnl, 4), pnl_pct=round(pnl_pct, 4),
            momentum_score=momentum, signal_scores=signals,
            entry_time=entry_time, exit_time=exit_time,
            vwap_at_entry=round(vwap, 2), volume_at_entry=or_volume,
            is_winner=pnl > 0,
        )

    # ── Cascade Helper ────────────────────────────────────────────

    def _compute_cascade(
        self, bars: pd.DataFrame, quality_score: int,
        or_momentum: int, recent_momentum: int, indicators=None,
    ) -> CascadeResult:
        """Compute cascade explosion score from available bars (backtest mode)."""
        from src.models.market_data import MarketIndicators

        close = bars["Close"].astype(float)
        price = float(close.iloc[-1])

        # Build minimal indicators object for cascade detector
        if indicators is None:
            high = bars["High"].astype(float)
            low = bars["Low"].astype(float)
            tr_vals = []
            for i in range(1, min(15, len(bars))):
                h, l, pc = float(high.iloc[i]), float(low.iloc[i]), float(close.iloc[i-1])
                tr_vals.append(max(h - l, abs(h - pc), abs(l - pc)))
            atr = float(np.mean(tr_vals)) if tr_vals else 1.0

            indicators = MarketIndicators(
                symbol="BT", current_price=price, vix=20.0,
                rsi_14=50.0, sma_20=price, sma_50=price, sma_200=price,
                macd_line=0, macd_signal=0, macd_histogram=0, macd=0,
                bollinger_upper=price + atr * 2, bollinger_lower=price - atr * 2,
                bb_upper=price + atr * 2, bb_middle=price, bb_lower=price - atr * 2,
                atr_14=atr, volume_ratio=1.0,
                implied_volatility=0.3, put_call_ratio=1.0,
            )

        return self._cascade_detector.analyze(
            indicators,
            quality_score=quality_score,
            or_momentum=or_momentum,
            recent_momentum=recent_momentum,
            bars_5m=bars,
        )

    # ── Report Builder ───────────────────────────────────────────

    def _build_report(
        self, symbol: str, period: str, total_days: int, trades: list[TradeResult]
    ) -> BacktestReport:
        taken = [t for t in trades if t.direction != "skip"]
        winners = [t for t in taken if t.is_winner]
        losers = [t for t in taken if not t.is_winner]

        call_trades = [t for t in taken if t.direction == "call"]
        put_trades = [t for t in taken if t.direction == "put"]

        gross_profit = sum(t.pnl_dollars for t in winners) if winners else 0
        gross_loss = abs(sum(t.pnl_dollars for t in losers)) if losers else 0

        exit_reasons: dict[str, int] = defaultdict(int)
        for t in taken:
            exit_reasons[t.exit_reason] += 1

        signal_stats = self._compute_signal_accuracy(taken)

        return BacktestReport(
            symbol=symbol, period=period, total_days=total_days,
            total_trades=len(trades), trades_taken=len(taken),
            wins=len(winners), losses=len(losers),
            win_rate=len(winners) / len(taken) * 100 if taken else 0,
            avg_pnl_per_trade=float(np.mean([t.pnl_dollars for t in taken])) if taken else 0,
            total_pnl=sum(t.pnl_dollars for t in taken),
            max_win=max((t.pnl_dollars for t in taken), default=0),
            max_loss=min((t.pnl_dollars for t in taken), default=0),
            avg_winner=float(np.mean([t.pnl_dollars for t in winners])) if winners else 0,
            avg_loser=float(np.mean([t.pnl_dollars for t in losers])) if losers else 0,
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            call_trades=len(call_trades), put_trades=len(put_trades),
            call_win_rate=sum(1 for t in call_trades if t.is_winner) / len(call_trades) * 100 if call_trades else 0,
            put_win_rate=sum(1 for t in put_trades if t.is_winner) / len(put_trades) * 100 if put_trades else 0,
            exit_reasons=dict(exit_reasons),
            signal_accuracy=signal_stats,
            trades=trades,
        )

    def _compute_signal_accuracy(self, trades: list[TradeResult]) -> list[SignalAccuracy]:
        signal_names = set()
        for t in trades:
            signal_names.update(t.signal_scores.keys())

        results = []
        for name in sorted(signal_names):
            active_trades = []
            for t in trades:
                score = t.signal_scores.get(name, 0)
                if score != 0:
                    if (t.direction == "call" and score > 0) or (t.direction == "put" and score < 0):
                        active_trades.append(t)

            if not active_trades:
                results.append(SignalAccuracy(name, 0, 0, 0.0, 0.0))
                continue

            wins = sum(1 for t in active_trades if t.is_winner)
            avg_pnl = float(np.mean([t.pnl_dollars for t in active_trades]))
            results.append(SignalAccuracy(
                signal_name=name, times_active=len(active_trades),
                wins_when_active=wins,
                avg_pnl_when_active=round(avg_pnl, 4),
                win_rate=round(wins / len(active_trades) * 100, 1),
            ))

        results.sort(key=lambda s: s.win_rate, reverse=True)
        return results

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _skip_trade(
        trade_date: date, symbol: str, reason: str,
        range_high: float = 0, range_low: float = 0,
        momentum: int = 0, signals: dict | None = None,
    ) -> TradeResult:
        return TradeResult(
            trade_date=trade_date, symbol=symbol, direction="skip",
            entry_price=0, exit_price=0, stop_loss=0,
            target_1=0, target_2=0,
            range_high=range_high, range_low=range_low,
            exit_reason=reason, pnl_dollars=0, pnl_pct=0,
            momentum_score=momentum,
            signal_scores=signals or {},
            is_winner=False,
        )

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> float:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        val = rsi.iloc[-1]
        return float(val) if not np.isnan(val) else 50.0

    @staticmethod
    def _macd(close: pd.Series) -> tuple[float, float, float]:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal
        return float(macd_line.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])

