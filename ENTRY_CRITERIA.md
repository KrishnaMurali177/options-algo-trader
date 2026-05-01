# Entry Point Analysis — 8 Technical Criteria

The **Entry Analyzer** scores whether the current moment is a good time to enter an options trade. It produces a **composite score from 0–100** by starting at a base of 50 (neutral) and adding/subtracting points from 8 independent technical signals.

| Score Range | Recommendation | Meaning |
|-------------|---------------|---------|
| **75–100** | 🟢 Strong Buy | Excellent entry — multiple signals align favorably |
| **60–74** | 🔵 Buy | Good entry — most signals are positive |
| **40–59** | ⚪ Neutral | Acceptable but not ideal — mixed signals |
| **25–39** | 🟡 Wait | Several bearish signals — better entries likely ahead |
| **0–24** | 🔴 Avoid | Unfavorable — technical signals are against you |

---

## Criteria Overview

| # | Criteria | Max Impact | What It Measures |
|---|---------|-----------|-----------------|
| 1 | RSI Mean-Reversion | +18 / −15 | Momentum exhaustion (oversold/overbought) |
| 2 | Bollinger Band Position | +15 / −12 | Price relative to volatility envelope |
| 3 | MACD Momentum & Crossover | +12 / −10 | Trend momentum direction and strength |
| 4 | SMA Trend Alignment | +12 / −8 | Multi-timeframe trend direction |
| 5 | VIX / Implied Volatility | +5 / −5 | Market fear level and option pricing |
| 6 | ATR Proximity to Support | +15 / −7 | Distance to key support/resistance |
| 7 | Earnings Proximity | +3 / −15 | Days until next earnings report |
| 8 | Volume Confirmation | +15 / −10 | Trade participation and liquidity |

---

## Detailed Criteria

### 1. RSI Mean-Reversion

**What:** The 14-period Relative Strength Index measures momentum. Extreme readings suggest the price has moved too far, too fast and is likely to reverse.

**Why it matters for options:** Buying calls when RSI is oversold (or puts when overbought) gives you a statistical edge from mean-reversion. Chasing overbought momentum leads to overpaying for premium.

| Condition | Signal | Score | Description |
|-----------|--------|-------|-------------|
| RSI < oversold threshold | 🟢 Strong Buy | +18 | Deeply oversold — strong bounce candidate |
| RSI < approaching threshold | 🔵 Buy | +10 | Approaching oversold — favorable entry |
| RSI in neutral zone | ⚪ Neutral | 0 | No directional edge |
| RSI > elevated threshold | 🟡 Wait | −5 | Extended — consider waiting for pullback |
| RSI > overbought threshold | 🔴 Avoid | −15 | Overbought — don't chase; good for selling premium |

**Timeframe-adaptive thresholds:**

| Timeframe | Oversold | Approaching | Elevated | Overbought |
|-----------|----------|-------------|----------|------------|
| ⚡ 15-min / 1-hour | 35 | 45 | 55 | 65 |
| 📅 Daily | 30 | 40 | 60 | 70 |
| 📆 Weekly | 25 | 35 | 65 | 75 |

> Intraday RSI mean-reverts faster, so tighter bands are used. Weekly RSI extremes are rarer and more meaningful.

---

### 2. Bollinger Band Position

**What:** Measures where the current price sits within the 20-period Bollinger Bands (2 standard deviations). A value of 0% = at the lower band, 100% = at the upper band.

**Why it matters for options:** Price near the lower band often represents a high-probability bounce zone (good for buying calls). Price at the upper band means overextension (good for selling premium or buying puts).

| Condition | Signal | Score | Description |
|-----------|--------|-------|-------------|
| ≤ 15% (near lower band) | 🟢 Strong Buy | +15 | Potential bounce entry |
| ≤ 35% (lower zone) | 🔵 Buy | +8 | Price in lower portion of bands |
| 35–65% (mid-band) | ⚪ Neutral | 0 | No edge from band position |
| ≤ 85% (upper zone) | 🟡 Wait | −5 | Extended toward upper band |
| > 85% (near upper band) | 🔴 Avoid | −12 | Overextended — wait for pullback |

---

### 3. MACD Momentum & Crossover

**What:** The MACD histogram (MACD line minus signal line) shows the speed and direction of momentum. A bullish crossover (MACD crossing above signal) confirms upward momentum.

**Why it matters for options:** A confirmed bullish crossover with positive histogram means momentum is accelerating — ideal for call entries. Negative histogram warns momentum is fading.

| Condition | Signal | Score | Description |
|-----------|--------|-------|-------------|
| Histogram > 0, MACD > signal | 🔵 Buy | +12 | Bullish crossover confirmed |
| Histogram > 0 | 🔵 Buy | +5 | Mild bullish momentum |
| Histogram > −0.5 | ⚪ Neutral | −3 | Momentum fading |
| Histogram < −0.5 | 🟡 Wait | −10 | Bearish momentum — wait for reversal |

---

### 4. SMA Trend Alignment

**What:** Checks the relationship between price, SMA 20, SMA 50, and SMA 200. A "golden cross" (SMA 50 > SMA 200) is a long-term bullish signal.

**Why it matters for options:** Trading in the direction of the trend dramatically improves win rates. Full alignment (price > SMA20 > SMA50, golden cross) is the strongest bullish setup.

