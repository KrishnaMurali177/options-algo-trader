"""Opening Range Analyzer — analyzes the first 60 minutes of market open.

Computes the opening range (high/low of first 60 min, 9:30–10:30 ET) and
generates scalping signals: breakout direction, momentum, and entry/exit levels.

Live mode:  Fetches actual 5-min intraday candles from Yahoo Finance.
Mock mode:  Synthesizes the opening range from daily indicators.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from src.models.market_data import MarketIndicators

logger = logging.getLogger(__name__)

# US Eastern timezone
_ET = timezone(timedelta(hours=-4))  # EDT

_MARKET_OPEN = time(9, 30)
_OPENING_RANGE_END = time(10, 30)  # 60-minute opening range


class BreakoutDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class OpeningRangeAnalysis:
    """Result of the 60-minute opening range analysis."""
    range_high: float
    range_low: float
    range_width: float
    range_width_pct: float
    current_price: float
    breakout_direction: BreakoutDirection
    breakout_confirmed: bool
    momentum_score: int
    volume_surge: bool
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    risk_per_share: float
    signals: list[dict] = field(default_factory=list)
    summary: str = ""
    data_source: str = "synthesized"  # "live_intraday" or "synthesized"
    opening_range_bars: int = 0


class OpeningRangeAnalyzer:
    """
    Analyzes the first 60 minutes of market open to identify
    intraday scalping opportunities for Buy Call / Buy Put.

    Strategy logic:
      - Compute the high/low of the first 60 min (9:30–10:30 ET)
      - If price breaks above range_high → bullish breakout → Buy Call
      - If price breaks below range_low → bearish breakout → Buy Put
      - Confirm with intraday RSI, MACD, VWAP, volume
      - Set tight stop-loss at opposite end of range
    """

    def analyze(
        self,
        indicators: MarketIndicators,
        mock: bool = False,
        bars_5m: "pd.DataFrame | None" = None,
    ) -> OpeningRangeAnalysis:
        """Analyze opening range breakout.

        Args:
            indicators: Market indicators snapshot.
            mock: If True, synthesize from indicators (no data fetch).
            bars_5m: Pre-fetched 5-min bars (already time-sliced for replay).
                     If provided, uses these directly instead of fetching live data.
        """
        if bars_5m is not None and not bars_5m.empty:
            return self._analyze_from_bars(indicators, bars_5m)
        if mock:
            return self._analyze_synthesized(indicators)
        try:
            return self._analyze_live(indicators)
        except Exception as exc:
            logger.warning(
                "Live intraday fetch failed for %s (%s) — falling back to synthesized",
                indicators.symbol, exc,
            )
            return self._analyze_synthesized(indicators)

    # ── Analysis from Pre-fetched Bars (for replay) ────────────────

    def _analyze_from_bars(self, indicators: MarketIndicators, bars: "pd.DataFrame") -> OpeningRangeAnalysis:
        """Analyze using pre-fetched, already time-sliced 5-min bars (replay mode).

        Same logic as _analyze_live but skips the yfinance download — uses the
        bars that the caller has already truncated to the replay time.
        """
        if len(bars) < 2:
            return self._analyze_synthesized(indicators)

        # Ensure timezone-aware index
        if bars.index.tz is None:
            bars.index = bars.index.tz_localize("UTC")
            bars.index = bars.index.tz_convert("US/Eastern")

        # Opening range: bars in 9:30-10:29 window
        opening_bars = bars.between_time("09:30", "10:29")
        if len(opening_bars) < 2:
            return self._analyze_synthesized(indicators)

        range_high = float(opening_bars["High"].max())
        range_low = float(opening_bars["Low"].min())
        open_price = float(opening_bars["Open"].iloc[0])
        or_close = float(opening_bars["Close"].iloc[-1])
        or_volume = int(opening_bars["Volume"].sum())

        # Volume surge: compare to average bar volume
        avg_bar_vol = float(bars["Volume"].mean())
        avg_or_bar_vol = float(opening_bars["Volume"].mean())
        volume_surge = avg_or_bar_vol > avg_bar_vol * 1.2

        price = float(bars["Close"].iloc[-1])

        # Intraday RSI from provided bars
        close_5m = bars["Close"].astype(float)
        intraday_rsi = self._rsi(close_5m, 14) if len(close_5m) >= 15 else indicators.rsi_14

        # Intraday MACD from provided bars
        if len(close_5m) >= 26:
            _, _, intraday_hist = self._macd(close_5m)
        else:
            intraday_hist = 0.0

        # VWAP from provided bars
        typical_price = (bars["High"] + bars["Low"] + bars["Close"]) / 3
        cumvol = bars["Volume"].cumsum()
        last_cv = float(cumvol.iloc[-1])
        vwap = float((typical_price * bars["Volume"]).cumsum().iloc[-1] / last_cv) if last_cv > 0 else price

        range_width = round(range_high - range_low, 2)
        range_width_pct = round((range_width / price) * 100, 3) if price > 0 else 0

        # ── Same momentum signal logic as _analyze_live ──
        signals = []
        momentum = 0

        # 1. Price vs opening range
        if price > range_high:
            dist = price - range_high
            momentum += 30
            signals.append({"name": "Price above 60-min range high", "score": 30,
                            "desc": f"${price:.2f} > ${range_high:.2f} (+${dist:.2f}) — bullish breakout ✅"})
        elif price < range_low:
            dist = range_low - price
            momentum -= 30
            signals.append({"name": "Price below 60-min range low", "score": -30,
                            "desc": f"${price:.2f} < ${range_low:.2f} (-${dist:.2f}) — bearish breakout ✅"})
        else:
            pct_in = (price - range_low) / range_width * 100 if range_width > 0 else 50
            if pct_in > 70:
                momentum += 5
                signals.append({"name": "Price in upper range", "score": 5,
                                "desc": f"${price:.2f} upper third ({pct_in:.0f}%) — mild bullish lean"})
            elif pct_in < 30:
                momentum -= 5
                signals.append({"name": "Price in lower range", "score": -5,
                                "desc": f"${price:.2f} lower third ({pct_in:.0f}%) — mild bearish lean"})
            else:
                signals.append({"name": "Price within range", "score": 0,
                                "desc": f"${price:.2f} mid-range ({pct_in:.0f}%) — no breakout"})

        # 2. Intraday RSI
        if intraday_rsi > 60:
            momentum += 20
            signals.append({"name": "Intraday RSI bullish", "score": 20,
                            "desc": f"5-min RSI={intraday_rsi:.1f} — bullish"})
        elif intraday_rsi < 40:
            momentum -= 20
            signals.append({"name": "Intraday RSI bearish", "score": -20,
                            "desc": f"5-min RSI={intraday_rsi:.1f} — bearish"})
        else:
            signals.append({"name": "Intraday RSI neutral", "score": 0,
                            "desc": f"5-min RSI={intraday_rsi:.1f} — neutral"})

        # 3. Intraday MACD
        if intraday_hist > 0.05:
            momentum += 20
            signals.append({"name": "Intraday MACD bullish", "score": 20,
                            "desc": f"MACD hist={intraday_hist:.3f} — positive"})
        elif intraday_hist < -0.05:
            momentum -= 20
            signals.append({"name": "Intraday MACD bearish", "score": -20,
                            "desc": f"MACD hist={intraday_hist:.3f} — negative"})
        else:
            signals.append({"name": "Intraday MACD flat", "score": 0,
                            "desc": f"MACD hist={intraday_hist:.3f} — flat"})

        # 4. Price vs VWAP
        if price > vwap:
            momentum += 20
            signals.append({"name": "Above VWAP", "score": 20,
                            "desc": f"${price:.2f} > VWAP ${vwap:.2f}"})
        else:
            momentum -= 20
            signals.append({"name": "Below VWAP", "score": -20,
                            "desc": f"${price:.2f} < VWAP ${vwap:.2f}"})

        # 5. Volume
        if volume_surge:
            vol_dir = 10 if momentum > 0 else -10
            momentum += vol_dir // 2
            signals.append({"name": "Volume surge", "score": abs(vol_dir),
                            "desc": f"OR volume above average — confirms move"})

        # 6. OR candle direction
        or_body_pct = (or_close - open_price) / range_width if range_width > 0 else 0
        if or_body_pct > 0.3:
            momentum += 10
            signals.append({"name": "Bullish OR candle", "score": 10,
                            "desc": f"Closed near highs ({or_body_pct:.0%})"})
        elif or_body_pct < -0.3:
            momentum -= 10
            signals.append({"name": "Bearish OR candle", "score": -10,
                            "desc": f"Closed near lows ({or_body_pct:.0%})"})

        # 7. VIX
        vix = indicators.vix
        if vix > 20:
            momentum += 5 if momentum > 0 else -5

        return self._build_result(
            range_high=range_high, range_low=range_low,
            range_width=range_width, range_width_pct=range_width_pct,
            price=price, atr=indicators.atr_14,
            momentum=momentum, signals=signals,
            volume_surge=volume_surge,
            data_source="replay_bars", n_bars=len(bars),
        )

    # ── Live Intraday Analysis ────────────────────────────────────

    def _analyze_live(self, indicators: MarketIndicators) -> OpeningRangeAnalysis:
        """Fetch real 5-min candles and compute the actual 60-min opening range."""
        import yfinance as yf

        symbol = indicators.symbol
        logger.info("Fetching live 5-min intraday data for %s …", symbol)

        df = yf.download(symbol, period="5d", interval="5m", progress=False)
        if df.empty:
            raise ValueError(f"No intraday data returned for {symbol}")

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert("US/Eastern")

        today = date.today()
        today_bars = df[df.index.date == today].copy()

        if today_bars.empty:
            last_trading_day = df.index[-1].date()
            today_bars = df[df.index.date == last_trading_day].copy()
            if today_bars.empty:
                raise ValueError(f"No bars found for {symbol} on {today} or recent trading day")
            logger.info("Using data from %s (last trading day)", last_trading_day)

        # First 60 minutes: 9:30–10:30 ET
        opening_bars = today_bars.between_time("09:30", "10:29")

        if len(opening_bars) < 2:
            raise ValueError(
                f"Only {len(opening_bars)} bars in 9:30–10:30 window for {symbol}. "
                f"Market may not have opened yet."
            )

        range_high = float(opening_bars["High"].max())
        range_low = float(opening_bars["Low"].min())
        open_price = float(opening_bars["Open"].iloc[0])
        or_close = float(opening_bars["Close"].iloc[-1])
        or_volume = int(opening_bars["Volume"].sum())

        # Volume comparison
        prev_days = df[df.index.date < today_bars.index[0].date()]
        if not prev_days.empty:
            daily_volumes = prev_days.groupby(prev_days.index.date)["Volume"].sum()
            avg_daily_vol = float(daily_volumes.mean()) if len(daily_volumes) > 0 else 0
            expected_or_vol = avg_daily_vol * 0.35
            volume_surge = or_volume > expected_or_vol * 1.2
        else:
            volume_surge = False

        price = float(today_bars["Close"].iloc[-1])

        # Intraday RSI from 5-min bars
        close_5m = today_bars["Close"].astype(float)
        intraday_rsi = self._rsi(close_5m, 14) if len(close_5m) >= 15 else indicators.rsi_14

        # Intraday MACD from 5-min bars
        if len(close_5m) >= 26:
            _, _, intraday_hist = self._macd(close_5m)
        else:
            intraday_hist = indicators.macd_histogram

        # VWAP
        typical_price = (today_bars["High"] + today_bars["Low"] + today_bars["Close"]) / 3
        cumvol = today_bars["Volume"].cumsum()
        vwap = float((typical_price * today_bars["Volume"]).cumsum().iloc[-1] / cumvol.iloc[-1]) if float(cumvol.iloc[-1]) > 0 else price

        range_width = round(range_high - range_low, 2)
        range_width_pct = round((range_width / price) * 100, 3) if price > 0 else 0

        # ── Momentum signals from real intraday data ──
        signals = []
        momentum = 0

        # 1. Price vs actual opening range
        # Weight: ±5 when inside range (weakest signal ~50% WR on backtest)
        # Weight: ±30 only on confirmed breakout (price above/below range)
        if price > range_high:
            dist = price - range_high
            momentum += 30
            signals.append({"name": "Price above 60-min range high", "score": 30,
                            "desc": f"${price:.2f} > ${range_high:.2f} (+${dist:.2f}) — bullish breakout ✅"})
        elif price < range_low:
            dist = range_low - price
            momentum -= 30
            signals.append({"name": "Price below 60-min range low", "score": -30,
                            "desc": f"${price:.2f} < ${range_low:.2f} (-${dist:.2f}) — bearish breakout ✅"})
        else:
            pct_in = (price - range_low) / range_width * 100 if range_width > 0 else 50
            # Lean based on position within range (downweighted to ±5)
            if pct_in > 70:
                momentum += 5
                signals.append({"name": "Price in upper range", "score": 5,
                                "desc": f"${price:.2f} in upper third of range ({pct_in:.0f}%) — mild bullish lean"})
            elif pct_in < 30:
                momentum -= 5
                signals.append({"name": "Price in lower range", "score": -5,
                                "desc": f"${price:.2f} in lower third of range ({pct_in:.0f}%) — mild bearish lean"})
            else:
                signals.append({"name": "Price within 60-min range", "score": 0,
                                "desc": f"${price:.2f} inside ${range_low:.2f}–${range_high:.2f} ({pct_in:.0f}% from low) — no breakout yet"})

        # 2. Intraday RSI (5-min bars)
        if intraday_rsi > 60:
            momentum += 20
            signals.append({"name": "Intraday RSI bullish", "score": 20,
                            "desc": f"5-min RSI={intraday_rsi:.1f} — bullish intraday momentum"})
        elif intraday_rsi < 40:
            momentum -= 20
            signals.append({"name": "Intraday RSI bearish", "score": -20,
                            "desc": f"5-min RSI={intraday_rsi:.1f} — bearish intraday momentum"})
        else:
            signals.append({"name": "Intraday RSI neutral", "score": 0,
                            "desc": f"5-min RSI={intraday_rsi:.1f} — no strong intraday bias"})

        # 3. Intraday MACD histogram (5-min bars)
        # Weight: ±20 (upweighted from ±15 — consistent 55-57% WR on backtest)
        if intraday_hist > 0.05:
            momentum += 20
            signals.append({"name": "Intraday MACD bullish", "score": 20,
                            "desc": f"5-min MACD histogram={intraday_hist:.3f} — positive momentum"})
        elif intraday_hist < -0.05:
            momentum -= 20
            signals.append({"name": "Intraday MACD bearish", "score": -20,
                            "desc": f"5-min MACD histogram={intraday_hist:.3f} — negative momentum"})
        else:
            signals.append({"name": "Intraday MACD flat", "score": 0,
                            "desc": f"5-min MACD histogram={intraday_hist:.3f} — no momentum edge"})

        # 4. Price vs real VWAP
        # Weight: ±20 (upweighted from ±15 — reliable always-on directional signal)
        if price > vwap:
            momentum += 20
            signals.append({"name": "Above VWAP", "score": 20,
                            "desc": f"${price:.2f} > VWAP ${vwap:.2f} — buyers in control"})
        else:
            momentum -= 20
            signals.append({"name": "Below VWAP", "score": -20,
                            "desc": f"${price:.2f} < VWAP ${vwap:.2f} — sellers in control"})

        # 5. Volume confirmation
        if volume_surge:
            vol_dir = 10 if momentum > 0 else -10
            momentum += vol_dir // 2
            signals.append({"name": "60-min volume surge", "score": abs(vol_dir),
                            "desc": f"60-min volume {or_volume:,} above average — high participation confirms move"})
        else:
            signals.append({"name": "Normal opening volume", "score": 0,
                            "desc": f"60-min volume {or_volume:,} — normal activity"})

        # 6. Opening range candle direction
        or_body_pct = (or_close - open_price) / range_width if range_width > 0 else 0
        if or_body_pct > 0.3:
            momentum += 10
            signals.append({"name": "Strong bullish OR candle", "score": 10,
                            "desc": f"60-min range closed near highs ({or_body_pct:.0%} of range) — bullish bias"})
        elif or_body_pct < -0.3:
            momentum -= 10
            signals.append({"name": "Strong bearish OR candle", "score": -10,
                            "desc": f"60-min range closed near lows ({or_body_pct:.0%} of range) — bearish bias"})
        else:
            signals.append({"name": "Indecisive OR candle", "score": 0,
                            "desc": f"60-min range closed mid-range ({or_body_pct:.0%}) — no strong bias"})

        # 7. VIX context
        vix = indicators.vix
        if vix > 20:
            momentum += 5 if momentum > 0 else -5
            signals.append({"name": "VIX elevated", "score": 10,
                            "desc": f"VIX={vix:.1f} — wider ranges, good for scalping"})
        else:
            signals.append({"name": "VIX low", "score": 0,
                            "desc": f"VIX={vix:.1f} — narrow ranges, tighter targets"})

        return self._build_result(
            range_high=range_high, range_low=range_low,
            range_width=range_width, range_width_pct=range_width_pct,
            price=price, atr=indicators.atr_14,
            momentum=momentum, signals=signals,
            volume_surge=volume_surge,
            data_source="live_intraday", n_bars=len(opening_bars),
        )

    # ── Synthesized Analysis (mock / fallback) ────────────────────

    def _analyze_synthesized(self, indicators: MarketIndicators) -> OpeningRangeAnalysis:
        """Synthesize opening range from daily indicators when live data is unavailable."""
        price = indicators.current_price
        atr = indicators.atr_14

        half_range = atr * 0.22  # 60-min range ≈ 44% of daily ATR

        if indicators.macd_histogram > 0:
            range_mid = price - half_range * 0.2
        else:
            range_mid = price + half_range * 0.2

        range_high = round(range_mid + half_range, 2)
        range_low = round(range_mid - half_range, 2)
        range_width = round(range_high - range_low, 2)
        range_width_pct = round((range_width / price) * 100, 3)

        signals = []
        momentum = 0

        # 1. Price vs range (weight: ±5 inside range, ±30 on breakout)
        if price > range_high:
            momentum += 30
            signals.append({"name": "Price above range high", "score": 30,
                            "desc": f"${price:.2f} > ${range_high:.2f} — bullish breakout"})
        elif price < range_low:
            momentum -= 30
            signals.append({"name": "Price below range low", "score": -30,
                            "desc": f"${price:.2f} < ${range_low:.2f} — bearish breakout"})
        else:
            signals.append({"name": "Price within range", "score": 0,
                            "desc": f"${price:.2f} inside ${range_low:.2f}–${range_high:.2f} — no breakout yet"})

        # 2. RSI (weight: ±20)
        rsi = indicators.rsi_14
        if rsi > 60:
            momentum += 20
            signals.append({"name": "RSI bullish", "score": 20,
                            "desc": f"RSI={rsi:.1f} — bullish momentum supports upside breakout"})
        elif rsi < 40:
            momentum -= 20
            signals.append({"name": "RSI bearish", "score": -20,
                            "desc": f"RSI={rsi:.1f} — bearish momentum supports downside breakout"})
        else:
            signals.append({"name": "RSI neutral", "score": 0,
                            "desc": f"RSI={rsi:.1f} — no strong directional bias"})

        # 3. MACD (weight: ±20, upweighted from ±15)
        hist = indicators.macd_histogram
        if hist > 0.1:
            momentum += 20
            signals.append({"name": "MACD bullish", "score": 20,
                            "desc": f"MACD histogram={hist:.3f} — positive momentum"})
        elif hist < -0.1:
            momentum -= 20
            signals.append({"name": "MACD bearish", "score": -20,
                            "desc": f"MACD histogram={hist:.3f} — negative momentum"})
        else:
            signals.append({"name": "MACD flat", "score": 0,
                            "desc": f"MACD histogram={hist:.3f} — no momentum edge"})

        # 4. VWAP proxy (weight: ±20, upweighted from ±15)
        vwap_proxy = indicators.sma_20
        if price > vwap_proxy:
            momentum += 20
            signals.append({"name": "Above VWAP (SMA20)", "score": 20,
                            "desc": f"${price:.2f} > ${vwap_proxy:.2f} — buyers in control"})
        else:
            momentum -= 20
            signals.append({"name": "Below VWAP (SMA20)", "score": -20,
                            "desc": f"${price:.2f} < ${vwap_proxy:.2f} — sellers in control"})

        # 5. VIX
        vix = indicators.vix
        if vix > 20:
            momentum += 5 if momentum > 0 else -5
            signals.append({"name": "VIX elevated", "score": 10,
                            "desc": f"VIX={vix:.1f} — wider ranges, good for scalping"})
        else:
            signals.append({"name": "VIX low", "score": 0,
                            "desc": f"VIX={vix:.1f} — narrow ranges, tighter targets"})

        # 6. BB Squeeze
        bb_width_pct = ((indicators.bb_upper - indicators.bb_lower) / indicators.bb_middle) * 100
        if bb_width_pct < 4:
            signals.append({"name": "BB Squeeze detected", "score": 10,
                            "desc": f"Band width {bb_width_pct:.1f}% — coiled for a move, breakout likely"})
            momentum += 10 if momentum > 0 else -10

        # 7. Volume
        vol = indicators.volume
        vol_avg = indicators.volume_sma_20
        volume_surge = False
        if vol_avg > 0:
            vol_ratio = vol / vol_avg
            if vol_ratio >= 1.3:
                volume_surge = True
                momentum += 5 if momentum > 0 else -5
                signals.append({"name": "Volume surge", "score": 10,
                                "desc": f"Volume {vol_ratio:.1f}× average — high participation"})
            else:
                signals.append({"name": "Normal volume", "score": 0,
                                "desc": f"Volume {vol_ratio:.1f}× average — normal"})
        else:
            volume_surge = range_width_pct > 0.5 and vix > 18

        return self._build_result(
            range_high=range_high, range_low=range_low,
            range_width=range_width, range_width_pct=range_width_pct,
            price=price, atr=indicators.atr_14,
            momentum=momentum, signals=signals,
            volume_surge=volume_surge,
            data_source="synthesized", n_bars=0,
        )

    # ── Shared: Build Result ─────────────────────────────────────

    def _build_result(
        self,
        range_high: float,
        range_low: float,
        range_width: float,
        range_width_pct: float,
        price: float,
        atr: float,
        momentum: int,
        signals: list[dict],
        volume_surge: bool,
        data_source: str,
        n_bars: int,
    ) -> OpeningRangeAnalysis:
        momentum = max(-100, min(100, momentum))

        if momentum >= 25:
            direction = BreakoutDirection.BULLISH
        elif momentum <= -25:
            direction = BreakoutDirection.BEARISH
        else:
            direction = BreakoutDirection.NEUTRAL

        breakout_confirmed = abs(momentum) >= 40

        if direction == BreakoutDirection.BULLISH:
            entry = round(range_high + atr * 0.05, 2)
            stop = round(range_low - atr * 0.05, 2)
            risk = round(entry - stop, 2)
            t1 = round(entry + risk, 2)
            t2 = round(entry + risk * 2, 2)
        elif direction == BreakoutDirection.BEARISH:
            entry = round(range_low - atr * 0.05, 2)
            stop = round(range_high + atr * 0.05, 2)
            risk = round(stop - entry, 2)
            t1 = round(entry - risk, 2)
            t2 = round(entry - risk * 2, 2)
        else:
            entry = price
            stop = round(price - atr * 0.2, 2) if momentum >= 0 else round(price + atr * 0.2, 2)
            risk = round(abs(entry - stop), 2)
            t1 = round(entry + risk, 2) if momentum >= 0 else round(entry - risk, 2)
            t2 = round(entry + risk * 2, 2) if momentum >= 0 else round(entry - risk * 2, 2)

        dir_emoji = "🟢" if direction == BreakoutDirection.BULLISH else "🔴" if direction == BreakoutDirection.BEARISH else "⚪"
        confirm_text = "✅ CONFIRMED" if breakout_confirmed else "⏳ Developing"
        source_text = "📡 Live 5-min candles" if data_source == "live_intraday" else "📊 Estimated from daily indicators"
        summary = (
            f"{dir_emoji} **{direction.value.upper()} Breakout** — {confirm_text} "
            f"(momentum {momentum:+d}/100)\n\n"
            f"**60-Min Opening Range:** ${range_low:.2f} – ${range_high:.2f} "
            f"(width: ${range_width:.2f}, {range_width_pct:.2f}% of price)\n\n"
            f"*Data: {source_text}"
            f"{f' ({n_bars} bars)' if n_bars > 0 else ''}*\n\n"
            f"{'**→ BUY CALL** — Enter long calls on bullish breakout above range.' if direction == BreakoutDirection.BULLISH else ''}"
            f"{'**→ BUY PUT** — Enter long puts on bearish breakout below range.' if direction == BreakoutDirection.BEARISH else ''}"
            f"{'**→ WAIT** — No clear breakout. Wait for price to break the range.' if direction == BreakoutDirection.NEUTRAL else ''}"
        )

        return OpeningRangeAnalysis(
            range_high=range_high,
            range_low=range_low,
            range_width=range_width,
            range_width_pct=range_width_pct,
            current_price=price,
            breakout_direction=direction,
            breakout_confirmed=breakout_confirmed,
            momentum_score=momentum,
            volume_surge=volume_surge,
            entry_price=entry,
            stop_loss=stop,
            target_1=t1,
            target_2=t2,
            risk_per_share=risk,
            signals=signals,
            summary=summary,
            data_source=data_source,
            opening_range_bars=n_bars,
        )

    # ── Technical Helpers ────────────────────────────────────────

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
