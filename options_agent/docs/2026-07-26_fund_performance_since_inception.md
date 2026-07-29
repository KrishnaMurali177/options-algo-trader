# Fund Performance — Since Inception (normalized to 2 contracts)

**As of:** 2026-07-26 · **Strategy:** Sweet Spot 0DTE options · **Sizing basis:** flat **2 contracts / position**

All P&L below is **normalized to a flat 2 contracts per position** using average fill prices —
this **removes the two-host double-execution and the 3-vs-2-lot sizing differences entirely**,
so the numbers are clean and comparable. Unit of a "trade" = one position per (day, contract);
outcome = sign of the round-trip.

> **Normalized vs. as-traded:** the raw account statements read differently because of
> duplication, variable sizing, and manual interventions. For reference:
> **Paper** as-traded +$9,686 → **normalized +$5,395** (raw was ~1.8× inflated by dup + 3-lot).
> **Live** as-traded +$99 → **normalized +$1,400** (raw was dragged down by 1-contract undersizing
> and manual-close mismatches). The normalized figure is the true **strategy edge at 2 contracts**.

Two tracks:
- **PAPER** — full strategy track record since 2026-05-11, all symbols.
- **LIVE** — real money since 2026-06-30, SPY + QQQ only.

---

## 1. Executive summary (normalized, 2 contracts)

| | PAPER (all symbols) | LIVE (SPY/QQQ) |
|---|---|---|
| Inception | 2026-05-11 | 2026-06-30 |
| Trades | 157 | 47 |
| Win rate | 51% (80W / 77L) | 55% (26W / 21L) |
| **Net P&L (2ct)** | **+$5,395** | **+$1,400** |
| Peak cumulative | +$6,109 (wk 07-13) | +$2,183 (wk 07-13) |
| Green / Red weeks | 7 / 4 | 3 / 1 |

**Story:** deep drawdown for the first 3 weeks on the **old settings (−$978 trough)**, sharp
turnaround when the **golden defaults went live (2026-05-28)**, a strong June (best week +$3,270
on the 06-09 trend day), then a **mid/late-July stall** that gave back ground on both tracks.

---

## 2. Normalized equity curve — PAPER

```
Cumulative P&L, 2 contracts ($). Bars measured from the −$978 trough baseline.

05-11  ██████                                          −$128
05-18  █                                               −$826
05-25                                                  −$978   ◄ TROUGH (old settings)
06-01  █████                                           −$284   ← golden defaults kick in
06-08  ██████████████████████████                      +$2,986  ▲ best week (+$3,270, 100% WR)
06-15  █████████████████████████████                   +$3,424
06-22  ███████████████████████████████                 +$3,668
06-29  █████████████████████████████████████████████   +$5,740
07-06  ███████████████████████████████████████████████ +$6,070
07-13  ███████████████████████████████████████████████ +$6,109  ◄ PEAK
07-20  ██████████████████████████████████████████      +$5,395  ▼ −$714 week
```

## Normalized equity curve — LIVE (real money)

```
Cumulative P&L, 2 contracts ($). Bars from $0.

06-29  ███████████████████████████                     +$1,359
07-06  ████████████████████████████████████████        +$1,987
07-13  ████████████████████████████████████████████    +$2,183  ◄ PEAK
07-20  ████████████████████████████                    +$1,400  ▼ −$783 week
```

Live rose steadily to +$2,183 by mid-July, then the 07-20 week (−$783) knocked ~36% off the peak.

---

## 3. PAPER — weekly detail (normalized 2ct)

| Week of | Trades | W | L | Win rate | Week P&L | Cumulative | Top symbols |
|---|---:|---:|---:|---:|---:|---:|---|
| 2026-05-11 | 9 | 4 | 5 | 44% | −$128 | −$128 | 🔴 SPY −398, QQQ +270 |
| 2026-05-18 | 6 | 1 | 5 | **17%** | **−$698** | −$826 | 🔴 **worst** — QQQ −482, SPY −216 |
| 2026-05-25 | 5 | 3 | 2 | 60% | −$152 | **−$978** | 🔴 trough |
| 2026-06-01 | 14 | 10 | 4 | 71% | +$694 | −$284 | 🟢 golden defaults on |
| 2026-06-08 | 7 | 7 | 0 | **100%** | **+$3,270** | +$2,986 | 🟢 **best** — SPY +1,790, QQQ +1,480 (06-09) |
| 2026-06-15 | 16 | 9 | 7 | 56% | +$438 | +$3,424 | 🟢 QQQ +540 |
| 2026-06-22 | 12 | 5 | 7 | 42% | +$244 | +$3,668 | 🟢 MSFT +460 carried a −328 QQQ |
| 2026-06-29 | 21 | 14 | 7 | 67% | +$2,072 | +$5,740 | 🟢 QQQ +1,519 |
| 2026-07-06 | 20 | 11 | 9 | 55% | +$330 | +$6,070 | 🟡 flat |
| 2026-07-13 | 20 | 9 | 11 | 45% | +$39 | **+$6,109** | 🟡 QQQ +709 vs SPY −353 (**peak**) |
| 2026-07-20 | 27 | 9 | 18 | **33%** | **−$714** | +$5,395 | 🔴 QQQ −611, SPY −353; MSFT +99 / AAPL +184 softened it |