| Condition | Signal | Score | Description |
|-----------|--------|-------|-------------|
| Price > SMA20 & SMA50, golden cross | 🔵 Buy | +12 | Full trend alignment — strong bullish |
| Price > SMA50, golden cross | 🔵 Buy | +8 | Bullish with minor pullback to SMA20 |
| Price > SMA50, no golden cross | ⚪ Neutral | 0 | Mixed trend signals |
| Price < SMA50 | 🟡 Wait | −8 | Below key average — bearish pressure |

---

### 5. VIX / Implied Volatility

**What:** The VIX ("fear index") measures expected S&P 500 volatility over 30 days. It directly impacts option pricing — higher VIX = more expensive options.

**Why it matters for options:** Low VIX means cheaper premiums (good for buying calls/puts). High VIX means expensive premiums (good for selling, bad for buying). Spiking VIX signals panic — wait for stabilization.

| Condition | Signal | Score | Description |
|-----------|--------|-------|-------------|
| VIX < 15 | 🔵 Buy | +5 | Calm market — cheap options |
| VIX 15–20 | ⚪ Neutral | +2 | Normal volatility |
| VIX 20–30 | ⚪ Neutral | 0 | Elevated — caution for buying, good for selling |
| VIX > 30 | 🟡 Wait | −5 | Fear spike — wait for stabilization |

---

### 6. ATR Proximity to Support

**What:** Uses the Average True Range (14-period) to estimate support (SMA 50 − ATR) and resistance (SMA 50 + ATR) levels. Measures how many ATRs the price is from support.

**Why it matters for options:** Entering near support gives you a tight stop-loss and favorable risk/reward. Entering far from support means more downside risk if the trade goes against you.

| Condition | Signal | Score | Description |
|-----------|--------|-------|-------------|
| Within near threshold | 🟢 Strong Buy | +15 | High-probability bounce zone |
| Within approach threshold | 🔵 Buy | +7 | Approaching support |
| Within mid threshold | ⚪ Neutral | 0 | Mid-range, no edge |
| Beyond mid threshold | 🟡 Wait | −7 | Extended from support — pullback risk |

**Timeframe-adaptive ATR thresholds:**

| Timeframe | Near | Approach | Mid |
|-----------|------|----------|-----|
| ⚡ 15-min / 1-hour | 0.5 ATR | 1.0 ATR | 2.0 ATR |
| 📅 Daily | 1.0 ATR | 2.0 ATR | 3.5 ATR |
| 📆 Weekly | 1.5 ATR | 2.5 ATR | 4.0 ATR |

> Intraday moves are smaller, so tighter proximity thresholds are used.

---

### 7. Earnings Proximity

**What:** Days until the next earnings report for the underlying stock.

**Why it matters for options:** Earnings cause massive implied volatility (IV) expansion leading up to the event, then a sharp "IV crush" after. Holding short-dated options through earnings is extremely risky.

| Condition | Signal | Score | Description |
|-----------|--------|-------|-------------|
| > 21 days away | ⚪ Neutral | +3 | No impact on entry timing |
| 8–21 days away | 🟡 Wait | −5 | IV expansion may distort pricing |
| ≤ 7 days away | 🔴 Avoid | −15 | Vol crush risk — avoid new positions |
| No data | ⚪ Neutral | 0 | Cannot assess |

---

### 8. Volume Confirmation

**What:** Compares the current bar's trading volume to the 20-period average volume. Expressed as a ratio (e.g., 1.5× = 50% above average).

**Why it matters for options:**
- **Confirms breakouts** — a price move on high volume is more likely to follow through (critical for intraday scalps with buy_call / buy_put)
- **Ensures liquidity** — high underlying volume correlates with tighter option bid-ask spreads and better fills
- **Filters false signals** — breakouts on low volume are often traps that reverse quickly

| Condition | Signal | Score | Description |
|-----------|--------|-------|-------------|
| ≥ 1.5× average | 🟢 Strong Buy | +15 | Volume surge — institutional participation confirms move |
| ≥ 1.1× average | 🔵 Buy | +8 | Healthy participation supports the move |
| 0.8–1.1× average | ⚪ Neutral | 0 | Normal activity, no edge |
| 0.5–0.8× average | 🟡 Wait | −5 | Low participation — breakout may fail |
| < 0.5× average | 🔴 Avoid | −10 | Very thin liquidity — wide spreads, poor fills |

---

## How the Score is Calculated

```
composite_score = clamp(50 + Σ signal_scores, 0, 100)
```

1. Start at **50** (neutral baseline).
2. Each of the 8 criteria adds or subtracts its score.
3. Clamp the result to **0–100**.

**Theoretical range:**
- **Maximum possible:** 50 + 18 + 15 + 12 + 12 + 5 + 15 + 3 + 15 = **145** → clamped to **100**
- **Minimum possible:** 50 + (−15) + (−12) + (−10) + (−8) + (−5) + (−7) + (−15) + (−10) = **−32** → clamped to **0**

In practice, scores between 30–80 are most common.

---

## Additional Outputs

Beyond the composite score, the analyzer provides:

| Output | Description |
|--------|-------------|
| **Optimal Entry Price** | Estimated support level (SMA 50 − ATR) — place limit orders here |
| **Support Level** | SMA 50 − ATR |
| **Resistance Level** | SMA 50 + ATR |
| **Per-Signal Breakdown** | Each criterion's name, score, signal, and description |
| **Bullish / Caution Count** | How many signals are bullish vs cautionary |

