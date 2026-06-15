# Experimental ETF Universe Analysis — 0DTE Sweet-Spot Strategy

**Date:** 2026-06-15
**Status:** EXPERIMENTAL — research only, NOT for live deployment.
**Author:** automated analysis (Claude Code)

## Purpose
Map how the existing sweet-spot 0DTE strategy (golden defaults, the same logic running
live on SPY/QQQ) performs across a broader ETF universe. This is a pure research sweep to
find where else an edge might exist. No live trading code was modified; the 4 live agents
(SPY/QQQ/MSFT/AAPL) were untouched.

## Methodology
- Tool: `scripts/replay_sweet_spot.py --symbol <X> --days 365 --require-real-options`, golden
  defaults (chop 2-5, quality 3-7, cascade≥2, decay-aware targets ON, pb_ema 13/55).
- **Real-pricing-only** (`--require-real-options`): every metric below counts only triggers
  that had a real Alpaca 0DTE contract + bars. Synth-priced fills are excluded. All 12 symbols
  ran with `synth_fallback=0` (pure real pricing).
- Cascade-sized P&L; 365-day lookback (~250 trading days).
- Deep-dive on standouts: `scripts/sweep_mag7_params.py` walk-forward, train 187d
  (2025-06-16→2026-03-13) / holdout 63d (2026-03-16→2026-06-12), 18-combo focused grid.
- **Overfit guard:** a parameter tweak is only "robust" if it improves the *holdout*, not just
  the training window.

## Caveats (read before trusting any number)
1. **Experimental.** Not validated for live trading. P&L is per-1-contract underlying-move
   simulation, cascade-sized; treat as relative signal, not dollars.
2. **0DTE cadence gates everything.** Most of these ETFs do NOT have daily expirations (see
   table). Low trade counts reflect few 0DTE days, not just selectivity.
3. **Cadence understates go-forward.** Mon/Tue/Wed expirations are recent additions for many
   ETFs; older history is Fri-weekly, so real-only frequency is lower than what's now available.
4. **Low-N fragility.** Symbols with <100 real trades and/or <40% win rate can show high PF
   driven by a few outlier winners — not durable.
5. **Period dependence.** The full-365d number can be carried entirely by the training half;
   the walk-forward holdout exposes this (see SLV).

## Step 0 — Expiration cadence (Alpaca chain, next ~18 days)
| Symbol | Exps/18d | Cadence | Notes |
|--------|----------|---------|-------|
| IWM | 12 | **near-daily** | Mon-Tue-Wed-Thu (+some Fri) — only true daily-0DTE ETF here |
| GLD | 9 | ~M/W/Th/F | |
| SLV | 9 | ~M/W/Th/F | |
| TLT | 9 | ~M/W/Th/F | |
| USO | 5 | ~2-3/wk | |
| DIA | 4 | weekly-ish | NOT daily 0DTE (contrary to assumption) |
| XLF | 4 | weekly-ish | Thu/Fri/Tue |
| XLE | 4 | weekly-ish | |
| EEM | 4 | weekly-ish | |
| FXI | 4 | weekly-ish | |
| HYG | 3 | weekly | Thu/Fri |
| XLK | 3 | weekly | Thu/Fri |

## Step 1 — Breadth ranking (365d, real-pricing-only, golden defaults)
Sorted by Sharpe. SPY/QQQ golden bar ≈ Sharpe 2.3-3.1 for reference.

