# What drives E[z]? 6.3 years of Config A triggers (2026-11-24)

This follows `../2026-11-19_live_drawdown/FUNCTION.md`. That study found P&L/ct ≈ A·E[z] + B·(E[z²]−1) + ε, and found that the whole 08-19 → 10-29 drawdown came from E[z], the follow-through after the signal, turning negative. This study asks whether E[z] can be explained or predicted with more data.

## Data and method

- **Triggers:** the Config A replay (`.worktree-live-main` @639d493, `--vix-lag 1`, `--skip-failed-bounce`, 1 ct, synthetic pricing) for SPY and QQQ, 2020-07-27 → 2026-11-20. Alpaca 5-minute bars start 2020-07-27; that is the hard limit, not 2016. That gives **4,746 triggers**, about 10× the live sample.
- **Target:** z = the underlying's move in the trade's direction ÷ the VIX-implied standard deviation over the horizon. It is measured at **+30 min (z6, the primary target)**, at +15 and +60 min, and over the strategy's actual hold (z_hold). Option prices are not needed for z.
- **Calibration against live:** per (symbol, day), the replay's z_hold has rank correlation **0.74** with the live Black-Scholes z from the 11-19 study. Replay z is on a smaller scale (VIX is not the 0DTE IV), but it ranks the same days the same way.
- **Features:** 45 values, all known at entry:
  - every trigger field (quality, explosion, chop and its parts, momentum, RSI, VWAP and SMA distances, ATR, risk, OR range, overshoot, flip, re-entry after a stagnation exit)
  - pre-entry bars (gap, the day's move so far, the previous day and previous 5 days, intraday realised vol against VIX, 5-day ac1)
  - the strategy's own trailing E[z] over 5, 20 and 60 days
- **Look-ahead check:** I verified the entry is the close of the bar *ending* at the trigger time. A first pass got this wrong by one bar and inflated `day_range_sofar` (ρ 0.06 → 0.04). Those numbers were discarded.

## Findings

### 1. E[z] is a regime, and 2024–26 was the good one

| Year | 2020 | 2021 | 2022 | 2023 | **2024** | **2025** | **2026** |
|---|---|---|---|---|---|---|---|
| E[z6] | +0.03 | +0.01 | +0.06 | +0.02 | **+0.10** | **+0.10** | +0.08 |
| t | 1.0 | 0.3 | 1.7 | 0.7 | **3.9** | **4.8** | 3.4 |

The 2026-08-19 → 10-29 window: **E[z6] −0.10 (t −2.5)**, with SPY at −0.07 and QQQ at −0.14. That is below every full year. In 2020–23 the signal had only a faint edge; the strong edge belongs to 2024–26, the same era the goldens were tuned on (see the extended backtest).

### 2. One strong, stable per-trade driver: re-entry right after a stagnation exit

| Era | Normal entry E[z6] (t) | **Post-stagnation re-entry** E[z6] (t) | Re-entry share |
|---|---|---|---|
| 2020–23 | +0.074 (4.3) | **−0.106 (−7.4)** | 28% |
| 2024 → 08-18-2026 | +0.153 (8.9) | **−0.049 (−2.8)** | 24% |
| Drawdown | −0.127 (−2.3) | −0.051 (−1.1) | 30% |
| After the stop | +0.425 | +0.073 | 52% |

`was_post_stag` gives ρ −0.18 (t −12) and has the same sign in **7/7 years**. Re-entering right after a stagnation exit has had negative follow-through throughout history.

**It does not explain the drawdown.** Inside the drawdown, the *normal* entries were the ones that flipped (+0.15 → −0.13).

### 3. Everything else is weak (|ρ| ≤ 0.05)

Robust in at least 6 of 7 years, but small:

- **More vol → more follow-through:** ATR (bps) ρ +0.05, prior-5-day rv/VIX +0.04, OR range +0.04.
- **Chasing a multi-day move → less:** previous day aligned −0.04, previous 5 days aligned −0.04.
- **Chop CI → less:** −0.04.

VIX level, quality, explosion, overshoot, time of day, gap and day of week are all noise.

### 4. No usable persistence

Daily E[z6] has autocorrelation of about 0 at lags 1–10 for both symbols. The strategy's trailing 5- or 20-day E[z] predicts the next day at ρ ≈ 0.0–0.03. Trailing 60 days predicts the next 20 days at about 0.15 for SPY and about −0.02 for QQQ. The weak 5-day persistence in the 75-day live sample does not hold up over 6 years.

### 5. A walk-forward model has some skill, but failed in the drawdown

Ridge regression on all features, trained on years before Y and tested on year Y (2023–2026):

- Out-of-sample ρ is **0.17**, stable each year (0.11, 0.25, 0.18, 0.18).
- E[z6] by predicted tercile: low −0.04, mid +0.09, high +0.16.

**In the drawdown window** the model's mean prediction sat at the 34th percentile, so it did not flag the window. Inside it, the trades the model would have kept were *worse* (E[z6] −0.13) than the ones it would have skipped (−0.07). The relationships broke in this window: the high-ATR tercile, which is the best in every other era, was the worst (−0.25).

### 6. Is the stagnation re-entry effect look-ahead? No. (added 2026-11-25)

The replay's `was_post_stag` flag is set from the previous trade's *eventual* outcome, even when that trade is still open at the new entry. So I split those re-entries by whether the stagnation exit had already happened (and would have been visible live):

| 2020 → 2026 | n | E[z6] | t |
|---|---|---|---|
| Normal entry | 3,490 | +0.105 | +8.8 |
| **Stagnation exit already happened (live-knowable)** | 188 | **−0.104** | **−3.8** |
| Previous trade still open, later stagnated (look-ahead) | 1,068 | −0.072 | −6.1 |

- **The live-knowable effect is real.** It is −0.135 in 2020–23 and −0.066 in 2024–26, and worst within 15 minutes of the exit (about −0.18).
- **About 85% of what the old flag marked was something else:** an entry taken while an earlier position was still open. The live-knowable version of that: entering while the earlier position is already **up more than 0.2R** has E[z6] −0.05 (t −1.7). Entering while it is flat or losing is fine (+0.08).

### 7. The A/B: blocking those re-entries LOSES money (added 2026-11-25)

I added a live-knowable replay flag, `--post-stag-block-bars N`. It reacts to a stagnation exit the agent has already seen (the same way stops are counted), and blocks new entries for N bars (N > 0) or for the rest of the day (N = −1). Default 0 is off. **Check:** with the flag off, the replay reproduces the 11-24 run exactly, 1216/1216 SPY and 1258/1258 QQQ trades identical.

Setup: Config A, 1 ct, `--slippage 0.035 --vix-lag 1 --skip-failed-bounce`. Two matched-end-date windows:
- **real**: 2024-02-01 → 2026-11-21, real option prices (98–99% of trades)
- **syn**: 2020-07-27 → 2024-01-31, synthetic option prices

Net $/1 ct (PF, Sharpe, max DD):

| Cell | Off (golden) | Block 6 bars | Block 12 bars | Halt for the day |
|---|---|---|---|---|
| SPY real | **$5,984** (1.34, 1.89, −1,843) | $5,428 (1.31, 1.76) | $5,202 (1.30, 1.70) | $5,289 (1.31, 1.73) |
| SPY syn | **$2,146** (1.18, 1.20, −716) | $2,076 | $2,102 | $1,941 |
| QQQ real | $1,308 (1.06, 0.37, −2,144) | $1,842 (1.08, 0.52, −1,771) | **$2,019** (1.09, 0.57, −1,749) | $1,887 |
| QQQ syn | **$5,924** (1.49, 2.69, −425) | $5,529 | $5,473 | $5,478 |
| **Total** | **$15,362** | $14,875 (−$487) | $14,796 (−$566) | $14,595 (−$767) |

- **Three of the four cells get worse.** SPY real was worse in 5 of 5 half-years by option bps. QQQ syn was worse in 6–7 of 8.
- **The only win is QQQ real** (+$530 to +$710, 4 of 6 half-years, and a smaller max DD). QQQ is already disabled live.
- **Drawdown window (08-19 → 10-29):** SPY is essentially unchanged (−$1,403 → −$1,331 to −$1,398). QQQ improves by about $400 but is still deeply negative. **As predicted, it would not have prevented the drawdown.**

**Why z said "skip" but P&L says "keep":**
- The block removes only 26–49 trades per cell, not the 188 in the z table. Most stagnation exits are followed by no re-entry anyway, or by one the cooldown already stops.
- The trades it does remove were **profitable** in SPY real (+$571, +7.3 bps on average) and QQQ syn (+$319).
- z6 measures the first 30 minutes. The trade's exits (stop, decay target, stagnation) truncate the bad paths, so negative average z6 does not become negative P&L for this small, specific subset.

**Verdict: REJECTED.** Keep `--post-stag-block-bars 0`. The flag stays on the research branch for future tests.

## Answer

**More data finds real drivers of E[z] per trade, but nothing that drives the drawdown.**

- What 6 years can explain is **which trades** follow through: stagnation re-entries do not, higher-vol setups do slightly better, and chasing multi-day moves does slightly worse. Taken together that is a modest, stable edge (OOS ρ 0.17).
- What it cannot explain is **when the base rate shifts**. E[z] ran at about +0.02 in 2020–23, about +0.10 in 2024–26 and −0.10 from August to October. That level is not predictable from anything observable at entry, or from the strategy's own recent results. In the drawdown it fell most for the setups that are normally best.
- The drawdown was the signal's base-rate edge disappearing, not a mix of bad trades that a filter could have screened out.

## Actionable

- ~~Skip re-entries right after a stagnation exit~~: **tested 2026-11-25 and REJECTED** (§7). The z effect is real and live-knowable (§6), but blocking those entries costs $487–$767 across the four symbol-window cells, and it would not have prevented the drawdown.
- Possible follow-up, low priority: §6's "don't add while an earlier position is up more than 0.2R" (E[z6] −0.05, t −1.7). It is weak, and §7 shows z effects of this size do not survive the trade's exits. Not queued.
- **Don't build a regime switch** from trailing results, VIX, or vol. Nothing here predicts the base-rate shifts.
- **Size expectations to the 2020–23 base rate (E[z] ≈ +0.02, close to breakeven after friction),** not to the 2024–26 one.

## Files

- `ez_triggers.csv`: every trigger with z3/z6/z12/z_hold, MAE, all features, and the walk-forward prediction (`pred`)
- `ez_univariate.csv`: per-feature Spearman, t, years-agree count, Q5−Q1
- `ab_summary.json`: every A/B cell, with removed and added trades vs baseline and half-year deltas
- `scripts/ez_causal.py`, `scripts/ez_open.py`: the §6 causal split. `scripts/ab_run.sh`, `scripts/ez_ab_parse.py`: the §7 A/B (16 replays, 8 in parallel, from the `.worktree-ez` worktree on the box; outputs in `data_cache/ez_ab/`)
- `scripts/run.sh`: the replays. `scripts/ez_drivers.py`, `scripts/ez_followup.py`: the analysis. All ran on the Ubuntu box; outputs are in `data_cache/ez_study/`.
