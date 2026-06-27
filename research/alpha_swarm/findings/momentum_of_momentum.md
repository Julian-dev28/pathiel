# A7 momentum_of_momentum (acceleration)

**Hypothesis:** Rank coins by the acceleration of their own trend (change in momentum);
long accelerating / short decelerating beats plain momentum.

**Exact rule:** day i, accel_c = ret(i,k) - ret(i-k,k). Long top-m=6 accel, short bottom-m=6.
Fill open[i+1], hold H, non-overlapping. vs random 50/50 baseline. k{7,14,30} x H{5,7,14}.
ACCEL+ = long accelerating; ACCEL- = inverse (symmetric negative, confirms directional content).

## Results (per-leg signed gross %, 12bps) — ACCEL+
| k | H | EV0 | EV12 | EV25 | OOS h1 / h2 | excess |
|---|---|---|---|---|---|---|
| 7 | 14 | +2.96 | +2.84 | +2.71 | +2.99 / +2.66 | +3.57 |
| 14 | 7 | +0.93 | +0.81 | +0.68 | +1.17 / +0.40 | +0.92 |
| 14 | 14 | +0.70 | +0.58 | +0.45 | +1.85 / -0.83 | +1.32 |
| 30 | 5 | -0.07 | -0.19 | -0.32 | -0.78 / +0.43 | -0.15 |

**Robustness on the k7/H14 headline:** m{4,6,8} all both-halves-positive, median ≈ mean
(not outlier-driven). BUT the H=14 non-overlapping sample is only **20 rebalances**. The
honest dense **overlapping daily-rebal** version (3240 legs, full time resolution) gives
EV12 **+1.18%** but OOS **h1 +2.28 / h2 +0.055** — the second half decays to ~zero.

## Verdict: **MARGINAL**
Deciding number: under the dense/overlapping OOS test the edge collapses from h1 **+2.28%**
to h2 **+0.06%**. The eye-popping both-halves +2.7/+3.0 on the sparse H=14 sample is a
sampling-phase artifact (each half = ~10 rebalances landing favorably). Acceleration is +EV
and slippage-robust (survives 50 bps) and is distinct from plain momentum (k=30 is dead, the
edge is short-k), but it's regime-loaded: it paid in the first/down leg and went flat on the
recovery. Keeper for a shadow trial at k=7-14 H=7-14, sized small, with the explicit
expectation that it decays to ~0 outside trending-down tape. Not ROBUST.
