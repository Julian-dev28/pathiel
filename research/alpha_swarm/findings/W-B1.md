# W-B1 survivor_stack

## Hypothesis
Combining the Wave-1 survivors (skew-arm B13 + turbulence-upsize B15 + ADX>25 gate B6) into one
overlay on the live XS-momentum book beats the best single overlay and the un-overlaid book on OOS Sharpe.

## Rule
XS-momentum book k=14, H=7, m=6 (long top-6/short bottom-6, daily-rebal, fill open[i+1], n=40 rebals).
Overlays, multiplicative sizing on the per-rebal book return:
- skew-arm: full size in negative-market-skew regime (W=20 skew<0) else 0.5x.
- turb-upsize: 2x in top-tercile BTC-trailing-vol state (causal HMM-turbulence proxy) else 1x.
- ADX gate: only include legs whose Wilder ADX(14) >= 25 at the decision bar.
Metric: annualized Sharpe LIFT + maxDD vs un-overlaid book, OOS both halves.

## Results (annSharpe / maxDD / OOS half-Sharpes)
| variant | annSh | maxDD | OOS h1/h2 sh | lift vs base |
|--|--|--|--|--|
| un-overlaid (base) | **+3.279** | -9.9% | 0.67/0.27 | — |
| ADX>25 legs only | +2.600 | -12.3% | 0.29/0.44 | -0.679 |
| skew-arm only | +3.412 | -9.9% | 0.68/0.30 | **+0.133** |
| turb-upsize only | +2.460 | -19.7% | 0.69/0.08 | -0.819 |
| skew+turb | +2.512 | -19.7% | 0.69/0.10 | -0.767 |
| STACK (ADX+skew+turb) | +2.411 | -23.2% | 0.24/0.54 | **-0.868** |

## VERDICT: REFUTED
Deciding number: the STACK annSharpe is **+2.411 vs base +3.279 (lift -0.868)** and its maxDD is
**-23.2% vs -9.9% (2.3x worse)**. Stacking strictly degrades risk-adjusted return. The damage comes from
turbulence-upsizing (annSh -0.819, maxDD nearly doubles) — exactly the "B15 is just vol-scaling, not
Sharpe lift" warning, here it actively hurts because up-sizing into high-vol states adds variance faster
than mean. The ADX gate also hurts (-0.679, drops legs the book wants). Only the skew-arm survives, and
only marginally (+0.133 annSh, ~+0.02 raw Sharpe) on n=40 rebals — inside noise. So no stack; if anything
ships it is the skew-arm alone, pinned in W-B2. RISK: n=40 non-overlapping rebals is a thin sample;
survivor universe = upper bound.
