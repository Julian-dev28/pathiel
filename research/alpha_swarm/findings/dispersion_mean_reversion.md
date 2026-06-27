# A8 dispersion_mean_reversion

**Hypothesis:** When cross-sectional return dispersion hits an extreme percentile it
reverts; trade convergence (long laggards / short leaders) gated on high dispersion.

**Exact rule:** day i, dispersion D = cross-sectional stdev of trailing-k returns. Gate:
D >= expanding-percentile (P66 or P80 of PAST D only, lookahead-safe). On gate, long
bottom-m=6 (laggards), short top-m=6 (leaders). Fill open[i+1], hold H, non-overlapping.
vs random 50/50 baseline. k{3,5,7} x H{3,5,7} x gate{P66,P80}.

## Results (per-leg signed gross %, 12bps) — selected
| gate | k | H | EV12 | OOS h1 / h2 | excess |
|---|---|---|---|---|---|
| P66 | 7 | 7 | +0.58 | -0.66 / **+1.99** | +0.70 |
| P66 | 7 | 5 | +0.36 | +0.88 / **-0.27** | +0.40 |
| P66 | 3 | 7 | +0.13 | -1.51 / **+1.92** | +0.24 |
| P80 | 3 | 7 | -1.46 | -2.26 / -0.55 | -1.35 |
| P80 | 7 | 5 | -1.01 | -1.95 / +0.12 | -0.97 |

## Verdict: **REFUTED**
Deciding number: **no config** has both OOS halves positive, and every positive-EV config
sign-flips violently across halves (k7/H7/P66: h1 -0.66, h2 +1.99). Tightening the gate to
P80 (more extreme dispersion) makes it strictly worse (uniformly negative). Convergence after
extreme cross-sectional dispersion does not reliably pay — when dispersion is extreme the
leaders kept leading as often as they reverted, and the sign is set by which regime each half
landed in. This is the dispersion-gated cousin of the already-refuted xs_reversal; gating did
not rescue it.
