# B1 hurst_regime_router

## Hypothesis
Routing momentum to trending coins (variance-ratio VR>1) and reversion to mean-reverting
coins (VR<1) beats applying either signal blindly to all coins.

## Exact rule
- Per coin, daily. Trailing W=60 log-returns -> VR(q=5) = Var(5-sum)/(5·Var(1)). VR>1=trending.
- mom = sign(14d return); rev = -sign(3d return).
- router: VR>1 -> mom, else rev. Baselines: mom-only, rev-only, random side.
- Lookahead-safe: decide on close i, fill i+1 open, fwd=candles[i+1:], 25% stop, 5d horizon,
  non-overlapping (step=horizon). 1769 trades. OOS+slippage via alpha_lib.summarize.

## Results (@12bps)
| mode | n | EV% | win | sharpe | h1 | h2 |
|--|--|--|--|--|--|--|
| router | 1769 | -0.525 | 0.483 | -0.047 | -0.99 | -0.06 |
| mom | 1769 | +0.134 | 0.490 | +0.012 | +0.68 | -0.42 |
| rev | 1769 | -0.329 | 0.490 | -0.028 | -1.33 | +0.68 |
| random | 1769 | -0.293 | 0.490 | -0.025 | -0.25 | -0.34 |

LIFT router vs best base (mom): **-0.659% EV, -0.059 Sharpe**. Router also -0.232% below random.
No mode is OOS-robust (all sign-flip across halves).

## VERDICT: REFUTED
Deciding number: router lift = **-0.66% EV** vs the best un-routed baseline, and it underperforms
random. VR>1/<1 carries no usable timing for choosing mom vs rev here; the classifier mostly mis-routes.
mom-only and rev-only each "win" only one half (mom h1, rev h2) = noise. Survivorship caveat moot (negative).
