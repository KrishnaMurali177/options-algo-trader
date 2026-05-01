"""Naked Call — sell OTM calls to collect premium without owning shares.

This is a bearish-to-neutral premium-selling strategy. You sell an OTM call
and profit if the stock stays below the strike at expiration. Best when
implied volatility is elevated (juicy premiums) and you expect the underlying
to stay flat or decline.

Example: QQQ at $620, sell the $632 call at $1.10 — keep the full $110 premium
if QQQ stays below $632 by expiration.

Risk: UNLIMITED upside risk if the stock rallies past the strike. This is the
highest-risk single-leg strategy. Use strict position sizing and stop-losses.

Eligibility:
  - Range-bound or bearish regime (elevated VIX helps premiums)
  - Sufficient margin/buying power
  - RSI not deeply oversold (avoids selling calls into a bounce)
"""

from __future__ import annotations

import logging

from src.models.market_data import MarketIndicators, MarketRegime
from src.models.options import OptionContract, OptionLeg, OptionOrder
from src.models.portfolio import PortfolioSummary
from src.strategies.base_strategy import BaseStrategy
from config.settings import settings

logger = logging.getLogger(__name__)


class NakedCallStrategy(BaseStrategy):
    name = "naked_call"

    # Delta targets for different vol regimes
    _DELTA_LOW_VOL = 0.20    # farther OTM when vol is low (less premium)
    _DELTA_HIGH_VOL = 0.15   # farther OTM when vol is high (plenty of premium)
    _DELTA_DEFAULT = 0.18

    # ── Eligibility ──────────────────────────────────────────────

    def evaluate_eligibility(
        self,
        regime: MarketRegime,
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> bool:
        # Best in range-bound / bearish / high-vol — when you expect
        # the stock NOT to rip higher
        if regime not in (
            MarketRegime.RANGE_BOUND_HV,
            MarketRegime.HIGH_VOL_BEARISH,
            MarketRegime.TRENDING_BEARISH,
            MarketRegime.LOW_VOL_NEUTRAL,
        ):
            return False

        # Don't sell calls when RSI < 30 (deep oversold = likely bounce)
        if indicators.rsi_14 < 30:
            logger.info("Naked call ineligible: RSI %.1f too low, bounce risk", indicators.rsi_14)
            return False

        # Need margin/buying power — rough estimate: 20% of underlying × 100
        margin_needed = indicators.current_price * 0.20 * 100
        if portfolio.account.buying_power < margin_needed:
            logger.info(
                "Naked call ineligible: need ~$%.0f margin, have $%.0f",
                margin_needed, portfolio.account.buying_power,
            )
            return False

        return True

    # ── Entry Point Scoring (call-selling specific) ──────────────

    @staticmethod
    def score_entry(indicators: MarketIndicators) -> dict:
        """
        Score how good the current moment is for selling a naked call.
        Returns a dict with score (0-100), signals, and suggested strike.

        Best entries:
          - RSI overbought (>65) — stock extended, likely to pull back
          - Price near upper Bollinger — resistance zone
          - VIX elevated (>20) — fat premiums
          - MACD histogram turning negative — momentum fading
          - Price at or above resistance (SMA50 + ATR)
        """
        signals = []
        base = 50

        # 1. RSI — overbought = great for call selling
        rsi = indicators.rsi_14
        if rsi >= 75:
            signals.append(("RSI Overbought", +18, f"RSI={rsi:.1f} — stock very extended, ideal for selling calls"))
        elif rsi >= 65:
            signals.append(("RSI Elevated", +12, f"RSI={rsi:.1f} — momentum stretched, good for call selling"))
        elif rsi >= 55:
            signals.append(("RSI Neutral-High", +4, f"RSI={rsi:.1f} — acceptable for call selling"))
        elif rsi >= 40:
            signals.append(("RSI Neutral", 0, f"RSI={rsi:.1f} — no edge"))
        else:
            signals.append(("RSI Low", -12, f"RSI={rsi:.1f} — stock may bounce, risky to sell calls"))

        # 2. Bollinger Band — selling calls when price at upper band
        price = indicators.current_price
        bb_range = indicators.bb_upper - indicators.bb_lower
        bb_pct = (price - indicators.bb_lower) / bb_range if bb_range > 0 else 0.5

        if bb_pct >= 0.85:
            signals.append(("Upper Bollinger", +15, f"Price at {bb_pct:.0%} of bands — resistance, excellent for selling calls"))
        elif bb_pct >= 0.65:
            signals.append(("Above Mid-Band", +7, f"Price at {bb_pct:.0%} — above average, decent for call selling"))
        elif bb_pct >= 0.40:
            signals.append(("Mid-Band", 0, f"Price at {bb_pct:.0%} — neutral"))
        else:
            signals.append(("Lower Band", -10, f"Price at {bb_pct:.0%} — too low to sell calls, bounce likely"))

        # 3. VIX — higher = fatter premiums
        vix = indicators.vix
        if vix >= 30:
            signals.append(("VIX High", +15, f"VIX={vix:.1f} — very fat premiums, excellent for selling"))
        elif vix >= 22:
            signals.append(("VIX Elevated", +10, f"VIX={vix:.1f} — good premiums"))
        elif vix >= 16:
            signals.append(("VIX Normal", +2, f"VIX={vix:.1f} — adequate premiums"))
        else:
            signals.append(("VIX Low", -8, f"VIX={vix:.1f} — thin premiums, less attractive"))

        # 4. MACD — fading momentum = good
        hist = indicators.macd_histogram
        if hist < -0.5:
            signals.append(("MACD Bearish", +10, f"MACD histogram={hist:.3f} — bearish momentum supports call selling"))
        elif hist < 0:
            signals.append(("MACD Turning", +5, f"MACD histogram={hist:.3f} — momentum fading"))
        elif hist < 0.5:
            signals.append(("MACD Mild Bullish", -3, f"MACD histogram={hist:.3f} — mild upward momentum, be cautious"))
        else:
            signals.append(("MACD Strong Bullish", -12, f"MACD histogram={hist:.3f} — strong momentum against call selling"))

        # 5. Price vs Resistance
        atr = indicators.atr_14
        resistance = indicators.sma_50 + atr
        dist_to_resistance = (resistance - price) / atr if atr > 0 else 5

        if dist_to_resistance <= 0:
            signals.append(("Above Resistance", +12, f"Price above resistance (${resistance:.2f}) — prime call-selling zone"))
        elif dist_to_resistance <= 1:
            signals.append(("Near Resistance", +7, f"Price within 1 ATR of resistance (${resistance:.2f})"))
        elif dist_to_resistance <= 2:
            signals.append(("Approaching Resistance", +2, f"Price within 2 ATR of resistance"))
        else:
            signals.append(("Far from Resistance", -5, f"Price {dist_to_resistance:.1f} ATRs below resistance — stock has room to run"))

        # Composite
        raw = base + sum(s[1] for s in signals)
        score = max(0, min(100, raw))

        # Suggested strike: current price + 1-2 ATR (depends on score)
        buffer_atrs = 2.0 if score < 50 else 1.5 if score < 70 else 1.0
        suggested_strike = round(price + buffer_atrs * atr, 0)

        return {
            "score": score,
            "signals": signals,
            "suggested_strike": suggested_strike,
            "resistance": round(resistance, 2),
            "buffer_atrs": buffer_atrs,
        }

    # ── Order Construction ───────────────────────────────────────

    def construct_order(
        self,
        symbol: str,
        chain: list[OptionContract],
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> OptionOrder:
        # Get entry scoring for strike guidance
        entry = self.score_entry(indicators)
        suggested_strike = entry["suggested_strike"]

        # Filter: calls, within configured DTE range
        calls = [c for c in chain if c.option_type == "call"]
        calls = self._filter_by_dte(calls, settings.min_dte, settings.max_dte)
        if not calls:
            raise ValueError(f"No suitable call contracts for {symbol}")

        # Select delta based on VIX environment
        vix = indicators.vix
        if vix >= 25:
            target_delta = self._DELTA_HIGH_VOL
        elif vix < 18:
            target_delta = self._DELTA_LOW_VOL
        else:
            target_delta = self._DELTA_DEFAULT

        # Find call near the target delta, preferring strike near suggested
        target = self._find_by_delta(calls, target_delta=target_delta)

        # Also look for a strike close to the suggested one
        near_suggested = min(
            [c for c in calls if c.strike >= indicators.current_price],
            key=lambda c: abs(c.strike - suggested_strike),
            default=None,
        )

        # Use whichever is farther OTM (safer) and still has decent premium
        if near_suggested and target:
            if near_suggested.strike > target.strike and near_suggested.mid >= 0.50:
                target = near_suggested  # farther OTM with enough premium

        if target is None:
            raise ValueError("No suitable OTM call found")

        premium = target.mid * 100  # per contract
        # Naked call max loss is theoretically unlimited, but for risk calc
        # we use a 2× current price catastrophic scenario
        catastrophic_price = indicators.current_price * 2
        max_loss = (catastrophic_price - target.strike) * 100 - premium
        max_profit = premium

        leg = OptionLeg(
            symbol=target.symbol,
            strike=target.strike,
            expiration=target.expiration,
            option_type="call",
            action="sell_to_open",
            quantity=1,
        )

        entry_note = (
            f"Entry score: {entry['score']}/100. "
            f"Resistance at ${entry['resistance']:.2f}. "
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
            rationale=(
                f"Sell {target.strike} call expiring {target.expiration} "
                f"(delta {target.greeks.delta:.2f}) for ${target.mid:.2f} premium. "
                f"{entry_note}"
                f"Profit if {symbol} stays below ${target.strike:.2f}. "
                f"⚠️ Unlimited upside risk — use stop-loss."
            ),
        )

    # ── Risk Calc ────────────────────────────────────────────────

    def calculate_max_loss(self, order: OptionOrder) -> float:
        return order.max_loss

