# B7 trend_ensemble_lookbacks

## Hypothesis
Voting an ensemble of TSMOM lookbacks {7,14,30,90}d is smoother (better Sharpe/OOS) than any
single lookback.

## Exact rule
- Per coin, daily. Each lookback votes sign(trailing-L return). Ensemble side = sign(sum of votes),
  skip ties. Single-LB strategies and random side for comparison.
- Fill i+1 open, 25% stop, 5d horizon, step=5 (non-overlapping). OOS+slippage via summarize.

## Results (@12bps)
| mode | n | EV% | win | sharpe | h1 | h2 | OOS |
|--|--|--|--|--|--|--|--|
| ensemble | 1503 | 0.164 | 0.518 | 0.015 | 0.36 | -0.03 | sign-flip |
| **lb7** | 1611 | **0.779** | 0.532 | 0.068 | 1.46 | 0.09 | ROBUST both |
| lb14 | 1611 | 0.378 | 0.521 | 0.034 | 1.07 | -0.33 | sign-flip |
| lb30 | 1611 | -0.522 | 0.487 | -0.048 | -1.13 | 0.09 | sign-flip |
| lb90 | 1611 | 0.383 | 0.533 | 0.034 | 1.33 | -0.58 | sign-flip |
| random | 1611 | -0.595 | 0.493 | -0.052 | -1.26 | 0.08 | sign-flip |

ensemble Sharpe lift vs best single (lb7): **-0.053**. ensemble EV 0.164 vs lb7 0.779.

## VERDICT: REFUTED
Deciding number: ensemble Sharpe lift = **-0.053** (worse than the best single). The edge lives almost
entirely in the **7-day** lookback (lb7: +0.779% EV, OOS-robust both halves, +0.76% over random);
lb30 is outright negative (-0.522). Averaging those in dilutes lb7 back to sign-flip noise. The
"smoother is better" claim fails — diversifying across lookbacks here mixes one signal with three
noise/negative ones.
LEAD (for A2 tsmom): short-horizon (1-week) TSMOM is the only robust single lookback, +0.76% excess
over random — but h2 EV is thin (+0.09%) and Sharpe low (0.068); survivor-biased upper bound.
