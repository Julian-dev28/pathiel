# B9 vol_of_vol_regime

## Hypothesis
A vol-of-vol (second-order vol) spike precedes a regime change / trend break, usable as a
de-risk trigger.

## Exact rule
- Market vol = avg coin 5d realized vol per day. vov = stdev of last 10 market-vol values (known at t).
- (1) Predictive: classify vov tercile at t, measure forward 5d BTC abs-return, realized vol, worst draw.
- (2) Overlay: scale XS book 0.5x when vov in top tercile vs flat. annSharpe + maxDD lift.

## Results
(1) vov tercile -> forward 5d BTC:
| tercile | n | fwd5d absRet% | fwd5d RV% | fwd5d minRet% |
|--|--|--|--|--|
| low | 93 | 4.48 | 1.82 | -3.47 |
| mid | 93 | 3.87 | 2.15 | -2.91 |
| high | 94 | **3.34** | 1.69 | -3.03 |

High vov precedes the SMALLEST forward move, not the largest. No predictive signal for regime break.

(2) Overlay: flat Sharpe 2.776 / maxDD -24.0%; derisk_vov Sharpe **2.869** (+0.093) / maxDD **-21.7%**
(+2.4pp), helps h2 (0.91->1.36), cuts mean return 0.399->0.366.

## VERDICT: REFUTED
Deciding number: high-vov forward 5d BTC abs-move = **3.34%** vs low-vov **4.48%** — the opposite of
the hypothesis (vov spikes precede CALMER tape, not trend breaks). The de-risk overlay's small gain
(+0.09 Sharpe / +2.4pp DD) is a generic risk-off effect (same magnitude as B2's correlation gate),
not vov-driven prediction. No tradeable vov-as-regime-predictor edge. Survivor-biased upper bound.
