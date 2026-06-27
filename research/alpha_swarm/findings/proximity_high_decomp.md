# W-A2 proximity_high_decomp — which HALF of A13 carries the edge?

**Hypothesis:** A13's edge lives in the long (proximity-to-50d-high), the short
(deepest-drawdown), or only the L/S spread. Decompose each leg, score as EXCESS over a
matched random-entry baseline (same side/horizon) via mc_null shuffle-label.

**Rule:** rank by close/max(50d high)−1 on bars≤i; long top-6 NEAR / short bottom-6 DEEP;
fill open[i+1]→open[i+1+H]. Pool = every coin's same-side fwd return each eligible day.

## Results (hold=7, k=6; EV in %/leg)
| leg | n | EV0 | EV12 | EV25 | EV50 | OOS h1 / h2 | excess vs random | z | p |
|---|---|---|---|---|---|---|---|---|---|
| **NEAR-long** (proximity-to-high) | 762 | +1.57 | +1.45 | +1.32 | +1.07 | **+2.70 / +0.18** | +1.06% | +2.35 | 0.011 |
| **DEEP-short** (short deepest-dd) | 762 | +1.09 | +0.97 | +0.84 | +0.59 | **+0.24 / +1.71** | **+1.60%** | **+3.54** | **0.00025** |
| DEEP-long (sanity) | 762 | −1.09 | −1.21 | −1.34 | −1.59 | −0.48 / −1.95 | −1.60 | −3.54 | 1.0 |
| NEAR-short (sanity) | 762 | −1.57 | −1.69 | −1.82 | −2.07 | −2.94 / −0.42 | −1.06 | −2.35 | 0.99 |
| **COMBINED L/S** | 1524 | +1.33 | +1.21 | +1.08 | +0.83 | **+1.47 / +0.95** | — | — | — |

Replicated at hold=5 (k6) and hold=7 (k8) — same ordering, both legs +EV-excess, combined
positive both halves, all survive 50bps.

## VERDICT: **ROBUST decomposition — the edge is in BOTH legs, and they are temporally
## complementary; the SHORT-deep leg is the stronger / more reliable standalone.**
Deciding numbers:
- **DEEP-short excess +1.60% over matched random-SHORT, z=+3.54, p=0.00025, OOS +0.24/+1.71
  (positive both halves).** The random-short pool carries the same −44% tape, so this excess is
  NOT just down-beta — shorting the deepest-drawdown beats shorting a random coin. It is the
  cleaner, both-halves-positive leg.
- **NEAR-long excess +1.06%, z=+2.35, p=0.011, but OOS +2.70/+0.18** — real but heavily
  front-loaded (the long-near-high leg paid in the first half, decays to ~flat in the second).
- The two legs pay in OPPOSITE halves (long h1, short h2) → the COMBINED L/S is positive both
  halves (+1.47/+0.95) precisely because of this complementarity. So the spread is the right
  construction; neither leg alone is robust across both halves, but short-deep is closest.

**Which half to wire:** keep the full L/S. If capital forces one leg, the SHORT-deep leg is the
more robust standalone (both-halves +, highest z). **Caveat for W-A3:** the matched-random-short
control already argues short-deep is not pure beta, but W-A3 must confirm with explicit
BTC-beta residualization before sizing the short side. Survivorship inflates the NEAR-long leg
(near-high names are selected survivors) → its +2.70 h1 is an upper bound; the short leg is
survivorship-safer (real dead coins would have paid the short MORE).
