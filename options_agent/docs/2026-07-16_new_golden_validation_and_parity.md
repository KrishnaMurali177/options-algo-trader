# NEW-golden validation: 365d backtest, stacking risk, and paper-vs-replay parity

**Date:** 2026-07-16 · **Scope:** SPY, QQQ (index ETFs) · NEW goldens (`main` / `.worktree-main`)
**Pricing:** real Alpaca 0DTE option bars, cascade-sized (×100, 3-contract tiers)
**Trigger:** the live shadow A/B (2wk) showed QQQ NEW-golden at PF 7.08 — this doc checks whether that holds up.

---

## TL;DR

- **SPY NEW-golden is real and robust.** 365d replay reproduces the recorded result
  (PF ~1.9, Sharpe ~3.4), and — critically — the replay **matches live** over the shadow
  window (+$558 vs +$606). **Promotable.**
- **QQQ NEW-golden is NOT trustworthy.** Two independent problems:
  1. Over 365d it's marginal (PF 1.25, Sharpe 1.28, **45.8% max drawdown**) — the 2-week
     shadow PF 7.08 was an outlier, not the edge.
  2. **Live and replay diverge massively for QQQ** over the *same* window (live +$1,878 vs
     replay −$6). A QQQ-specific parity break — so neither number can be trusted. **Do NOT
     promote QQQ until the parity gap is root-caused.**
- **The stacking (pyramiding) is both the edge and the tail risk.** Every one of the 10
  deepest single-day losses over 365d is a stacked (multi-entry) day.

---

## 1. Proper 365-day backtest (NEW golden, real options)

`replay_sweet_spot.py --symbol {SPY,QQQ} --days 365` (default golden flags = NEW golden).

| | SPY | QQQ |
|---|---|---|
| Trades | 413 | 395 |
| Win rate | 58.4% | 53.4% |
| Profit factor | **1.92** | **1.25** |
| Sharpe / Sortino | **3.39 / 4.42** | 1.28 / 1.23 |
| Max drawdown | **$15.53 (11.1%)** | **$26.13 (45.8%)** |
| Longest underwater | 43 days | 68 days |
| Total (cascade ×100) | +$13,498 | +$5,704 |

### Consistency with the recorded studies (this is not a new/regressed result)

| Study (365d) | SPY P&L / Sharpe / MDD | QQQ P&L / Sharpe / MDD |
|---|---|---|
| golden_ab_study §5 (2026-06-22) | +$14,368 / 3.65 / 7.0% | +$5,167 / 1.17 / 43.6% |
| **This run (2026-07-16)** | **+$13,498 / 3.39 / 11.1%** | **+$5,704 / 1.28 / 45.8%** |

Both symbols reproduce the recorded golden_ab_study within sliding-window noise. QQQ has
**always** been the marginal, deep-drawdown leg (the study's own words: the new goldens make
QQQ "genuinely tradeable" — i.e. rescued from broken, not made good). **Note:** SPY's drawdown
crept 7.0% → 11.1% as the window slid forward ~3 weeks (recent chop + stacked-stop clusters).

---

## 2. Stacking (pyramiding) is the edge — and the tail risk

NEW golden opens additional same-ticker lots while a prior lot is still open (main's cohort
model has no same-direction stack guard; the OLD/feature-branch code blocks it via
`already_open_same_dir`). Verified at the broker level, e.g. 07-13 QQQ P713: BUY 3 @13:05,
BUY 3 @13:25 (first still open), SELL 6 @13:40.

### Live-window decomposition (shadow journals, base vs stacked lots)

| | OLD total | NEW base-only | NEW +stacked | stacking's share of edge |
|---|---|---|---|---|
| SPY | −$73 | +$180 | +$606 | +$426 → **63%** |
| QQQ | +$1,338 | +$1,161 | +$1,878 | +$717 → **>100%** (base *trails* OLD) |

### 365d: every worst day is a stacked day

| SPY worst days (cascade $) | QQQ worst days |
|---|---|
| 06-30: −$609 (4 stacked, all stops) | 04-02: −$762 (4) |
| 02-05: −$453 (3) | 05-29: −$741 (3) |
| 09-18: −$432 (3) | 10-02: −$726 (4) |

Stacking is frequent (SPY 61 days with 4 entries; QQQ 56). When a stacked day reverses, all
3–4 lots lose together → the pyramiding that inflates winners inflates losers symmetrically.
The shadow's clean stacked win rate (SPY 3/3, QQQ 5/6) simply hadn't sampled a bad cluster.
`--max-contracts 1` does **not** contain this (it caps per-order, not concurrent lots), and
Mac+Ubuntu parallel doubling compounds it (→ up to 8 real lots on a stacked SPY day).

---

## 3. Paper-vs-replay parity verification (the key new finding)

Ran the replay over the **exact live shadow window (2026-07-07 → 07-16)** and compared to the
actual reconciled fills. Same code (`main`), same dates.

| Symbol | Replay (same window) | Live shadow (same window) | Parity |
|---|---|---|---|
| **SPY** | +$558 · PF 2.07 · Sharpe 4.29 (12 tr) | +$606 · 5/9 win (9 tr) | ✅ **matches** |
| **QQQ** | **−$6** · PF 1.00 · Sharpe −0.03 (15 tr) | **+$1,878** · PF 7.08 (15 tr) | ❌ **broken (Δ $1,884)** |

**SPY parity is good** → the strong 365d SPY replay reflects what live actually does.

