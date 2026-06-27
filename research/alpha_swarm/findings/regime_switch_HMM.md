# B15 regime_switch_HMM

## Hypothesis
A 2-state HMM on BTC (ret, |ret|) reveals latent regimes in which each live edge's EV concentrates,
enabling a regime-conditional size multiplier.

## Exact rule
- 2-state diagonal-Gaussian HMM, EM (25 iters) on the BTC feature stream [ret, |ret|], fit on
  TRAIN = first 60% of bars only (no test leakage). Decode CAUSALLY (filtering forward pass) over the
  full series using train params (uses obs up to t only).
- States: s0 calm (|ret|~0.99%), s1 turbulent (|ret|~3.9%, ret -1.0% = down-vol). calm=s0.
- Measure XS-book and long_all daily EV by decoded state, separately on train and held-out TEST half.

## Results (mean daily ret %)
| edge | half | calm n / ret% | turb n / ret% |
|--|--|--|--|
| xs_book | train | 128 / 0.169 | 37 / **1.585** |
| xs_book | TEST | 93 / 0.102 | 27 / **0.907** |
| long_all | train | 128 / -0.059 | 37 / -1.612 |
| long_all | TEST | 93 / 0.073 | 27 / 0.204 |

XS-book EV is ~6-9x higher in the turbulent state, and the concentration HOLDS OOS (TEST: turb 0.907%
vs calm 0.102%).

## VERDICT: MARGINAL (regime-conditional multiplier candidate, OOS-consistent)
Deciding number: on the held-out half the XS book earns **0.907% in the turbulent state vs 0.102% in
calm** — a ~9x EV concentration that generalizes from train (1.585 vs 0.169). So XS momentum is a
dispersion/turbulence-loving strategy: up-sizing it in the high-vol HMM state is the implied lever,
and it agrees with B2 (de-risk when correlation high / vol low). CAVEAT keeping this MARGINAL not
ROBUST: turbulent-state EV is partly just higher VOL (bigger moves both ways), so the Sharpe lift from
a size multiplier is far smaller than the 9x EV ratio, and the turbulent state is rare (27/120 test
days). long_all (beta) shows no stable concentration. Survivor-biased upper bound. Worth a shadow
regime-size multiplier on the XS book, sized by Sharpe not raw EV.
