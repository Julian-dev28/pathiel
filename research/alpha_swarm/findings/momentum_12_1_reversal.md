# B8 momentum_12_1_reversal

## Hypothesis
Classic 12-1 skip momentum (score = return over [t-L, t-skip], skipping the most recent window
to dodge short-term reversal) beats the no-skip version, in XS and TS forms.

## Exact rule
- Score = return from index (t-1-skip-L) to (t-1-skip), known at decision t. L in {30,60,90}, skip in {0,7,14}.
- XS: daily-rebal market-neutral book, long top-8 / short bottom-8 by score. Metric annualized Sharpe.
- TS: per-coin long if score>0 else short, fill i+1 open, 25% stop, 5d hold, step=5. OOS via summarize.

## Results
XS book annSharpe (deciding: does skip beat skip=0?):
| L | skip0 | skip7 | skip14 |
|--|--|--|--|
| 30 | 2.17 | 2.12 | 2.38 |
| 60 | 2.05 | 2.09 | 1.12 |
| 90 | 1.74 | 2.01 | 0.98 |

TS variant: only **L60/skip7** is OOS-robust (EV 0.615, h1 1.09 h2 0.091); L60/skip0 has higher raw
EV (0.743) but is NOT robust (h2 -0.28). 1 of 9 cells robust. Random-side baseline EV 0.143.

## VERDICT: REFUTED
Deciding numbers: the skip gives at best a marginal, INCONSISTENT XS Sharpe change (L30 skip14
2.38>2.17, but L60 skip14 1.12<2.05 — no monotone benefit). In TS only 1 of 9 (L,skip) cells passes
OOS-both-halves (L60/skip7), and its h2 (+0.09%) is thin — exactly the rate you expect from chance
across 9 trials, so it fails the multiple-comparison gate. The positive XS books are the pre-existing
XS-momentum edge, NOT the 12-1 skip refinement. Skipping the recent window adds no robust value here
(consistent with B7: the recent 7d window CARRIES the signal, so removing it doesn't help).
Survivor-biased / cost-free upper bound on the XS numbers.
