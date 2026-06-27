# B3 vol_term_structure

## Hypothesis
Short-RV/long-RV ratio routes between books: a vol spike (high ratio) -> fade the recent move,
vol compression (low ratio) -> trend/momentum. Beats running either book blind.

## Exact rule
- Per coin, daily. shortRV=pstdev(5d ret), longRV=pstdev(30d ret), ratio=sRV/lRV.
- ratio>1.2 -> fade = -sign(3d ret); ratio<0.8 -> trend = sign(14d ret); else skip.
- Baselines evaluated on the SAME entry days: mom-only, fade-only, random side.
- Lookahead-safe: decide close i, fill i+1 open, fwd=candles[i+1:], 25% stop, 5d horizon, step=5.
- 1711 routed entries (406 fade / 1305 trend). OOS+slippage via summarize.

## Results (@12bps)
| mode | n | EV% | win | sharpe | h1 | h2 | OOS |
|--|--|--|--|--|--|--|--|
| **router** | 1711 | +0.577 | 0.537 | 0.051 | 1.137 | **0.003** | ROBUST both halves |
| mom | 1711 | +0.452 | 0.520 | 0.040 | 1.233 | -0.345 | sign-flip |
| fade | 1711 | +0.121 | 0.515 | 0.011 | -0.102 | 0.350 | sign-flip |
| random | 1711 | -0.030 | 0.495 | -0.003 | -0.011 | -0.049 | noise |

Lift router vs best base (mom): **+0.124% EV**; vs random +0.607%.

## VERDICT: MARGINAL
Deciding number: the router is the **only** OOS-robust mode (both halves >0) and adds **+0.124%
EV** over mom-only, but second-half EV is **+0.003%** (essentially break-even). The mechanism is real
in spirit — fade pays only in h2, mom only in h1, and routing by vol-ratio stitches a positive series
from two one-sided ones — but the realized h2 edge is razor-thin. Survivor-biased upper bound. Worth a
shadow note as a regime switch; not strong enough to deploy standalone.
