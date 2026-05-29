"""Daily Range Analyzer — maps prior day's range to opening-range signals.

Replaces OpeningRangeAnalyzer for the weekly timeframe. Instead of the
9:30-10:30 intraday range, uses yesterday's high/low as the "range" and
scores today's price position relative to it.

Output matches the interface that compute_quality_score() expects:
  or_direction, or_momentum, or_confirmed, range_high, range_low.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.models.market_data import MarketIndicators

logger = logging.getLogger(__name__)


@dataclass
class DailyRangeResult:
    """Result of daily range analysis — same interface as OpeningRangeAnalysis."""
    range_high: float
    range_low: float
    range_width: float
    current_price: float
    breakout_direction: str        # "bullish", "bearish", "neutral"
    breakout_confirmed: bool       # |momentum| >= 40
    momentum_score: int            # -100 to +100
    volume_surge: bool
    signals: list[dict] = field(default_factory=list)


class DailyRangeAnalyzer:
    """Analyzes price position relative to prior day's range on daily bars.

    Produces or_direction / or_momentum / or_confirmed that plug directly
    into compute_quality_score() without modification.
    """

    def analyze(
        self,
        daily_bars: pd.DataFrame,
        indicators: MarketIndicators,
    ) -> DailyRangeResult:
        if len(daily_bars) < 3:
            return self._insufficient_data(indicators)

        prior_day = daily_bars.iloc[-2]
        current_day = daily_bars.iloc[-1]

        range_high = float(prior_day["High"])
        range_low = float(prior_day["Low"])
        range_width = range_high - range_low
        price = float(current_day["Close"])

        if range_width <= 0:
            return self._insufficient_data(indicators)

        signals = []
        momentum = 0

        # 1. Price vs prior day's range (±30)
        if price > range_high:
            dist_pct = (price - range_high) / range_width * 100
            momentum += 30
            signals.append({"name": "Above prior day high", "score": 30,
                            "desc": f"${price:.2f} > ${range_high:.2f} (+{dist_pct:.0f}% of range) — bullish breakout"})
        elif price < range_low:
            dist_pct = (range_low - price) / range_width * 100
            momentum -= 30
            signals.append({"name": "Below prior day low", "score": -30,
                            "desc": f"${price:.2f} < ${range_low:.2f} (-{dist_pct:.0f}% of range) — bearish breakout"})
        else:
            pct_in = (price - range_low) / range_width * 100
            if pct_in > 70:
                momentum += 5
                signals.append({"name": "Upper prior day range", "score": 5,
                                "desc": f"${price:.2f} at {pct_in:.0f}% of range — mild bullish lean"})
            elif pct_in < 30:
                momentum -= 5
                signals.append({"name": "Lower prior day range", "score": -5,
                                "desc": f"${price:.2f} at {pct_in:.0f}% of range — mild bearish lean"})
            else:
                signals.append({"name": "Mid prior day range", "score": 0,
                                "desc": f"${price:.2f} at {pct_in:.0f}% of range — no breakout"})

        # 2. Daily RSI-14 (±20)
        rsi = indicators.rsi_14
        if rsi > 60:
            momentum += 20
            signals.append({"name": "Daily RSI bullish", "score": 20, "desc": f"RSI={rsi:.1f}"})
        elif rsi < 40:
            momentum -= 20
            signals.append({"name": "Daily RSI bearish", "score": -20, "desc": f"RSI={rsi:.1f}"})
        else:
            signals.append({"name": "Daily RSI neutral", "score": 0, "desc": f"RSI={rsi:.1f}"})

        # 3. Daily MACD histogram (±20)
        hist = indicators.macd_histogram
        if hist > 0.1:
            momentum += 20
            signals.append({"name": "Daily MACD bullish", "score": 20, "desc": f"Histogram={hist:.3f}"})
        elif hist < -0.1:
            momentum -= 20
            signals.append({"name": "Daily MACD bearish", "score": -20, "desc": f"Histogram={hist:.3f}"})
        else:
            signals.append({"name": "Daily MACD flat", "score": 0, "desc": f"Histogram={hist:.3f}"})

        # 4. Price vs 20-day SMA (±20)
        sma20 = indicators.sma_20
        if price > sma20:
            momentum += 20
            signals.append({"name": "Above SMA20", "score": 20,
                            "desc": f"${price:.2f} > ${sma20:.2f}"})
        else:
            momentum -= 20
            signals.append({"name": "Below SMA20", "score": -20,
                            "desc": f"${price:.2f} < ${sma20:.2f}"})

        # 5. Volume vs 20-day average (±10)
        vol_ratio = indicators.volume / indicators.volume_sma_20 if indicators.volume_sma_20 > 0 else 1.0
        volume_surge = vol_ratio >= 1.2
        if volume_surge:
            vol_dir = 10 if momentum > 0 else -10
            momentum += vol_dir
            signals.append({"name": "Volume surge", "score": abs(vol_dir),
                            "desc": f"Volume {vol_ratio:.1f}x avg — confirms direction"})

        # 6. Prior day candle body direction (±10)
        prior_open = float(prior_day["Open"])
        prior_close = float(prior_day["Close"])
        body_pct = (prior_close - prior_open) / range_width if range_width > 0 else 0
        if body_pct > 0.3:
            momentum += 10
            signals.append({"name": "Prior day bullish candle", "score": 10,
                            "desc": f"Body {body_pct:.0%} of range — conviction"})
        elif body_pct < -0.3:
            momentum -= 10
            signals.append({"name": "Prior day bearish candle", "score": -10,
                            "desc": f"Body {body_pct:.0%} of range — conviction"})

        momentum = max(-100, min(100, momentum))

        if momentum >= 25:
            direction = "bullish"
        elif momentum <= -25:
            direction = "bearish"
        else:
            direction = "neutral"

        return DailyRangeResult(
            range_high=range_high,
            range_low=range_low,
            range_width=round(range_width, 2),
            current_price=price,
            breakout_direction=direction,
            breakout_confirmed=abs(momentum) >= 40,
            momentum_score=momentum,
            volume_surge=volume_surge,
            signals=signals,
        )

    def _insufficient_data(self, indicators: MarketIndicators) -> DailyRangeResult:
        price = indicators.current_price
        return DailyRangeResult(
            range_high=price,
            range_low=price,
            range_width=0.0,
            current_price=price,
            breakout_direction="neutral",
            breakout_confirmed=False,
            momentum_score=0,
            volume_surge=False,
            signals=[{"name": "Insufficient data", "score": 0, "desc": "Need at least 3 daily bars"}],
        )
