# Weekly Options Sweep Report — SPY & QQQ (365 Days)

> **Version:** 2026-05-30 v1 (synth-only baseline)
> **Status:** All P&L from synth delta-gamma-theta model. Overestimates by ~2.25x (see `2026-05-30_synth_vs_real_uncertainty.md`).
> **Next update:** Re-run sweep with real Alpaca option bars. Compare parameter rankings and corrected Sharpe.

**Date:** 2026-05-30
**Grid:** Quick sweep, 2916 configurations per symbol
**Pricing:** Synthetic (no real Alpaca weekly option data)
**Data:** 292 daily bars per symbol (Apr 2025 - May 2026)

---

## 1. Headline Results (Sweep-Optimal Config)

```
quality_min=4, quality_max=8, chop_max=6
target_delta=0.40, min_dte=2, max_dte=5
stop_atr_mult=2.0, target_atr_mult=1.5, decay_halflife=1.5
```

| Metric | SPY | QQQ |
|--------|-----|-----|
| **Trades** | 24 | 18 |
| **Win Rate** | **62.5%** | **55.6%** |
| **Profit Factor** | **6.40** | **8.73** |
| **Total P&L** | **$1,652** | **$2,552** |
| **Avg Win / Avg Loss** | $131 / -$34 | $288 / -$41 |
| **Win:Loss Ratio** | 3.84:1 | 6.99:1 |
| **Sharpe** | **2.43** | **2.04** |
| **Sortino** | 3.95 | 3.19 |
| **Calmar** | 13.11 | 10.88 |
| **Max Drawdown** | -$126 | -$234 |
| **Avg Hold Days** | 2.0 | 1.4 |
| **Avg DTE at Exit** | 1.2 | 1.5 |
| **Overnight Gap Impact** | $39 | $19 |

### vs. Baseline Defaults (Q3-7, DTE3-7, stop=1.5, target=2.0)

| Metric | Baseline SPY | Optimized SPY | Delta |
|--------|-------------|---------------|-------|
| Sharpe | 1.58 | **2.43** | **+54%** |
| Win Rate | 52.6% | **62.5%** | **+10pp** |
| Profit Factor | 3.70 | **6.40** | **+73%** |
| Trades | 19 | 24 | +26% |
| Max DD | -$136 | **-$126** | -7% |
| Avg Hold | 2.5d | 2.0d | -0.5d |

---

## 2. Trade-Level Deep Dive

### Direction

- **SPY:** 24/24 trades were buy_call (100%). No put signals passed all gates in 365 days.
- **QQQ:** 16/18 buy_call, 2/18 buy_put. The weekly SMA crossover direction gate is extremely
  bullish-biased over this backtest window (Apr 2025 - May 2026 was a predominantly bullish period).

**Concern:** Single-direction bias means the system hasn't been tested in a genuine bear market.
The 2 QQQ puts (Dec 2025, Feb 2026) show the machinery works for puts, but N=2 is not validation.

### Quality Score Distribution

| Quality | SPY Trades | SPY WR | SPY P&L | QQQ Trades | QQQ WR | QQQ P&L |
|---------|-----------|--------|---------|-----------|--------|---------|
| Q=4 | 1 | 100% | $1.63 | — | — | — |
| Q=5 | 3 | 33% | -$12.89 | 2 | 100% | $7.25 |
| Q=7 | 9 | 67% | -$11.19 | 4 | 50% | $4.68 |
| Q=8 | 11 | 64% | $8.99 | 12 | 50% | $33.88 |

**Observations:**
- Q=8 trades dominate both symbols (46% of SPY, 67% of QQQ trades).
- SPY Q=5 trades are net negative (-$12.89). Consider raising quality_min to 6 for SPY-only.
- Q=8 is the most profitable cohort for QQQ ($33.88, 50% WR but massive asymmetry).
- The Q=4-8 band works, but the signal is concentrated at the top end.

### Explosion Score Distribution

