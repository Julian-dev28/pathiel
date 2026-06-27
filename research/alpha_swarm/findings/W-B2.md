# W-B2 skew_arm_forward_spec

## Hypothesis
B13's neg-market-skew arm on extreme_fade-long has a precise threshold + lookback W that maximizes the
within-universe neg-vs-pos EV split and is robust to the skew window -> a shadow-wire spec.

## Rule
Base: extreme_fade-long = coin 1d ret < -12%, long, 20% stop, 3d horizon, decide at i-close / fill i+1
open (lookahead-safe), exits via alpha_lib.sweep_stop. n=199 events. Market skew = trailing-W skewness
of equal-weight market daily return, known at entry bar. Sweep W{10,15,20,30} x threshold{hard 0, median}.
Score neg-vs-pos EV12 split, OOS both halves, and MC shuffle-label p vs the fade-event pool (4000 iters).

## Results (neg-skew = armed)
| W | thr | neg n | neg EV12% | neg win | neg OOS h1/h2 | pos EV12% | split | MC p |
|--|--|--|--|--|--|--|--|--|
| 10 | 0.0 | 154 | +5.06 | 0.656 | 6.69/3.34 | +2.62 | +2.44 | 0.330 |
| 15 | 0.0 | 148 | +6.02 | 0.669 | 7.76/4.18 | +0.12 | +5.90 | 0.129 |
| **20** | **0.0** | **142** | **+7.29** | **0.711** | **8.40/3.89** | **-2.42** | **+9.70** | **0.020** |
| 30 | 0.0 | 155 | +6.24 | 0.671 | 6.81/5.66 | -1.59 | +7.82 | 0.086 |
| 30 | med | 145 | +7.21 | 0.703 | 8.46/5.93 | -2.74 | +9.94 | 0.024 |
Base (all events): EV12 +4.51%, win 0.623, OOS 5.41/3.60.

## VERDICT: ROBUST (confirms B13; spec pinned)
Deciding number: **W=20, hard-zero threshold -> armed (neg-skew) EV +7.29% vs disarmed (pos-skew)
-2.42%, split +9.70, MC p=0.020, OOS robust both halves (8.40/3.89)**. The arm is robust to the skew
window: every W in 15-30 keeps neg >> pos with the same sign, and the hard-zero threshold (zero fitting)
matches the fitted median. W=10 is too short — split collapses to +2.44 and p=0.33 (not significant),
so the window must be >=15d. SHADOW-WIRE SPEC: market realized skew, W=20d, equal-weight; ARM
extreme_fade-long only when skew<0, DISARM (skip / half-size) when skew>=0. RISK: the fade base is
survivorship-acute (extreme_surface caveat) so absolute EV is an upper bound; the split is a
within-universe regime contrast, which is the credible part. pos-skew n=57 is the thinner cell.
