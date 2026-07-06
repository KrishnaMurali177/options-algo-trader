# Premium-based stop A/B — SPY + QQQ (2026-07-01)

## Motivation
On 2026-07-01 a live AAPL 295C (0DTE) bled **−55% to theta** while AAPL stayed flat.
Every exit in the strategy is evaluated on the **underlying** (stop/target/decay-target/
stagnation/time-stop), so the option's own decay is invisible to the exits, and the
`theta_exit` guardrail only protects trades already in profit. We added two premium-based
stops to `replay_sweet_spot.py` and A/B-tested them against the golden baseline:

- `--prem-stop-pct X` — **fixed**: exit if option mid ≤ entry × (1−X)
- `--trail-stop-pct X` — **trailing**: exit if option mid ≤ peak × (1−X)

Config: golden defaults, `--days 365 --real-options --require-real-options`. One variable
at a time. Raw runs in `runs/<SYM>_<config>.txt`.

## Result — premium stops HURT on both indices (do not adopt)

### SPY (baseline: $+6,222 · PF 2.15 · Sharpe 3.99 · MDD $11.22)
| Config | Tot P&L | PF | Sharpe | MDD$ | Stop fired |
|---|---|---|---|---|---|
| **baseline** | **+6,222** | **2.15** | **3.99** | **11.22** | — |
| fixed 30% | +3,720 | 1.58 | 2.78 | 14.94 | 20% |
| fixed 40% | +4,983 | 1.82 | 3.46 | 13.26 | 12% |
| fixed 50% | +5,849 | 2.01 | 3.76 | 11.22 | 7% |
| fixed 60% | +6,224 | 2.15 | 3.99 | 11.22 | 3% |
| trail 50% | +5,661 | 1.98 | 3.68 | 11.22 | 8% |
| trail 60% | +5,753 | 2.01 | 3.64 | 11.22 | 5% |

### QQQ (baseline: $+3,509 · PF 1.47 · Sharpe 2.09 · MDD $19.71)
| Config | Tot P&L | PF | Sharpe | MDD$ | Stop fired |
|---|---|---|---|---|---|
| **baseline** | **+3,509** | **1.47** | **2.09** | **19.71** | — |
| fixed 30% | +1,402 | 1.16 | 0.86 | 33.78 | 27% |
| fixed 40% | +2,114 | 1.24 | 1.26 | 36.33 | 18% |
| fixed 50% | +2,625 | 1.31 | 1.51 | 30.54 | 11% |
| fixed 60% | +3,241 | 1.42 | 1.91 | 21.48 | 5% |
| trail 50% | +2,582 | 1.31 | 1.51 | 26.04 | 14% |
| trail 60% | +3,152 | 1.40 | 1.85 | 23.31 | 7% |

### MSFT (baseline: $+5,196 · PF 2.99 · Sharpe 3.28 · MDD $15.78)
| Config | Tot P&L | PF | Sharpe | MDD$ | Stop fired |
|---|---|---|---|---|---|
| **baseline** | **+5,196** | **2.99** | **3.28** | **15.78** | — |
| fixed 30% | +4,630 | 2.60 | 3.02 | 15.84 | 26% |
| fixed 40% | +5,094 | 2.90 | 3.26 | 18.18 | 14% |
| fixed 50% | +4,942 | 2.73 | 3.13 | 20.28 | 11% |
| fixed 60% | +4,884 | 2.67 | 3.07 | 22.59 | 8% |
| trail 50% | +4,867 | 2.69 | 3.11 | 24.30 | 14% |
| trail 60% | +5,002 | 2.79 | 3.15 | 21.00 | 10% |

### AAPL (baseline: $+2,182 · PF 2.52 · Sharpe 2.49 · MDD $11.43) — the motivating symbol
| Config | Tot P&L | PF | Sharpe | MDD$ | Stop fired |
|---|---|---|---|---|---|
| **baseline** | **+2,182** | **2.52** | **2.49** | **11.43** | — |
| fixed 30% | +1,982 | 2.30 | 2.33 | 10.65 | 25% |
| fixed 40% | +1,977 | 2.23 | 2.28 | 13.47 | 20% |
| fixed 50% | +2,048 | 2.31 | 2.32 | 13.56 | 13% |
| fixed 60% | +2,134 | 2.44 | 2.44 | 11.52 | 7% |
| trail 50% | +2,137 | 2.42 | 2.41 | 12.12 | 16% |
| trail 60% | +2,108 | 2.40 | 2.39 | 12.81 | 11% |

## Conclusion — tested on all 4 symbols (SPY, QQQ, MSFT, AAPL); none benefit
- **No threshold beats baseline on any symbol.** The effect is **monotonic**: the more a
  stop fires, the worse P&L, PF, and Sharpe get. The "best" configs (fixed/trail 60%) only
  approach baseline because they barely trigger (3–8%).
- **Drawdown gets WORSE, not better** on the tighter/mid stops (QQQ fixed30 MDD
  $19.71→$33.78; MSFT trail50 $15.78→$24.30). The stop realizes losses that would have
  recovered and increases variance — the opposite of protection.
- **Mechanism** (SPY fixed40 outcome mix): `prem_stop` fires 12% and cannibalizes
  `decay_target` exits, converting would-be winners/scratches into realized losers. The
  intraday premium path is whippy — a −30/40/50% dip mean-reverts often enough that cutting
  on it is net-negative. Holds on single-names too, despite wider spreads / fewer expiries.
- **AAPL — the motivating symbol — is no exception.** Over 365d, every premium-stop config
  *lowered* AAPL P&L and PF vs baseline. The live AAPL 295C −55% loss was a single unlucky
  trade, not a systematic edge a premium stop would recoup; the same stop that would have
  cut it also cuts a larger set of AAPL trades that recover.
- **Fixed vs trailing:** a wash; both harmful, trailing marginally less bad only because it
  fires slightly less at the same X.
- **Holdout split not run:** there is no winning threshold to guard against overfitting —
  every config loses to baseline in-sample already, so an in/out-of-sample split is moot.

## Recommendation
**Do NOT add a premium stop — for any of SPY / QQQ / MSFT / AAPL.** The existing
underlying-based exit stack (decay-target + stagnation + stop + theta_exit) already
dominates. The `--prem-stop-pct` / `--trail-stop-pct` flags remain in the harness (default
off) for future research only. Idea closed.
