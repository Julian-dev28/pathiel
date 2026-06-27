# Seasonality (hour-of-day / day-of-week / session block) — agent `seasonality`

## Hypothesis
Crypto has a systematic UTC calendar/clock window (hour-of-day, weekday, or
session block) where a directional bet has edge (the crypto analogue of equities'
overnight drift / Monday effect).

## Data
`dataset.json`, 40 liquid perps. 1h candles ~2001 bars/coin (2026-04-04 →
2026-06-27, ~83 days). 1d candles 301 bars/coin (2025-08-31 → 2026-06-27).
Timestamps are bar-START, UTC, hour/day aligned. Confirmed.

## Rule tested (lookahead-safe)
Calendar buckets are known in advance, so entry is decision-free.
- **Hour-of-day**: trade hour h = enter at the OPEN of the bar covering [h:00,h+1:00),
  exit at its CLOSE. Bucket return = (C−O)/O. Pooled over all coins/days.
- **Day-of-week**: 1d bar (00:00→00:00 UTC), enter day OPEN, exit day CLOSE,
  grouped by weekday (Mon=0..Sun=6).
- **Session block**: Asia 00–08, Europe 08–16, US 16–24 UTC. One position:
  enter OPEN of first hour, exit CLOSE of last hour, per coin per day.
Best bucket → long candidate, worst bucket → short candidate. Validated with
`alpha_lib.summarize` (slippage sweep + OOS first/second half).
**Multiple-comparison guard**: with 24 hours + 7 weekdays + 3 sessions you WILL
find spurious in-sample winners, so the bar is OOS-both-halves same-sign AND
(for the weekday survivor) a date-level permutation test.

## Results — pooled coin-trade level (`summarize`)
| Bucket | best/worst IS mean | slip0 | slip12 | slip25 | OOS H1 / H2 @12bps | verdict |
|---|---|---|---|---|---|---|
| Hour LONG h17 | +14.2bps | +0.142% | +0.022% | −0.108% | +0.046% / −0.002% | SIGN-FLIP, dies @25bps |
| Hour SHORT h13 | −17.0bps | +0.170% | +0.050% | −0.080% | −0.014% / +0.114% | SIGN-FLIP, dies @25bps |
| Session LONG US(16-24) | +16.7bps | +0.167% | +0.047% | −0.083% | +0.216% / −0.125% | SIGN-FLIP, dies @25bps |
| Session SHORT Asia(00-08) | −3.2bps | +0.032% | −0.089% | −0.219% | −0.071% / −0.106% | weak, dies @6bps |
| Weekday LONG Mon | +51.9bps | +0.519% | +0.399% | +0.269% | −0.604% / +1.424% | SIGN-FLIP (noise) |
| **Weekday SHORT Thu** | **−156bps** | **+1.56%** | **+1.44%** | **+1.31%** | **+1.98% / +0.89%** | passes pooled gate |

Hour-of-day and session blocks: **every** candidate either sign-flips across the
two time halves or dies by 25bps slippage. Multiple-comparison audit: only 5/24
hours are both-half-positive and 4/24 both-half-negative *pre-cost*, and none
clears 25bps — exactly the spurious-winner count you expect from noise.

## The one survivor: Thursday short — audited at the correct independence level
The pooled n=1699 is fake independence: 40 highly-correlated coins on the same
~43 Thursdays. Re-ran at the **date level** (cross-sectional mean per day = ONE
observation, 43 Thursdays):

- Thu cross-sectional daily mean (long) = **−156bps**, median **−151bps**
  (median ≈ mean → NOT a one-crash-day artifact).
- Down-days: **29/43 Thursdays** were negative (67% short hit-rate).
- Drop the 2 worst crash Thursdays (Feb-05 −1550bps, Jun-04 −796bps): still
  **+107bps** short EV. Drop worst-1: +123bps.
- OOS date-level SHORT: **H1 +184bps (n21) / H2 +130bps (n22)** — both halves same sign.
- t-stat (date-level, vs 0) = **2.58** on n=43.
- **Permutation test** (shuffle weekday labels 20k×, test the MIN weekday mean →
  family-wise over all 7 days): **p = 0.039**.
- Not just bear drift: whole-sample daily mean = −16bps; Thursday −156bps is ~10×
  the baseline drift and the only weekday that extreme (next worst Sun −22, Tue −18).
- Costs are negligible: a 24h hold pays one round-trip (~12–25bps) against a
  156bps signal.
- Plausible mechanism: Deribit weekly options/futures expire Friday 08:00 UTC;
  Thursday = de-risking/unwind into expiry. Speculative, not proven here.

## VERDICT
- **Hour-of-day: REFUTED.** Best/worst both sign-flip across halves and die by 25bps.
- **Session blocks: REFUTED.** US-long and Asia-short both sign-flip / die early.
- **Day-of-week (Monday long etc.): REFUTED.** Monday sign-flips (−0.60% then +1.42%).
- **Thursday short: MARGINAL.** It is the only seasonality bucket that survives
  date-level independence, both OOS halves, and outlier removal, with permutation
  p = 0.039 and t = 2.58. But it is NOT `ROBUST`: the deciding number is **n = 43
  weekly observations in a single ~10-month, net-down window**, and p = 0.039 sits
  right on the multiple-comparison edge. One regime, thin weekly sample,
  survivor-biased universe (today's liquid set → upper bound).

## If traded (parameters) and the single biggest risk
- Rule: short the cross-sectional basket (or BTC) at **Thursday 00:00 UTC open**,
  cover at **Friday 00:00 UTC close** (24h hold). Size small.
- **Biggest risk it isn't real**: only ~43 independent weekly samples from one
  bearish regime. A weekly calendar effect needs years to confirm; this window
  cannot distinguish "Thursday expiry-unwind effect" from "the drawdowns of
  Sep-2025→Jun-2026 happened to cluster on Thursdays." Treat as a SHADOW/forward
  test signal at most, not a live keeper. Do not size it like the validated
  cross-sectional-momentum edge.
