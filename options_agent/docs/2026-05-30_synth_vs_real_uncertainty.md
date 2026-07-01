# Synth vs Real Pricing — Uncertainty Model

> **Version:** 2026-05-30 v2 (real Alpaca 5-min option bars)
> **Status:** 42/42 trades priced with real Alpaca option data. Fix: switched from 1-day bars (missing low-volume days) to 5-min bars with EOD close extraction.
> **Supersedes:** v1 (same date) which fell back to synth on all trades due to 1-day bar gap bug.
> **Next update:** Extend backtest to 3+ years. Run walk-forward OOS validation.

**Date:** 2026-05-30
**Config:** Sweep-optimal (Q4-8, DTE2-5, delta=0.40, stop=2.0, target=1.5, decay=1.5)
**Data:** 365 days, SPY (24 trades) + QQQ (18 trades)
**Real pricing:** 42/42 trades (100%) via Alpaca 5-min option bars

---

## 1. The Synth Model Overestimates P&L by 13x

| | Synth P&L | Real P&L | Overestimate |
|--|----------|----------|--------------|
| **SPY** | +$1,652 | **-$1,085** | Synth says profit, reality is a loss |
| **QQQ** | +$2,552 | **+$1,406** | 1.8x overestimate |
| **Combined** | +$4,204 | **+$319** | **13.2x** |

The synth model doesn't just inflate numbers — it **flips the sign** on SPY. What the synth
model calls a Sharpe 2.43 strategy is actually Sharpe -1.33 with real pricing.

---

## 2. Full Metrics: Synth vs Real

| Metric | Synth SPY | Real SPY | Synth QQQ | Real QQQ |
|--------|----------|---------|----------|---------|
| **Trades** | 24 | 24 | 18 | 18 |
| **Win Rate** | 62.5% | **37.5%** | 55.6% | **44.4%** |
| **Profit Factor** | 6.40 | **0.48** | 8.73 | **2.12** |
| **Total P&L** | +$1,652 | **-$1,085** | +$2,552 | **+$1,406** |
| **Sharpe** | 2.43 | **-1.33** | 2.04 | **0.99** |
| **Sortino** | 3.95 | -0.77 | 3.19 | 1.04 |
| **Calmar** | 13.11 | -0.88 | 10.88 | 2.45 |
| **Max Drawdown** | -$126 | **-$1,240** | -$234 | **-$574** |
| **Avg Win** | $131 | $111 | $288 | $332 |
| **Avg Loss** | -$34 | **-$139** | -$41 | **-$125** |

---

## 3. Trade-by-Trade Error Analysis

### Direction of Error

- **SPY:** Synth overestimates on all 24 trades. Zero exceptions.
- **QQQ:** Synth overestimates on 14/18 trades, underestimates on 4.
- **Combined:** 38/42 overestimate (90%), 4/42 underestimate (10%).

### Error Magnitude

| Metric | SPY | QQQ | Combined |
|--------|-----|-----|----------|
| Mean |error| | $114 | $110 | $92 |
| Median |error| | $114 | $104 | — |
| Max |error| | $227 | $228 | $228 |

### Winner/Loser Flips

| | SPY | QQQ | Combined |
|--|-----|-----|----------|
| W/L agreement | 18/24 (75%) | 14/18 (78%) | 32/42 (76%) |
| **Flips** | **6/24 (25%)** | **4/18 (22%)** | **10/42 (24%)** |

One in four trades flips between winner and loser when moving from synth to real pricing.
This is catastrophic for parameter optimization — the sweep was optimizing on wrong labels.

---

## 4. Where the Synth Model Breaks

### 4.1 Losses Are Massively Underestimated

The single biggest failure. Examples from SPY:

| Date | Exit | Synth P&L | Real P&L | Error |
|------|------|----------|---------|-------|
| 2025-09-23 | dte_expiry | -$15 | **-$199** | 13.3x undercount |
| 2025-12-29 | dte_expiry | -$24 | **-$172** | 7.2x |
| 2026-01-06 | dte_expiry | -$15 | **-$126** | 8.4x |
| 2025-11-03 | dte_expiry | -$60 | **-$257** | 4.3x |
| 2026-02-02 | stop_loss | -$51 | **-$274** | 5.4x |

The synth model's `-$15` floor on dte_expiry losers is an artifact of the theta decay model
capping losses at premium. But the real premium was much higher than synth estimated, so the
real loss is $100-$270, not $15-$60.

### 4.2 Winners Are Also Overestimated, But Less