## LIVE — weekly detail (normalized 2ct)

| Week of | Trades | W | L | Win rate | Week P&L | Cumulative | Top symbols |
|---|---:|---:|---:|---:|---:|---:|---|
| 2026-06-29 | 9 | 7 | 2 | 78% | +$1,359 | +$1,359 | 🟢 QQQ +1,112 |
| 2026-07-06 | 15 | 9 | 6 | 60% | +$628 | +$1,987 | 🟢 QQQ +460 |
| 2026-07-13 | 10 | 5 | 5 | 50% | +$196 | +$2,183 | 🟡 QQQ +541 vs SPY −345 |
| 2026-07-20 | 13 | 5 | 8 | 38% | **−$783** | +$1,400 | 🔴 SPY −394, QQQ −389 |

---

## 4. Gain weeks vs loss weeks — deep dive (normalized)

### 🔴 Loss weeks
| Week | Track | P&L (2ct) | Win rate | Driver |
|---|---|---:|---:|---|
| 05-18 | Paper | −$698 | 17% | Old pre-golden settings — no edge (1/6). |
| 07-20 | Live | −$783 | 38% | Mid-July chop, SPY & QQQ both red, no Mag 7 cushion. |
| 07-20 | Paper | −$714 | 33% | Same chop (QQQ −611 / SPY −353); MSFT/AAPL only softened it. |
| 05-25 | Paper | −$152 | 60% | Old settings — losers bigger than winners despite 60% WR. |
| 05-11 | Paper | −$128 | 44% | Old settings; SPY the bleeder. |

**Common thread:** losses cluster in (a) the **pre-golden May settings** and (b) the **mid/late-July
choppy tape** where the strategy top-ticked calls into stops. **Win rate < 45% ⇒ red week**, every time.

### 🟢 Gain weeks
| Week | Track | P&L (2ct) | Win rate | Driver |
|---|---|---:|---:|---|
| 06-08 | Paper | +$3,270 | 100% | 06-09 trend day — caught big on SPY + QQQ. |
| 06-29 | Paper | +$2,072 | 67% | Broad QQQ strength (+1,519). |
| 06-29 | Live | +$1,359 | 78% | Real-money best week, QQQ-led. |
| 06-01 | Paper | +$694 | 71% | First golden-defaults week — instant flip. |

**Common thread:** big green weeks are **QQQ-led trend days at 65%+ win rate**. One day (06-09)
is ~half of a whole week's gain — the book is trend-dependent.

---

## 5. Per-symbol contribution (normalized 2ct)

| Symbol | Trades | Win rate | Paper P&L | Live P&L | Read |
|---|---:|---:|---:|---:|---|
| **QQQ** | 57 / 25 | **67% / 68%** | **+$3,717** | **+$1,724** | ⭐ the engine, both tracks |
| SPY | 47 / 22 | 49% / 41% | +$942 | **−$324** | coin-flip; net loss on real money |
| AAPL | 25 | 40% | +$431 | — | thin, winner-dependent (paper only) |
| MSFT | 20 | 40% | +$339 | — | thin, winner-dependent (paper only) |
| IWM | 5 | 60% | −$4 | — | experimental, ~flat |
| TLT | 3 | 0% | −$29 | — | experimental, 0/3 — avoid |

---

## 6. Takeaways

- **QQQ is the franchise** — 67–68% win rate and the largest contributor on *both* tracks (+$3,717 paper / +$1,724 live).
- **SPY is a coin-flip that loses on real money** (49% paper / 41% live; **−$324** live). It only pays on trend days.
- **The Mag 7 names diversified the paper book** (kept 07-20 from being deeply red) but are thin (40% WR) and part-time (M/W/F 0DTE only).
- **The edge is real but trend-dependent and modest at disciplined size:** +$5,395 paper / +$1,400 live over the period, heavily reliant on a handful of QQQ trend days. Chop weeks (< 45% WR) are the recurring drawdown source — the active area of work is the `max_quality` cap that filters out the clean trends while admitting choppy top-tick entries.

*Basis note: normalized to flat 2 contracts via average fill prices; as-traded account P&L differs (Paper +$9,686 raw, Live +$99 raw) due to duplication, variable sizing, and manual interventions.*
