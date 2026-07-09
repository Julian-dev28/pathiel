# W-H1 — BTC->alt residual catch-up after 2-sigma 1h shocks — REFUTED

## Hypothesis
After a large BTC 1h move (|ret| > 2 sigma_168), the alts that most UNDER-reacted
relative to their own BTC-beta (residual laggards) catch up over the next 1-6h;
a beta-hedged laggard basket (long laggards after up-shocks / short
least-fallen after down-shocks) is +EV.

## Distinct from prior art
`btc_leadlag.md` refuted NEXT-BAR directional/threshold follow at 5m+1h. This
tested a different claim: 2-sigma-adaptive shocks, per-coin rolling 240h beta,
RESIDUAL (not raw) ranking, BTC-hedged pair returns, 1-6h horizons, on ~208d of
extended 1h data (W-H0). Spec pre-registered in `hypotheses/W-H1.py` docstring.

## Rule
Shock at bar i (|BTC ret| > 2x strictly-past sigma_168) -> rank alts by
resid_j = altret_j[i] - beta_j*btcret[i]; take the 8 most under-reacted in the
shock direction; fill open[i+1], exit open[i+1+H], H in {1,3,6}; pair ret =
sign*(alt_fwd - beta_j*btc_fwd); costs = tier on alt leg + tier/2 on BTC hedge
(net12 = gross - 18bps). Unit = per-EVENT basket (legs are cross-correlated).
185 deduped events (92 up / 93 dn), MC null = same rule at 4544 non-shock bars.

## Results (per-event basket)
| cell | n | gross | net12 | net25 | OOS12 h1/h2 | mc_p |
|--|--|--|--|--|--|--|
| ALL H=1 | 184 | +0.037% | -0.143% | -0.338% | -0.125/-0.162 | 0.267 |
| ALL H=3 | 184 | -0.047% | -0.227% | -0.422% | -0.275/-0.178 | 0.855 |
| ALL H=6 | 184 | -0.170% | -0.350% | -0.545% | -0.258/-0.444 | 0.971 |
| best cohort: beta-low H=1 | 111 | +0.152% | **-0.028%** | -0.223% | +0.033/**-0.090** | 0.002 |
| worst: beta-high H=6 | 103 | -0.408% | -0.588% | -0.783% | -0.422/-0.758 | 1.000 |

Cohort splits (liquidity halves, beta terciles) change nothing: no cell is
net-positive at 12bps with both OOS halves positive.

## VERDICT: REFUTED
Deciding number: the single best pre-registered cohort (beta-low, H=1,
mc_p=0.002 gross) is still **-0.028% net at 12bps and sign-flips OOS
(+0.033/-0.090)**; every aggregate cell is negative net. Worse for the
hypothesis, EV becomes MORE negative as the horizon extends (ALL: +0.037% ->
-0.170% gross from 1h to 6h) and beta-high/H=6 is -0.41% gross (mc_p=1.0):
residual under-reaction does NOT converge — relative strength PERSISTS
(residual momentum), consistent with the live xs-momentum finding and the
refuted xs_reversal. There is no hedged catch-up trade at 1-6h. Survivorship
caveat applies (positive cells were upper bounds anyway).
