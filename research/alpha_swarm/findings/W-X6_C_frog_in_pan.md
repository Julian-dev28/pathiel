# W-X6-C — frog-in-the-pan smoothness (information discreteness)

## Hypothesis
Da-Gurun-Warachka FIP: momentum is stronger for SMOOTH accruers (information arriving in
small continuous doses) than for discrete jumpers. Layer: among rank-eligible names,
prefer low information-discreteness legs — long smooth winners, short smooth losers.

## Cousin prior (cited before running)
No direct cousin in findings/. Nearest neighbor: A7 momentum_of_momentum (MARGINAL —
acceleration decays to ~0 in the dense h2 test). Acceleration != smoothness; FIP is
genuinely new here.

## Exact rule (pre-registered in hypotheses/W-X6_momentum_theory.py before first run)
- BASELINE as in W-X6-A (reproduces W-X4 PRIMARY exactly; asserted).
- id14 = information discreteness = max|daily ret| / sum|daily ret| over the trailing
  14 completed bars (>=10 valid rets required; LOW = smooth). Declared before running;
  bounded (0,1]; the "fraction of the trailing move from the largest single day".
- PRIMARY: longs = the 4 LOWEST id14 among the top-8 by pct_k14; shorts = the 4 lowest
  id14 among the bottom-8. SENSITIVITY: pools widened to 12. id14 None sorts last;
  deterministic tiebreak = pct_k rank position. Full k4 books (no empty slots).
- GATE: strict dominance vs baseline (net25 EV AND Sharpe, both halves).

## Results (n=33; $/wk at $76.8/leg)
| book | gross | net25 | oos h1/h2 | Sharpe | null p | turn | delta net25 (t) | $delta/wk |
|---|---|---|---|---|---|---|---|---|
| BASELINE | +3.88% | +3.68% | +4.34/+3.06 | +0.636 | 0.000 | 0.81 | — | — |
| FIP smooth-4-of-8 (PRIMARY) | +1.64% | +1.43% | +2.23/+0.67 | +0.259 | 0.020 | 0.86 | −2.25% (t=−2.33) | −$9.69 |
| FIP smooth-4-of-12 (SENS) | +1.13% | +0.91% | +2.00/−0.11 | +0.199 | 0.0755 | 0.89 | −2.77% (t=−2.60) | −$11.92 |

Dominance: PRIMARY 0/4, SENS 0/4 (SENS even goes negative in h2). Delta split PRIMARY:
long −1.538, short −0.703, fee −0.011. Marginal turnover: 0.86-0.89 vs 0.81 — the layer
also turns over MORE (smoothness churns membership), +0.011 to +0.020%/rebal extra fee
on top of the gross give-up. Widening the pool (SENS) makes everything worse —
monotone in the wrong direction.

## Mechanism — FIP is INVERTED in crypto
Within the top-8 winners pool, forward returns split by discreteness (median split):
DISCRETE movers +3.21% vs SMOOTH movers +1.03% (132 legs each). The equity FIP premium
is upside down here: crypto continuation lives in the names whose trailing move came in
one violent day (listing pops, unlock/news gaps, liquidation cascades keep running).
Preferring smoothness systematically deselects the very legs that drive the live book's
EV. Consistent with the live edge profile ("edge is trend-aligned momentum bursts") and
with A7's short-k acceleration being the only thing that ever looked alive.

## VERDICT: **REFUTED-AS-LAYER (0/4 both variants) — FIP sign-flips in this universe.**
Deciding numbers: net25 +1.43% vs +3.68% (PRIMARY), discrete winners out-run smooth
winners +3.21% vs +1.03% forward. Do not revisit smoothness-preference as a leg filter;
if anything the finding says discreteness is a POSITIVE marker here, but that inverse
layer was not pre-registered and is left as a hypothesis for a future cell, not a claim.

Caveats: survivor-biased cache (upper bounds); n=33; id14 window tied to the rank
window (14d) by design; funding not modeled; ungated sim.

## Scoreboard line
W-X6-C frog_in_pan_smoothness: REFUTED-AS-LAYER 0/4 both variants — smooth-leg
preference on the live book: net25 +1.43% vs +3.68% (t=−2.33), SENS worse (+0.91%, h2
negative), higher turnover too. FIP INVERTED in crypto: among top-8 winners, discrete
movers' forward +3.21% vs smooth +1.03% — the violent-day names carry the continuation.
Inverse (discreteness-preference) layer NOT tested (not pre-registered); flagged as
future-cell material only. −$9.7/wk.
