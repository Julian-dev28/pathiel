# A2 tsmom (time-series absolute momentum)

**Hypothesis:** Each coin long if its own trailing-L return > 0 else short, vol-scaled,
is a distinct +EV factor from the live cross-sectional book.

**Exact rule:** day i signal_c = sign(close[i]/close[i-L]-1) (bars <= i). Fill open[i+1],
hold H, exit open[i+1+H]. Non-overlapping rebal every H days. Per-leg equal-weight + a
1/realized-vol portfolio series. Scored as EXCESS over a matched random-entry baseline at
the SAME realized long-fraction (controls for the down-tape short tilt).

## Results (per-leg signed gross %, 12bps)
| L | H | longfrac | EV12 | base EV12 | EXCESS | OOS h1 / h2 | both>0 |
|---|---|---|---|---|---|---|---|
| 60 | 14 | 0.26 | +2.63 | +0.82 | **+1.82** | +4.78 / -0.04 | NO (h2 flat) |
| 7 | 14 | 0.27 | +2.26 | +0.71 | +1.56 | +3.58 / +0.70 | YES |
| 30 | 14 | 0.33 | +1.62 | +0.46 | +1.17 | +2.45 / +0.73 | YES |
| 14 | 14 | 0.38 | +0.95 | +0.18 | +0.77 | +2.76 / -1.21 | NO |
| 60 | 3 | 0.28 | +0.63 | +0.26 | +0.37 | +1.13 / +0.11 | YES(thin) |
| 14 | 3 | 0.39 | +0.23 | +0.23 | +0.00 | +0.93 / -0.48 | NO |

Short holds (H=3,7) collapse to ~0 excess. The edge lives only at H=14.

## Verdict: **MARGINAL**
Deciding number: L30/H14 excess = **+1.17%/leg** with both OOS halves positive
(+2.45 / +0.73), survives to 50 bps (+1.49). Real selection value: choosing *which* coins
to short by their own trend beats a random short at the same long-fraction by +1.2 to +1.8%.
But three caveats keep it off ROBUST: (1) heavy decay h1 >> h2 (~3-6x) = regime-loaded on the
-44% tape via a 26-33% long-fraction (it is mostly "be short, smartly"); (2) only the H=14
long-hold survives, and only ~19-20 non-overlapping rebalances back the vol-portfolio (thin);
(3) survivorship upper bound. Keeper config if pursued: **L30 H14, vol-scaled** (port EV12
+1.65, both halves +). Distinct from the live XS book (absolute vs relative) so plausibly
additive, but validate the long-fraction tilt isn't just a down-beta bet before sizing it.
