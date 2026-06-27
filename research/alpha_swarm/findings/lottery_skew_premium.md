# A3 lottery_skew_premium

**Hypothesis:** High recent MAX-daily-return / high realized-skew ("lottery") coins
underperform; short top-decile, long bottom-decile, weekly rebal, market-neutral.

**Exact rule:** day i, trailing window W, signal = max daily return (MAX) or sample
skewness (SKEW) of daily rets. Long bottom-m=4, short top-m=4. Fill open[i+1], hold H,
non-overlapping. Scored vs matched random 50/50 baseline. Swept W{14,20,30} x H{5,7}.

## Results (per-leg signed gross %, 12bps)
| signal | W | H | EV0 | EV12 | EV25 | OOS h1 / h2 | excess |
|---|---|---|---|---|---|---|---|
| max | 30 | 7 | -0.004 | -0.124 | -0.254 | +1.39 / **-1.80** | -0.01 |
| max | 14 | 7 | -0.613 | -0.733 | -0.863 | +0.55 / -2.15 | -0.62 |
| skew | 20 | 5 | +0.197 | +0.077 | **-0.053** | +0.11 / +0.05 | +0.11 |
| skew | 14 | 7 | -1.725 | -1.844 | -1.975 | -1.22 / -2.53 | -1.73 |

## Verdict: **REFUTED**
Deciding number: the *only* both-halves-positive config (skew W20 H5) has EV12 = +0.077%
and goes **negative (-0.053) by 25 bps** with excess just +0.11 — that is one cherry from
12 and inside the noise. The MAX variant shows a textbook sign-flip across halves (h1 > 0,
h2 ≈ -1 to -2): lottery coins underperformed early then *outperformed* hard in the second
half (high-beta bounce on the recovery leg). No stable lottery/skew premium here.
