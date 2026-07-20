# W-X6-D — turnover-conditioned momentum (Lee-Swaminathan)

## Hypothesis
Lee-Swaminathan "momentum life cycle": momentum persists better among high-turnover
(high-attention) names. Layer: restrict the eligible pool to the top turnover quantile
before ranking. No mcap exists on perps, so the declared proxy is the 30d mean daily
notional rank — stated upfront: this conflates attention with SIZE.

## Cousin prior (cited before running)
W-X3 liquid_majors_xs: **REFUTED + NOT ADDITIVE** — top-15 by 30d MEDIAN notional; its
only numeric gate-passer was pct_k14-on-majors, "a diluted live book (49% duplicate
legs, Sh 0.360 vs 0.589), not actionable". This cell is the direct heir and differs
only in: meme exclusion KEPT (W-X3 predates the 18622d3 wiring), mean (not median)
notional, quantile cut of the eligible pool (tercile/half of 42) rather than a fixed
top-15, and judged strictly as a LAYER vs the meme-excluded baseline. Registered
expectation from the cousin: dilution/refutation; run to close it as a layer.

## Exact rule (pre-registered in hypotheses/W-X6_momentum_theory.py before first run)
- BASELINE as in W-X6-A (reproduces W-X4 PRIMARY exactly; asserted).
- PRIMARY: keep the top ceil(n/3) pool names by 30d mean notional (close*volume),
  then rank pct_k14 within the survivors, top-4/bottom-4. SENSITIVITY: top half.
- GATE: strict dominance vs baseline (net25 EV AND Sharpe, both halves).

## Results (n=33; $/wk at $76.8/leg)
| book | gross | net25 | oos h1/h2 | Sharpe | null p | turn | overlap | delta net25 (t) | $delta/wk |
|---|---|---|---|---|---|---|---|---|---|
| BASELINE | +3.88% | +3.68% | +4.34/+3.06 | +0.636 | 0.000 | 0.81 | 100% | — | — |
| top-tercile ntl30 (PRIMARY) | +2.44% | +2.27% | +1.62/+2.88 | +0.472 | 0.000 | 0.67 | 39% | −1.41% (t=−1.60) | −$6.07 |
| top-half ntl30 (SENS) | +2.46% | +2.29% | +1.67/+2.87 | +0.562 | 0.000 | 0.70 | 54% | −1.40% (t=−1.91) | −$6.00 |

Dominance: PRIMARY 0/4; SENS 1/4 (h2 Sharpe 0.687 vs 0.606 — a lone WIN drowned by h1
EV −2.67%/rebal). Delta split PRIMARY: long −1.344, short −0.102, fee +0.036. Marginal
turnover: 0.67-0.70 vs 0.81 — the restricted pool is stickier and cheaper (fee saving
up to +0.036%/rebal) but the saving is ~40x too small to cover the gross give-up.

## Mechanism
Exactly W-X3's dilution, reproduced as a layer: cutting to the high-notional third
keeps only 39% of the baseline's legs, and the forfeited legs are mostly LONGS from the
lower-notional half of the top-50 (long delta −1.344 vs short delta −0.102). The
attention premium, if it exists here, is already harvested by the top-50 universe gate
itself — within the top-50, higher notional = majors = LESS xs momentum, not more. Both
books remain solidly +EV standalone (p=0.000) — they are diluted live books, not
alternative edges, confirmed by the 39-54% leg overlap.

## VERDICT: **REFUTED-AS-LAYER (0/4 primary, 1/4 sens) — confirms W-X3 as a layer.**
Deciding numbers: net25 +2.27% vs +3.68%, h1 EV −2.7%/rebal delta, long-side forfeit
−1.34%/rebal. The Lee-Swaminathan cut is a strictly worse live book at any quantile
tried. No spec / no revert: nothing to wire.

Caveats: survivor-biased cache; notional rank is a size proxy, not true
turnover/attention (no mcap on perps — a share-of-notional-change or social proxy would
be a different, unregistered cell); n=33; funding not modeled; ungated sim.

## Scoreboard line
W-X6-D turnover_conditioned: REFUTED-AS-LAYER (0/4 primary tercile, 1/4 half) —
restricting the meme-excluded pool to top 30d-notional names: net25 +2.27%/+2.29% vs
+3.68%, only 39%/54% leg overlap, long-side forfeit −1.34%/rebal, fee saving 40x too
small. Reproduces W-X3's dilution verdict as a LAYER on the settled recipe: within the
top-50, higher notional = less xs momentum. −$6.0/wk.