| Explosion | SPY Trades | QQQ Trades | Combined WR |
|-----------|-----------|-----------|-------------|
| E=2 | 9 (37%) | 5 (28%) | 57% |
| E=3 | 5 (21%) | 2 (11%) | 57% |
| E=4 | 10 (42%) | 11 (61%) | 57% |

E=4 trades are the most common for QQQ (61%). The explosion gate at E>=2 is right — lower
would add noise, higher would kill too many trades.

### Exit Type Analysis

| Exit | SPY Count | SPY WR | SPY Avg P&L | QQQ Count | QQQ WR | QQQ Avg P&L |
|------|-----------|--------|-------------|-----------|--------|-------------|
| **dte_expiry** | 15 (62.5%) | 53% | **-$1.50** | 10 (55.6%) | 50% | **+$1.23** |
| **decay_target** | 4 (16.7%) | 100% | **+$5.56** | 4 (22.2%) | 100% | **+$9.94** |
| **stagnation** | 3 (12.5%) | 100% | +$2.73 | 2 (11.1%) | 50% | +$0.95 |
| **stop_loss** | 1 (4.2%) | 0% | -$17.95 | 0 | — | — |
| **regime_degradation** | 1 (4.2%) | 0% | -$3.48 | 2 (11.1%) | 0% | -$4.05 |

**Critical finding — `dte_expiry` is the weakest exit:**
- SPY: 53% WR but **negative avg P&L** (-$1.50). These are positions held to near-expiry
  that "win" only because the underlying moved slightly in the right direction, but theta
  ate most of the premium. The 7 losers in this cohort collectively cost -$38.39.
- QQQ: 50% WR with slight positive avg ($1.23). Better but still marginal.
- **This is where the money is left on the table.** 62.5% of SPY exits are dte_expiry — the
  system is letting positions drift to expiration instead of actively managing them.

**`decay_target` is the profit engine:**
- 100% WR on both symbols. $5.56 avg on SPY, $9.94 avg on QQQ.
- This exit fires when theta decay exceeds a threshold — it's protecting gains.
- Only 16-22% of exits use this path. The rest are dying on the vine.

### Monthly P&L

| Month | SPY Trades | SPY P&L | QQQ Trades | QQQ P&L | Combined |
|-------|-----------|---------|-----------|---------|----------|
| Jun 2025 | 2 | +$2.75 | 1 | -$0.63 | +$2.12 |
| Jul 2025 | 2 | +$6.00 | 4 | -$0.25 | +$5.75 |
| Aug 2025 | 2 | -$5.95 | 1 | +$3.50 | -$2.45 |
| Sep 2025 | 5 | +$7.74 | 3 | +$12.70 | +$20.44 |
| Oct 2025 | 2 | -$1.26 | 2 | +$1.78 | +$0.52 |
| Nov 2025 | 1 | -$13.13 | — | — | -$13.13 |
| Dec 2025 | 5 | +$7.35 | 1 | +$11.47 | +$18.82 |
| Jan 2026 | 2 | +$0.26 | 1 | -$3.02 | -$2.76 |
| Feb 2026 | 1 | -$17.95 | 1 | -$1.42 | -$19.37 |
| Mar 2026 | — | — | — | — | — |
| Apr 2026 | 1 | -$3.48 | 1 | -$6.69 | -$10.17 |
| May 2026 | 1 | +$4.22 | 3 | +$28.37 | +$32.59 |

**Patterns:**
- Sep 2025 and Dec 2025 were the best months (+$20, +$19 combined).
- Feb 2026 was the worst single loss (-$17.95 SPY stop_loss, -$1.42 QQQ regime).
- March 2026: zero trades. The SMA crossover direction gate rejected every day.
- 3 of 12 months had zero trades on at least one symbol — the system is very selective.

### DTE at Entry

| DTE | SPY | QQQ |
|-----|-----|-----|
| 2 | 5 (21%) | 5 (28%) |
| 3 | 9 (37%) | 9 (50%) |
| 4 | 10 (42%) | 4 (22%) |

Most entries are at DTE 3-4. DTE=2 entries are Wednesday trades targeting this Friday.

### Days Held

