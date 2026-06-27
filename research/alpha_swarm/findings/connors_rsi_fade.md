# A11 connors_rsi_fade

**Hypothesis:** Cross-sectional fade of Connors-RSI extremes (long low-CRSI / short
high-CRSI) captures short-term mean reversion.

**Exact rule:** CRSI = mean(RSI(close,3), RSI(streak,2), PercentRank(ROC1,100)), all from
closes <= i. fade book: long bottom-m=6 CRSI / short top-m=6. Fill open[i+1], hold H{1,2,3},
daily overlap rebal. regime{all,up,down}. vs random 50/50 baseline. mom = inverse.

## Results (per-leg signed gross %, 12bps)
| book | regime | H | EV0 | EV12 | OOS h1 / h2 | excess |
|---|---|---|---|---|---|---|
| fade | all | 1 | -0.18 | -0.30 | -0.37 / -0.23 | -0.38 |
| fade | down | 3 | -0.81 | -0.93 | -1.15 / -0.70 | -0.97 |
| fade | up | 3 | -0.27 | -0.39 | -0.84 / +0.08 | -0.43 |
| mom | down | 3 | +0.81 | +0.69 | +0.91 / +0.46 | +0.65 |
| mom | all | 3 | +0.56 | +0.44 | +0.85 / +0.03 | +0.40 |

## Verdict: **REFUTED**
Deciding number: the CRSI fade is negative at 0 bps in **all 18** configs (best -0.10, worst
-0.93) with both halves negative in the bulk — long-oversold-CRSI loses, same family kill as
xs_reversal and rsi reversal. The inverse (CRSI-momentum) is weakly positive and only the
down-regime H=3 holds both halves (EV12 +0.69), but that is the same down-regime momentum
overlap A10 already surfaced, and weaker. Connors-RSI adds no reversion edge here.
