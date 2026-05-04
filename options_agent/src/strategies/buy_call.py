"""Buy Call — intraday scalping strategy, buy ATM/slightly ITM calls on bullish breakout.

Optimized from 1-year backtest (SPY/AAPL 250 days, hourly + 5-min data):

  Signal rankings (1yr SPY):
    #1  Range Width vs ATR   68.8% WR  +$0.39 avg  ← NEW — best signal
    #2  Volume Surge         64.6% WR  +$0.28 avg
    #3  VIX Elevated         57.9% WR  +$0.37 avg
    #4  Gap Open             56.6% WR  +$0.00 avg  ← NEW
    #5  VWAP                 54.7% WR  +$0.05 avg
    #6  OR Candle            52.9% WR  −$0.02 avg
    ✗   EMA 9/21 Cross       46.7% WR  −$0.17 avg  ← NEGATIVE — fade it

  Execution rules (backtest-validated):
    - Stop at range midpoint (36.5% stop rate, well-calibrated)
    - T1 at 0.75R (39.6% hit rate), T2 at 1.5R (11.3% hit rate)
    - Time stop 3:00 PM (12.6% of trades)
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


class BuyCallStrategy(BaseStrategy):
    name = "buy_call"

    # ── Eligibility ──────────────────────────────────────────────

    def evaluate_eligibility(
        self,
        regime: MarketRegime,
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> bool:
        # For intraday timeframes, prefer the 5-min RSI (covers ~70 min,
        # matches what traders see on charts) over the primary timeframe RSI.
        rsi = indicators.rsi_5min if indicators.rsi_5min is not None else indicators.rsi_14

        if regime in (MarketRegime.TRENDING_BEARISH, MarketRegime.HIGH_VOL_BEARISH):
            if rsi < 30:
                return True  # oversold bounce play
            return False

        if rsi > 80:
            logger.info("Buy call ineligible: RSI %.1f too high", rsi)
            return False

        # ── Regime Guard: block calls during active sell-offs ──
        # When price is >1.5% below SMA20 AND falling (SMA20 < SMA50),
        # buying calls is fighting the trend — the March 2026 correction
        # showed this creates false breakout signals that get stopped out.
        if indicators.current_price < indicators.sma_20 * 0.985 and indicators.sma_20 < indicators.sma_50:
            logger.info(
                "Buy call blocked by regime guard: price $%.2f is %.1f%% below SMA20 $%.2f "
                "and SMA20 < SMA50 — active sell-off, calls likely to fail",
                indicators.current_price,
                (1 - indicators.current_price / indicators.sma_20) * 100,
                indicators.sma_20,
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
        calls = [c for c in chain if c.option_type == "call"]
        if not calls:
            raise ValueError(f"No suitable call contracts for {symbol}")

        # Higher delta (0.60) for better underlying tracking (backtest-validated)
        target = self._find_by_delta(calls, target_delta=0.60, tolerance=0.15)
        if target is None:
            target = min(calls, key=lambda c: abs(c.strike - indicators.current_price))

        premium_cost = target.mid * 100
        max_loss = premium_cost

        # ── Opening range analysis ──
        ora = OpeningRangeAnalyzer()
        orng = ora.analyze(indicators)

        # ── Recent 30-min momentum analysis ──
        rma = RecentMomentumAnalyzer()
        recent = rma.analyze(indicators)

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
        # Entry trigger: 10% inside range (catches near-misses like $648.38 vs $648.75)
        # ATR buffer: 0 (backtest showed 0.05 buffer was too aggressive, reducing fill quality)
        entry_trigger = round(active_high - range_width * 0.10, 2)
        entry_px = entry_trigger  # no additional ATR buffer

        # Stop at range midpoint (36.5% stop rate over 1yr — well-calibrated)
        range_mid = round((active_high + active_low) / 2, 2)
        stop_px = round(range_mid - atr * 0.02, 2)

        risk_per = round(abs(entry_px - stop_px), 2)
        if risk_per <= 0:
            risk_per = round(atr * 0.3, 2)

        # T1 at 0.75R (39.6% hit rate on 1yr), T2 at 1.5R (11.3% hit rate)
        t1 = round(entry_px + risk_per * 0.75, 2)
        t2 = round(entry_px + risk_per * 1.5, 2)

        max_profit = round(abs(target.greeks.delta) * risk_per * 0.75 * 100, 2)

        # ── 9-point quality score (shared with dashboard & backtester) ──
        quality = compute_quality_score(
            direction="buy_call",
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
            option_type="call",
            action="buy_to_open",
            quantity=1,
        )

        range_source_label = "recent 30-min" if range_source == "recent_30min" else "60-min opening"
        rationale_parts = [
            f"Buy {target.strike} call expiring {target.expiration} "
            f"(delta {target.greeks.delta:.2f}) for ${target.mid:.2f}.",
            f"Breakout trigger: stock above ${entry_trigger:.2f} "
            f"(90% of {range_source_label} high ${active_high:.2f}).",
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
            "1yr backtest: Calls 61.9% WR on SPY, T1 hit 39.6%, PF 1.06."
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