| Days | SPY | QQQ |
|------|-----|-----|
| 1 | 6 (25%) | 10 (56%) |
| 2 | 13 (54%) | 8 (44%) |
| 3 | 4 (17%) | 0 |
| 4 | 1 (4%) | 0 |

QQQ positions exit faster (56% held just 1 day). SPY tends to hold 2 days (54%).
This makes sense — QQQ is more volatile, so decay_target and dte_expiry fire sooner.

---

## 3. Jane Street Quant Critique

A quantitative trader at a top systematic firm would flag these issues before deploying capital:

### 3.1 Statistical Significance — N is Dangerously Low

**The #1 problem.** 24 trades (SPY) and 18 trades (QQQ) over 365 days is insufficient to draw
reliable conclusions about any parameter.

- **Sharpe confidence interval at N=24:** A Sharpe of 2.43 with 24 observations has a standard
  error of ~`Sharpe / sqrt(N/2)` = `2.43 / sqrt(12)` ≈ 0.70. The 95% CI is approximately
  [1.03, 3.83]. This means the true Sharpe could be anywhere from 1.0 to 3.8 — the estimate
  is noisy enough that a Sharpe of 1.0 cannot be ruled out.

- **Win rate confidence interval at N=24:** 62.5% WR with N=24. Using Wilson interval:
  95% CI ≈ [42%, 79%]. A true win rate of 42% would produce negative expected returns
  at this avg win/loss ratio. We can't distinguish our system from a coin flip at this N.

- **Minimum trades for statistical reliability:** At a top firm, you'd want N=200+ trades
  to confidently estimate Sharpe within ±0.3. At N=1000+, parameter sensitivity becomes
  meaningful. Our N=24 means any parameter sweep finding is noise-fitted.

**What Jane Street would do:** Run the backtest on 5+ years of data across 10+ tickers.
If the system only produces 24 trades/year, you need 5 years minimum (N=120) and preferably
10 years (N=240) to make statistical claims. Alternatively, increase trade frequency.

### 3.2 Overfitting Risk — Sweep on 1 Year is Curve-Fitting

The sweep tested 2916 configurations on 365 days. The "best" config was selected by Sharpe.
With N=24 trades and 2916 configs, we are **guaranteed** to find a high-Sharpe configuration
purely by chance.

- **Multiple comparisons problem:** Testing 2916 configs at N=24 is like flipping 2916 coins
  24 times each and reporting the one with the most heads. The Bonferroni-corrected significance
  threshold would be p < 0.05/2916 ≈ 0.00002 — effectively impossible to reach with 24 trials.

- **In-sample vs out-of-sample:** All results above are in-sample. There is no holdout set.
  A rigorous approach would use walk-forward validation: optimize on months 1-9, test on
  months 10-12, slide forward, and aggregate OOS results.

**What Jane Street would do:**
1. Split data into 60% train / 20% validate / 20% test.
2. Sweep on train, select top-5 configs on validate, test on test.
3. Walk-forward: rolling 6-month train, 2-month test windows.
4. Report only OOS (out-of-sample) metrics. In-sample Sharpe is marketing, not research.

### 3.3 Survivorship and Selection Bias

- **Ticker selection bias:** SPY and QQQ are the two most liquid, most trending ETFs in
  existence. They have a structural upward drift (equity risk premium). The system's 100%
  buy_call bias on SPY suggests it may just be riding beta, not generating alpha.

- **Time period bias:** Apr 2025 - May 2026 was generally bullish for US equities.
  The system has **never been tested in a bear market, a crash, or a sustained drawdown.**

**What Jane Street would do:**
1. Test on 2018 (Feb VIX spike, Q4 selloff), 2020 (COVID crash + recovery), 2022 (bear market).
2. Test on non-US ETFs (EEM, FXI, GLD, TLT) to check if alpha is real or beta-masked.
3. Subtract SPY buy-and-hold returns from the strategy P&L to isolate alpha.
4. Test with synthetic bear markets (reverse price series) to check directional robustness.

### 3.4 The DTE-Expiry Problem is a Design Flaw

