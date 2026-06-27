# xs_reversal — short-horizon cross-sectional REVERSAL vs the live MOMENTUM book

## Hypothesis (one sentence)
Last period's cross-sectional winners underperform next period (and losers bounce),
so a market-neutral book that longs recent losers / shorts recent winners is +EV at
short horizons — the documented complement to the live momentum book.

## Rule tested
- 1d candles, 40 perps. At rebalance day t, rank coins by trailing-k-day return
  `close[t]/close[t-k]-1` (decided on bars up to & including t).
- FILL at `open[t+1]`, hold H = rebal days, EXIT at `open[t+1+H]` (lookahead-safe, next-open fill).
- REVERSAL: long bottom-m (losers), short top-m (winners). MOMENTUM: same ranks, opposite sign.
- Grid: k∈{1,2,3,5}, m∈{4,6,8}, rebal∈{1,2,3}. Each trade = one coin-leg, side-signed gross return.
- Vol-scaling (1/realized-vol weighting) computed as a check — does not change the per-leg sign.
- Conditioning: BTC 7d-return regime (down if <0) and top-tercile cross-sectional dispersion.
- Cost: `alpha_lib.summarize` slippage sweep 0/6/12/25/50 bps + OOS first/second half @12bps.
- Intraday crossover hunt on 1h candles (k,rebal in hours).
- Scripts: `xs_reversal.py`, `xs_reversal_cond.py`, `xs_reversal_decomp.py`.

## Result 1 — Unconditional daily reversal is an EXACT mirror of momentum, and loses
Because the reversal long-leg = the momentum short-leg (equal weight, symmetric ranks),
gross `REV EV0 = -MOM EV0` at every single node. Momentum is +EV at gross across the
WHOLE grid (k=1..5, m=4/6/8, rebal=1/2/3); reversal is therefore -EV everywhere.

| node (k,m,rebal) | REV EV0 | REV EV12 | MOM EV0 | REV OOS h1/h2 @12bps |
|---|---|---|---|---|
| 1,4,1 | -0.186 | -0.306 | +0.186 | -0.39 / -0.22 |
| 1,6,1 | -0.164 | -0.284 | +0.164 | -0.34 / -0.23 |
| 2,4,2 | -0.404 | -0.524 | +0.404 | -0.62 / -0.43 |
| 3,4,1 | -0.540 | -0.660 | +0.540 | -1.05 / -0.27 |

EV in % per leg. There is NO daily k where reversal beats momentum — momentum dominates
at every daily horizon tested.

## Result 2 — The momentum↔reversal crossover lives BELOW one day (intraday), 1h candles
Reversal flips +EV at gross only for holds shorter than ~8 hours; momentum is +EV for
holds ≥ ~8h. The crossover sits around a 4–8h hold. But the intraday reversal is a
microstructure bounce: peak gross EV0 = **+0.046%/leg** (k=2h, rebal=4h), and it dies
before 6bps — EV12 is -0.07 to -0.12 at every node, both OOS halves negative.

| hold (rebal) | best REV EV0 | REV EV12 | verdict |
|---|---|---|---|
| 1–4h | +0.02…+0.046 | -0.07…-0.11 | dead on cost |
| ≥8h | negative | — | momentum regime |

Reversal re-trades the same names every 1–4h → enormous turnover; fees eat it whole.

## Result 3 — The only daily node that survives conditioning is NOT reversal, it's the
## already-live extreme-fade LONG, re-found in a relative frame
Gating k=1/m=4/rebal=1 on **BTC-down AND top-tercile dispersion** made the "both-legs"
book look ROBUST (EV12 +0.40%, OOS h1/h2 = +0.38/+0.43, survives 25bps, n=456). Leg
decomposition kills the reversal interpretation:

| leg (down+disp, k=1,m=4,rebal=1) | n | EV0 | EV12 | EV25 | OOS h1/h2 | verdict |
|---|---|---|---|---|---|---|
| both | 456 | +0.522 | +0.403 | +0.273 | +0.38/+0.43 | looks robust |
| **long losers-bounce** | 228 | +1.258 | +1.138 | +1.008 | +1.39/+0.87 | **carries it** |
| short winners-revert | 228 | -0.213 | -0.333 | -0.463 | -0.64/-0.01 | -EV noise |

The short-winners-revert leg (the actual cross-sectional reversal claim) is -EV in every
m. The whole "edge" is the long-the-biggest-recent-loser leg in a down/high-vol tape =
the **extreme-fade-LONG already flagged as the live keeper** (memory: extreme-fade-LONG
@−12%, +4.71%). Gate ablation confirms it needs BOTH gates (down-only -EV, disp-only -EV,
unconditional -EV) and it's not symmetric — so it is a one-sided long fade, not a
market-neutral reversal book.

## VERDICT — REFUTED (as a cross-sectional reversal book)
- Unconditional daily cross-sectional reversal: **REFUTED.** Exact gross mirror of the
  live momentum book; -EV at every k/m/rebal. The deciding number: `REV EV0 = -MOM EV0`,
  and momentum EV0 > 0 everywhere.
- Intraday (1h) reversal: crossover located at ~4–8h hold, but **REFUTED on cost** —
  peak gross +0.046%/leg, negative by 6bps, both OOS halves negative at 12bps.
- Conditional (BTC-down + high-dispersion) "reversal": the symmetric book is one-sided
  noise; its short-winners leg is -EV. What survives is the **long-losers-bounce leg =
  the existing extreme-fade-LONG keeper** re-discovered via a relative ranking frame, not
  a new edge. Deciding number: short-winners leg EV12 = -0.33% with OOS sign-flip.

This is inverted-momentum noise at daily scale and an uneconomic microstructure bounce
intraday. No new tradeable cross-sectional reversal edge.

## Caveats
- Survivor-biased universe (today's 40 liquid perps) → any positive number is an upper
  bound; dead coins (the ones a loser-bounce would have most hurt on) are absent, so the
  long-fade leg in particular is flattered.
- The conditional result is triple-selected (regime × dispersion × best-m), small n (228
  legs on the long leg). Treat as confirmation of the known extreme-fade-LONG, not a new
  signal worth wiring.
