"""Recent Momentum Analyzer — analyzes the last 30 minutes of 5-min candles.

Provides a real-time momentum snapshot at the moment of analysis.
Complements the 60-min opening range (which is hours old by afternoon).

Live mode:  Fetches actual 5-min candles from Yahoo Finance.
Mock mode:  Synthesizes from daily indicators.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd

from src.models.market_data import MarketIndicators

logger = logging.getLogger(__name__)


@dataclass
class RecentMomentumResult:
    """Result of the recent 30-min momentum analysis."""
    direction: str                    # "bullish", "bearish", "neutral"
    momentum_score: int               # -100 to +100
    price_change_pct: float           # % change over the 30-min window
    mini_trend: str                   # "up", "down", "flat"
    rsi_5min: float                   # RSI computed on 5-min bars
    vwap_position: str                # "above", "below"
    volume_trend: str                 # "increasing", "decreasing", "flat"
    candle_count: int                 # number of 5-min bars used
    window_high: float
    window_low: float
    window_open: float
    window_close: float
    signals: list[dict] = field(default_factory=list)
    summary: str = ""
    data_source: str = "synthesized"  # "live" or "synthesized"


class RecentMomentumAnalyzer:
    """
    Analyzes the most recent 30 minutes of 5-min candle data to determine
    current momentum direction. Used alongside the opening range analysis
    for strategy selection.
    """

    def analyze(self, indicators: MarketIndicators, mock: bool = False, bars_5m=None) -> RecentMomentumResult:
        if bars_5m is not None and not bars_5m.empty:
            # Use pre-fetched bars (replay mode) — take last 6 bars as "recent 30 min"
            if bars_5m.index.tz is None:
                bars_5m.index = pd.to_datetime(bars_5m.index)
                bars_5m.index = bars_5m.index.tz_localize("UTC").tz_convert("US/Eastern")
            recent = bars_5m.tail(6)
            if len(recent) >= 3:
                return self._score(recent, bars_5m, indicators, data_source="replay_bars")
            return self._synthesize(indicators)
        if mock:
            return self._synthesize(indicators)
        try:
            return self._analyze_live(indicators)
        except Exception as exc:
            logger.warning(
                "Live recent momentum fetch failed for %s (%s) — falling back to synthesized",
                indicators.symbol, exc,
            )
            return self._synthesize(indicators)

    def _analyze_live(self, indicators: MarketIndicators) -> RecentMomentumResult:
        """Fetch real 5-min candles and analyze the last 30 minutes."""
        import yfinance as yf

        symbol = indicators.symbol
        logger.info("Fetching recent 5-min data for %s …", symbol)

        df = yf.download(symbol, period="5d", interval="5m", progress=False)
        if df.empty:
            raise ValueError(f"No intraday data for {symbol}")

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert("US/Eastern")

        today = date.today()
        today_bars = df[df.index.date == today].copy()
        if today_bars.empty:
            last_day = df.index[-1].date()
            today_bars = df[df.index.date == last_day].copy()
            if today_bars.empty:
                raise ValueError(f"No bars for {symbol}")

        # Take the last 6 bars (30 min of 5-min candles)
        recent = today_bars.tail(6)
        if len(recent) < 3:
            raise ValueError(f"Only {len(recent)} recent bars — need at least 3")

        return self._score(recent, today_bars, indicators, data_source="live")

    def _synthesize(self, indicators: MarketIndicators) -> RecentMomentumResult:
        """Synthesize recent momentum from daily indicators."""
        price = indicators.current_price
        atr = indicators.atr_14

        # Simulate a 30-min window from daily data
        half_range = atr * 0.08  # ~8% of daily ATR for 30 min
        if indicators.macd_histogram > 0:
            w_open = price - half_range * 0.6
            w_close = price
        else:
            w_open = price + half_range * 0.6
            w_close = price

        w_high = max(w_open, w_close) + half_range * 0.3
        w_low = min(w_open, w_close) - half_range * 0.3

        change_pct = ((w_close - w_open) / w_open) * 100 if w_open > 0 else 0

        signals = []
        momentum = 0

        # 1. Price direction
        if change_pct > 0.05:
            momentum += 25
            signals.append({"name": "30-min price rising", "score": 25,
                            "desc": f"+{change_pct:.2f}% over last 30 min"})
        elif change_pct < -0.05:
            momentum -= 25
            signals.append({"name": "30-min price falling", "score": -25,
                            "desc": f"{change_pct:.2f}% over last 30 min"})
        else:
            signals.append({"name": "30-min price flat", "score": 0,
                            "desc": f"{change_pct:.2f}% — no clear direction"})

        # 2. RSI proxy
        rsi = indicators.rsi_14
        if rsi > 55:
            momentum += 20
            signals.append({"name": "RSI bullish", "score": 20, "desc": f"RSI={rsi:.1f}"})
        elif rsi < 45:
            momentum -= 20
            signals.append({"name": "RSI bearish", "score": -20, "desc": f"RSI={rsi:.1f}"})
        else:
            signals.append({"name": "RSI neutral", "score": 0, "desc": f"RSI={rsi:.1f}"})

        # 3. MACD
        hist = indicators.macd_histogram
        if hist > 0:
            momentum += 15
            signals.append({"name": "MACD positive", "score": 15, "desc": f"Histogram={hist:.3f}"})
        else:
            momentum -= 15
            signals.append({"name": "MACD negative", "score": -15, "desc": f"Histogram={hist:.3f}"})

        # 4. VWAP proxy
        vwap_pos = "above" if price > indicators.sma_20 else "below"
        if vwap_pos == "above":
            momentum += 15
            signals.append({"name": "Above VWAP", "score": 15, "desc": f"${price:.2f} > SMA20 ${indicators.sma_20:.2f}"})
        else:
            momentum -= 15
            signals.append({"name": "Below VWAP", "score": -15, "desc": f"${price:.2f} < SMA20 ${indicators.sma_20:.2f}"})

        momentum = max(-100, min(100, momentum))
        direction = "bullish" if momentum >= 20 else "bearish" if momentum <= -20 else "neutral"
        mini_trend = "up" if change_pct > 0.05 else "down" if change_pct < -0.05 else "flat"

        dir_emoji = "🟢" if direction == "bullish" else "🔴" if direction == "bearish" else "⚪"
        summary = (
            f"{dir_emoji} **Recent 30-min: {direction.upper()}** (momentum {momentum:+d}/100)\n\n"
            f"*Data: 📊 Estimated from daily indicators*"
        )

        return RecentMomentumResult(
            direction=direction, momentum_score=momentum,
            price_change_pct=round(change_pct, 3),
            mini_trend=mini_trend, rsi_5min=rsi,
            vwap_position=vwap_pos, volume_trend="flat",
            candle_count=0,
            window_high=round(w_high, 2), window_low=round(w_low, 2),
            window_open=round(w_open, 2), window_close=round(w_close, 2),
            signals=signals, summary=summary, data_source="synthesized",
        )

    def _score(self, recent: pd.DataFrame, all_today: pd.DataFrame,
               indicators: MarketIndicators, data_source: str) -> RecentMomentumResult:
        """Score a 30-min window of 5-min bars."""
        w_open = float(recent["Open"].iloc[0])
        w_close = float(recent["Close"].iloc[-1])
        w_high = float(recent["High"].max())
        w_low = float(recent["Low"].min())
        w_volume = recent["Volume"].astype(float)
        close_series = recent["Close"].astype(float)

        change_pct = ((w_close - w_open) / w_open) * 100 if w_open > 0 else 0

        # RSI on 5-min bars (use all today's bars for enough data)
        all_close = all_today["Close"].astype(float)
        if len(all_close) >= 15:
            rsi = self._rsi(all_close, 14)
        else:
            rsi = indicators.rsi_14

        # VWAP
        typical = (all_today["High"] + all_today["Low"] + all_today["Close"]) / 3
        cumvol = all_today["Volume"].cumsum()
        last_cumvol = float(cumvol.iloc[-1])
        vwap = float((typical * all_today["Volume"]).cumsum().iloc[-1] / last_cumvol) if last_cumvol > 0 else w_close
        vwap_pos = "above" if w_close > vwap else "below"

        # Volume trend: compare last 3 bars vs first 3 bars
        if len(w_volume) >= 6:
            first_half_vol = float(w_volume.iloc[:3].mean())
            second_half_vol = float(w_volume.iloc[3:].mean())
            if first_half_vol > 0:
                vol_ratio = second_half_vol / first_half_vol
                vol_trend = "increasing" if vol_ratio > 1.15 else "decreasing" if vol_ratio < 0.85 else "flat"
            else:
                vol_trend = "flat"
        else:
            vol_trend = "flat"

        # Count green vs red candles
        green = sum(1 for i in range(len(recent)) if float(recent["Close"].iloc[i]) >= float(recent["Open"].iloc[i]))
        red = len(recent) - green

        # ── Score signals ──
        signals = []
        momentum = 0

        # 1. Price change direction
        if change_pct > 0.1:
            momentum += 30
            signals.append({"name": "30-min price rising", "score": 30,
                            "desc": f"+{change_pct:.2f}% — strong bullish momentum"})
        elif change_pct > 0.03:
            momentum += 15
            signals.append({"name": "30-min price drifting up", "score": 15,
                            "desc": f"+{change_pct:.2f}% — mild bullish"})
        elif change_pct < -0.1:
            momentum -= 30
            signals.append({"name": "30-min price falling", "score": -30,
                            "desc": f"{change_pct:.2f}% — strong bearish momentum"})
        elif change_pct < -0.03:
            momentum -= 15
            signals.append({"name": "30-min price drifting down", "score": -15,
                            "desc": f"{change_pct:.2f}% — mild bearish"})
        else:
            signals.append({"name": "30-min price flat", "score": 0,
                            "desc": f"{change_pct:.2f}% — no clear direction"})

        # 2. Candle color ratio
        if green >= 5:
            momentum += 20
            signals.append({"name": "Strong green candles", "score": 20,
                            "desc": f"{green}/{len(recent)} candles green — consistent buying"})
        elif green >= 4:
            momentum += 10
            signals.append({"name": "Mostly green candles", "score": 10,
                            "desc": f"{green}/{len(recent)} candles green"})
        elif red >= 5:
            momentum -= 20
            signals.append({"name": "Strong red candles", "score": -20,
                            "desc": f"{red}/{len(recent)} candles red — consistent selling"})
        elif red >= 4:
            momentum -= 10
            signals.append({"name": "Mostly red candles", "score": -10,
                            "desc": f"{red}/{len(recent)} candles red"})
        else:
            signals.append({"name": "Mixed candles", "score": 0,
                            "desc": f"{green} green / {red} red — indecisive"})

        # 3. 5-min RSI
        if rsi > 60:
            momentum += 15
            signals.append({"name": "5-min RSI bullish", "score": 15,
                            "desc": f"RSI={rsi:.1f} — bullish momentum"})
        elif rsi < 40:
            momentum -= 15
            signals.append({"name": "5-min RSI bearish", "score": -15,
                            "desc": f"RSI={rsi:.1f} — bearish momentum"})
        else:
            signals.append({"name": "5-min RSI neutral", "score": 0,
                            "desc": f"RSI={rsi:.1f}"})

        # 4. Price vs VWAP
        if vwap_pos == "above":
            momentum += 15
            signals.append({"name": "Above VWAP", "score": 15,
                            "desc": f"${w_close:.2f} > VWAP ${vwap:.2f}"})
        else:
            momentum -= 15
            signals.append({"name": "Below VWAP", "score": -15,
                            "desc": f"${w_close:.2f} < VWAP ${vwap:.2f}"})

        # 5. Volume trend
        if vol_trend == "increasing":
            vol_dir = 10 if momentum > 0 else -10
            momentum += vol_dir
            signals.append({"name": "Volume increasing", "score": abs(vol_dir),
                            "desc": "Volume rising — confirms move"})
        elif vol_trend == "decreasing":
            signals.append({"name": "Volume fading", "score": -5,
                            "desc": "Volume declining — move may stall"})
            momentum -= 5
        else:
            signals.append({"name": "Volume steady", "score": 0, "desc": "Normal volume"})

        momentum = max(-100, min(100, momentum))
        direction = "bullish" if momentum >= 20 else "bearish" if momentum <= -20 else "neutral"
        mini_trend = "up" if change_pct > 0.03 else "down" if change_pct < -0.03 else "flat"

        dir_emoji = "🟢" if direction == "bullish" else "🔴" if direction == "bearish" else "⚪"
        source_text = "📡 Live 5-min candles" if data_source == "live" else "📊 Estimated"
        summary = (
            f"{dir_emoji} **Recent 30-min: {direction.upper()}** (momentum {momentum:+d}/100) — "
            f"{change_pct:+.2f}%\n\n"
            f"Window: ${w_low:.2f} – ${w_high:.2f} | "
            f"{green} green / {red} red candles | Volume: {vol_trend}\n\n"
            f"*Data: {source_text} ({len(recent)} bars)*"
        )

        return RecentMomentumResult(
            direction=direction, momentum_score=momentum,
            price_change_pct=round(change_pct, 3),
            mini_trend=mini_trend, rsi_5min=round(rsi, 1),
            vwap_position=vwap_pos, volume_trend=vol_trend,
            candle_count=len(recent),
            window_high=round(w_high, 2), window_low=round(w_low, 2),
            window_open=round(w_open, 2), window_close=round(w_close, 2),
            signals=signals, summary=summary, data_source=data_source,
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

