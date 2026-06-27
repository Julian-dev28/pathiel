# W-B7 turbulence_upsize_spec

## Hypothesis
B15's "XS-book EV concentrates ~9x in the turbulent state" is a real Sharpe lift you can harvest with a
size multiplier — or it is just vol-scaling restated.

## Rule
XS book k=14,H=7,m=6 (n=40 rebals). Turbulent = top-tercile BTC 10d realized vol at the decision bar
(transparent causal proxy for B15's HMM turbulent state). Diagnostic: compare per-rebal EV RATIO vs
SHARPE RATIO turb/calm (if EV ratio >> Sharpe ratio, the concentration is vol not alpha). Then build the
turbulence-upsize multiplier (2x in turbulent) and compare annSharpe to flat and to plain inverse-vol
sizing; report maxDD.

## Results
| state | n | mean | rebal-vol | per-rebal Sharpe |
|--|--|--|--|--|
| turbulent | 14 | +0.67% | 4.54% | +0.148 |
| calm | 26 | +2.52% | 3.71% | +0.677 |
EV ratio turb/calm = 0.27x, VOL ratio 1.22x, SHARPE ratio 0.22x.

| sizing | annSh | maxDD | lift vs flat |
|--|--|--|--|
| flat | +3.279 | -9.9% | — |
| turb-upsize (2x) | +2.460 | -19.7% | -0.819 |
| inverse-vol | +1.534 | -21.3% | -1.745 |

## VERDICT: REFUTED ("turbulence alpha" framing)
Deciding number: the turbulence-upsize multiplier lifts annSharpe **-0.819** and **doubles maxDD (-19.7%
vs -9.9%)**, and under a transparent rolling-vol turbulence proxy the book's per-rebal Sharpe is actually
**HIGHER in calm (0.677) than turbulent (0.148)**. B15's 9x EV concentration does NOT survive as a Sharpe
lift: it was an artifact of (a) measuring EV not Sharpe and (b) an HMM partition + daily sampling that
isolated a few big-move episodes. At the book's own 7d-rebal frequency the high-vol state carries more
variance than mean, so up-sizing into it is net-negative risk-adjusted — the same failure mode as W-B1,
W-B3, W-B6. There is no "turbulence alpha" to harvest; if anything the book prefers calm tape on a
Sharpe basis (agrees with B5). Inverse-vol sizing is worse still (-1.745), confirming W-B3. RISK: n=40
thin and my turbulence proxy differs from B15's HMM — but the verdict (EV concentration != Sharpe lift)
is exactly what B15 itself flagged as the caveat; W-B7 sharpens it from "smaller lift" to "negative".