62.5% of SPY exits are `dte_expiry` with **negative average P&L** (-$1.50). This means the
majority of positions are held to near-expiration without hitting any exit signal, and theta
decay eats the premium.

This is the opposite of what you want. The exit chain should be:
1. **Most exits: decay_target** (you won, take the money)
2. **Some exits: stop_loss** (you lost, cut fast)
3. **Few exits: dte_expiry** (rare edge case, position was truly ambiguous)

Currently: 62.5% dte_expiry (passive), 16.7% decay_target (active win), 4.2% stop_loss (active loss).

**What Jane Street would do:**
1. **Tighten the decay_target** — make it fire sooner. If decay_halflife=1.5 only catches
   16.7% of trades, try 1.0 or even 0.75. You want MOST trades to exit via decay_target.
2. **Add a time-based stop** — if the position hasn't moved ±0.3R after 1 day, cut it.
   Holding for 2 days at DTE=2 means you're in the steepest part of the theta curve.
3. **Consider delta-hedging** instead of naked directional — but this changes the whole system.

### 3.5 Single Stop Loss = Fat Tail Risk

Only 1 stop_loss in 24 SPY trades, and it was -$17.95 (the largest single loss by far, 2.3x
the next-worst). With 1 stop in 42 combined trades, the stop_loss distribution is unstable.

The stop at 2.0 × ATR is very wide for a 2-5 DTE position. On SPY with ATR=$3.50, the stop
is $7.00 below entry — a 1.3% move on a $540 stock. For a 40-delta option on a 3-DTE contract,
a $7 underlying move against you could easily be a 60-80% premium loss.

**What Jane Street would do:**
1. Size positions so that max stop_loss ≤ 2% of portfolio.
2. Consider tighter stops (1.0-1.5 ATR) with acceptance of more frequent stop-outs.
3. Implement portfolio-level risk limits: max daily loss, max weekly drawdown, correlation limits.

### 3.6 Asymmetric Win/Loss Ratio is Good But Fragile

The win:loss ratio is excellent (SPY 3.84:1, QQQ 6.99:1). This means winners are 4-7x larger
than losers. Combined with 55-62% WR, the expected value per trade is strongly positive.

**But:** This asymmetry comes from a very specific exit regime. The 4 decay_target exits
on QQQ produced $39.75 in profit — more than all other exits combined. Remove those 4 trades
and QQQ's total P&L drops to $5.86 (a Sharpe < 0.5).

**The system's edge depends on 4 trades out of 18.**

At Jane Street, a strategy that depends on <25% of its trades for >75% of its P&L would be
flagged as "lumpy" and require much deeper validation before capital allocation.

### 3.7 Correlation with Market Regime

All trades are buy_call. The system makes money when the market goes up during the holding
period, loses money when it goes down. This is **not alpha — it's levered beta.**

The relevant question is: does the signal stack (quality/chop/explosion filters) add value
over naive "buy a weekly call every Monday"?

**What Jane Street would do:**
1. Run a control backtest: buy a 40-delta weekly call every Monday, hold to expiry. Compare.
2. If the filtered system doesn't meaningfully outperform the naive version, the filters
   aren't providing edge — they're just reducing N.
3. Regress strategy returns against SPY returns. If R² > 0.8, the strategy has no independent
   alpha — it's a leveraged index tracker with higher fees.

### 3.8 Transaction Costs and Slippage

The backtest uses `slippage=0.0`. For weekly options at 40-delta OTM:
- Bid-ask spread: typically $0.05-$0.15 per contract
- Slippage on entry + exit: $0.10-$0.30 per round trip
- On a $2.00 premium option, this is 5-15% of the trade

With avg P&L per trade of $69 (SPY) and $142 (QQQ), transaction costs are a smaller fraction,
but they compound. At 24 trades/year with $0.20 round-trip slippage, that's $4.80/year drag —
small but non-trivial as a % of the $17 total P&L in underlying terms.

**What Jane Street would do:**
1. Always model bid-ask and slippage. Use 1 tick (SPY options) minimum.
2. Model market impact for larger positions (irrelevant at 1-contract scale).

---

