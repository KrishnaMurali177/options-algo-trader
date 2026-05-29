"""Daily Momentum Analyzer — maps last 3-5 trading days to momentum signals.

Replaces RecentMomentumAnalyzer for the weekly timeframe. Instead of the
last 6 x 5-min bars (30 minutes), analyzes the last 5 daily bars.

Output matches the interface that compute_quality_score() expects:
  recent_dir, recent_momentum.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.models.market_data import MarketIndicators

logger = logging.getLogger(__name__)

LOOKBACK = 5  # trading days


@dataclass
class DailyMomentumResult:
    """Result of daily momentum analysis — same interface as RecentMomentumResult."""
    direction: str           # "bullish", "bearish", "neutral"
    momentum_score: int      # -100 to +100
    price_change_pct: float  # % change over the lookback window
    signals: list[dict] = field(default_factory=list)


class DailyMomentumAnalyzer:
    """Analyzes the last 3-5 daily bars for momentum direction.

    Produces direction / momentum_score that plug directly into
    compute_quality_score() as recent_dir / recent_momentum.
    """

    def analyze(
        self,
        daily_bars: pd.DataFrame,
        indicators: MarketIndicators,
        lookback: int = LOOKBACK,
    ) -> DailyMomentumResult:
        if len(daily_bars) < 4:
            return DailyMomentumResult(
                direction="neutral", momentum_score=0, price_change_pct=0.0,
                signals=[{"name": "Insufficient data", "score": 0, "desc": "Need 4+ daily bars"}],
            )

        recent = daily_bars.tail(lookback) if len(daily_bars) >= lookback else daily_bars.tail(len(daily_bars))
        n = len(recent)

        open_price = float(recent["Open"].iloc[0])
        close_price = float(recent["Close"].iloc[-1])
        change_pct = ((close_price - open_price) / open_price) * 100 if open_price > 0 else 0

        signals = []
        momentum = 0

        # 1. Price change over window (±30)
        if change_pct > 1.0:
            momentum += 30
            signals.append({"name": f"{n}-day price rising", "score": 30,
                            "desc": f"+{change_pct:.2f}% — strong bullish"})
        elif change_pct > 0.3:
            momentum += 15
            signals.append({"name": f"{n}-day price drifting up", "score": 15,
                            "desc": f"+{change_pct:.2f}% — mild bullish"})
        elif change_pct < -1.0:
            momentum -= 30
            signals.append({"name": f"{n}-day price falling", "score": -30,
                            "desc": f"{change_pct:.2f}% — strong bearish"})
        elif change_pct < -0.3:
            momentum -= 15
            signals.append({"name": f"{n}-day price drifting down", "score": -15,
                            "desc": f"{change_pct:.2f}% — mild bearish"})
        else:
            signals.append({"name": f"{n}-day price flat", "score": 0,
                            "desc": f"{change_pct:.2f}% — no clear direction"})

        # 2. Green/red daily candle ratio (±20)
        closes = recent["Close"].astype(float).values
        opens = recent["Open"].astype(float).values
        green = sum(1 for i in range(n) if closes[i] >= opens[i])
        red = n - green

        if green >= n - 1:  # 4 of 5 or better
            momentum += 20
            signals.append({"name": "Strong green candles", "score": 20,
                            "desc": f"{green}/{n} days green — consistent buying"})
        elif green >= n - 2:  # 3 of 5
            momentum += 10
            signals.append({"name": "Mostly green candles", "score": 10,
                            "desc": f"{green}/{n} days green"})
        elif red >= n - 1:
            momentum -= 20
            signals.append({"name": "Strong red candles", "score": -20,
                            "desc": f"{red}/{n} days red — consistent selling"})
        elif red >= n - 2:
            momentum -= 10
            signals.append({"name": "Mostly red candles", "score": -10,
                            "desc": f"{red}/{n} days red"})
        else:
            signals.append({"name": "Mixed candles", "score": 0,
                            "desc": f"{green} green / {red} red — indecisive"})

        # 3. Daily RSI-14 (±15)
        rsi = indicators.rsi_14
        if rsi > 60:
            momentum += 15
            signals.append({"name": "Daily RSI bullish", "score": 15, "desc": f"RSI={rsi:.1f}"})
        elif rsi < 40:
            momentum -= 15
            signals.append({"name": "Daily RSI bearish", "score": -15, "desc": f"RSI={rsi:.1f}"})
        else:
            signals.append({"name": "Daily RSI neutral", "score": 0, "desc": f"RSI={rsi:.1f}"})

        # 4. Price vs 20-day SMA (±15)
        sma20 = indicators.sma_20
        if close_price > sma20:
            momentum += 15
            signals.append({"name": "Above SMA20", "score": 15,
                            "desc": f"${close_price:.2f} > ${sma20:.2f}"})
        else:
            momentum -= 15
            signals.append({"name": "Below SMA20", "score": -15,
                            "desc": f"${close_price:.2f} < ${sma20:.2f}"})

        # 5. Volume trend (±10)
        volumes = recent["Volume"].astype(float)
        if n >= 4:
            split = n // 2
            first_half_vol = float(volumes.iloc[:split].mean())
            second_half_vol = float(volumes.iloc[split:].mean())
            if first_half_vol > 0:
                vol_ratio = second_half_vol / first_half_vol
                if vol_ratio > 1.15:
                    vol_dir = 10 if momentum > 0 else -10
                    momentum += vol_dir
                    signals.append({"name": "Volume increasing", "score": abs(vol_dir),
                                    "desc": f"Recent volume {vol_ratio:.1f}x prior — confirms"})
                elif vol_ratio < 0.85:
                    momentum -= 5
                    signals.append({"name": "Volume fading", "score": -5,
                                    "desc": f"Volume declining {vol_ratio:.1f}x — move may stall"})

        momentum = max(-100, min(100, momentum))

        if momentum >= 20:
            direction = "bullish"
        elif momentum <= -20:
            direction = "bearish"
        else:
            direction = "neutral"

        return DailyMomentumResult(
            direction=direction,
            momentum_score=momentum,
            price_change_pct=round(change_pct, 3),
            signals=signals,
        )
