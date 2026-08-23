# "Buy after-hours, sell at the open" — MU / NVDA and controls

Branch: `research/overnight-drift` · Script: `options_agent/scripts/overnight_drift_study.py`
Data: Alpaca **SIP** 5-min bars, split+dividend adjusted, 2021-08-25 → 2026-08-20 (1,252 sessions/symbol).

## 1. What the metric actually is

"More money cumulatively" is three different claims. They give different answers, so
each is reported separately.

| Sense | Definition | What it tests |
|---|---|---|
| **Sum of returns** (`sum_$`) | Σ rₜ × $10k, fixed notional every night | Is the *average night* positive? Path-independent. |
| **Compounded** (`compound_$`) | ($10k)·(Π(1+rₜ) − 1), full reinvestment | The headline "overnight beats intraday" number. Inflated by volatility drag asymmetry — a leg can compound higher purely by being less volatile. |
| **Risk-adjusted** (Sharpe, t) | mean/σ | The only one that survives leverage-scaling. |

Second, the decomposition is an **identity, not a competition**:
`close-to-close = intraday × overnight`. "Overnight made all the money" is
automatically true whenever intraday drift is ≤ 0; it is not evidence of a tradable
edge, because you can't own the overnight leg without paying to get in and out.

Third, the thesis as stated (**buy AH**, sell at open) is *not* the classic overnight
effect (**buy the 4 pm close**, sell at open). Buying at 19:55 means you deliberately
**forfeit the 16:00–20:00 move**, which is exactly when earnings print. So the study
measures both.

Legs computed per session:
- `intraday` 09:30 open → 16:00 close
- `overnight` 16:00 close → next 09:30 open (classic)
- `ah_to_open` last AH print (~19:55) → next open (**the thesis**)
- `ah_to_open5` last AH print → **close of the first 5-min bar** (realistic exit, no auction print)
- `ah_to_open_adv` **high** of the last AH bar → first 5-min bar close (adverse fill ≈ half-spread)
- `buy_hold` close → close

## 2. Data caveat that nearly sank this

The repo's default Alpaca feed is **IEX**, and IEX after-hours coverage is a mirage:
MU had ≥1 AH bar on only **28%** of days, NVDA 49%, and the "last AH print" was ~16:55.
Running the thesis on IEX would have measured a **biased subset of news days**.
The account *is* SIP-entitled — SIP gives 100% coverage, prints to 19:55, and AH volume
of 8–18% of RTH. All results below are SIP. **Do not run extended-hours research on IEX.**

## 3. Results (mean bps per night, $10k notional)

| Symbol | intraday | overnight | **ah_to_open** | ah_to_open5 | ah_to_open_adv | buy_hold |
|---|---|---|---|---|---|---|
| MU | 9.0 | **18.3** | 10.5 | 13.8 | 8.7 | 27.0 |
| NVDA | 7.7 | 16.3 | **15.1** | 15.0 | 10.1 | 23.4 |
| AMD | 4.3 | 14.2 | **15.6** | 16.8 | 11.4 | 18.2 |
| SPY | 2.1 | 3.4 | 2.5 | 3.0 | 1.5 | 5.4 |
| QQQ | 2.3 | 4.2 | 2.8 | 3.3 | — | 6.4 |

Sharpe / t-stat on the thesis leg: NVDA 1.51 (t=3.36), AMD 1.26 (t=2.81),
MU 0.84 (t=1.88), SPY 0.66 (t=1.47).

Cumulative on $10k, thesis leg: NVDA +$18.8k summed / +$46.1k compounded;
MU +$13.2k / +$19.2k; AMD +$19.5k / +$46.0k.

## 4. Findings

1. **The overnight effect is real and it is bigger in MU/NVDA/AMD than in SPY/QQQ** —
   ~15 bps/night vs ~3 bps. It is not a split or data artifact (the two >25% sessions
   flagged are genuine: NVDA 2023-05-24 earnings, AMD 2025-10-03 OpenAI deal).
2. **But "cumulatively more money" is the wrong frame.** On every symbol,
   plain **buy-and-hold beats the thesis leg** in total dollars (MU 27.0 vs 10.5 bps/night).
   The overnight leg only "wins" against *intraday*, and only because intraday drift is ~0.
   You are not finding free money; you are choosing to own the risky half of the day.
3. **Waiting for after-hours is worse than just buying the 4 pm close on MU** —
   18.3 → 10.5 bps, a 7.8 bps/night giveaway, because AH is when earnings reprice.
   NVDA 2023-05-24 is the clean illustration: overnight leg +26.2%, thesis leg **+1.3%**.
   The AH entry hands the gap to whoever bought at 16:00. For NVDA/AMD it's roughly a wash.
4. **The edge is not an auction-print artifact.** Selling into the first 5-min bar
   instead of the exact open leaves the edge intact (`ah_to_open5` ≥ `ah_to_open`).
5. **It dies on friction.** Break-even round-trip cost is the mean itself:
   ~15 bps NVDA/AMD, ~10 bps MU, ~2.5 bps SPY. Charging the AH bar high as entry
   (`_adv`, a crude half-spread) already removes ~5 bps. AH spreads on MU are wider than
   NVDA's. **SPY/QQQ are unambiguously dead** — 2.5 bps cannot pay for an AH fill.
6. **Not stable across years.** Every symbol's thesis leg was **negative through 2022**
   (MU −23.5, NVDA −12.6, AMD −7.1, SPY −5.4 bps). The whole result is a 2024–2026
   bull-tape phenomenon. This is a long-beta trade wearing a market-neutral costume.
7. **AMD is lottery-dependent**: the top-5 days are 27% of the total, and ex-top-5 the
   mean drops 15.6 → 11.4 bps. NVDA is the cleanest (top-5 share **−10%**, i.e. the edge is
   broad-based, ex-top-5 t = 4.04).

## 5. Verdict

**Directionally real for NVDA (and AMD), not for MU as stated, and not for SPY/QQQ.**
NVDA's ~15 bps/night at t=3.4 with a broad-based distribution is the one result worth
anything. Even there, the margin over realistic AH-entry + open-exit friction is thin,
the trade is unhedged overnight beta on a name with 50%+ drawdowns, and it underperforms
simply holding the stock. For MU specifically the thesis is **strictly dominated by
buying the 4 pm close instead**.

**Not recommended as a standalone strategy.** If pursued, the only defensible next steps:
measure real AH NBBO spreads (not the bar-high proxy) at 19:55, test a beta-hedged
version (long name / short SPY overnight) to see whether anything survives after the
market factor is removed, and exclude earnings dates to confirm it isn't an
earnings-drift trade in disguise.
