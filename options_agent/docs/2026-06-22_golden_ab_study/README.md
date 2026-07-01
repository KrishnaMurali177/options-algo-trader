# Golden-Parameter A/B Study — PR #4 ("new-goldens-with-replay-fixed")

**Backtests run:** 2026-06-22 (SPY/QQQ), 2026-06-27 (MSFT/AAPL) · **Persisted:** 2026-06-27
**Analyst:** Claude Code (Opus 4.8) · **Question owner:** Rohith
**Symbols:** SPY, QQQ (index ETFs) + MSFT, AAPL (live Mag-7 single names) · **Pricing:** real Alpaca 0DTE option bars, cascade-sized (×100, 3-contract tiers)
**Raw run outputs:** [`runs/`](./runs) (every file is self-annotated with its exact golden params + code basis)

> **TL;DR:** New goldens are a clear win on **SPY/QQQ** (§5) but **do NOT transfer to the live single
> names** (§7). On **MSFT** the new goldens are *strictly worse* (−$6.8k/yr and higher drawdown);
> on **AAPL** they're a risk-adjusted win (−$558 P&L but half the drawdown). **A blanket rebase
> would hurt the live MSFT agent** — see §7 for the recommendation.

---

## 1. Why this study exists

`origin/main` advanced 6 commits (merged as **PR #4**, `e9ecf8c`) while the live work sat on
`feature/mag7-single-name-agents`. PR #4 bundled a **look-ahead-bias fix** in the replay engine
with **five golden-parameter flips**. The question: *how would these changes have affected our
trading, and is the new golden set actually better?*

This document is the full evidence trail. It is **SPY/QQQ only** — see §7 for the live-capital caveat.

### The five golden parameters that changed

| Parameter | OLD value | NEW value (golden) | Commit rationale |
|---|---|---|---|
| `cluster_penalty` | **ON** | **OFF** | Honest replay showed removal wins; original promotion was a look-ahead artifact |
| `rsi_extreme_penalty` | absent / **0** | **30 (ON)** | Hard-reject triggers at directional exhaustion (RSI≥80 CALL / ≤20 PUT) |
| `tiered_stag_early_bar` | **8** (40 min) | **6** (30 min) | Exit flat trades earlier; Phase-3b strict grid passed 4/4 quartiles |
| `vwap_slope_override` (t/k/cmax) | **0.7 / 3 / 0.65** (ON) | **0 / 0 / 0** (OFF) | Re-val showed neutral / per-symbol arb; original promotion a look-ahead artifact |
| `skip_failed_bounce` | **OFF** | **ON** | Re-promoted 2026-06-20; 7/8 cells improved on the look-ahead-fixed replay |

> "OLD" = the defaults on `feature/mag7-single-name-agents` (`f83ab2a`), i.e. the world *before* PR #4.
> "NEW" = the defaults on `main` (`e9ecf8c`), i.e. the world *after* PR #4.

---

## 2. Methodology — two code bases, read carefully

There are **two distinct comparison bases** in this study. Mixing them up is the easiest way to
draw a wrong conclusion, so they are kept separate:

| Basis | Code | What a delta measures | Files |
|---|---|---|---|
| **Branch-as-is** | OLD on `feature@f83ab2a`, NEW on `main@e9ecf8c` | **Everything PR #4 changed together** — the look-ahead fix *and* the 5 params (conflated) | `old_*`, `new_*`, `old24_*`, `new24_*` |
| **Param-isolated** | **Both** on `main@e9ecf8c` (look-ahead-fixed), 5 flags toggled via CLI | **Only the 5 golden params** | `allold_*`, `fb_*`, `loo_*` |

**The param-isolated 365-day basis (§5–6) is the authoritative one.** The short-window
branch-as-is runs (§3–4) are kept because they are how the investigation unfolded and they
surface a real small-sample trap.

All runs: `replay_sweet_spot.py --days N --real-options`. Old-param runs on `main` append the
five reverting flags:
`--cluster-penalty --vwap-slope-override-t 0.7 --vwap-slope-override-k 3 --vwap-slope-override-cmax 0.65 --tiered-stag-early-bar 8 --rsi-extreme-penalty 0 --allow-failed-bounce`.

---

## 3. Short windows (branch-as-is) — and the small-sample trap

### 14-day (Jun 9 → Jun 22, 9 trading days)

| Metric | OLD SPY | NEW SPY | OLD QQQ | NEW QQQ |
|---|---|---|---|---|
| Trades | 9 | 10 | 19 | 17 |
| P&L (×100) | +$2,136 | +$2,053 | +$126 | +$210 |
| Profit factor | 6.61 | 13.53 | 1.07 | 1.18 |
| Sharpe | 5.72 | 5.30 | 0.64 | 1.33 |
| Max DD | 1.7% | 4.8% | 100% | 59.5% |

First read: roughly flat on SPY, QQQ de-risked. **This conclusion was wrong** — see §4.

### 24-day (Jun 1 → Jun 22, 16 SPY / 15 QQQ trading days)

| Metric | OLD SPY | NEW SPY | OLD QQQ | NEW QQQ |
|---|---|---|---|---|
| Trades | 17 | 15 | 31 | 26 |
| P&L (×100) | **+$2,880** | **−$77** | +$1,911 | +$1,554 |
| Profit factor | 8.27 | 0.85 | 2.06 | 2.35 |
| Sharpe | 5.72 | −1.00 | 5.57 | 5.52 |
| Max DD | 1.3% | 777.8% | 24.6% | 16.6% |

The 24-day window made the NEW goldens look **catastrophic on SPY** (+$2,880 → −$77).

---

## 4. Root cause of the 24-day SPY reversal: ONE day, ONE filter

The entire swing is **June 9, 2026**:

- **OLD goldens, Jun 9:** 4 PUTs → +$447, +$873, −$345, **+$1,014 = +$1,989 net** (biggest day in the window)
- **NEW goldens, Jun 9:** **zero trades** — the day was filtered out

Isolation tests (24d, NEW goldens, one filter disabled):

| Test | File | June 9 restored? | SPY total |
|---|---|---|---|
| NEW, RSI-extreme OFF | `new24_SPY_norsi` | No | −$320 (worse) |
| NEW, **failed-bounce OFF** | `new24_SPY_nofb` | **Yes (3 PUTs, +$2,043)** | **+$1,966** |

**The `skip_failed_bounce` filter classified June 9 as its sit-out archetype** (small gap-up + weak
prior-day close) and stood aside — but June 9 became a sharp trend-down day the PUT cluster would
have crushed.

### Why the 14-day run hid this
The 14-day window *started* on June 9, so June 9 had **no prior-day context loaded**, and the
failed-bounce filter (which keys off the prior day) **silently could not apply** — so NEW *did*
trade June 9 in the 14-day run. With a week of lead-in (24-day), the filter fires as it would
**live**. Lesson: **day-level sit-out filters need lead-in history; never benchmark them on a window
whose first day is the day in question.**

---

## 5. Authoritative result — param-isolated package A/B (365 days)

Same look-ahead-fixed code (`main@e9ecf8c`); only the five flags differ. 251–252 trading days.

### SPY — `allold_SPY` vs `fb_SPY_on`
| Metric | All-OLD | All-NEW | Δ |
|---|---|---|---|
| Trades | 534 | 437 | −97 |
| P&L (×100) | +$14,171 | +$14,368 | **+$197** |
| Profit factor | 1.65 | 1.99 | +0.34 |
| Sharpe | 2.74 | 3.65 | **+0.91** |
| Max DD | 15.1% | **7.0%** | **−54% rel.** |
| Calmar | 6.61 | **13.34** | **+102%** |

### QQQ — `allold_QQQ` vs `fb_QQQ_on`
| Metric | All-OLD | All-NEW | Δ |
|---|---|---|---|
| Trades | 504 | 396 | −108 |
| P&L (×100) | +$594 | +$5,167 | **+$4,573 (≈9×)** |
| Profit factor | 1.02 | 1.23 | +0.21 |
| Sharpe | 0.09 | 1.17 | **+1.08** |
| Max DD | 152.7% | **43.6%** | **−71% rel.** |
| Calmar | 0.08 | 1.98 | +24× |

**Combined package: +$14,765 → +$19,535 (+$4,770, +32% P&L)**, drawdown roughly halved on SPY and
cut 71% on QQQ.

- **SPY is a pure risk story:** P&L essentially flat (+$197), but achieved with 97 fewer trades,
  half the drawdown, +0.91 Sharpe. The old config grinds the same dollars through ~100 extra
  low-quality (PF 1.65) high-variance trades.
- **QQQ was effectively broken under old goldens** (Sharpe 0.09, PF 1.02, 152.7% drawdown — a
  coin-flip bleeding risk). The new goldens make QQQ genuinely tradeable. **This is the single
  strongest argument in the study.**

---

## 6. Per-flag decomposition (365-day leave-one-out)

From the all-NEW baseline, revert ONE flag to OLD; Δ = that NEW flag's marginal contribution.
Baselines: **SPY +$14,368** (Sharpe 3.65, MDD 7.0%) · **QQQ +$5,167** (Sharpe 1.17, MDD 43.6%).

### SPY
| Flag (NEW vs OLD) | Δ P&L | Δ Sharpe | Δ MDD | file |
|---|---|---|---|---|
| **rsi-extreme** (ON vs off) | **+$2,490** | **+0.70** | −3.1pp | `loo_SPY_norsi` |
| **tiered-stag** (6 vs 8) | **+$1,772** | +0.22 | −1.0pp | `loo_SPY_stag8` |
| cluster-penalty (off vs on) | +$231 | +0.42 | −0.3pp | `loo_SPY_cluster` |
| **failed-bounce** (ON vs off) | **−$930** | +0.26 | **−4.5pp** | `fb_SPY_off` |
| vwap-override (off vs on) | −$189 | −0.02 | 0 | `loo_SPY_vwap` |

### QQQ
| Flag (NEW vs OLD) | Δ P&L | Δ Sharpe | Δ MDD | file |
|---|---|---|---|---|
| **rsi-extreme** (ON vs off) | **+$2,244** | +0.54 | **−67.9pp** | `loo_QQQ_norsi` |
| **cluster-penalty** (off vs on) | **+$1,403** | +0.50 | **−45.5pp** | `loo_QQQ_cluster` |
| **failed-bounce** (ON vs off) | **+$933** | +0.25 | −9.1pp | `fb_QQQ_off` |
| tiered-stag (6 vs 8) | +$767 | +0.17 | −15.9pp | `loo_QQQ_stag8` |
| vwap-override (off vs on) | −$312 | −0.07 | +1.1pp | `loo_QQQ_vwap` |

### What the decomposition says
1. **rsi-extreme is the dominant change** — #1 P&L on both symbols; on QQQ it alone cuts max
   drawdown from 111% to 44%.
2. **cluster-penalty removal is the hidden second engine** — trivial on SPY (+$231) but on QQQ it
   nearly halves drawdown (+$1,403). Easy to dismiss; shouldn't be.
3. **tiered-stag 8→6** is solidly positive on both, larger on SPY.
4. **failed-bounce is genuinely symbol-dependent** — costs SPY P&L for drawdown protection, but is
   a clean win on QQQ (the "per-symbol arb" the commit messages flagged).
5. **vwap-override off is the only truly negligible flip** — slightly negative on both. Demoting it
   was housekeeping, not improvement.

### Leave-one-out vs true package (why marginals don't sum)
| | Σ LOO marginals | True package Δ | Interaction |
|---|---|---|---|
| SPY | +$3,374 | +$197 | **−$3,177** |
| QQQ | +$5,035 | +$4,573 | −$462 |

**SPY flags are highly redundant for P&L** — rsi-extreme, tiered-stag and failed-bounce all cut
overlapping marginal/losing trades, so each looks worth ~$1–2.5k alone, but combined they net only
+$197. Their value **stacks on risk, not dollars.** QQQ flags are more independent, so the QQQ
marginals nearly add up.

---

## 7. Live single names — MSFT & AAPL (the result that matters for capital)

The §5 package A/B re-run on the two live Mag-7 names. **Methodology note:** MSFT/AAPL are
**M/W/Th/F-expiry** names, not daily 0DTE — a plain `--real-options` run prices ~66% of trades
synthetically (MSFT real=149/438), which inflates returns and is invalid. These runs use
**`--strict-real-options`** (trade only on real-0DTE-chain days, no synth fallback); both sides are
100% real-priced and apples-to-apples. 365d, look-ahead-fixed `main` code, params isolated.

### MSFT — `strict_allold_MSFT` vs `strict_allnew_MSFT`
| Metric | All-OLD | All-NEW | Δ |
|---|---|---|---|
| Trades (all real) | 167 | 123 | −44 |
| P&L (×100) | **+$12,309** | **+$5,514** | **−$6,795 (−55%)** |
| Profit factor | 2.23 | 1.68 | −0.55 |
| Sharpe | 2.53 | 1.38 | **−1.15** |
| Max DD | 21.5% | 33.7% | **worse** |
| Calmar | 4.66 | 2.96 | −1.70 |

**On MSFT the new goldens are strictly worse on every metric** — less than half the P&L, lower
Sharpe/PF, *and* a deeper drawdown. The new filters cut 44 trades (167→123) and the ones removed
were net profitable. This is the **opposite** of the SPY/QQQ result.

### AAPL — `strict_allold_AAPL` vs `strict_allnew_AAPL`
| Metric | All-OLD | All-NEW | Δ |
|---|---|---|---|
| Trades (all real) | 164 | 115 | −49 |
| P&L (×100) | +$4,017 | +$3,459 | −$558 (−14%) |
| Profit factor | 1.62 | **1.93** | +0.31 |
| Sharpe | 1.37 | **1.74** | +0.37 |
| Max DD | 62.8% | **30.3%** | **−52% rel.** |
| Calmar | 1.39 | **3.10** | **+123%** |

**On AAPL the new goldens are a risk-adjusted win** — gives up $558 of P&L (−14%) for half the
drawdown, +0.37 Sharpe and 2×+ Calmar. Same shape as the SPY result (risk reduction, slight P&L cost).

### Verdict: the goldens do NOT transfer uniformly
| Symbol | New-golden effect | One-line |
|---|---|---|
| SPY | risk-adjusted win | same P&L, half the drawdown |
| QQQ | clear win | ~9× P&L, broken→viable |
| **AAPL** | risk-adjusted win | −$558 P&L, half the drawdown |
| **MSFT** | **clear loss** | **−$6.8k P&L AND deeper drawdown** |

The five flags were tuned on index ETFs; three of four names benefit, but **MSFT is actively harmed.**
This is exactly the "doesn't automatically transfer to single names" risk flagged at the outset.

---

## 8. Conclusions & recommendation

**SPY/QQQ:** the new goldens are a validated, unambiguous upgrade (+32% combined P&L, QQQ
broken→viable, major risk reduction on both). The scary 24-day SPY "−$77" was a single-day,
single-filter small-sample artifact (failed-bounce skipping June 9), not a regression.

**Live single names — actionable:**
- **Do NOT let a blanket rebase flip the live MSFT agent onto the new goldens.** It costs ~$6.8k/yr
  and *increases* drawdown on MSFT's strict-real history. AAPL would tolerate it (risk-adjusted win),
  but MSFT is a clear loss.
- The Mag-7 agents inherit goldens by default (no CLI overrides), and the Mag-7 sweep
  (`sweep_mag7_params.py`) holds these five flags fixed at OLD values — so after a rebase the live
  MSFT/AAPL agents would silently adopt the new flags. **Mitigation options:**
  1. **Pin OLD-golden flags per-symbol in `docker-compose.yml`** for `agent-msft` (and optionally
     `agent-aapl`) — explicit `--cluster-penalty --tiered-stag-early-bar 8 --rsi-extreme-penalty 0
     --allow-failed-bounce --vwap-slope-override-t 0.7 --vwap-slope-override-k 3
     --vwap-slope-override-cmax 0.65` — so the rebase changes SPY/QQQ behavior only.
  2. **Run a per-flag leave-one-out on MSFT** (as in §6) to find *which* of the five flags drives the
     MSFT loss — it may be possible to keep the harmless/beneficial ones and revert only the
     culprit, rather than freeze all five.
- **Caveat on strict-real representativeness:** strict mode trades only on real-0DTE-chain days
  (~half of days for these names). If the live agent enters on non-0DTE days via a nearest-expiry
  contract, that exposure isn't captured here. Worth confirming how the live `run_sweet_spot_agent`
  handles a missing 0DTE chain on MSFT/AAPL before finalizing the per-symbol config.

---

## 9. File index (`runs/`)

Each file is prefixed with a header block stating its symbol, window, **code basis**, exact CLI
command, and the **five golden-param values** used (reverted lines flagged `<<<`).

| File | Window | Basis | Config |
|---|---|---|---|
| `old_SPY` / `old_QQQ` | 14d | feature@f83ab2a (old code) | All-OLD |
| `new_SPY` / `new_QQQ` | 14d | main@e9ecf8c | All-NEW |
| `old24_SPY` / `old24_QQQ` | 24d | feature@f83ab2a (old code) | All-OLD |
| `new24_SPY` / `new24_QQQ` | 24d | main@e9ecf8c | All-NEW |
| `new24_SPY_nofb` | 24d | main | NEW − failed-bounce (restores Jun 9) |
| `new24_SPY_norsi` | 24d | main | NEW − rsi-extreme |
| `fb_SPY_on` / `fb_QQQ_on` | 365d | main | All-NEW baseline |
| `fb_SPY_off` / `fb_QQQ_off` | 365d | main | NEW − failed-bounce |
| `loo_{SPY,QQQ}_cluster` | 365d | main | NEW + cluster-penalty reverted ON |
| `loo_{SPY,QQQ}_vwap` | 365d | main | NEW + vwap-override reverted ON |
| `loo_{SPY,QQQ}_stag8` | 365d | main | NEW + tiered-stag reverted to 8 |
| `loo_{SPY,QQQ}_norsi` | 365d | main | NEW + rsi-extreme reverted OFF |
| `allold_SPY` / `allold_QQQ` | 365d | main | All-OLD (params only) |
| `strict_allnew_MSFT` / `strict_allnew_AAPL` | 365d | main | All-NEW, **strict real options** (single names) |
| `strict_allold_MSFT` / `strict_allold_AAPL` | 365d | main | All-OLD, **strict real options** (single names) |

> Single-name note: MSFT/AAPL runs use `--strict-real-options` (100% real-priced, synth_fallback=0)
> because a plain `--real-options` run on these M/W/Th/F-expiry names is ~66% synthetic and invalid.
> The non-strict MSFT probe (`allnew_MSFT.txt`, 66% synth) was discarded and is not included here.

> Cascade-sizing note: P&L figures are the cascade-sized (×100, 3-contract) totals, matching the
> README backtest convention. Real Alpaca pricing throughout (~1–2% synth fallback on no-chain days,
> equal across compared runs, hence neutral).
