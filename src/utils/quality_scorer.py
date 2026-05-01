"""Shared 11-point quality scorer for Buy Call / Buy Put strategies.

Used by:
  - dashboard/app.py (auto-selection between strategies)
  - src/strategies/buy_call.py (order construction quality gate)
  - src/strategies/buy_put.py  (order construction quality gate)
  - src/backtester.py          (backtest quality filter)

Scoring criteria (max 11, min 0 after penalties):

  Opening Range (60-min):
    #1  Breakout direction aligned   +2  (or −1 if against)
    #2  Breakout confirmed (|M|≥40)  +1

  Recent 30-Min Momentum:
    #3  Recent direction aligned     +2  (or −1 if against)

  Daily Indicators:
    #4  Volume surge (V/SMA20 ≥ 1.2) +1
    #5  VIX elevated (> 18)          +1
    #6  VWAP confirmation            +1
    #7  Trend alignment (SMA20 vs 50)+1

  Momentum Acceleration (catches 5x+ explosive moves):
    #8  Dual momentum alignment      +1  (both OR and recent strongly agree)
    #9  Volume climax (V/SMA20 ≥ 2)  +1  (institutions piling in)

  Advanced Indicators:
    #10 ZLEMA trend confirmation     +1  (Zero-Lag EMA cross aligned with direction)
    #11 VPVR level break             +1  (price broke through High Volume Node)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class QualityResult:
    """Result of the 11-point quality scoring."""
    score: int               # 0-11
    label: str               # "🟢 HIGH", "🔵 MEDIUM", "🟡 LOW"
    confirmations: list[str] # human-readable confirmation descriptions
    cautions: list[str]      # human-readable caution descriptions


def compute_quality_score(
    *,
    direction: str,           # "buy_call" or "buy_put"
    current_price: float,
    sma_20: float,
    sma_50: float,
    vix: float,
    volume: float,
    volume_sma_20: float,
    or_direction: str,        # "bullish", "bearish", "neutral"
    or_momentum: int,         # -100 to +100
    or_confirmed: bool,       # |momentum| >= 40
    recent_dir: str,          # "bullish", "bearish", "neutral"
    recent_momentum: int,     # -100 to +100
    zlema_trend: str | None = None,   # "bullish", "bearish", "neutral"
    vpvr_level_broken: bool = False,  # True if price broke a High Volume Node
) -> QualityResult:
    """Compute quality score (0-11) combining opening range + recent momentum + daily signals."""
    score = 0
    confirmations: list[str] = []
    cautions: list[str] = []

    # ── Opening Range Signals (60-min, 9:30–10:30) ──────────────

    # #1 Breakout direction alignment (STRONGEST signal for scalping, +2)
    if direction == "buy_call" and or_direction == "bullish":
        score += 2
        confirmations.append(f"✅ 60-min breakout is BULLISH (momentum {or_momentum:+d}) — aligned with Buy Call (+2)")
    elif direction == "buy_put" and or_direction == "bearish":
        score += 2
        confirmations.append(f"✅ 60-min breakout is BEARISH (momentum {or_momentum:+d}) — aligned with Buy Put (+2)")
    elif or_direction == "neutral":
        cautions.append(f"⚠️ 60-min breakout is NEUTRAL (momentum {or_momentum:+d}) — no directional edge")
    else:
        score -= 1
        cautions.append(f"🚫 60-min breakout is {or_direction.upper()} — AGAINST {direction.replace('_', ' ')} (−1)")

    # #2 Breakout confirmed (|momentum| >= 40, +1)
    if or_confirmed:
        if (direction == "buy_call" and or_momentum > 0) or (direction == "buy_put" and or_momentum < 0):
            score += 1
            confirmations.append(f"✅ Breakout CONFIRMED (|momentum|={abs(or_momentum)} ≥ 40) (+1)")
        else:
            cautions.append(f"⚠️ Breakout confirmed but in wrong direction (momentum {or_momentum:+d})")
    else:
        cautions.append(f"⏳ Breakout not yet confirmed (|momentum|={abs(or_momentum)} < 40)")

    # ── Recent 30-Min Momentum ──────────────────────────────────

    # #3 Recent direction alignment (+2, or −1 if against)
    if direction == "buy_call" and recent_dir == "bullish":
        score += 2
        confirmations.append(f"✅ Recent 30-min momentum is BULLISH ({recent_momentum:+d}) — aligned (+2)")
    elif direction == "buy_put" and recent_dir == "bearish":
        score += 2
        confirmations.append(f"✅ Recent 30-min momentum is BEARISH ({recent_momentum:+d}) — aligned (+2)")
    elif recent_dir == "neutral":
        cautions.append(f"⚠️ Recent 30-min momentum is NEUTRAL ({recent_momentum:+d}) — no current edge")
    else:
        score -= 1
        cautions.append(f"🚫 Recent 30-min momentum is {recent_dir.upper()} — AGAINST {direction.replace('_', ' ')} (−1)")

    # ── Daily Indicator Signals ─────────────────────────────────

    # #4 Volume surge (64.6% WR)
    vol_ratio = volume / volume_sma_20 if volume_sma_20 > 0 else 0
    if vol_ratio >= 1.2:
        score += 1
        confirmations.append(f"✅ Volume surge ({vol_ratio:.1f}× avg ≥ 1.2) — confirms participation (+1)")
    else:
        cautions.append(f"⚠️ Volume normal ({vol_ratio:.1f}× avg) — no surge")

    # #5 VIX elevated (57.9% WR)
    if vix > 18:
        score += 1
        confirmations.append(f"✅ VIX elevated ({vix:.1f} > 18) — wider ranges, better R:R (+1)")

    # #6 VWAP confirmation (54.7% WR)
    if direction == "buy_call" and current_price > sma_20:
        score += 1
        confirmations.append(f"✅ Above VWAP (${current_price:.2f} > SMA20 ${sma_20:.2f}) — buyers in control (+1)")
    elif direction == "buy_put" and current_price < sma_20:
        score += 1
        confirmations.append(f"✅ Below VWAP (${current_price:.2f} < SMA20 ${sma_20:.2f}) — sellers in control (+1)")
    else:
        side = "above" if current_price > sma_20 else "below"
        cautions.append(f"⚠️ Price {side} VWAP — against {direction.replace('_', ' ')} direction")

    # #7 Trend alignment (SMA20 vs SMA50)
    if direction == "buy_call" and sma_20 > sma_50:
        score += 1
        confirmations.append(f"✅ Trend aligned: SMA20 (${sma_20:.0f}) > SMA50 (${sma_50:.0f}) (+1)")
    elif direction == "buy_put" and sma_20 < sma_50:
        score += 1
        confirmations.append(f"✅ Trend aligned: SMA20 (${sma_20:.0f}) < SMA50 (${sma_50:.0f}) (+1)")
    else:
        cautions.append(f"⚠️ Trend not aligned (SMA20=${sma_20:.0f}, SMA50=${sma_50:.0f})")

    # ── Momentum Acceleration Signals (catch 5x+ explosive moves) ──

    # #8 Dual momentum alignment: both OR and recent strongly agree
    both_bearish = or_momentum <= -40 and recent_momentum <= -40
    both_bullish = or_momentum >= 40 and recent_momentum >= 40
    if (direction == "buy_call" and both_bullish) or (direction == "buy_put" and both_bearish):
        score += 1
        dir_label = "BULLISH" if both_bullish else "BEARISH"
        confirmations.append(
            f"🔥 Dual momentum: OR ({or_momentum:+d}) + recent ({recent_momentum:+d}) both strongly {dir_label} (+1)"
        )

    # #9 Volume climax: institutional participation spike (2x+ average)
    if vol_ratio >= 2.0:
        score += 1
        confirmations.append(
            f"🔥 Volume CLIMAX ({vol_ratio:.1f}× avg ≥ 2.0) — institutions piling in, explosive move likely (+1)"
        )

    # ── Advanced Indicators ────────────────────────────────────────

    # #10 ZLEMA trend confirmation (Zero-Lag EMA crossover aligned with trade direction)
    if zlema_trend:
        if (direction == "buy_call" and zlema_trend == "bullish") or (direction == "buy_put" and zlema_trend == "bearish"):
            score += 1
            confirmations.append(
                f"✅ ZLEMA trend {zlema_trend.upper()} — Zero-Lag EMA confirms direction (+1)"
            )
        elif (direction == "buy_call" and zlema_trend == "bearish") or (direction == "buy_put" and zlema_trend == "bullish"):
            cautions.append(
                f"⚠️ ZLEMA trend {zlema_trend.upper()} — against {direction.replace('_', ' ')}"
            )

    # #11 VPVR level break (price broke through a High Volume Node S/R level)
    if vpvr_level_broken:
        score += 1
        confirmations.append(
            f"🔥 VPVR level broken — price broke through High Volume Node, momentum likely to continue (+1)"
        )

    # ── Regime Guard Penalty ──────────────────────────────────────
    # Penalize trades that fight an extended trend (backtest-validated):
    #   - Buy Call when price is >1.5% below SMA20 and SMA20 < SMA50 (active sell-off)
    #   - Buy Put when price is >3% below SMA20 and >2% below SMA50 (V-reversal trap)
    price_vs_sma20_pct = (current_price - sma_20) / sma_20 if sma_20 > 0 else 0

    if direction == "buy_call" and price_vs_sma20_pct < -0.015 and sma_20 < sma_50:
        score -= 2
        cautions.append(
            f"🛑 Regime guard: price {price_vs_sma20_pct*100:.1f}% below SMA20 with SMA20 < SMA50 "
            f"— active sell-off, calls likely to fail (−2)"
        )
    elif direction == "buy_put" and price_vs_sma20_pct < -0.03 and current_price < sma_50 * 0.98:
        score -= 2
        cautions.append(
            f"🛑 Regime guard: price {price_vs_sma20_pct*100:.1f}% below SMA20 "
            f"— extended sell-off, V-reversal bounce likely (−2)"
        )

    score = max(0, score)
    label = "🟢 HIGH" if score >= 7 else "🔵 MEDIUM" if score >= 4 else "🟡 LOW"

    return QualityResult(
        score=score,
        label=label,
        confirmations=confirmations,
        cautions=cautions,
    )

