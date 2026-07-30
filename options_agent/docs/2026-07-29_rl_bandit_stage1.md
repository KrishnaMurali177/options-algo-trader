# RL Regime Bandit — Stage 1 verdict

**Date:** 2026-07-29 · **Branch:** `feature/rl-policy-layer` · **Scope:** offline only, no live changes

## Verdict

**STOP. Do not deploy the bandit, and do not change the live parameters either.**

Two separate negative results, in order of importance:

1. **The controller is not worth it.** Out of sample the bandit beat the golden defaults
   pooled (+224.4R vs +207.1R) but lost badly to the single best *static* arm
   (+331.5R). On SPY+QQQ alone it was **worse than golden** (+14.0R vs +17.1R).
2. **The static config it pointed at does not survive execution costs.** `permissive`
   looked strong at zero slippage (t=+3.24 pooled) but at a realistic round-trip cost
   its edge is not statistically distinguishable from golden on *any* cut.

A third finding, unplanned and more important than either: **at realistic slippage the
0DTE edge on SPY/QQQ over the last year is thin enough that sitting out is close to
competitive** — see [The uncomfortable finding](#the-uncomfortable-finding).

## What was built

A contextual bandit choosing one parameter preset per symbol-day, including a genuine
"sit out" arm, on top of the unchanged strategy. `options_agent/src/rl/`:
`presets.py` (6 arms as deltas over golden defaults), `features.py` (context vector),
`dataset.py` (full-feedback dataset builder), `bandit.py` (LinUCB + Thompson),
`evaluate.py` (purged walk-forward). Driver: `scripts/run_rl_bandit.py`. 31 tests in
`tests/test_rl_bandit.py`.

Because `replay_day` is a deterministic simulator, every arm is scored on every
symbol-day — **full feedback**, so no importance weighting is needed and an exact
per-day oracle exists.

## Method choices that materially affected the answer

**Decision time is 10:00 ET, not 10:35.** With `dynamic_or` on (a golden default) a
decisive 10:00 break moves scan-start to 10:00 (`replay_sweet_spot.py:249-274`). A
10:35 context could therefore see the outcome of trades the decision is meant to
govern. This costs the 60-minute opening range — only the 30-minute one is available.
Enforced by tests that NaN *and* 100×-corrupt every post-10:00 bar and assert the
context vector is byte-identical.

**Reward is return-on-premium** (`pnl / est_premium`), not underlying points. In
options mode `replay_day` overwrites `pnl` with per-contract option P&L and sets
`est_premium` to the real entry premium (`:1024-1030`). An underlying-points reward
would have credited `runner` for price movement while ignoring the theta it pays for
wider targets.

**Untradable sessions excluded.** Where a symbol has no 0DTE listing, every arm scores
exactly 0.0 and ties with `sit_out`. Prewarm measured how common this is: EEM 58/251
sessions with a chain, HYG 52/251, AMZN 103/251, GOOGL 101/251. **3,092 of 5,271
symbol-days (58.7%) were dropped.** Keeping them would have manufactured a fake result
— the controller would appear to "wisely sit out" on days nothing was on offer.

**Purged walk-forward**, expanding monthly window with a 5-day embargo (one more than
the 4-session `prior_bars` warmup). The existing `sweep_mag7_params.sweep_symbol` uses
a single 75/25 split with no purge and reports best-of-486 on one holdout.

## Result 1 — the bandit (zero slippage, 21 symbols)

| policy | days | total R | mean R | Sharpe | vs golden |
|---|---|---|---|---|---|
| bandit | 1,530 | +224.4 | 0.147 | 2.29 | **+17.3** |
| golden_always | 1,530 | +207.1 | 0.135 | 2.21 | — |
| **best_fixed_arm** | 1,530 | **+331.5** | 0.217 | 3.09 | **+124.4** |
| oracle | 1,530 | +940.7 | 0.615 | 9.69 | +733.6 |

SPY+QQQ only: bandit **+14.0R vs golden +17.1R** — the controller actively hurt.

`permissive` was the best fixed arm in 7 of 10 folds, but the bandit chose it only 62%
of the time. **The learning is what costs it**: a static "always permissive" rule beat
the thing that tried to learn when to use it.

Ignore the oracle number. Taking the max of six noisy daily draws harvests noise, which
is why it is 2.8× the best static arm. It is a sanity bound, not attainable headroom.

## Result 2 — `permissive` does not survive slippage

`permissive` (`min_cascade=2`, quality `2-8`, `max_chop=6`) fires ~57% more trades than
golden, so it is the arm most exposed to execution cost — and `--slippage` defaults to
**0.0** in the replay parser, i.e. free execution.

Paired per-session t-stat of `permissive − golden`, identical sessions at each cost:

| cut | sessions | t @ 0.00 | t @ 0.03 | t @ 0.05 |
|---|---|---|---|---|
| SPY+QQQ | 461 | +0.71 | +0.54 | +0.28 |
| SPY/QQQ/IWM | 692 | +1.57 | +1.22 | +0.88 |
| single names | 618 | +2.03 | — | +1.53 |
| ALL (21 sym) | 2,179 | **+3.24** | — | — |

Slippage roughly halves the t-stat on identical samples. **At realistic cost no cut
clears |t| > 2.** The direction is consistent (9/13 months) but the magnitude is not
separable from noise. The pooled t=+3.24 was carried by zero-cost execution plus
symbols that are not traded live — including IWM/TLT/SLV, already flagged internally as
overfit.

`selective` is robustly *worse* everywhere (t = −2.4 to −4.0). That is the one solid
directional conclusion: tightening the gates costs money.

## The uncomfortable finding

Compare golden against **doing nothing**, on the symbols actually traded live:

| cut | cost | golden total | `sit_out` edge | t | months `sit_out` beat golden |
|---|---|---|---|---|---|
| SPY+QQQ | 0.00 | +79.1R | −79.1R | −3.84 | 2/13 |
| SPY+QQQ | 0.03 | +59.0R | −59.0R | −2.90 | 4/13 |
| SPY+QQQ | 0.05 | +46.1R | −46.1R | −2.28 | **6/13** |
| SPY/QQQ/IWM | 0.05 | +46.6R | −46.6R | −1.82 | **7/13** |

At $0.05 round-trip on SPY/QQQ/IWM, golden's whole-year edge is +46.6R over 692
sessions (+0.067R/session) and **not trading beat it in 7 of 13 months** (t = −1.82,
i.e. not significant). The strategy is still positive at every cost tested, but the
margin over sitting out is much thinner than the zero-slippage backtests imply.

This is a statement about *replay* at an *assumed* cost, not about the live book — and
it is the single most decision-relevant number here.

## Recommended next step

**Measure actual realized slippage from the live and paper fills, then re-run.** The
data exists: `sweet_spot_journal*/`<date>_<SYM>.json` carries `actual_entry`,
`option_premium` and reconciled `exit_price` per order. Comparing those to the replay's
mid-price assumption gives the real number, which replaces the guessed 0.03/0.05 and
determines whether any of the above matters. Everything else is secondary to that.

Do **not** pursue the Stage 2 exit policy yet. It optimises holding time, and holding
time is exactly what execution cost taxes; without a measured slippage figure it would
be tuning against a mis-specified objective.

## Incidental fixes landed

- **VIX lookahead fixed** (`resolve_day_vix()` + `--vix-lag`, default 1). Measured
  impact: **immaterial** — SPY $+5,987 → $+5,890 (−1.6%), QQQ $+3,718 → $+4,092
  (**+10.1%**), net +2.9%. Only ~5 trades differ in a low-VIX year. **Prior golden and
  A/B studies are not invalidated**, contrary to the note in
  `2026-07-16_new_golden_validation_and_parity.md`.
- `replay_sweet_spot.py --help` crashed on an unescaped `%` (`>50% of` parsed as the
  `%o` format spec). Pre-existing.
- Isolated `docker-compose.rl.yml` (`-p rl`, no `container_name`, no `build`) so RL jobs
  cannot adopt, recreate or rebuild the live trading containers.

## Reproduce

```bash
# from the RL worktree on the Ubuntu box — never the live clone
docker-compose -p rl -f docker-compose.rl.yml run --rm rl \
  python scripts/run_rl_bandit.py build-dataset --symbols all --days 365 --prewarm
docker-compose -p rl -f docker-compose.rl.yml run --rm rl \
  python scripts/run_rl_bandit.py evaluate  --dataset rl_runs/dataset_2026-07-29.parquet
docker-compose -p rl -f docker-compose.rl.yml run --rm rl \
  python scripts/run_rl_bandit.py arm-stats --dataset rl_runs/ds_sl0.05.parquet
docker-compose -p rl -f docker-compose.rl.yml run --rm rl \
  python -m pytest tests/test_rl_bandit.py -q      # 31 passed
```

Artifacts: `options_agent/rl_runs/dataset_2026-07-29.parquet` (21 symbols, zero
slippage), `ds_sl0.03.parquet`, `ds_sl0.05.parquet` (10 symbols each), plus
`.summary.txt` beside each.
