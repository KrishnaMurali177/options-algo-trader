"""Entry Point Analyzer — identifies optimal entry timing using technical signals.

Produces a composite entry score (0–100) and a list of actionable signals
that tell the user whether NOW is a good time to enter a trade, or whether
they should wait for better conditions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from src.models.market_data import MarketIndicators

logger = logging.getLogger(__name__)


class EntrySignal(str, Enum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    NEUTRAL = "neutral"
    WAIT = "wait"
    AVOID = "avoid"


@dataclass
class EntrySignalDetail:
    """One individual technical signal contributing to the entry score."""
    name: str
    signal: EntrySignal
    score: int            # -20 to +20 contribution
    description: str


@dataclass
class EntryAnalysis:
    """Complete entry point analysis result."""
    composite_score: int          # 0-100; higher = better entry
    recommendation: EntrySignal
    summary: str
    signals: list[EntrySignalDetail] = field(default_factory=list)
    optimal_entry_price: float | None = None
    support_level: float | None = None
    resistance_level: float | None = None


class EntryAnalyzer:
    """
    Scores whether the current moment is a good entry point for an options trade.

    Uses 8 technical signals, each contributing ±20 points to a 0-100 composite.
    Base score is 50 (neutral). Signals push it toward "strong buy" or "avoid".
    """

    def analyze(self, indicators: MarketIndicators, timeframe: str = "daily") -> EntryAnalysis:
        signals: list[EntrySignalDetail] = []

        # Adapt thresholds for intraday vs swing vs position timeframes
        # Intraday: tighter RSI bands (mean-reverts faster), tighter ATR distances
        if timeframe in ("15min", "1hour"):
            rsi_oversold, rsi_approaching, rsi_elevated, rsi_overbought = 35, 45, 55, 65
            atr_near, atr_approach, atr_mid = 0.5, 1.0, 2.0
        elif timeframe == "weekly":
            rsi_oversold, rsi_approaching, rsi_elevated, rsi_overbought = 25, 35, 65, 75
            atr_near, atr_approach, atr_mid = 1.5, 2.5, 4.0
        else:  # daily (default)
            rsi_oversold, rsi_approaching, rsi_elevated, rsi_overbought = 30, 40, 60, 70
            atr_near, atr_approach, atr_mid = 1.0, 2.0, 3.5

        # ── 1. RSI Mean-Reversion ─────────────────────────────────
        rsi = indicators.rsi_14
        if rsi < rsi_oversold:
            signals.append(EntrySignalDetail(
                "RSI Oversold", EntrySignal.STRONG_BUY, 18,
                f"RSI={rsi:.1f} is deeply oversold (<{rsi_oversold}) — strong mean-reversion entry for bullish strategies."
            ))
        elif rsi < rsi_approaching:
            signals.append(EntrySignalDetail(
                "RSI Approaching Oversold", EntrySignal.BUY, 10,
                f"RSI={rsi:.1f} is approaching oversold (<{rsi_approaching}) — favorable entry."
            ))
        elif rsi <= rsi_elevated:
            signals.append(EntrySignalDetail(
                "RSI Neutral", EntrySignal.NEUTRAL, 0,
                f"RSI={rsi:.1f} is neutral ({rsi_approaching}–{rsi_elevated}) — no strong directional signal."
            ))
        elif rsi < rsi_overbought:
            signals.append(EntrySignalDetail(
                "RSI Elevated", EntrySignal.WAIT, -5,
                f"RSI={rsi:.1f} is elevated — consider waiting for a pullback."
            ))
        else:
            signals.append(EntrySignalDetail(
                "RSI Overbought", EntrySignal.AVOID, -15,
                f"RSI={rsi:.1f} is overbought (>{rsi_overbought}) — avoid new bullish entries, good for selling premium."
            ))

        # ── 2. Bollinger Band Position ────────────────────────────
        price = indicators.current_price
        bb_range = indicators.bb_upper - indicators.bb_lower
        bb_pct = (price - indicators.bb_lower) / bb_range if bb_range > 0 else 0.5

        if bb_pct <= 0.15:
            signals.append(EntrySignalDetail(
                "Price at Lower Bollinger", EntrySignal.STRONG_BUY, 15,
                f"Price near lower Bollinger band ({bb_pct:.0%}) — potential bounce entry."
            ))
        elif bb_pct <= 0.35:
            signals.append(EntrySignalDetail(
                "Price in Lower Band Zone", EntrySignal.BUY, 8,
                f"Price in lower portion of Bollinger bands ({bb_pct:.0%})."
            ))
        elif bb_pct <= 0.65:
            signals.append(EntrySignalDetail(
                "Price Mid-Band", EntrySignal.NEUTRAL, 0,
                f"Price at middle of Bollinger bands ({bb_pct:.0%})."
            ))
        elif bb_pct <= 0.85:
            signals.append(EntrySignalDetail(
                "Price in Upper Band Zone", EntrySignal.WAIT, -5,
                f"Price in upper portion of bands ({bb_pct:.0%}) — extended."
            ))
        else:
            signals.append(EntrySignalDetail(
                "Price at Upper Bollinger", EntrySignal.AVOID, -12,
                f"Price near upper Bollinger ({bb_pct:.0%}) — overextended, wait for pullback."
            ))

        # ── 3. MACD Momentum & Crossover ──────────────────────────
        hist = indicators.macd_histogram
        if hist > 0 and indicators.macd > indicators.macd_signal:
            signals.append(EntrySignalDetail(
                "MACD Bullish Crossover", EntrySignal.BUY, 12,
                f"MACD above signal (hist={hist:.3f}) — bullish momentum confirmed."
            ))
        elif hist > 0:
            signals.append(EntrySignalDetail(
                "MACD Positive", EntrySignal.BUY, 5,
                f"MACD histogram positive ({hist:.3f}) — mild bullish momentum."
            ))
        elif hist > -0.5:
            signals.append(EntrySignalDetail(
                "MACD Weakening", EntrySignal.NEUTRAL, -3,
                f"MACD histogram slightly negative ({hist:.3f}) — momentum fading."
            ))
        else:
            signals.append(EntrySignalDetail(
                "MACD Bearish", EntrySignal.WAIT, -10,
                f"MACD histogram negative ({hist:.3f}) — bearish momentum, wait for reversal."
            ))

        # ── 4. SMA Trend Alignment ────────────────────────────────
        above_20 = price > indicators.sma_20
        above_50 = price > indicators.sma_50
        golden = indicators.sma_50 > indicators.sma_200

        if above_20 and above_50 and golden:
            signals.append(EntrySignalDetail(
                "Full Trend Alignment", EntrySignal.BUY, 12,
                "Price above SMA20 & SMA50, golden cross — strong bullish trend."
            ))
        elif above_50 and golden:
            signals.append(EntrySignalDetail(
                "Trend Aligned (minor dip)", EntrySignal.BUY, 8,
                "Price above SMA50 with golden cross — bullish, minor pullback to SMA20."
            ))
        elif above_50:
            signals.append(EntrySignalDetail(
                "Mixed Trend", EntrySignal.NEUTRAL, 0,
                "Price above SMA50 but no golden cross — neutral trend."
            ))
        else:
            signals.append(EntrySignalDetail(
                "Below Key SMAs", EntrySignal.WAIT, -8,
                "Price below SMA50 — bearish pressure, wait for recovery above SMA50."
            ))

        # ── 5. VIX / Implied Volatility ──────────────────────────
        vix = indicators.vix
        if vix < 15:
            signals.append(EntrySignalDetail(
                "VIX Very Low", EntrySignal.BUY, 5,
                f"VIX={vix:.1f} — calm market, cheap insurance, good for bullish entries."
            ))
        elif vix < 20:
            signals.append(EntrySignalDetail(
                "VIX Normal", EntrySignal.NEUTRAL, 2,
                f"VIX={vix:.1f} — normal volatility, no edge from vol."
            ))
        elif vix < 30:
            signals.append(EntrySignalDetail(
                "VIX Elevated", EntrySignal.NEUTRAL, 0,
                f"VIX={vix:.1f} — elevated vol, good for premium selling, caution for buying."
            ))
        else:
            signals.append(EntrySignalDetail(
                "VIX Spiking", EntrySignal.WAIT, -5,
                f"VIX={vix:.1f} — fear spike, wait for vol to stabilize before new entries."
            ))

        # ── 6. ATR-Based Proximity to Support ────────────────────
        atr = indicators.atr_14
        support_level = indicators.sma_50 - atr  # simple support estimate
        resistance_level = indicators.sma_50 + atr
        dist_to_support = (price - support_level) / atr if atr > 0 else 5

        if dist_to_support <= atr_near:
            signals.append(EntrySignalDetail(
                "Near Support", EntrySignal.STRONG_BUY, 15,
                f"Price within {atr_near} ATR of support (${support_level:.2f}) — high-probability bounce zone."
            ))
        elif dist_to_support <= atr_approach:
            signals.append(EntrySignalDetail(
                "Approaching Support", EntrySignal.BUY, 7,
                f"Price within {atr_approach} ATR of support (${support_level:.2f})."
            ))
        elif dist_to_support <= atr_mid:
            signals.append(EntrySignalDetail(
                "Mid-Range", EntrySignal.NEUTRAL, 0,
                f"Price in middle of ATR range, no proximity edge."
            ))
        else:
            signals.append(EntrySignalDetail(
                "Extended from Support", EntrySignal.WAIT, -7,
                f"Price {dist_to_support:.1f} ATRs from support — extended, higher risk of pullback."
            ))

        # ── 7. Earnings Proximity ─────────────────────────────────
        dte = indicators.days_to_earnings
        if dte is not None:
            if dte <= 7:
                signals.append(EntrySignalDetail(
                    "Earnings Imminent", EntrySignal.AVOID, -15,
                    f"Earnings in {dte} days — avoid new positions, vol crush risk."
                ))
            elif dte <= 21:
                signals.append(EntrySignalDetail(
                    "Earnings Approaching", EntrySignal.WAIT, -5,
                    f"Earnings in {dte} days — be cautious, IV expansion may distort pricing."
                ))
            else:
                signals.append(EntrySignalDetail(
                    "Earnings Distant", EntrySignal.NEUTRAL, 3,
                    f"Earnings in {dte} days — no impact on entry timing."
                ))
        else:
            signals.append(EntrySignalDetail(
                "No Earnings Data", EntrySignal.NEUTRAL, 0,
                "No earnings date available."
            ))

        # ── 8. Volume Confirmation ───────────────────────────────
        vol = indicators.volume
        vol_avg = indicators.volume_sma_20
        if vol_avg > 0:
            vol_ratio = vol / vol_avg
            if vol_ratio >= 1.5:
                signals.append(EntrySignalDetail(
                    "Volume Surge", EntrySignal.STRONG_BUY, 15,
                    f"Volume {vol_ratio:.1f}× average — strong institutional participation, confirms breakout."
                ))
            elif vol_ratio >= 1.1:
                signals.append(EntrySignalDetail(
                    "Above-Average Volume", EntrySignal.BUY, 8,
                    f"Volume {vol_ratio:.1f}× average — healthy participation supports the move."
                ))
            elif vol_ratio >= 0.8:
                signals.append(EntrySignalDetail(
                    "Normal Volume", EntrySignal.NEUTRAL, 0,
                    f"Volume {vol_ratio:.1f}× average — normal activity, no edge."
                ))
            elif vol_ratio >= 0.5:
                signals.append(EntrySignalDetail(
                    "Below-Average Volume", EntrySignal.WAIT, -5,
                    f"Volume {vol_ratio:.1f}× average — low participation, breakout may be unreliable."
                ))
            else:
                signals.append(EntrySignalDetail(
                    "Very Low Volume", EntrySignal.AVOID, -10,
                    f"Volume {vol_ratio:.1f}× average — thin liquidity, wide spreads likely. Avoid."
                ))
        else:
            signals.append(EntrySignalDetail(
                "No Volume Data", EntrySignal.NEUTRAL, 0,
                "Volume average not available."
            ))

        # ── Composite Score ───────────────────────────────────────
        raw_score = 50 + sum(s.score for s in signals)
        composite = max(0, min(100, raw_score))

        if composite >= 75:
            rec = EntrySignal.STRONG_BUY
            summary = f"🟢 **Excellent entry point** (score {composite}/100). Multiple technical signals align favorably."
        elif composite >= 60:
            rec = EntrySignal.BUY
            summary = f"🔵 **Good entry point** (score {composite}/100). Most signals are positive."
        elif composite >= 40:
            rec = EntrySignal.NEUTRAL
            summary = f"⚪ **Neutral** (score {composite}/100). Mixed signals — entry is acceptable but not ideal."
        elif composite >= 25:
            rec = EntrySignal.WAIT
            summary = f"🟡 **Consider waiting** (score {composite}/100). Several bearish signals — better entries may come."
        else:
            rec = EntrySignal.AVOID
            summary = f"🔴 **Avoid entry** (score {composite}/100). Technical signals are unfavorable."

        return EntryAnalysis(
            composite_score=composite,
            recommendation=rec,
            summary=summary,
            signals=signals,
            optimal_entry_price=round(support_level, 2),
            support_level=round(support_level, 2),
            resistance_level=round(resistance_level, 2),
        )

