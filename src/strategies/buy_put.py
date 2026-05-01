"""Buy Put — intraday scalping strategy, buy ATM/slightly ITM puts on bearish breakout.

Optimized from 1-year backtest (SPY/QQQ, hourly + 5-min data, 2026-04-20):

  Momentum signals (OpeningRangeAnalyzer — determines direction):
    Price vs range   ±5/±30  (±5 inside range, ±30 on breakout)
    Intraday RSI     ±20     (threshold 40/60)
    Intraday MACD    ±20     (threshold ±0.05, upweighted — consistent 55-57% WR)
    Price vs VWAP    ±20     (always-on, upweighted — reliable directional)
    Volume surge     ±5      (directional with momentum)
    OR candle dir    ±10     (body > 30% of range)
    VIX context      ±5      (VIX > 20, directional)

  Quality signals (6-point score, require ≥ 2 to trade):
    #1  Range Width vs ATR > 1.2   68.8% WR  +$0.39 avg  ← best signal
    #2  Volume Surge               64.6% WR  +$0.28 avg
    #3  VIX Elevated > 18          57.9% WR  +$0.37 avg
    #4  Gap Open confirms          56.6% WR
    #5  VWAP confirms direction    54.7% WR
    #6  OR Candle confirms         52.9% WR
    ✗   EMA 9/21 Cross disagrees → quality -1  (46.7% WR = negative)

  Execution rules (backtest-validated):
    - Stop at range midpoint (36.5% stop rate, well-calibrated)
    - T1 at 0.75R (39.6% hit rate), T2 at 1.5R (11.3% hit rate)
    - Time stop 3:00 PM (12.6% of trades)
    - Puts excel in corrections (63.6% WR short-term)
    - Optimized weights: combined +$47.30, SPY 59.1% WR, QQQ 55.2% WR

Risk: Premium paid (defined risk). Time decay works against you.
"""

from __future__ import annotations

import logging

from src.models.market_data import MarketIndicators, MarketRegime
from src.models.options import OptionContract, OptionLeg, OptionOrder
from src.models.portfolio import PortfolioSummary
from src.strategies.base_strategy import BaseStrategy
from src.opening_range import OpeningRangeAnalyzer
from src.recent_momentum import RecentMomentumAnalyzer
from src.utils.quality_scorer import compute_quality_score
from src.momentum_cascade import MomentumCascadeDetector

logger = logging.getLogger(__name__)