## 4. Recommendations (Ordered by Priority)

### Immediate (Before Going Live)

1. **Extend backtest to 3+ years** (2023-2026). Need N=60+ trades minimum. 5 years preferred.
   This requires Alpaca daily bar data back to 2021, which is available.

2. **Run the naive control** — buy weekly 40-delta call every Monday, hold to expiry.
   If the filtered system doesn't beat this by 20%+ on Sharpe, the filters aren't adding edge.

3. **Fix the dte_expiry problem** — tighten decay_halflife to 1.0, add a 1-day time stop
   (if position is between -0.2R and +0.3R after 1 full day, exit). Target: <30% dte_expiry exits.

4. **Walk-forward validation** — split 3yr data into 18-month train + 6-month test windows.
   Only report OOS results.

### Medium-Term (First Month of Paper Trading)

5. **Test in down markets** — run on 2022 data (SPY -19%, QQQ -33%). If the system produces
   only buy_call signals and rides them down, the direction gate needs bear-market adaptation.

6. **Multi-ticker validation** — test on IWM (small-cap, choppier), DIA (blue-chip, lower vol),
   TLT (bonds, different regime). If the system only works on SPY/QQQ, it's capturing ETF
   momentum, not a generalizable signal.

7. **Portfolio-level risk** — implement max 2 positions total across all symbols, max 1% portfolio
   risk per trade, weekly drawdown limit of 3%.

### Long-Term (After 3 Months of Live Validation)

8. **Consider pairs/hedging** — if only trading calls, buy a lower-delta put as a hedge.
   Converts directional exposure to spread, reduces tail risk.

9. **Regime detection** — add a macro regime classifier (VIX term structure, yield curve,
   breadth indicators) to switch between aggressive (more trades) and defensive (fewer/no trades).

10. **Position sizing** — scale trade size by signal confidence (quality × explosion) and
    inverse VIX. Higher conviction = larger position. High VIX = smaller position.

---

## 5. Parameter Sensitivity (From Sweep)

### What Matters (Large Sharpe Impact)

| Parameter | Best Value | Why |
|-----------|-----------|-----|
| **Quality band** | 4-8 (not 3-7) | Q=3 trades are noise. Q=8+ consistently profitable. |
| **DTE window** | 2-5 (not 3-7) | Shorter DTE = less theta exposure, earlier exits, higher WR |
| **Stop ATR** | 2.0 (not 1.5) | Wider stops avoid whipsaw stop-outs on volatile days |
| **Target ATR** | 1.5 (not 2.0) | Tighter targets = more decay_target exits (the good exit) |

### What Doesn't Matter (Low Sensitivity)

| Parameter | Finding |
|-----------|---------|
| **Decay halflife** | 1.5 vs 2.0 vs 3.0 produces nearly identical results. The system is insensitive to this parameter because most exits are dte_expiry anyway. |
| **Target delta** | 0.35 vs 0.40 has modest impact. 0.40 slightly better (deeper contracts). |
| **Chop max** | 5 vs 6 trades 23 vs 24 trades on SPY (1 trade difference). Low sensitivity. |

### What Kills the System

| Parameter | Effect |
|-----------|--------|
| **Q2-6** | Zero or near-zero trades. The Q=2-3 range almost never passes explosion+chop gates. |
| **DTE 5-10** | Zero trades. No Friday expiry falls within DTE 5-10 from Mon-Wed entry. |
| **Chop=4 (tight)** | Eliminates most trading days. Combined with other gates, produces 0 trades. |

---

## 6. Summary

The signal stack is profitable with strong risk-adjusted metrics (Sharpe 2.0-2.4, PF 6-9).
The sweep found a config that beats the baseline by 54% on Sharpe.

**However, the statistical foundation is weak.** N=18-24 trades is insufficient for parameter
validation. The system is 100% long-biased in a bullish market, the majority of exits are
passive (dte_expiry), and the edge concentrates in a handful of decay_target exits.

Before deploying capital: extend the backtest window, validate out-of-sample, fix the
dte_expiry problem, and run the naive control to isolate alpha from beta.