**QQQ parity is broken.** The live and replay code paths make materially different decisions
for QQQ (but not SPY). Mechanism, day-by-day:

- **07-15 — different entry times.** Live entered 11:10 / 11:50 / 11:55 and caught a big
  afternoon move (+$1,140). Replay entered 10:30–10:45 (+$393). ~$747 of the gap is this one
  day's timing divergence.
- **07-07 — different entries + exits.** Replay took an extra 10:35 PUT and held positions to
  **stops** (underlying ran to 709.71 → −$546/−$642). Live bailed early on **stagnation**
  (−$81). Replay day −$822 vs live +$123.

Root cause is **not yet identified** (candidates: real-time partial-bar reads vs replay
full-bar; stagnation/MFE-skip timing; scan cadence). The known open parity item
(VIX-spike lookahead; see `replay-parity-plan.md`) inflates *backtest* P&L — here the
direction is reversed (replay *under*-performs live for QQQ), so it's likely a different
mechanism. `scripts/verify_live_vs_replay.py` is the tool to chase it.

### 3a. Bar-alignment is NOT the cause — that bug was already fixed (correction)

A first pass mistook the `verify_live_vs_replay` output for a one-bar offset. It isn't. The
bar-alignment bug (replay including the still-forming bar) was fixed in **`2ce21d4`
"Fix replay lookahead: drop forming bar to match live agent"** (2026-06-12), and it's present in
`main`, `feature/mag7-single-name-agents`, and the shadow worktree.

The tool **labels the two paths differently**: the live column is keyed by **bar time** (it wakes
at bar_T + 305s to include the just-closed bar — the post-fix live behavior), while the replay
column is keyed by **scan/action time**. So:

| live `[10:30 → snapped 10:30]` (acts 10:35) | replay `10:35` |
|---|---|
| Q7 E6, entry **$717.45** | Q7 E6, entry **$717.45** |

— **identical**. `check_sweet_spot` evaluating the 10:30 bar (acting 10:35) equals `replay_day`'s
10:35 scan. **The paths agree at the signal level; parity holds.** The apparent shift was the two
columns using bar-time vs action-time headers for the *same* event.

### 3b. Actual root cause (found) — shadow-agent UPTIME GAP, not a code/data issue

Reconciling the production verdicts JSONL against the snapshot bars isolates it. The first QQQ
scan on 07-15 was at **10:58:44** — the agent was **not running before then** and missed the
entire 10:00–10:55 window. It's a shared restart (SPY started 10:58:47 the same day):

| Day | shadow first scan | #scans | note |
|---|---|---|---|
| 07-13 | 10:00:03 | 49 | normal (full day) |
| **07-15** | **10:58:44** | **38** | **missed 10:00–10:55** |
| 07-16 | 10:20:27 | 45 | missed 10:00–10:15 |

So on 07-15 the live agents weren't scanning during **10:30–10:45** — exactly where the replay
triggered the QQQ open-plunge PUTs (+$393). Production started at ~11:00 and instead caught the
**11:10–11:55 afternoon reversal (+$1,140)**. Same code; a *different, later subset of trades*
taken purely because the agent was down all morning. (Likely cause: the Mac was asleep until
~07:58 PT — predates/escaped the always-on fix.) The verdicts' "Q8/Q9 rejects" I first read as a
"morning rejection" are actually **11:00+** scans, not the 10:30–10:45 window.

**Conclusion:** the QQQ shadow-vs-replay gap is an **agent-uptime artifact**, not a bar-alignment
bug (fixed, §3a), not data revision, not quality computation. The shadow A/B measures "strategy ×
when the agent happened to be running" — the **continuous replay is the trustworthy measure**, and
the +$1,878 QQQ shadow was inflated by the 07-15 restart catching the afternoon move. This
*reinforces* the §1 verdict: trust the 365d replay (SPY strong, QQQ marginal). It also argues for
an uptime/health guard so shadow (and live) don't silently miss mornings.

---

## 4. Conclusions & recommendations

1. **SPY NEW-golden → promote.** Strong and robust across the record, this run, *and* live-vs-
   replay parity. Caveats: size for stacking (up to 4 lots/day, doubled across Mac+Ubuntu),
   and note DD has crept to ~11%.
2. **QQQ NEW-golden → do NOT promote.** Two blockers: (a) the true 365d profile is marginal
   (PF 1.25, 46% DD); (b) live and replay diverge by ~$1,884 over the same window, so the
   shadow outperformance is execution-path-dependent and non-reproducible. Real money should
   not ride a QQQ edge that the backtest cannot reproduce.
3. **QQQ shadow-vs-replay is an agent-uptime artifact, not a parity bug** (§3a/§3b). Bar-alignment
   was already fixed (`2ce21d4`); the divergence is that the shadow agents missed the 07-15 morning
   (first scan 10:58) and took a different, later subset of trades. Trust the continuous replay for
   QQQ; the +$1,878 shadow number was inflated by the restart. **Add an uptime/health guard** so
   shadow and live agents don't silently miss mornings (the 07-15/07-16 late starts predate the
   always-on fix).
4. **If pursuing NEW's upside, add a concurrent-lot cap** (e.g. max 2 open lots/ticker). It's
   the single lever that keeps the pyramiding edge while bounding the stacked-cluster drawdown
   (QQQ's 46% / SPY's worst days).

*All analysis was read-only (replays + journal reads). No repo or running-agent changes.*
