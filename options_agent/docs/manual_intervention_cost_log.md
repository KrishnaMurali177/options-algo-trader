# Manual Intervention Cost Log — Live vs Paper Counterfactual

**Purpose:** track the cost of manual interventions on the **live** (real-money) account by
comparing each intervened trade against the **paper** account, which runs the same signals
untouched. Paper = "what would have happened if the agent had been left alone."

**Method:** paper and live take the same signals (verified). When a live position is closed by
hand (order `client_order_id` is a raw UUID, not the agent's `ssa-…` prefix), compare its
realized outcome to the paper agent's exit on the same contract. Cost is stated on a **flat
2-contract basis** (sizing-normalized).

> Recurring theme (see memory): manual closes on live have repeatedly turned winners into
> breakevens/losses. Live Discord alerts were disabled specifically to reduce this temptation.

## Log

| Date | Symbol | Manual action | Live result | Paper (agent) result | Cost (2ct) |
|---|---|---|---|---:|---:|
| 2026-07-29 | SPY 734P (0DTE) | Manual sell @ **$2.75** at 11:51 ET (21 min after entry @2.76) — closed at breakeven | ≈ **$0** (−$0.01/ct) | Agent held, exited **@ $3.61** (+$0.84/ct) | **≈ −$170** |

**2026-07-29 detail:** Both accounts entered SPY 734P @ ~2.76 at 11:30 (same signal). The other
trade that day (SPY 736P) was agent-managed on both and won identically (+$0.60/ct). The **only**
divergence was the manual close of 734P at breakeven; leaving it to the agent (as paper did) would
have returned **+$170 (2ct)**. Live also restarted at 12:27 ET as part of the intervention.

## Running total
| Interventions logged | Cumulative cost (2ct) |
|---|---:|
| 1 | **≈ −$170** |

*Prior known manual closes (07-22 QQQ, 07-24 SPY 743C) are not tallied here — 743C was a
deliberate risk-cut on a genuinely dying 0DTE, not a winner cut short. This log tracks
interventions that demonstrably forfeited paper-confirmed gains.*