| Rank | Symbol | Trades (/day) | WR | PF | Sharpe | Calmar | MaxDD% | Verdict |
|------|--------|---------------|-----|-----|--------|--------|--------|---------|
| 1 | **IWM** | 419 (1.7) | 52.7% | 1.55 | **2.17** | 4.59 | 21% | ⭐ strong + high volume |
| 2 | XLF | 86 (0.3) | 46.5% | 2.49 | 2.00 | 7.37 | 14% | promising, low N |
| 3 | TLT | 210 (0.8) | 49.5% | 1.58 | 1.56 | 2.96 | 33% | decent |
| 4 | EEM | 76 (0.3) | 25.0% | 2.50 | 1.34 | 2.31 | 30% | fragile (25% WR) |
| 5 | SLV | 217 (0.9) | 51.2% | 2.44 | 1.28 | 9.64 | 10% | great-looking (but see below) |
| 6 | HYG | 53 (0.2) | 35.8% | 2.14 | 1.08 | 3.64 | 24% | fragile, low N |
| 7 | FXI | 61 (0.2) | 27.9% | 1.86 | 0.99 | 1.91 | 51% | fragile |
| 8 | GLD | 253 (1.0) | 49.4% | 1.15 | 0.63 | 0.87 | 104% | weak |
| 9 | DIA | 110 (0.4) | 55.5% | 1.15 | 0.53 | 0.71 | 136% | weak |
| 10 | XLE | 87 (0.3) | 48.3% | 1.12 | 0.28 | 0.36 | 73% | weak |
| 11 | XLK | 69 (0.3) | 37.7% | 1.15 | 0.28 | 0.28 | 306% | weak |
| 12 | USO | 158 (0.6) | 49.4% | 0.88 | -0.59 | -0.39 | 164% | ❌ losing |

## Step 2 — Walk-forward deep-dive (standouts: IWM, SLV, TLT, XLF)
Train 187d → holdout 63d. "Robust pick" = best train combo that also holds the holdout.

| Symbol | Best combo (vs golden) | Train PF / Sharpe | **Holdout PF / Sharpe** | Verdict |
|--------|------------------------|-------------------|--------------------------|---------|
| **IWM** | chop 3-5, q 3-7, casc≥2 | 1.72 / 2.56 | **1.45 / 2.07** (+$13.32, 88 trades) | ✅ **Robust** — holds out strongly |
| **XLF** | chop 2-5 (golden), casc≥3 | 2.49 / 1.94 | **3.22 / 2.39** (+$1.20, 20 trades) | ✅ Validates OOS but very low N |
| **TLT** | chop 3-5, q 2-7, casc≥3 | 1.86 / 2.46 | **1.33 / 0.59** (+$0.81, 34 trades) | 🟡 Marginal OOS (Sharpe 0.59) |
| **SLV** | — | 3.00 / 1.65 | **0.44 / -2.82** (-$4.32, 32 trades) | ❌ **OVERFIT** — fails holdout |

### Key reads
- **IWM** is the genuine find. Robust across the grid (multiple combos hold holdout Sharpe
  ~2.0), high real-trade volume (1.7/day), and it's the only near-daily-0DTE ETF — so it runs
  the strategy exactly as designed and is directly comparable to SPY/QQQ. Golden defaults
  already deliver Sharpe 2.17; the sweep hints a tighter chop floor (3-5) may help, but that's
  experimental and not needed to call IWM strong.
- **SLV is a cautionary tale.** Its breadth numbers looked elite (PF 2.44, Calmar 9.64, MaxDD
  9.8%), but **every** top-5 combo flips to **negative holdout Sharpe** (-2.8 to -1.8). The
  edge lived entirely in the training half (Jun 2025–Mar 2026); SLV reversed in the recent
  quarter. This is exactly what walk-forward is for — the full-period number was a mirage.
- **XLF** validates out-of-sample impressively (holdout PF 3.22, Sharpe 2.39) at the golden
  chop band, but on only 20 holdout trades (~0.3/day). Real but too thin to lean on.
- **TLT** holds out only marginally (Sharpe 0.59). Mediocre.

## Conclusions
1. **IWM is the one clearly worth further (paper) experimentation** — robust, daily-0DTE,
   SPY/QQQ-class risk-adjusted performance.
2. **XLF** is a secondary curiosity: strong but low-frequency; would be a part-time agent.
3. **Do not trust breadth metrics without a holdout** — SLV would have been a top pick and is
   actually overfit/period-dependent.
4. **Avoid:** USO (losing), and the weak/fragile cluster (GLD, DIA, XLE, XLK, EEM, HYG, FXI).
5. Nothing here changes live config. If pursued, IWM would be the next paper agent — separate
   service per the modular preference — but only after a forward paper-trading confirmation.
