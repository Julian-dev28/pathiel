# W-A4 idio_momentum_residual — does BTC-beta-residual momentum beat raw-return momentum?

**Hypothesis:** ranking on idiosyncratic (BTC-beta-residual) momentum beats raw-return
momentum on Sharpe AND cuts the down-beta confound that W-A3 exposed.

**Rule:** RAW score = trailing L-day return; RESID score = rc − beta_i·(BTC trailing-L return),
beta_i from trailing 30 daily rets. long top-8 / short bottom-8, fill open[i+1]→[i+1+H],
non-overlapping. Report per-leg EV, OOS both halves, book Sharpe, and NET beta tilt
(mean long beta − mean short beta; ~0 = beta-neutral).

## Results
| lb | H | book | n | EV12 | EV50 | OOS h1 / h2 | Sharpe full/h1/h2 | beta tilt |
|---|---|---|---|---|---|---|---|---|
| 14 | 7 | raw | 21 | +1.30 | +0.92 | +0.83 / +1.82 | +0.38 / +0.55 / +0.32 | **−0.15** |
| 14 | 7 | **resid** | 21 | **+1.78** | +1.40 | +1.35 / +2.25 | **+0.58 / +0.79 / +0.53** | **+0.02** |
| 14 | 10 | raw | 15 | +0.90 | +0.52 | +0.79 / +1.03 | +0.23 | −0.13 |
| 14 | 10 | **resid** | 15 | **+1.61** | +1.23 | +1.77 / +1.43 | **+0.56** | −0.03 |
| 30 | 7 | raw | 21 | +1.76 | +1.38 | +1.41 / +2.15 | +0.50 | −0.06 |
| 30 | 7 | resid | 21 | +1.54 | +1.16 | +1.21 / +1.89 | +0.51 | −0.02 |
| 30 | 10 | raw | 15 | +1.41 | +1.03 | +0.27 / +2.71 | +0.33 | −0.06 |
| 30 | 10 | resid | 15 | +0.88 | +0.50 | +0.28 / +1.56 | +0.23 | −0.02 |

## VERDICT: **MARGINAL (positive at lb=14, wash at lb=30) — residual momentum is the cleaner
## construction; it fixes the down-beta confound.**
Deciding number: **at lb=14 (the live channel window) residual momentum lifts Sharpe to +0.58
vs raw +0.38 (H7) / +0.56 vs +0.23 (H10), both OOS halves positive, AND cuts net beta tilt
from −0.15 to +0.02.** That negative raw tilt (long lower-beta / short higher-beta = net
short-beta) is exactly the down-beta confound W-A3 flagged on the short leg — residualizing
removes it. At lb=30 the two are a tie / raw slightly ahead, so the lift is lookback-conditional.

**Actionable:** the live book runs `ranking="pct_k"` (residual flag ignored). For beta-cleanliness
the `residual_score` ranker (already coded in xs_momentum.py) at lb≈14 is the better choice — it
both raises Sharpe and de-confounds the beta tilt. NOT new capacity (same momentum family) — an
implementation improvement. **Caveat:** thin n (15–21 non-overlapping rebals on 188 bars), and
the win does not hold at lb=30, so treat as a config recommendation to A/B in shadow, not a
validated flip.
