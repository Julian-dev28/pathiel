# A14 granger_leadlag_network

**Hypothesis:** A lead-lag graph across the 40 coins (beyond just BTC) lets you trade
consistent followers of consistent leaders one bar ahead.

**Exact rule:** rolling window W, LL[L][F] = corr(r_L[t], r_F[t+1]). At day i, follower
score_F = sum_L LL[L][F] * r_L(i). Long top-m=6 score / short bottom-m=6. Fill open[i+1],
hold H, daily overlap rebal. vs random 50/50 baseline. W{40,60} x H{1,2,3} (decay scan).

## Results (per-leg signed gross %, 12bps)
| W | H | EV0 | EV12 | EV25 | OOS h1 / h2 | excess |
|---|---|---|---|---|---|---|
| 40 | 1 | -0.17 | -0.29 | -0.42 | -0.23 / -0.36 | -0.37 |
| 40 | 2 | -0.25 | -0.37 | -0.50 | -0.43 / -0.32 | -0.47 |
| 60 | 1 | -0.17 | -0.29 | -0.42 | -0.23 / -0.34 | -0.37 |
| 60 | 3 | -0.17 | -0.29 | -0.42 | -0.19 / -0.39 | -0.33 |

## Verdict: **REFUTED**
Deciding number: negative at **0 bps in all 6 configs** (-0.14 to -0.27%), negative in both
OOS halves, and negative excess (-0.33 to -0.47) over random. The in-sample lagged
cross-correlations do **not** persist out-of-sample — predicted-up followers slightly
*underperform* next bar. With cost-brutal daily rebalancing this only gets worse. No tradeable
lead-lag structure beyond the contemporaneous co-movement already captured by BTC-beta.
