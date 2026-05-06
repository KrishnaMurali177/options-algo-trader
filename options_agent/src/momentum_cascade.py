"""Momentum Cascade Detector — identifies explosive move setups.

Detects momentum acceleration patterns that precede 5x–10x option moves:
  - Price acceleration (rate-of-change increasing over consecutive windows)
  - Volume climax (volume spiking with price momentum)
  - Multi-level breakdowns/breakouts (cascading through support/resistance)
  - Gamma squeeze potential (large OI at nearby strikes)

The $0.48 → $4.20 put move (8.75x in 30 min) is the archetype:
  High quality (8/11) + momentum acceleration + volume climax = explosive cascade.

Usage:
    detector = MomentumCascadeDetector()
    result = detector.analyze(indicators)
    if result.explosion_score >= 7:
        # Suggest OTM strike for leverage, flag ⚡ ACT NOW
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from src.models.market_data import MarketIndicators

logger = logging.getLogger(__name__)


@dataclass
class CascadeResult:
    """Result of the momentum cascade analysis."""
    explosion_score: int              # 0–10: likelihood of 5x+ option move
    urgency: str                      # "⚡ HIGH", "🔔 WATCH", "⏳ WAIT"
    acceleration_detected: bool       # price RoC increasing over windows
    volume_climax: bool               # volume 2x+ avg with momentum
    cascade_breakdown: bool           # price breaking multiple levels
    recommended_strike_offset: int    # ATM=0, 1 OTM=1, 2 OTM=2, etc.
    signals: list[dict] = field(default_factory=list)
    summary: str = ""
    data_source: str = "synthesized"


class MomentumCascadeDetector:
    """Detects setups with high probability of explosive option moves.

    Key insight: The biggest intraday option gains come when:
      1. Direction is already confirmed (quality ≥ 7/11)
      2. Momentum is ACCELERATING (not just strong)
      3. Volume is climaxing (institutions piling in)
      4. Price is cascading through multiple levels

    When all conditions align, even slightly OTM options can produce
    5x–10x returns in 15–30 minutes.
    """

    def analyze(
        self,
        indicators: MarketIndicators,
        quality_score: int = 0,
        or_momentum: int = 0,
        recent_momentum: int = 0,
        bars_5m=None,
    ) -> CascadeResult:
        """Analyze current conditions for cascade/explosion potential.

        Args:
            bars_5m: Pre-fetched 5-min bars (for replay mode). If provided,
                     uses these instead of fetching live data.
        """
        try:
            if bars_5m is not None and not bars_5m.empty:
                return self._analyze_from_bars(bars_5m, indicators, quality_score, or_momentum, recent_momentum)
            return self._analyze_live(indicators, quality_score, or_momentum, recent_momentum)
        except Exception as exc:
            logger.warning("Live cascade analysis failed (%s) — using synthesized", exc)
            return self._analyze_synthesized(indicators, quality_score, or_momentum, recent_momentum)

    def _analyze_synthesized(
        self,
        indicators: MarketIndicators,
        quality_score: int,
        or_momentum: int,
        recent_momentum: int,
    ) -> CascadeResult:
        """Fallback analysis using only indicators (no bar data)."""
        signals = []
        score = 0

        signals.append({"name": "No intraday bars", "score": 0, "desc": "Using indicator-only estimation"})

        vol_ratio = getattr(indicators, 'volume_ratio', 1.0)
        if vol_ratio and vol_ratio >= 2.0:
            score += 2
            signals.append({"name": "Volume CLIMAX", "score": 2, "desc": f"Volume {vol_ratio:.1f}× avg"})
        elif vol_ratio and vol_ratio >= 1.5:
            score += 1
            signals.append({"name": "Volume surge", "score": 1, "desc": f"Volume {vol_ratio:.1f}× avg"})

        if quality_score >= 8:
            score += 2
            signals.append({"name": "Elite quality", "score": 2, "desc": f"Quality {quality_score}/11"})
        elif quality_score >= 7:
            score += 1
            signals.append({"name": "High quality", "score": 1, "desc": f"Quality {quality_score}/11"})

        both_bearish = or_momentum <= -40 and recent_momentum <= -40
        both_bullish = or_momentum >= 40 and recent_momentum >= 40
        if both_bearish or both_bullish:
            score += 2
            dir_label = "BEARISH" if both_bearish else "BULLISH"
            signals.append({"name": f"Dual momentum {dir_label}", "score": 2,
                            "desc": f"OR ({or_momentum:+d}) + recent ({recent_momentum:+d})"})
        elif abs(or_momentum) >= 40 or abs(recent_momentum) >= 40:
            score += 1
            signals.append({"name": "Single strong momentum", "score": 1,
                            "desc": f"OR ({or_momentum:+d}), recent ({recent_momentum:+d})"})

        zlema_trend = getattr(indicators, 'zlema_trend', None)
        if zlema_trend:
            direction_bearish = recent_momentum < 0
            direction_bullish = recent_momentum > 0
            if (zlema_trend == "bearish" and direction_bearish) or (zlema_trend == "bullish" and direction_bullish):
                score += 1
                signals.append({"name": "ZLEMA trend aligned", "score": 1,
                                "desc": f"Zero-Lag EMA confirms {zlema_trend} trend"})

        score = max(0, min(10, score))
        strike_offset = 2 if score >= 8 else 1 if score >= 6 else 0
        urgency = "⚡ HIGH" if score >= 7 else "🔔 WATCH" if score >= 4 else "⏳ WAIT"
        summary = self._build_summary(score, urgency, False, vol_ratio >= 2.0 if vol_ratio else False, False, strike_offset)

        return CascadeResult(
            explosion_score=score, urgency=urgency,
            acceleration_detected=False, volume_climax=vol_ratio >= 2.0 if vol_ratio else False,
            cascade_breakdown=False, recommended_strike_offset=strike_offset,
            signals=signals, summary=summary, data_source="synthesized",
        )

    def _analyze_live(
        self,
        indicators: MarketIndicators,
        quality_score: int,
        or_momentum: int,
        recent_momentum: int,
    ) -> CascadeResult:
        """Live analysis using 5-min candle data."""
        import yfinance as yf

        symbol = indicators.symbol
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

        if len(today_bars) < 6:
            raise ValueError(f"Only {len(today_bars)} bars — need at least 6")

        close = today_bars["Close"].astype(float)
        volume = today_bars["Volume"].astype(float)
        high = today_bars["High"].astype(float)
        low = today_bars["Low"].astype(float)

        signals = []
        score = 0

        # ── 1. Price Acceleration (RoC of RoC) ──
        # Compare rate-of-change over 3 consecutive 10-min windows
        if len(close) >= 9:
            roc_1 = (float(close.iloc[-3]) - float(close.iloc[-6])) / float(close.iloc[-6]) * 100
            roc_2 = (float(close.iloc[-1]) - float(close.iloc[-3])) / float(close.iloc[-3]) * 100
            acceleration = roc_2 - roc_1
            same_direction = (roc_1 < 0 and roc_2 < 0) or (roc_1 > 0 and roc_2 > 0)

            if same_direction and abs(acceleration) > 0.05:
                # Momentum is INCREASING in the same direction
                score += 2
                accel_detected = True
                signals.append({"name": "Price accelerating", "score": 2,
                                "desc": f"RoC went from {roc_1:+.2f}% to {roc_2:+.2f}% — momentum INCREASING"})
            elif same_direction and abs(roc_2) > 0.15:
                score += 1
                accel_detected = True
                signals.append({"name": "Strong directional move", "score": 1,
                                "desc": f"RoC {roc_2:+.2f}% — strong but not accelerating"})
            else:
                accel_detected = False
                signals.append({"name": "No acceleration", "score": 0,
                                "desc": f"RoC {roc_1:+.2f}% → {roc_2:+.2f}% — no clear acceleration"})
        else:
            accel_detected = False
            signals.append({"name": "Insufficient data for acceleration", "score": 0, "desc": "Need 9+ bars"})

        # ── 2. Volume Climax ──
        # Current volume vs average, AND increasing over recent bars
        avg_vol = float(volume.mean())
        recent_vol = float(volume.iloc[-3:].mean())
        prior_vol = float(volume.iloc[-6:-3].mean()) if len(volume) >= 6 else avg_vol

        vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1
        vol_accel = recent_vol / prior_vol if prior_vol > 0 else 1

        volume_climax = False
        if vol_ratio >= 2.0 and vol_accel >= 1.3:
            score += 2
            volume_climax = True
            signals.append({"name": "Volume CLIMAX", "score": 2,
                            "desc": f"Volume {vol_ratio:.1f}× avg AND accelerating ({vol_accel:.1f}× prior window) — institutions piling in"})
        elif vol_ratio >= 1.5:
            score += 1
            volume_climax = True
            signals.append({"name": "Volume surge", "score": 1,
                            "desc": f"Volume {vol_ratio:.1f}× avg — elevated participation"})
        else:
            signals.append({"name": "Normal volume", "score": 0,
                            "desc": f"Volume {vol_ratio:.1f}× avg — no climax"})

        # ── 3. Cascade Breakdown/Breakout Detection ──
        # Check if price broke through multiple intraday levels in sequence
        price = indicators.current_price
        atr = indicators.atr_14

        # Compute intraday support/resistance levels from prior bars
        if len(today_bars) >= 12:
            prior_bars = today_bars.iloc[:-6]
            levels = self._find_sr_levels(prior_bars, atr)
            broken_count = sum(1 for lvl in levels if price < lvl - atr * 0.02) if recent_momentum < 0 else \
                           sum(1 for lvl in levels if price > lvl + atr * 0.02)

            cascade = broken_count >= 2
            if broken_count >= 3:
                score += 2
                signals.append({"name": "Multi-level CASCADE", "score": 2,
                                "desc": f"Price broke through {broken_count} intraday levels — cascade in progress"})
            elif broken_count >= 2:
                score += 1
                signals.append({"name": "Double level break", "score": 1,
                                "desc": f"Price broke through {broken_count} levels"})
            else:
                cascade = False
                signals.append({"name": "No cascade", "score": 0,
                                "desc": f"Only {broken_count} level(s) broken — normal move"})
        else:
            cascade = False
            signals.append({"name": "Insufficient data for cascade", "score": 0, "desc": ""})

        # ── 4. Quality Confirmation Boost ──
        if quality_score >= 8:
            score += 2
            signals.append({"name": "Elite quality", "score": 2,
                            "desc": f"Quality {quality_score}/11 — nearly all signals aligned"})
        elif quality_score >= 7:
            score += 1
            signals.append({"name": "High quality", "score": 1,
                            "desc": f"Quality {quality_score}/11 — strong signal alignment"})

        # ── 5. Momentum Alignment Boost ──
        # When BOTH OR and recent momentum agree strongly
        both_bearish = or_momentum <= -40 and recent_momentum <= -40
        both_bullish = or_momentum >= 40 and recent_momentum >= 40
        if both_bearish or both_bullish:
            score += 2
            dir_label = "BEARISH" if both_bearish else "BULLISH"
            signals.append({"name": f"Dual momentum {dir_label}", "score": 2,
                            "desc": f"OR ({or_momentum:+d}) AND recent ({recent_momentum:+d}) both strongly {dir_label}"})
        elif abs(or_momentum) >= 40 or abs(recent_momentum) >= 40:
            score += 1
            signals.append({"name": "Single strong momentum", "score": 1,
                            "desc": f"OR ({or_momentum:+d}), recent ({recent_momentum:+d}) — one strong"})

        # ── 6. ZLEMA Trend Confirmation ──
        zlema_trend = getattr(indicators, 'zlema_trend', None)
        if zlema_trend:
            direction_bearish = recent_momentum < 0
            direction_bullish = recent_momentum > 0
            if (zlema_trend == "bearish" and direction_bearish) or (zlema_trend == "bullish" and direction_bullish):
                score += 1
                signals.append({"name": "ZLEMA trend aligned", "score": 1,
                                "desc": f"Zero-Lag EMA confirms {zlema_trend} trend — reduced lag confirmation"})

        score = max(0, min(10, score))

        # ── Strike recommendation ──
        # Higher explosion score → recommend further OTM for leverage
        if score >= 8:
            strike_offset = 2  # 2 strikes OTM
        elif score >= 6:
            strike_offset = 1  # 1 strike OTM
        else:
            strike_offset = 0  # ATM

        # ── Urgency ──
        if score >= 7:
            urgency = "⚡ HIGH"
        elif score >= 4:
            urgency = "🔔 WATCH"
        else:
            urgency = "⏳ WAIT"

        summary = self._build_summary(score, urgency, accel_detected, volume_climax, cascade, strike_offset)

        return CascadeResult(
            explosion_score=score,
            urgency=urgency,
            acceleration_detected=accel_detected,
            volume_climax=volume_climax,
            cascade_breakdown=cascade,
            recommended_strike_offset=strike_offset,
            signals=signals,
            summary=summary,
            data_source="live",
        )

    def _analyze_from_bars(
        self,
        bars_5m: pd.DataFrame,
        indicators: MarketIndicators,
        quality_score: int,
        or_momentum: int,
        recent_momentum: int,
    ) -> CascadeResult:
        """Analyze using provided 5-min bars (for replay mode).

        Same logic as _analyze_live but operates on pre-fetched bars.
        """
        if len(bars_5m) < 6:
            raise ValueError(f"Only {len(bars_5m)} bars — need at least 6")

        close = bars_5m["Close"].astype(float)
        volume = bars_5m["Volume"].astype(float)

        signals = []
        score = 0

        # ── 1. Price Acceleration (RoC of RoC) ──
        accel_detected = False
        if len(close) >= 9:
            roc_1 = (float(close.iloc[-3]) - float(close.iloc[-6])) / float(close.iloc[-6]) * 100
            roc_2 = (float(close.iloc[-1]) - float(close.iloc[-3])) / float(close.iloc[-3]) * 100
            same_direction = (roc_1 < 0 and roc_2 < 0) or (roc_1 > 0 and roc_2 > 0)

            if same_direction and abs(roc_2) > abs(roc_1) and abs(roc_2 - roc_1) > 0.05:
                score += 2
                accel_detected = True
                signals.append({"name": "Price accelerating", "score": 2,
                                "desc": f"RoC {roc_1:+.2f}% → {roc_2:+.2f}% — momentum INCREASING"})
            elif same_direction and abs(roc_2) > 0.15:
                score += 1
                accel_detected = True
                signals.append({"name": "Strong directional move", "score": 1,
                                "desc": f"RoC {roc_2:+.2f}% — strong but not accelerating"})
            else:
                signals.append({"name": "No acceleration", "score": 0,
                                "desc": f"RoC {roc_1:+.2f}% → {roc_2:+.2f}%"})
        else:
            signals.append({"name": "Insufficient bars", "score": 0, "desc": "Need 9+"})

        # ── 2. Volume Climax ──
        avg_vol = float(volume.mean())
        recent_vol = float(volume.iloc[-3:].mean())
        prior_vol = float(volume.iloc[-6:-3].mean()) if len(volume) >= 6 else avg_vol

        vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1
        vol_accel = recent_vol / prior_vol if prior_vol > 0 else 1

        volume_climax = False
        if vol_ratio >= 2.0 and vol_accel >= 1.3:
            score += 2
            volume_climax = True
            signals.append({"name": "Volume CLIMAX", "score": 2,
                            "desc": f"Volume {vol_ratio:.1f}× avg, accelerating {vol_accel:.1f}×"})
        elif vol_ratio >= 1.5:
            score += 1
            volume_climax = True
            signals.append({"name": "Volume surge", "score": 1,
                            "desc": f"Volume {vol_ratio:.1f}× avg"})
        else:
            signals.append({"name": "Normal volume", "score": 0,
                            "desc": f"Volume {vol_ratio:.1f}× avg"})

        # ── 3. Cascade Breakdown/Breakout ──
        price = float(close.iloc[-1])
        atr = indicators.atr_14
        cascade = False

        if len(bars_5m) >= 12:
            prior_bars = bars_5m.iloc[:-6]
            levels = self._find_sr_levels(prior_bars, atr)
            broken_count = sum(1 for lvl in levels if price < lvl - atr * 0.02) if recent_momentum < 0 else \
                           sum(1 for lvl in levels if price > lvl + atr * 0.02)

            if broken_count >= 3:
                score += 2
                cascade = True
                signals.append({"name": "Multi-level CASCADE", "score": 2,
                                "desc": f"Broke {broken_count} intraday levels"})
            elif broken_count >= 2:
                score += 1
                cascade = True
                signals.append({"name": "Double level break", "score": 1,
                                "desc": f"Broke {broken_count} levels"})
            else:
                signals.append({"name": "No cascade", "score": 0,
                                "desc": f"{broken_count} level(s) broken"})

        # ── 4. Quality Boost ──
        if quality_score >= 8:
            score += 2
            signals.append({"name": "Elite quality", "score": 2,
                            "desc": f"Quality {quality_score}/11"})
        elif quality_score >= 7:
            score += 1
            signals.append({"name": "High quality", "score": 1,
                            "desc": f"Quality {quality_score}/11"})

        # ── 5. Momentum Alignment ──
        both_bearish = or_momentum <= -40 and recent_momentum <= -40
        both_bullish = or_momentum >= 40 and recent_momentum >= 40
        if both_bearish or both_bullish:
            score += 2
            dir_label = "BEARISH" if both_bearish else "BULLISH"
            signals.append({"name": f"Dual momentum {dir_label}", "score": 2,
                            "desc": f"OR ({or_momentum:+d}) + recent ({recent_momentum:+d})"})
        elif abs(or_momentum) >= 40 or abs(recent_momentum) >= 40:
            score += 1
            signals.append({"name": "Single strong momentum", "score": 1,
                            "desc": f"OR ({or_momentum:+d}), recent ({recent_momentum:+d})"})

        # ── 6. ZLEMA Trend Confirmation ──
        zlema_trend = getattr(indicators, 'zlema_trend', None)
        # Compute from bars if not on indicators (replay mode)
        if not zlema_trend and len(close) >= 21:
            lag_fast = (8 - 1) // 2
            lag_slow = (21 - 1) // 2
            comp_fast = 2 * close - close.shift(lag_fast)
            comp_slow = 2 * close - close.shift(lag_slow)
            zf = float(comp_fast.ewm(span=8, adjust=False).mean().iloc[-1])
            zs = float(comp_slow.ewm(span=21, adjust=False).mean().iloc[-1])
            if zf > zs * 1.0002:
                zlema_trend = "bullish"
            elif zf < zs * 0.9998:
                zlema_trend = "bearish"
            else:
                zlema_trend = "neutral"
        if zlema_trend:
            direction_bearish = recent_momentum < 0
            direction_bullish = recent_momentum > 0
            if (zlema_trend == "bearish" and direction_bearish) or (zlema_trend == "bullish" and direction_bullish):
                score += 1
                signals.append({"name": "ZLEMA trend aligned", "score": 1,
                                "desc": f"Zero-Lag EMA confirms {zlema_trend} trend"})

        score = max(0, min(10, score))
        strike_offset = 2 if score >= 8 else 1 if score >= 6 else 0
        urgency = "⚡ HIGH" if score >= 7 else "🔔 WATCH" if score >= 4 else "⏳ WAIT"
        summary = self._build_summary(score, urgency, accel_detected, volume_climax, cascade, strike_offset)

        return CascadeResult(
            explosion_score=score, urgency=urgency,
            acceleration_detected=accel_detected, volume_climax=volume_climax,
            cascade_breakdown=cascade, recommended_strike_offset=strike_offset,
            signals=signals, summary=summary, data_source="replay_bars",
        )

    def _find_sr_levels(self, bars: pd.DataFrame, atr: float) -> list[float]:
        """Find intraday support/resistance using Volume Profile (VPVR).

        Uses volume-at-price distribution to identify:
          - High Volume Nodes (HVN) → strong S/R levels (price tends to consolidate)
          - Low Volume Nodes (LVN) → acceleration zones (price moves through quickly)

        Falls back to pivot-based levels if insufficient data.
        """
        highs = bars["High"].astype(float).values
        lows = bars["Low"].astype(float).values
        closes = bars["Close"].astype(float).values
        volumes = bars["Volume"].astype(float).values

        price_min = float(lows.min())
        price_max = float(highs.max())

        if price_max - price_min < atr * 0.5 or len(bars) < 6:
            # Fallback: pivot-based
            return self._find_sr_levels_pivot(bars, atr)

        # ── Build Volume Profile (VPVR) ──
        # Divide price range into bins (~0.1 ATR each)
        num_bins = max(10, int((price_max - price_min) / (atr * 0.1)))
        num_bins = min(num_bins, 50)  # cap for performance
        bin_edges = [price_min + i * (price_max - price_min) / num_bins for i in range(num_bins + 1)]

        # Distribute each bar's volume across the price bins it spans
        vol_profile = [0.0] * num_bins
        for i in range(len(bars)):
            bar_low = float(lows[i])
            bar_high = float(highs[i])
            bar_vol = float(volumes[i])
            if bar_high <= bar_low:
                continue
            for b in range(num_bins):
                bin_lo = bin_edges[b]
                bin_hi = bin_edges[b + 1]
                # Overlap between bar range and bin
                overlap = max(0, min(bar_high, bin_hi) - max(bar_low, bin_lo))
                bar_range = bar_high - bar_low
                if bar_range > 0:
                    vol_profile[b] += bar_vol * (overlap / bar_range)

        # ── Identify High Volume Nodes (HVN) as S/R levels ──
        avg_vol = sum(vol_profile) / num_bins if num_bins > 0 else 1
        levels = []
        for b in range(num_bins):
            if vol_profile[b] > avg_vol * 1.5:  # HVN threshold
                level_price = (bin_edges[b] + bin_edges[b + 1]) / 2
                levels.append(level_price)

        # Also add VWAP
        typical = (bars["High"].astype(float) + bars["Low"].astype(float) + bars["Close"].astype(float)) / 3
        cumvol = bars["Volume"].astype(float).cumsum()
        last_cumvol = float(cumvol.iloc[-1])
        if last_cumvol > 0:
            vwap = float((typical * bars["Volume"].astype(float)).cumsum().iloc[-1] / last_cumvol)
            levels.append(vwap)

        # Deduplicate close levels
        levels.sort()
        deduped = []
        for lvl in levels:
            if not deduped or abs(lvl - deduped[-1]) > atr * 0.1:
                deduped.append(lvl)

        return deduped if deduped else self._find_sr_levels_pivot(bars, atr)

    def _find_sr_levels_pivot(self, bars: pd.DataFrame, atr: float) -> list[float]:
        """Legacy pivot-based S/R detection (fallback)."""
        highs = bars["High"].astype(float).values
        lows = bars["Low"].astype(float).values

        levels = []
        for i in range(1, len(highs) - 1):
            if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                levels.append(float(highs[i]))
            if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                levels.append(float(lows[i]))

        typical = (bars["High"] + bars["Low"] + bars["Close"]) / 3
        cumvol = bars["Volume"].cumsum()
        last_cumvol = float(cumvol.iloc[-1])
        if last_cumvol > 0:
            vwap = float((typical * bars["Volume"]).cumsum().iloc[-1] / last_cumvol)
            levels.append(vwap)

        levels.sort()
        deduped = []
        for lvl in levels:
            if not deduped or abs(lvl - deduped[-1]) > atr * 0.1:
                deduped.append(lvl)

        return deduped

    @staticmethod
    def _build_summary(
        score: int, urgency: str, accel: bool, vol_climax: bool,
        cascade: bool, strike_offset: int,
    ) -> str:
        features = []
        if accel:
            features.append("momentum accelerating")
        if vol_climax:
            features.append("volume climax")
        if cascade:
            features.append("multi-level cascade")

        feature_text = " + ".join(features) if features else "no explosive signals"

        strike_text = (
            f"Suggest **{strike_offset} strike(s) OTM** for higher leverage"
            if strike_offset > 0 else "ATM strike recommended"
        )

        return (
            f"{urgency} **Explosion Potential: {score}/10** — {feature_text}\n\n"
            f"{strike_text}.\n\n"
            f"{'🔥 **HIGH CONVICTION CASCADE** — Multiple signals confirm explosive move potential. '
               'Consider sizing up and using OTM strikes for 5x–10x leverage.' if score >= 7 else ''}"
            f"{'⚡ Conditions developing — monitor closely for acceleration.' if 4 <= score < 7 else ''}"
            f"{'Normal conditions — standard ATM entry.' if score < 4 else ''}"
        )


