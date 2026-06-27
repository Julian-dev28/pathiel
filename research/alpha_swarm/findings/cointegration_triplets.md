# A9 cointegration_triplets

**Hypothesis:** 3-coin cointegrated baskets (vs refuted 2-coin pairs) have a residual
that mean-reverts; trade convergence of the basket spread.

**Survivorship guard (the key design):** the pairs edge was a false positive from selecting
the coins that happened to co-move (100%-VVV). So we DO NOT select. We sample 400 random
triplets (seeded) and trade ALL of them uniformly with rolling hedge ratios.

**Exact rule:** triplet (a,b,c), day i: regress price_a on [1,price_b,price_c] over trailing
W (bars <= i) -> betas, residual, z. Entry |z|>=entry_z, position -sign(z) on spread
= a - bb*b - bc*c. Fill open[i+1] spread, exit open[i+1+H]. Return = pos*Δspread/gross_notional.
Non-overlap per triplet. 3 legs => real cost ~3x the per-trade bps. W{30,40} x H{3,5} x z{1.5,2.0}.

## Results (per-trade fractional %, pooled across 400 triplets)
| W | H | z | n | EV0 | EV12 | EV25 | OOS h1 / h2 |
|---|---|---|---|---|---|---|---|
| 30 | 3 | 1.5 | 12308 | -0.26 | -0.38 | -0.51 | -0.38 / -0.38 |
| 30 | 3 | 2.0 | 6655 | -0.27 | -0.39 | -0.52 | -0.38 / -0.40 |
| 40 | 5 | 1.5 | 8455 | -0.39 | -0.51 | -0.64 | -0.50 / -0.52 |
| 40 | 3 | 2.0 | 6266 | -0.38 | -0.50 | -0.63 | -0.60 / -0.39 |

## Verdict: **REFUTED**
Deciding number: **every** config is negative at **0 bps** (-0.26 to -0.39%) and negative in
**both** OOS halves with no sign flip (consistent loss, not noise), on n = 5k-12k trades. When
you refuse to cherry-pick the survivors, the basket residual *diverges* on average after a
2-sigma stretch — it does not revert — and that's before the ~3x leg cost. This is the direct,
honest confirmation of the prior pairs false-positive: cointegration "edge" in this universe is
survivorship selection, not a tradeable signal.
