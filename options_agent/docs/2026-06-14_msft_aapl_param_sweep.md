# Mag 7 Walk-Forward Parameter Sweep

- Grid: {'min_chop': [1, 2, 3], 'max_chop': [5], 'min_quality': [2, 3], 'max_quality': [7], 'min_cascade': [2, 3, 4], 'target_mult_mid': [1.5]}
- Combos/symbol: 18
- Lookback: 365d | train_frac: 0.75 | top-K validated on holdout: 6 | min train trades: 20
- Pricing: real-only (require_real_options=True). All non-grid params = golden defaults.
- Composite = mean of (Sharpe rank, PF rank, Calmar rank); lower is better.

## MSFT

Train 2025-06-16→2026-03-13 (187d), Holdout 2026-03-16→2026-06-12 (63d). 18/18 combos eligible.

| rank | chop | qual | casc | tgt | TR trades | TR PF | TR Shrp | TR Cal | TR MDD% | HO trades | HO PF | HO Shrp | HO P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1-5 | 2-7 | 2 | 1.5 | 96 | 4.51 | 3.43 | 29.54 | 3 | 49 | 2.09 | 2.99 | $+41.22 |
| 2 | 1-5 | 2-7 | 4 | 1.5 | 92 | 4.32 | 3.37 | 18.87 | 5 | 44 | 1.95 | 2.65 | $+32.64 |
| 3 | 1-5 | 2-7 | 3 | 1.5 | 92 | 4.37 | 3.31 | 16.75 | 6 | 49 | 1.91 | 2.78 | $+34.17 |
| 4 | 1-5 | 3-7 | 4 | 1.5 | 92 | 4.32 | 3.37 | 18.87 | 5 | 44 | 1.95 | 2.65 | $+32.64 |
| 5 | 1-5 | 3-7 | 3 | 1.5 | 92 | 4.37 | 3.31 | 16.75 | 6 | 48 | 2.02 | 2.83 | $+36.27 |
| 6 | 1-5 | 3-7 | 2 | 1.5 | 96 | 4.14 | 3.32 | 16.52 | 6 | 48 | 2.21 | 3.05 | $+43.32 |

**Robust pick:** chop 1-5, quality 2-7, cascade≥2, target_mid 1.5 — holdout PF 2.09, Sharpe 2.99, P&L $+41.22.

## AAPL

Train 2025-06-16→2026-03-13 (187d), Holdout 2026-03-16→2026-06-12 (63d). 18/18 combos eligible.

| rank | chop | qual | casc | tgt | TR trades | TR PF | TR Shrp | TR Cal | TR MDD% | HO trades | HO PF | HO Shrp | HO P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2-5 | 3-7 | 2 | 1.5 | 73 | 3.13 | 2.32 | 9.81 | 10 | 50 | 1.74 | 1.99 | $+15.57 |
| 2 | 2-5 | 2-7 | 2 | 1.5 | 73 | 3.10 | 2.30 | 9.75 | 10 | 50 | 1.80 | 2.11 | $+16.59 |
| 3 | 3-5 | 3-7 | 2 | 1.5 | 72 | 2.94 | 2.30 | 9.54 | 10 | 46 | 1.54 | 1.53 | $+11.97 |
| 4 | 3-5 | 2-7 | 2 | 1.5 | 72 | 2.92 | 2.29 | 9.48 | 11 | 46 | 1.59 | 1.65 | $+12.99 |
| 5 | 2-5 | 3-7 | 3 | 1.5 | 68 | 2.97 | 2.18 | 9.18 | 10 | 50 | 1.67 | 1.88 | $+14.16 |
| 6 | 2-5 | 2-7 | 3 | 1.5 | 68 | 2.94 | 2.17 | 9.12 | 10 | 50 | 1.72 | 2.00 | $+15.18 |

**Robust pick:** chop 2-5, quality 3-7, cascade≥2, target_mid 1.5 — holdout PF 1.74, Sharpe 1.99, P&L $+15.57.


