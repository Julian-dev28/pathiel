# B13 realized_skew_timing

## Hypothesis
Extreme-negative aggregate (market) realized skew is a crash predictor: de-risk longs and ARM the
fade when market skew goes extreme-negative.

## Exact rule
- Market return = equal-weight avg coin daily ret. skew = trailing W=20d skewness (m3/m2^1.5), known at t.
- (1) skew tercile -> forward 5d market return / min / crash(>5% draw) freq.
- (2) crash-fade-long (coin 1d return <-12%, long, 20% stop, 3d) EV conditioned on market-skew regime at entry.

## Results
(1) market-skew tercile -> forward 5d market:
| skew | n | fwd5d ret% | fwd5d min% | crash5% freq |
|--|--|--|--|--|
| neg | 91 | **+0.42** | -4.06 | 33.0% |
| mid | 92 | -2.48 | -5.72 | 43.5% |
| pos | 92 | -0.34 | -3.87 | 31.5% |

Negative skew does NOT predict crashes — it is the BEST forward bucket. Crash-predictor claim refuted.

(2) crash-fade-long EV by market-skew regime (@12bps):
| regime | n | EV% | win | h1 | h2 | OOS |
|--|--|--|--|--|--|--|
| all | 177 | +5.20 | 0.638 | 6.71 | 3.67 | ROBUST both |
| **neg** | 129 | **+7.85** | 0.736 | 9.40 | 2.94 | ROBUST both |
| mid | 26 | -0.91 | 0.346 | 3.85 | -7.40 | sign-flip |
| pos | 22 | -3.14 | 0.409 | -2.36 | -4.07 | sign-flip |

## VERDICT: ROBUST (fade-arming overlay) — crash-predictor sub-claim REFUTED
Deciding number: gating the live extreme_fade-long to the **negative market-skew regime lifts EV from
+5.20% to +7.85%** (win 64%->74%), OOS-robust both halves (9.40/2.94), and that regime holds 129 of
177 crash events. The fade pays in neg-skew, is flat/negative in mid/pos skew. So negative aggregate
skew ARMS the fade — confirmed. BUT the de-risk-longs / crash-prediction half is refuted: neg skew is
the best forward bucket (+0.42%), not the worst. RISK: the crash-fade base is survivorship-acute
(extreme_surface caveat); the +2.65% lift is a within-universe regime split (more credible than the
absolute level) but still an upper bound. Shadow-worthy as a skew regime filter on extreme_fade.
