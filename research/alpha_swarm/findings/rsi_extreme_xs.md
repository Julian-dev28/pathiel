# A10 rsi_extreme_xs

**Hypothesis:** Cross-sectional RSI reversal — long the most-oversold RSI(14) basket /
short the most-overbought, daily rebal, regime-gated — is +EV.

**Exact rule:** day i, RSI(14) per coin (closes <= i). reversal book: long bottom-m=6 RSI,
short top-m=6. momentum book = inverse. Fill open[i+1], hold H, daily overlapping rebal.
Regime via BTC 7d sign. vs random 50/50 baseline. book{rev,mom} x regime{all,up,down} x H{1,3,5}.

## Results (per-leg signed gross %, 12bps)
| book | regime | H | EV0 | EV12 | EV25 | OOS h1 / h2 | excess |
|---|---|---|---|---|---|---|---|
| reversal | all | 5 | -1.63 | -1.75 | -1.88 | -2.20 / -1.30 | -1.72 |
| reversal | up | 5 | -2.04 | -2.16 | -2.29 | -3.66 / -0.64 | -2.12 |
| momentum | all | 5 | +1.63 | +1.51 | +1.38 | +1.96 / +1.06 | +1.55 |
| momentum | down | 5 | +1.32 | +1.20 | +1.07 | **+0.90 / +1.51** | +1.24 |
| momentum | down | 3 | +0.87 | +0.75 | +0.62 | +0.71 / +0.79 | +0.71 |

## Verdict: **REFUTED** (the stated oversold-reversal hypothesis)
Deciding number: the reversal book is negative at 0 bps in **all 9** configs (best -0.40,
worst -2.04) and negative in both halves — long-oversold is a money-loser in this universe,
same kill as xs_reversal. The *inverse* (RSI-momentum, long high-RSI) is robustly positive,
and the down-regime H=5 variant is genuinely clean (EV12 +1.20, both halves + with h2 > h1,
survives 50 bps, excess +1.24) — but that is just the **live cross-sectional momentum book**
restated through a vol-normalized rank, not a new edge. Net: reversal refuted; RSI-momentum
re-confirms the existing momentum factor (notably durable in down-regime).