class BuyPutStrategy(BaseStrategy):
    name = "buy_put"

    # ── Eligibility ──────────────────────────────────────────────

    def evaluate_eligibility(
        self,
        regime: MarketRegime,
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> bool:
        # For intraday timeframes, prefer the 5-min RSI (covers ~70 min,
        # matches what traders see on charts) over the primary timeframe RSI
        # which can be hyper-sensitive on 15-min candles.
        rsi = indicators.rsi_5min if indicators.rsi_5min is not None else indicators.rsi_14

        if regime in (MarketRegime.LOW_VOL_BULLISH,):
            if rsi > 70:
                return True  # overbought reversal play
            return False

        # RSI < 20 "bounce likely" guard: on daily/weekly, extreme oversold
        # suggests a bounce. On intraday with 5-min RSI, the threshold is
        # still meaningful but we use the less noisy 5-min RSI value.
        if rsi < 20:
            logger.info("Buy put ineligible: RSI %.1f extremely oversold, bounce likely", rsi)
            return False

        # ── Regime Guard: block puts near correction bottoms (V-reversal trap) ──
        # When price is >3% below SMA20 AND >2% below SMA50, the sell-off is
        # extended and a bounce is likely. Buying puts here catches you in
        # V-reversals (e.g., March 2026: SPY dropped to $635, then snapped
        # back to $712). The further below the SMAs, the higher bounce risk.
        if (indicators.current_price < indicators.sma_20 * 0.97
                and indicators.current_price < indicators.sma_50 * 0.98):
            logger.info(
                "Buy put blocked by regime guard: price $%.2f is %.1f%% below SMA20 $%.2f "
                "and %.1f%% below SMA50 $%.2f — extended sell-off, bounce likely",
                indicators.current_price,
                (1 - indicators.current_price / indicators.sma_20) * 100,
                indicators.sma_20,
                (1 - indicators.current_price / indicators.sma_50) * 100,
                indicators.sma_50,
            )
            return False

        return True

    # ── Order Construction ───────────────────────────────────────

    def construct_order(
        self,
        symbol: str,
        chain: list[OptionContract],
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> OptionOrder:
        # Filter by DTE: use whatever's in the chain (dashboard controls the range)
        # If chain already has only 0DTE, this keeps only 0DTE
        puts = [c for c in chain if c.option_type == "put"]
        if not puts:
            raise ValueError(f"No suitable put contracts for {symbol}")

        # Higher delta (0.55) for better tracking of breakdown
        target = self._find_by_delta(puts, target_delta=0.55, tolerance=0.15)
        if target is None:
            target = min(puts, key=lambda c: abs(c.strike - indicators.current_price))

        premium_cost = target.mid * 100
        max_loss = premium_cost

        # ── Opening range analysis ──
        ora = OpeningRangeAnalyzer()
        orng = ora.analyze(indicators)

        # ── Recent 30-min momentum analysis ──
        rma = RecentMomentumAnalyzer()
        recent = rma.analyze(indicators)

        breakout_price = orng.range_low
        atr = indicators.atr_14

        # ── Use most recent range for breakout levels ──
        # The 60-min opening range is stale by afternoon. Use the recent 30-min
        # window (high/low) for tighter, more relevant breakout triggers while
        # keeping the 60-min range for regime/quality scoring.
        recent_range_valid = (recent.window_high > recent.window_low > 0)
        if recent_range_valid:
            active_high = recent.window_high
            active_low = recent.window_low
            range_source = "recent_30min"
        else:
            active_high = orng.range_high
            active_low = orng.range_low
            range_source = "opening_60min"

        range_width = active_high - active_low

        # ── BACKTEST-OPTIMIZED LEVELS ──
        # Entry trigger: 10% inside range (catches near-misses)
        # ATR buffer: 0 (backtest showed 0.05 buffer too aggressive)
        entry_trigger = round(active_low + range_width * 0.10, 2)
        entry_px = entry_trigger  # no additional ATR buffer

        # Stop at range midpoint (36.5% stop rate — well-calibrated)
        range_mid = round((active_high + active_low) / 2, 2)
        stop_px = round(range_mid + atr * 0.02, 2)

        risk_per = round(abs(stop_px - entry_px), 2)
        if risk_per <= 0:
            risk_per = round(atr * 0.3, 2)

        # T1 at 0.75R, T2 at 1.5R
        t1 = round(entry_px - risk_per * 0.75, 2)
        t2 = round(entry_px - risk_per * 1.5, 2)

        max_profit = round(abs(target.greeks.delta) * risk_per * 0.75 * 100, 2)

        # ── 9-point quality score (shared with dashboard & backtester) ──
        quality = compute_quality_score(
            direction="buy_put",
            current_price=indicators.current_price,
            sma_20=indicators.sma_20,
            sma_50=indicators.sma_50,
            vix=indicators.vix,
            volume=indicators.volume,
            volume_sma_20=indicators.volume_sma_20,
            or_direction=orng.breakout_direction.value,
            or_momentum=orng.momentum_score,
            or_confirmed=orng.breakout_confirmed,
            recent_dir=recent.direction,
            recent_momentum=recent.momentum_score,
            zlema_trend=getattr(indicators, 'zlema_trend', None),
        )

        # ── Momentum Cascade Detection (catches 5x–10x explosive moves) ──
        cascade_detector = MomentumCascadeDetector()
        cascade = cascade_detector.analyze(
            indicators,
            quality_score=quality.score,
            or_momentum=orng.momentum_score,
            recent_momentum=recent.momentum_score,
        )

        # ── Build rationale ──
        range_atr_ratio = range_width / atr if atr > 0 else 1.0

        leg = OptionLeg(
            symbol=target.symbol,
            strike=target.strike,
            expiration=target.expiration,
            option_type="put",
            action="buy_to_open",
            quantity=1,
        )

        range_source_label = "recent 30-min" if range_source == "recent_30min" else "60-min opening"
        rationale_parts = [
            f"Buy {target.strike} put expiring {target.expiration} "
            f"(delta {target.greeks.delta:.2f}) for ${target.mid:.2f}.",
            f"Breakdown trigger: stock below ${entry_trigger:.2f} "
            f"(90% of {range_source_label} low ${active_low:.2f}).",
            f"Active range ({range_source_label}): ${active_low:.2f}–${active_high:.2f} "
            f"(width: {range_atr_ratio:.1f}× ATR).",
            f"Stop: ${stop_px:.2f} (midpoint) | T1: ${t1:.2f} (0.75R) | T2: ${t2:.2f} (1.5R).",
            f"Quality: {quality.label} ({quality.score}/11 signals confirmed).",
        ]
        rationale_parts.extend(quality.confirmations)
        rationale_parts.extend(quality.cautions)
        rationale_parts.append(
            "Exit: Half at T1 → trail stop to breakeven → rest at T2. "
            "Time stop: 3:00 PM ET. "
            "1yr backtest: Puts excel in corrections (63.6% WR short-term). "
            "Best when VIX elevated + wide range + volume surge. "
            "Optimized weights: MACD ±20, VWAP ±20, PVR ±5."
        )

        # ── Cascade / Explosion Alert ──
        if cascade.explosion_score >= 4:
            rationale_parts.append(
                f"\n\n{cascade.urgency} Explosion Potential: {cascade.explosion_score}/10. "
                f"{'Momentum ACCELERATING. ' if cascade.acceleration_detected else ''}"
                f"{'Volume CLIMAX detected. ' if cascade.volume_climax else ''}"
                f"{'Multi-level CASCADE in progress. ' if cascade.cascade_breakdown else ''}"
            )
            if cascade.recommended_strike_offset > 0:
                rationale_parts.append(
                    f"💡 Consider {cascade.recommended_strike_offset} strike(s) OTM for higher leverage "
                    f"(cheaper premium, 5x–10x potential on explosive moves)."
                )

        return OptionOrder(
            strategy_name=self.name,
            underlying=symbol,
            legs=[leg],
            order_type="limit",
            limit_price=target.mid,
            duration="gfd",
            max_loss=max_loss,
            max_profit=max_profit,
            risk_reward_ratio=round(max_profit / max_loss, 3) if max_loss > 0 else 0,
            breakout_price=entry_trigger,
            opening_range_high=active_high,
            opening_range_low=active_low,
            entry_price=entry_px,
            stop_loss_price=stop_px,
            profit_target_1=t1,
            profit_target_2=t2,
            breakout_direction=orng.breakout_direction.value,
            rationale=" ".join(rationale_parts),
        )

    # ── Risk Calc ────────────────────────────────────────────────

    def calculate_max_loss(self, order: OptionOrder) -> float:
        return order.max_loss

