# 0DTE vs Weekly Options — Detailed Signal Comparison

> **Version:** 2026-05-29 (baseline)
> **Status:** Pre-real-data. Weekly system uses synth pricing only.
> **Next update:** When real Alpaca weekly option bars are integrated, re-validate signal behavior differences.

Both systems call the **exact same scoring functions** (`compute_quality_score`, `compute_choppiness`,
`MomentumCascadeDetector`, `gainz_signal`). The difference is entirely in what produces the inputs
to those functions and how positions are managed.

This document walks through every signal, side by side, with concrete examples showing how the same
scorer produces different results on intraday vs daily bar data.

---

## Table of Contents

1. [Direction Decision](#1-direction-decision)
2. [Opening Range → Daily Range (Quality slots #1-2)](#2-opening-range--daily-range)
3. [Recent Momentum → Daily Momentum (Quality slot #3)](#3-recent-momentum--daily-momentum)
4. [Quality Scorer Walkthrough](#4-quality-scorer-walkthrough)
5. [Choppiness Filter](#5-choppiness-filter)
6. [Momentum Cascade / Explosion](#6-momentum-cascade--explosion)
7. [Position Lifecycle](#7-position-lifecycle)
8. [Exit Priority Chain](#8-exit-priority-chain)
9. [Theta Decay Model](#9-theta-decay-model)
10. [Risk Geometry](#10-risk-geometry)
11. [Parameter Defaults Side by Side](#11-parameter-defaults)
12. [Score Distribution & Trade Count](#12-score-distribution--trade-count)
13. [Dead Slots & Implications](#13-dead-slots--implications)

---

## 1. Direction Decision

The direction decision determines whether to buy a call or a put. The two systems use
completely different logic for this.

### 0DTE: Intraday Momentum Score

```
OpeningRangeAnalyzer computes M ∈ [-100, +100] from 7 weighted signals on 5-min bars:

  Price vs range (±5) + RSI (±20) + MACD (±20) + VWAP (±20) + Volume (±5) + OR candle (±10) + VIX (±5)
  Total budget: 85

  M ≥ +25  →  buy_call
  M ≤ -25  →  buy_put
  else     →  SKIP (no trade)
```

**Character:** Reactive. Can flip direction within hours. A stock can be "buy_call" at 10:30
and "buy_put" by 11:00 if momentum reverses.

### Weekly: SMA Crossover + Price Position

```
if SMA20 > SMA50 AND price > SMA20:
    direction = "buy_call"
elif SMA20 < SMA50 AND price < SMA20:
    direction = "buy_put"
else:
    REJECT — no trade
```

**Character:** Structural. Requires both the moving average crossover AND price on the correct
side. This is a multi-day trend signal — it doesn't flip intraday. A stock in a bearish SMA
configuration that rallies above SMA20 for one day won't trigger buy_call because SMA20 < SMA50
still holds.

### Example: SPY on a ranging day

```
  Day: 2026-03-15 (SPY at $545, SMA20=$543, SMA50=$540)

  0DTE at 10:45:  5-min bars show price broke above 60-min range high ($544.80).
                  RSI=65, MACD hist=+0.12, above VWAP.
                  M = +30 + 20 + 20 + 20 + 5 = +95  →  buy_call ✓

  0DTE at 11:30:  Price pulled back to $544.20. RSI=38, MACD hist=-0.08, below VWAP.
                  M = -5 - 20 - 20 - 20 = -65  →  buy_put ✓ (flipped!)

  Weekly:         SMA20 ($543) > SMA50 ($540), price ($545) > SMA20 ($543)
                  →  buy_call ✓ (stable all day, all week)
```

**Takeaway:** 0DTE direction is a momentum snapshot that can change every 5 minutes. Weekly
direction is a trend state that holds for days or weeks.

---

## 2. Opening Range → Daily Range

Both produce: `or_direction`, `or_momentum` (-100 to +100), `or_confirmed` (|momentum| ≥ 40).
These feed quality scorer slots #1 and #2.

### 0DTE: OpeningRangeAnalyzer

| Signal | Weight | Data Source |
|--------|--------|-------------|
| Price vs 60-min range | ±30 | 5-min bars 09:30-10:30 |
| Intraday RSI-14 | ±20 | 5-min closes |
| Intraday MACD 12/26/9 | ±20 | 5-min closes |
| Price vs session VWAP | ±20 | Cumulative 5-min typical × volume |
| Volume surge | ±5 | OR bar vol vs day avg |
| OR candle body | ±10 | 60-min candle open→close |
| VIX direction | ±5 | Daily VIX + momentum direction |
| **Total budget** | **110** | |

**The "range"** = high/low of the first 60 minutes (09:30-10:30 ET).

### Weekly: DailyRangeAnalyzer

| Signal | Weight | Data Source |
|--------|--------|-------------|
| Price vs prior day's range | ±30 | Yesterday's High/Low |
| Daily RSI-14 | ±20 | Daily closes |
| Daily MACD 12/26/9 histogram | ±20 | Daily closes |
| Price vs 20-day SMA | ±20 | Daily closes |
| Volume vs 20-day avg | ±10 | Daily volume |
| Prior day candle body | ±10 | Yesterday's Open→Close |
| **Total budget** | **110** | |

**The "range"** = prior day's high and low.

### Key Differences

| Aspect | 0DTE | Weekly |
|--------|------|--------|
| Range meaning | Intraday consolidation (60 min) | Full trading day |
| RSI source | 5-min intraday RSI (noisy, whipsaws) | Daily RSI-14 (smooth, laggy) |
| MACD source | 5-min MACD (reacts in minutes) | Daily MACD (reacts in days) |
| Price reference | vs session VWAP (intraday flow) | vs 20-day SMA (multi-week trend) |
| MACD threshold | hist > 0.05 / < -0.05 (tight, 5-min noise) | hist > 0.1 / < -0.1 (wider, daily moves) |
| Breakout frequency | Multiple per day | ~1 per day (if price broke yesterday's range) |

### Example: SPY Bullish Breakout

```
── 0DTE (10:45 AM, 5-min bars) ──────────────────────────────────────────

  60-min range: High=$544.80, Low=$543.20 (width=$1.60)
  Current price: $545.50 (above range high)

  1. Price vs range:     $545.50 > $544.80  →  +30  (breakout!)
  2. Intraday RSI:       RSI=68             →  +20  (bullish)
  3. Intraday MACD:      hist=+0.15         →  +20  (positive)
  4. Price vs VWAP:      $545.50 > $544.90  →  +20  (above VWAP)
  5. Volume surge:       1.4x avg           →  +5   (confirms direction)
  6. OR candle body:     +0.45 of range     →  +10  (bullish body)
  7. VIX:                VIX=19, M>0        →  +5   (elevated + bullish)

  or_momentum = +110 → clamped to +100
  or_direction = "bullish"
  or_confirmed = True (|100| ≥ 40)


── Weekly (daily bars, today's close) ──────────────────────────────────

  Prior day: High=$544.80, Low=$541.50 (width=$3.30)
  Today's close: $546.10 (above prior day high)

  1. Price vs prior day:  $546.10 > $544.80  →  +30  (breakout above yesterday)
  2. Daily RSI-14:        RSI=62             →  +20  (bullish)
  3. Daily MACD hist:     hist=+0.35         →  +20  (positive, above 0.1 threshold)
  4. Price vs SMA20:      $546.10 > $543.00  →  +20  (above)
  5. Volume vs avg:       1.3x 20-day avg    →  +10  (confirms direction)
  6. Prior day candle:    body=+0.5 of range →  +10  (bullish candle yesterday)

  or_momentum = +110 → clamped to +100
  or_direction = "bullish"
  or_confirmed = True (|100| ≥ 40)
```

In this example both produce similar outputs. But note:
- The 0DTE breakout above $544.80 happens within minutes and could reverse by noon.
- The weekly breakout above yesterday's $544.80 high is a full-day closing event — if today
  closes above it, that's a structurally significant move.

### When They Diverge

```
── Choppy intraday, trending weekly ─────────────────────────────────

  0DTE at 11:00: Price oscillating around range mid. RSI=48, MACD flat.
                 M = 0 + 0 + 0 + (-20) + 0 + 0 + 0 = -20
                 or_direction = "neutral"  →  no trade

  Weekly:        Price closed at $548 (well above yesterday's $545.50 high).
                 RSI=64, MACD hist=+0.28, above SMA20.
                 M = +30 + 20 + 20 + 20 + 0 + 10 = +100
                 or_direction = "bullish"  →  trade candidate
```

The weekly system sees the bigger picture (price trending up all week) even when intraday
action is noisy.

---

## 3. Recent Momentum → Daily Momentum

Both produce: `recent_dir`, `recent_momentum` (-100 to +100).
These feed quality scorer slot #3.

### 0DTE: RecentMomentumAnalyzer

Lookback: **6 × 5-min bars = 30 minutes of real-time data.**

| Signal | Weight |
|--------|--------|
| Price change over 30 min | ±30 |
| Green/red bar ratio (6 bars) | ±20 |
| Intraday RSI-14 | ±15 |
| Price vs SMA20 | ±15 |
| Volume trend (first half vs second half) | ±10 |
| **Total budget** | **90** |
| **Direction threshold** | ≥ 20 = bullish |

### Weekly: DailyMomentumAnalyzer

Lookback: **5 daily bars = one trading week.**

| Signal | Weight |
|--------|--------|
| Price change over 5 days | ±30 |
| Green/red daily candle ratio | ±20 |
| Daily RSI-14 | ±15 |
| Price vs SMA20 | ±15 |
| Volume trend (first vs second half) | ±10 |
| **Total budget** | **90** |
| **Direction threshold** | ≥ 20 = bullish |

### Same structure, different thresholds

| Detail | 0DTE | Weekly |
|--------|------|--------|
| "Strong" price change | > 0.15% in 30 min | > 1.0% over 5 days |
| "Mild" price change | > 0.05% in 30 min | > 0.3% over 5 days |
| Green/red ratio "strong" | 5 of 6 bars | 4 of 5 days |
| Stability | Can flip every scan (15 min) | Stable for days |

### Example

```
── 0DTE at 11:15 ─────────────────────────────────────────

  Last 30 min (6 bars): 4 green, 2 red. Price +0.18%.
  RSI=58, above SMA20. Volume flat.

  1. Price change:    +0.18%  →  +30  (strong)
  2. Green/red:       4/6     →  +10  (mostly green)
  3. RSI:             58      →   0   (neutral, 40-60 band)
  4. SMA20:           above   →  +15
  5. Volume:          flat    →   0

  recent_momentum = +55
  recent_dir = "bullish"


── Weekly ─────────────────────────────────────────────────

  Last 5 days: 4 green, 1 red. Price +1.8%.
  RSI=63, above SMA20. Volume rising (1.2x).

  1. Price change:    +1.8%   →  +30  (strong, > 1.0%)
  2. Green/red:       4/5     →  +20  (4 of 5 green)
  3. RSI:             63      →  +15  (bullish, > 60)
  4. SMA20:           above   →  +15
  5. Volume:          1.2x    →  +10  (increasing, confirms)

  recent_momentum = +90
  recent_dir = "bullish"
```

**Key difference:** The weekly momentum is much more stable. It won't flip from +90 to -60
in the next 15 minutes like the 0DTE version can. But it also won't catch intraday reversals.

---

## 4. Quality Scorer Walkthrough

`compute_quality_score()` is a pure function. It takes keyword arguments and produces a score
0-13. Both systems call it identically. Here's a slot-by-slot comparison of how each system
provides inputs.

### Slot-by-Slot: What Each System Passes

| Slot | Signal | Max | 0DTE Input Source | Weekly Input Source | Behavioral Difference |
|------|--------|-----|-------------------|--------------------|-----------------------|
| #1 | OR direction aligned | +2 / -1 | `OpeningRangeAnalyzer` (5-min bars, 60-min range) | `DailyRangeAnalyzer` (prior day's H/L) | Weekly breakout is rarer but more decisive |
| #2 | OR confirmed (|M|≥40) | +1 | Same analyzer, 85-point budget | Same interface, 110-point budget | **Weekly reaches ±40 more easily** (higher budget) |
| #3 | Recent direction aligned | +2 / -1 | `RecentMomentumAnalyzer` (30 min) | `DailyMomentumAnalyzer` (5 days) | Weekly is stable; 0DTE flips often |
| #4 | Volume surge (≥1.2x) | +1 | Current bar vol vs 20-bar SMA | Daily vol vs 20-day avg | Both use the same ratio, different timeframes |
| #5 | VIX elevated (≥18.5) | +1 | Same VIX for both | Same VIX for both | Identical behavior |
| #6 | VWAP/SMA20 confirmation | +1 | Falls back to SMA20 (golden default) | Falls back to SMA20 (no intraday VWAP) | **Identical** — both use SMA20 proxy |
| #7 | Trend (SMA20 vs SMA50) | +1 | Daily SMAs | Daily SMAs | **Identical** — same data source |
| #8 | Dual momentum (both ≥40) | +1 | OR + recent both ≥40 | DailyRange + DailyMomentum both ≥40 | Weekly more likely to align (stable inputs) |
| #9 | Volume climax (≥2.0x) | +1 | Current bar vol 2x+ avg | Daily vol 2x+ avg | Intraday spikes more common |
| #10 | ZLEMA trend | +1 | ZLEMA 8/21 on 5-min closes | ZLEMA 8/21 on daily closes | Daily ZLEMA is smoother, more reliable |
| #11 | VPVR level break | +1 | Computed from intraday volume profile | **Not passed** (always False) | **DEAD SLOT for weekly** — always +0 |

### Example: Full Score Comparison

```
Scenario: SPY trending bullish, moderate VIX, direction = buy_call

── 0DTE Score ─────────────────────────────────────────────

  #1  OR direction aligned:     bullish, momentum=+75    → +2
  #2  OR confirmed:             |75| ≥ 40                → +1
  #3  Recent direction aligned: bullish, momentum=+55    → +2
  #4  Volume surge:             vol_ratio=1.4            → +1
  #5  VIX elevated:             VIX=21.5                 → +1
  #6  SMA20 confirmation:       price > SMA20            → +1
  #7  Trend alignment:          SMA20 > SMA50            → +1
  #8  Dual momentum:            OR=+75, recent=+55       → +1  (both ≥ 40)
  #9  Volume climax:            vol_ratio=1.4 (< 2.0)    → +0
  #10 ZLEMA trend:              bullish                   → +1
  #11 VPVR level break:         True (broke HVN)          → +1
                                                          ────
                                        QUALITY SCORE:      12  🟢 HIGH


── Weekly Score (same day, same underlying conditions) ────

  #1  OR direction aligned:     bullish, momentum=+80    → +2
  #2  OR confirmed:             |80| ≥ 40                → +1
  #3  Recent direction aligned: bullish, momentum=+60    → +2
  #4  Volume surge:             vol_ratio=1.3            → +1
  #5  VIX elevated:             VIX=21.5                 → +1
  #6  SMA20 confirmation:       price > SMA20            → +1
  #7  Trend alignment:          SMA20 > SMA50            → +1
  #8  Dual momentum:            OR=+80, recent=+60       → +1  (both ≥ 40)
  #9  Volume climax:            vol_ratio=1.3 (< 2.0)    → +0
  #10 ZLEMA trend:              bullish                   → +1
  #11 VPVR level break:         not passed (False)        → +0  ← DEAD
                                                          ────
                                        QUALITY SCORE:      11  🟢 HIGH
```

**Differences on this example:**
- Weekly loses 1 point on VPVR (#11) — this slot is never active for weeklies.
- Weekly's OR and recent momentum are slightly higher (smoother daily data, higher budgets).
- Net effect: weekly score is 1 point lower due to the dead VPVR slot.

### Worst Case: The VPVR Gap

The maximum quality score for weekly is effectively **12** (not 13), because slot #11
(VPVR level break) is always 0. This means:
- Fewer "🟢 HIGH" quality labels (need 7+ to qualify)
- The quality gate (3-7) still works, but the upper tail is cut by 1.

---

## 5. Choppiness Filter

Both call `compute_choppiness(bars, lookback=N, vix=V)` — same function, same scoring,
same 0-10 scale.

### What Changes: Bar Definition

| Aspect | 0DTE | Weekly |
|--------|------|--------|
| Each "bar" is | 1 × 5-min candle | 1 × daily candle |
| Default lookback | 30 bars = 2.5 hours | 10 bars = 2 weeks |
| CI measures | Intraday path efficiency | Multi-day path efficiency |
| Direction reversals | 5-min bar flips | Daily close-to-close flips |
| Range ratio | Day range / avg 5-min bar | 2-week range / avg daily bar |
| Max consecutive | Longest run of same-dir 5-min bars | Longest run of same-dir days |

### What "Choppy" Means in Each Context

```
── 0DTE: Choppy Day ────────────────────────────────────────

  SPY is stuck between $544.50 and $545.20 all morning.
  5-min bars flip up/down every 1-2 bars. CI=0.78.
  No directional edge — breakout signals are fake-outs.

  Chop score: 8/10 → 🌊 EXTREMELY CHOPPY → no 0DTE trade

  This is a single bad day. Tomorrow could trend perfectly.


── Weekly: Choppy Period ───────────────────────────────────

  SPY has been between $540 and $548 for the past 2 weeks.
  Daily closes alternate up/down. CI=0.72.
  No sustained multi-day move — weekly options will theta-bleed.

  Chop score: 7/10 → 🌊 CHOPPY → no weekly trade

  This is a multi-week regime. It could persist for weeks.
```

### VIX Scaling Applies to Both

The vol_scale factor (`vix / 20`, clamped 0.67-1.5) adjusts thresholds identically:
- VIX 30 → factor 1.5 → harder to be "choppy" (high-vol days/weeks have legitimate large swings)
- VIX 13 → factor 0.65 → easier to be "choppy" (small moves in low-vol are pure noise)

### Gate: Both Use chop 2-5

Both systems require chop between 2 and 5 (inclusive). But the interpretation differs:
- **0DTE chop=2:** "trending intraday" — 5-min bars moving consistently in one direction
- **Weekly chop=2:** "trending multi-day" — daily closes moving consistently up or down

A chop=2 on daily bars is a much stronger trend signal (5+ days of consistent direction)
vs chop=2 on 5-min bars (30 minutes of consistent direction, which happens every trending hour).

---

## 6. Momentum Cascade / Explosion

Both call `MomentumCascadeDetector().analyze(indicators, quality, or_momentum, recent_momentum)`.

### Explosion Score Breakdown (0-10)

| Signal | Max | 0DTE Behavior | Weekly Behavior |
|--------|-----|---------------|-----------------|
| Price acceleration | +2 | ATR-normalized velocity on 5-min bars. Dual-detector (4-bar weighted + 3-bar RoC). Consensus = +2, single = +1. | **Same function, but daily bars**. Daily ATR-normalized acceleration is much rarer — requires multi-day price surge. |
| Volume climax | +2 | Volume ≥ 2x avg AND accelerating on 5-min bars. Detects institutional intraday surges. | Daily volume ≥ 2x avg. Earnings days, Fed days — otherwise rare. |
| VPVR cascade | +2 | Price broke ≥ 3 High Volume Nodes on intraday volume profile. | **DEAD — no intraday VPVR data**. Always +0. |
| Quality boost | +2 | Quality ≥ 8 → elite alignment | Same logic, but weekly max quality is effectively 12 (VPVR slot dead), so this CAN still fire. |
| Dual momentum | +2 | OR ≥ 40 AND recent ≥ 40 | DailyRange ≥ 40 AND DailyMomentum ≥ 40. More likely to align (stable inputs). |
| ZLEMA trend | +1 | ZLEMA 8/21 crossover on 5-min closes | ZLEMA 8/21 crossover on daily closes. Smoother signal. |
| **Effective max** | **10** | **All 6 signals active** | **Max ~8** (VPVR cascade is dead, -2) |

### Practical Impact

```
── 0DTE: Explosion=6 (common on trending days) ───────────

  Price acceleration: +1 (single detector)
  Volume climax:      +2 (institutions piling in at 10:45)
  VPVR cascade:       +1 (broke 2 HVNs)
  Quality boost:      +0 (quality=7, needs 8)
  Dual momentum:      +2 (OR=+70, recent=+65)
  ZLEMA:              +0 (not yet crossed)
                      ──
  Explosion:           6  🔔 WATCH


── Weekly: Explosion=4 (same quality/momentum inputs) ────

  Price acceleration: +0 (no multi-day acceleration detected)
  Volume climax:      +0 (volume 1.5x avg, not 2x)
  VPVR cascade:       +0 (DEAD — always 0)
  Quality boost:      +0 (quality=7, needs 8)
  Dual momentum:      +2 (DailyRange=+80, DailyMomentum=+60)
  ZLEMA:              +1 (daily ZLEMA crossed bullish)
  Regime:             +1 (SMA alignment)
                      ──
  Explosion:           4  🔔 WATCH
```

**Result:** Weekly explosion scores run **2-4 points lower** on average than 0DTE scores
for similar market conditions. Combined with `EXPLOSION_MIN = 2`, the weekly system needs
at least dual momentum alignment or ZLEMA confirmation to pass. This is an intentional
selectivity gate — weeklies shouldn't fire on marginal setups.

---

## 7. Position Lifecycle

This is the **fundamental** architectural difference.

### 0DTE: Single-Bar Resolution

```
  Entry                  Exit
    ↓                      ↓
    |====== 1 to 60 bars ===|
    |   (5 to 300 minutes)  |
    |                       |
    Every 5-min bar:
    - Check stop/target/stagnation/decay/gainz
    - Position MUST close by 3:00 PM ET (same day)

  Time unit: 5-min bar
  Max hold:  ~54 bars (4.5 hours, 10:30 → 15:00)
  Overnight: NEVER (0DTE expires today)
```

### Weekly: Multi-Day Position

```
  Entry (Mon/Tue/Wed)                              Exit (any day before expiry)
    ↓                                                ↓
    |=========== 1 to 5+ trading days ================|
    |                                                 |
    Day 1: Entry scan (10:00-11:00 ET), evaluation at close
    Day 2: Morning evaluation, midday check, EOD summary
    Day 3: Same as Day 2
    ...
    Day N: Exit triggered or DTE expiry

  Time unit: 1 trading day
  Max hold:  7 calendar days (DTE window)
  Overnight: ALWAYS (positions span days)
  Gap risk:  Significant — each morning opens at a potentially different price
```

### Position State

```
── 0DTE Position ─────────────────────────────────────────

  {
    "entry_time": "2026-03-15 10:35:00",
    "entry_price": 544.80,           ← underlying price
    "stop": 544.00,                  ← hit → full exit
    "target": 546.00,                ← hit → full exit
    "option_premium": 2.45,          ← option price paid
    "bars_held": 0,                  ← incremented each 5-min bar
    "mfe": 0.0,                      ← max favorable excursion
    "trailing_stop": null,           ← not used in 0DTE
  }

  Storage: In-memory only. Persisted to journal at EOD.


── Weekly Position ───────────────────────────────────────

  {
    "entry_date": "2026-03-15",
    "entry_underlying": 544.80,      ← underlying price at entry
    "stop_underlying": 539.60,       ← 1.5 × ATR below entry
    "target_underlying": 551.80,     ← 2.0 × ATR above entry
    "strike": 550.0,                 ← option strike
    "expiration": "2026-03-21",      ← Friday
    "dte_at_entry": 6,               ← calendar days to expiry
    "premium_at_entry": 3.20,        ← option price paid
    "trailing_stop": null,           ← updated at R-multiple tiers
    "max_favorable_excursion": 0.0,  ← MFE in R-multiples
    "daily_evaluations": [           ← one per day held
      {"date": "2026-03-16", "close": 545.90, "pnl_r": 0.31, ...},
      {"date": "2026-03-17", "close": 547.20, "pnl_r": 0.69, ...},
    ]
  }

  Storage: Per-position JSON file.
           YYYY-MM-DD_SYMBOL_OPEN.json → renamed to _CLOSED.json on exit.
```

---

## 8. Exit Priority Chain

Both systems check exits in priority order. The first match wins.

### 0DTE Exit Chain

```
  1. Hard stop         — price hit stop level
  2. Target hit        — price hit profit target (T1 at 0.75R, T2 at 1.5R)
  3. Decay target      — effective target shrinks via decay_factor (halflife=8 bars/40min)
  4. Theta breakeven   — projected theta burn ≥ 80% of current option profit
  5. Stagnation        — no meaningful move after 12 bars (60 min); early tier at bar 8
  6. Gainz exit        — opposing reversal candle with ≥ 0.3R profit
  7. Time stop         — 3:00 PM ET forced close (0DTE expires today)
```

### Weekly Exit Chain

```
  1. Gap stop          — adverse overnight gap > 1.5 × ATR        ← NEW, 0DTE has no gaps
  2. Trailing stop     — R-multiple tiered stops                  ← NEW for weekly
  3. Hard stop         — underlying breaches stop level
  4. Decay target      — theta decay threshold (halflife=2.0 sessions)
  5. DTE expiry        — position held to < 1 DTE remaining       ← replaces time stop
  6. Regime degradation — choppiness spikes above threshold        ← NEW for weekly
  7. Stagnation        — no meaningful move after 2 sessions
  8. Gainz exit        — opposing reversal candle with profit
```

### What's New in Weekly

| Exit | Why It Exists | 0DTE Equivalent |
|------|---------------|-----------------|
| **Gap stop** | Overnight gaps can move 2-3% against you. If SPY gaps down $8 when your stop was $5 away, you're past your intended risk. | N/A — 0DTE never holds overnight |
| **Trailing stop** | Multi-day positions can trend significantly. Lock in profits at R-multiple tiers: +1R→breakeven, +1.5R→+0.5R, +2R→+1R | 0DTE uses T1/T2 fixed targets instead |
| **DTE expiry** | Force-close before option expires worthless | Time stop at 3:00 PM ET serves same purpose |
| **Regime degradation** | Market regime can change mid-hold (e.g., choppy→trending→choppy). If choppiness spikes above 7 during hold, exit. | N/A — 0DTE is too short for regime change |

### Trailing Stop Detail (Weekly Only)

```
  At entry: trailing_stop = None

  Position P&L reaches +1.0R:
    trailing_stop = breakeven (entry price)
    "We're not losing money on this trade anymore"

  Position P&L reaches +1.5R:
    trailing_stop = entry + 0.5R
    "Locked in half a risk unit of profit"

  Position P&L reaches +2.0R:
    trailing_stop = entry + 1.0R
    "Locked in a full risk unit — this is now a free trade"

  Example:
    Entry at $545.00, stop at $540.00 (risk = $5.00, 1R = $5.00)
    Price reaches $550.00 (+1R) → trail = $545.00 (breakeven)
    Price reaches $552.50 (+1.5R) → trail = $547.50 (+0.5R)
    Price reaches $555.00 (+2R) → trail = $550.00 (+1R)
    Price pulls back to $549.50 → trail hit at $550.00 → exit with +$5.00 (+1R)
```

---

## 9. Theta Decay Model

Theta decay is non-linear for options. Both systems model it, but with different time units.

### 0DTE Decay

```
  decay_factor = max(0.4, 0.5 ^ (bars_held / 8))

  Time unit: 5-min bars
  Halflife: 8 bars = 40 minutes
  Floor: 0.4 (target never shrinks below 40% of original)

  Bar 0:   decay_factor = 1.00  →  target at 100% of original
  Bar 4:   decay_factor = 0.84  →  target at 84%
  Bar 8:   decay_factor = 0.50  →  target at 50% (halflife)
  Bar 16:  decay_factor = 0.40  →  target at 40% (floor)
  Bar 24:  decay_factor = 0.40  →  stays at floor

  Why: 0DTE options lose ~50% of remaining premium in the last 2 hours.
       After 40 minutes in a stagnant trade, the target must shrink fast.
```

### Weekly Decay

```
  decay = premium * 0.70 * (1 - sqrt(dte_remaining / dte_at_entry))

  Time unit: calendar days (DTE)
  The 0.70 factor means max decay at expiry is 70% of premium.
  sqrt() curve makes decay accelerate as expiry approaches.

  DTE at entry: 5
  Day 1 (DTE=4): decay = premium * 0.70 * (1 - sqrt(4/5)) = premium * 0.074  (7.4%)
  Day 2 (DTE=3): decay = premium * 0.70 * (1 - sqrt(3/5)) = premium * 0.158  (15.8%)
  Day 3 (DTE=2): decay = premium * 0.70 * (1 - sqrt(2/5)) = premium * 0.258  (25.8%)
  Day 4 (DTE=1): decay = premium * 0.70 * (1 - sqrt(1/5)) = premium * 0.388  (38.8%)
  Day 5 (DTE=0): decay = premium * 0.70 * (1 - sqrt(0/5)) = premium * 0.700  (70.0%)

  Why: Weekly options lose theta gradually early in the week, then accelerate
       into expiry. A 5-DTE option loses ~7% on day 1 but ~39% on day 4.
```

### Visual Comparison

```
  Remaining effective target (% of original)

  100% ─ ●─────●
   90% ─ │     │ ╲
   80% ─ │     │   ╲              0DTE (bars, 5-min each)
   70% ─ │     │     ╲
   60% ─ │     │       ╲
   50% ─ │     │         ● ─ ─ ─ ─ ─ halflife (8 bars = 40 min)
   40% ─ │     │         │ ●─────── floor
   30% ─ │     │         │
         0     4         8    12   16   20  bars

  100% ─ ●───────────●
   90% ─ │           │ ╲
   80% ─ │           │   ╲         Weekly (calendar days)
   70% ─ │           │     ╲
   60% ─ │           │       ╲
   50% ─ │           │         ╲
   40% ─ │           │           ╲
   30% ─ │           │             ● ── 70% decayed at expiry
         DTE5        DTE4     DTE3    DTE2   DTE1   DTE0
```

---

## 10. Risk Geometry

### 0DTE

```
  Risk defined by: Active range (60-min OR, blended with recent 30-min)

  Entry:    Range high - 10% of width (buy call)
            Range low + 10% of width (buy put)
  Stop:     Midpoint + 10% of width (tighter than bare midpoint)
  Risk (R): |Entry - Stop|, floor at 0.3 × ATR
  Target 1: Entry + 0.75R
  Target 2: Entry + 1.5R (or 1.5R for cascade E≥6)

  Typical R: $0.50 - $2.00 (5-min range width)
  Typical premium: $1.00 - $5.00
```

### Weekly

```
  Risk defined by: ATR-14 on daily bars

  Entry:    Current close (position opened at market)
  Stop:     Entry ± 1.5 × ATR (directional)
  Risk (R): 1.5 × ATR
  Target:   Entry ± 2.0 × ATR (directional)

  Typical R: $5.00 - $15.00 (daily ATR × 1.5)
  Typical premium: $1.00 - $5.00

  Example (SPY, ATR=$3.50):
    Entry:  $545.00
    Stop:   $545.00 - (1.5 × $3.50) = $539.75  ($5.25 risk)
    Target: $545.00 + (2.0 × $3.50) = $552.00  ($7.00 target)
    R:R = 1:1.33
```

---

## 11. Parameter Defaults

| Parameter | 0DTE | Weekly | Same? |
|-----------|------|--------|-------|
| Quality gate | 3-7 | 3-7 | YES |
| Chop gate | 2-5 | 2-5 | YES |
| Explosion gate | ≥ 2 | ≥ 2 | YES |
| Chop lookback | 30 bars (2.5 hrs) | 10 bars (2 weeks) | Different unit |
| Entry window | 10:30-14:00 ET | Mon-Wed, 10:00-11:00 ET | Different |
| Max trades/period | 4/day | 2 open/symbol | Different |
| Max stops/period | 1/day | 1/week | Different scope |
| Direction logic | Momentum M ≥ ±25 | SMA20/50 crossover | **DIFFERENT** |
| Stop | 60% of range | 1.5 × ATR | **DIFFERENT** |
| Target | 0.75R / 1.5R (cascade-scaled) | 2.0 × ATR | **DIFFERENT** |
| Trailing stops | Not used (T1/T2 instead) | R-multiple tiers | **DIFFERENT** |
| Decay halflife | 8 bars (40 min) | 2.0 sessions | Different unit |
| Stagnation | 12 bars (60 min) + early tier at bar 8 | 2 sessions (2 days) | Different unit |
| Gainz exit | RSI 70/30, body 0.7, min 0.3R | RSI 75/25, body 0.6, min 0.3R | Slightly different |
| VIX filter | Max 30, spike 20% | Max 30, spike 20% | YES |
| Option delta | 0.50 (ATM) | 0.35 (~35-delta OTM) | **DIFFERENT** |
| Option DTE | 0 (same-day expiry) | 3-7 (next Friday) | **DIFFERENT** |
| Overnight risk | None | Gap stop at 1.5 × ATR | N/A vs NEW |
| Regime degradation | Not checked mid-position | Chop spike → exit | N/A vs NEW |

---

## 12. Score Distribution & Trade Count

### Why 0DTE Gets ~1.9 Trades/Day but Weekly Gets ~0.07/Day (19/year)

The selectivity gates cascade multiplicatively:

```
── 0DTE: ~250 trading days, ~1.9 trades/day = ~475 trades/year ──

  Window:       10:30-14:00 = 42 × 5-min bars per day
  × evaluations: Every bar (or every 3 bars with cooldown)
  × direction:  M ≥ ±25 fires on ~60% of evaluations (noisy signals flip)
  × quality:    Q 3-7 passes ~55% of directed signals
  × chop:       chop 2-5 passes ~40% of days
  × explosion:  E ≥ 2 passes ~70% of quality-passing signals
  × trade cap:  4/day

  Result: lots of opportunities because intraday noise creates many valid entry points.


── Weekly: ~250 trading days, ~19 trades/year ───────────────────

  Window:       Mon/Tue/Wed only = 156 eligible days
  × evaluations: Once per day (daily bar close)
  × direction:  SMA20/50 aligned AND price on right side → ~40% of days
  × quality:    Q 3-7 passes ~30% (smooth inputs cluster mid-range, VPVR dead)
  × chop:       chop 2-5 on daily bars → ~35% (daily chop is more stable)
  × explosion:  E ≥ 2 with dead VPVR → ~45% (needs dual momentum or ZLEMA)
  × position cap: 2 open/symbol, 1 stop/week

  156 × 0.40 × 0.30 × 0.35 × 0.45 ≈ 2.9 trades per year
  (actual: 19 — the gates aren't independent, correlated signals pass together)
```

**The low count is by design.** The weekly system is meant to take only high-conviction
multi-day setups. The 0DTE system fires frequently because:
1. Intraday noise creates many direction signals
2. It evaluates every 5 minutes (not once per day)
3. It can trade Mon-Fri (not just Mon-Wed)
4. Intraday VPVR gives the cascade detector more signals

---

## 13. Dead Slots & Implications

### Slots That Don't Work for Weekly

| Slot | Why Dead | Impact |
|------|----------|--------|
| #11 VPVR level break | Requires intraday volume profile data. Weekly only has daily bars. `vpvr_level_broken` is never passed, defaults to `False`. | Quality max is 12 not 13. Harder to hit Q≥8 for cascade quality boost (+2). |
| Cascade: VPVR cascade (+2) | Same reason — no intraday VPVR levels to detect cascading breakdowns. | Explosion max is ~8 not 10. Weekly trades will have lower explosion scores. |
| VWAP (as real intraday signal) | No real intraday VWAP available. Falls back to SMA20 — but this is also what 0DTE does by default (golden config). | **No actual impact** — both use SMA20 proxy. |

### Slots That Behave Differently

| Slot | 0DTE Behavior | Weekly Behavior |
|------|---------------|-----------------|
| #2 OR confirmed | 85-point budget, |M|≥40 requires ~47% of max signals to agree | 110-point budget, |M|≥40 requires only ~36% — **easier to confirm** |
| #8 Dual momentum | Noisy inputs, both need |M|≥40. Fires ~20% of the time. | Stable inputs, both more likely to exceed 40. Fires ~35% of the time. |
| #10 ZLEMA | 5-min ZLEMA flips multiple times per day | Daily ZLEMA flips every few days — when it aligns, it's more meaningful |

### What This Means for the Quality Distribution

```
  0DTE quality distribution (approximate, from 730d backtest):

  Q=0-2:  15%  ← blocked by gate
  Q=3:    10%  ← low end of gate
  Q=4-5:  25%  ← bulk of trades
  Q=6-7:  30%  ← sweet spot
  Q=8-9:  15%  ← high quality
  Q=10+:   5%  ← elite (requires VPVR + volume climax)


  Weekly quality distribution (estimated from signal analysis):

  Q=0-2:  20%  ← blocked by gate (more neutral days)
  Q=3:    12%  ← low end of gate
  Q=4-5:  30%  ← bulk of trades
  Q=6-7:  25%  ← sweet spot
  Q=8-9:  10%  ← high quality (VPVR dead → harder to reach)
  Q=10+:   3%  ← very rare without VPVR
```

The weekly distribution is shifted slightly left because of the dead VPVR slot and the
stable-but-binary nature of daily signals (they tend to cluster in the middle rather than
producing extreme scores).

---

## Summary: Key Takeaways

1. **Same scorer, different inputs.** `compute_quality_score()` is identical — what differs
   is the analyzers that produce `or_direction`, `or_momentum`, `recent_dir`, `recent_momentum`.

2. **Weekly inputs are smoother and more stable.** A Q=6 on weekly bars represents multi-day
   alignment of RSI + MACD + SMA + breakout + momentum. A Q=6 on 0DTE bars could flip to Q=3
   in the next 15 minutes.

3. **Two dead slots** (VPVR: quality #11 and cascade VPVR cascade) cap the weekly maximum
   quality at 12 and explosion at ~8. This is acceptable — weeklies don't need explosive
   intraday moves, they need sustained multi-day trends.

4. **Direction logic is fundamentally different.** 0DTE uses intraday momentum (reactive,
   flips fast). Weekly uses SMA crossover (structural, holds for days).

5. **Position lifecycle is the core difference.** 0DTE exits within hours. Weekly holds for
   days, introducing overnight gap risk, trailing stops, and regime degradation checks that
   0DTE doesn't need.

6. **Trade frequency: ~475/year vs ~19/year.** By design — weekly should be highly selective.
   The parameter sweep may find configurations that open this up to 30-50 trades/year.
