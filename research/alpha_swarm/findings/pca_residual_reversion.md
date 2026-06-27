# A1 pca_residual_reversion

**Hypothesis:** Stripping the top 1-2 PCs (market + sector) from the daily return
matrix leaves idiosyncratic residuals that mean-revert; long the most-beaten-down
residual / short the most-stretched (Avellaneda-Lee s-score) is a market-neutral edge.

**Exact rule:** At day t, window of L daily returns ending at t, standardize, SVD ->
top n_pc eigenportfolio factor returns F. Regress each coin on F, residual series e_c,
s-score = (cumsum(e)[-1] - mean)/std. Long bottom-m (s<<0), short top-m. Fill open[t+1],
hold H, exit open[t+1+H]. m=6 each side. Swept L{20,40,60} x n_pc{1,2,3} x hold{1,2,3}.

## Results (per-leg signed gross %, all 27 configs negative)
| config | EV0 | EV12 | EV25 | win | OOS h1 / h2 |
|---|---|---|---|---|---|
| L40 n_pc3 hold1 (best) | -0.145 | -0.265 | -0.395 | 0.48 | -0.226 / -0.305 |
| L20 n_pc1 hold1 | -0.167 | -0.287 | -0.417 | 0.49 | -0.316 / -0.258 |
| L60 n_pc2 hold3 (worst) | -0.719 | -0.839 | -0.969 | 0.47 | -0.858 / -0.820 |

Matched random 50/50 baseline: EV0 **+0.08 to +0.20%** per leg. The signal underperforms
even random entry by 0.3-1.0% per leg.

## Verdict: **REFUTED**
Deciding number: best-case EV0 = **-0.145%/leg** before any slippage, and *every* one of
27 configs is negative in both OOS halves. The idiosyncratic residual *continues*, it does
not revert, at the daily horizon — flipping the sign (residual momentum) would be positive,
but that is just the live cross-sectional momentum book restated. Principled-pairs reversion
does not exist here. Survivorship makes this an upper bound, which only deepens the refute.