| Date | Exit | Synth P&L | Real P&L | Error |
|------|------|----------|---------|-------|
| 2025-12-22 | decay_target | +$247 | +$222 | 11% over |
| 2025-07-08 | decay_target | +$255 | +$171 | 49% over |
| 2025-09-30 | dte_expiry | +$129 | +$93 | 39% over |

### 4.3 QQQ Has Cases Where Synth Underestimates

| Date | Exit | Synth P&L | Real P&L | Error |
|------|------|----------|---------|-------|
| 2025-07-28 | stagnation | -$5 | **+$159** | Synth says lose, real says big win |
| 2026-05-05 | decay_target | +$753 | **+$928** | 23% underestimate |
| 2026-05-04 | decay_target | +$416 | **+$480** | 15% underestimate |

The QQQ underestimates on winners suggest that QQQ weekly options have higher convexity
(gamma effect on big moves) that the fixed-delta model misses.

---

## 5. Why the Synth Model Fails This Badly

### 5.1 Entry Premium is Wrong

The synth formula `premium = ATR × delta × sqrt(DTE/5) × (VIX/20)` produces premiums of
$0.50-$2.00 for weekly OTM options. Real premiums for 40-delta SPY weeklies are typically
$3.00-$8.00. When the model underestimates entry premium by 3-5x:

- Max loss (capped at -premium) is 3-5x too small
- Theta decay per day (proportional to premium) is 3-5x too small
- The whole P&L scale is compressed

### 5.2 Fixed Delta Misses the Entire Gamma Curve

A 40-delta option that moves 1 ATR against you doesn't lose `0.40 × ATR`. The delta
drops as it goes further OTM (say to 0.15), and the loss is the integral of delta
across the price path. The fixed-delta model treats this as `0.40 × move` throughout.

For winners, the opposite: delta increases as the option goes ITM (0.40 → 0.65), so the
real gain is larger than `0.40 × move`. This is why QQQ real wins are sometimes bigger than
synth estimates.

### 5.3 No Vega = Missing the Biggest Single Factor

When VIX drops 2-3 points during a hold period (common in the post-entry "calm after the
storm"), the option loses IV even if the underlying doesn't move. This vol crush can cost
20-40% of premium on a 40-delta weekly. The synth model has no vega component.

---

## 6. What's Still Reliable

### Signal Direction (76% agreement)

The quality/chop/explosion filters identify winning setups 76% of the time. This is lower
than the previous (incorrect) 95% estimate, but still meaningful — the signal stack picks
direction correctly more often than not.

### Relative Config Rankings (correlation 0.947)

The synth-to-real correlation is 0.947. While absolute values are wrong by 13x, the relative
ordering is preserved: a config that produces higher synth P&L will likely produce higher real
P&L. **The sweep correctly identified the optimal parameter region.**

### QQQ Has Real Edge

QQQ shows PF 2.12 and Sharpe 0.99 with real pricing. This is genuine alpha — not spectacular,
but positive after accounting for actual option prices. SPY does not have edge (PF 0.48).

---

## 7. Corrected Assessment

### SPY Weekly: NOT VIABLE at Current Parameters

- Real P&L: **-$1,085** (loss)
- Win rate: 37.5% (minority wins)
- PF: 0.48 (losing $2 for every $1 won)
- The dte_expiry exits that were "small wins" in synth are real losses of $100-$270

### QQQ Weekly: MARGINALLY VIABLE

- Real P&L: **+$1,406** (profitable)
- Win rate: 44.4% (minority wins, but avg win >> avg loss)
- PF: 2.12 (winning $2 for every $1 lost)
- Sharpe: 0.99 (just below the 1.0 threshold)
- Carried by 4 decay_target wins totaling +$2,154 (153% of total P&L)

### What Needs to Change

1. **The synth model must not be used for parameter optimization.** All sweep results are
   invalid for absolute P&L. Use synth only for relative config comparison.

2. **Re-run the sweep with real pricing** on QQQ to validate parameter rankings.

3. **Fix the dte_expiry problem** — these trades are destroying SPY. Either tighten exits
   (more aggressive decay_target) or avoid SPY entirely for weeklies.

4. **Premium estimation needs complete rework** — the ATR-scaled model underestimates by
   3-5x. Use Black-Scholes with proper IV (from VIX surface or historical IV data) to get
   realistic entry premiums even when Alpaca bars aren't available.

---

## 8. Appendix: Previous Synth-Only Comparison (v1, Superseded)

v1 of this document compared two synth calibrations (with and without strike-aware IV).
That comparison showed 2.25x overestimate. With actual Alpaca option prices, the true
overestimate is **13.2x** — the v1 comparison was itself contaminated by the synth model's
systematic bias on both sides.
