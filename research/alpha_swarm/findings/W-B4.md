# W-B4 efficiency_ratio_gate

## Hypothesis
Kaufman efficiency ratio (net move / path length) as a trend-quality gate on momentum legs cuts
choppy-path names, lowering dud-rate and lifting Sharpe over the ADX>25 gate (B6).

## Rule
XS book k=14,H=7,m=6. ER(c,i)=|close_i - close_{i-14}| / sum|single-bar move| over last 14 bars,
known at i. Gate: include a leg only if ER >= thr {0.30,0.40,0.50}. Dud-rate = fraction of legs with
return<=0. Compare to ADX>25 gate and raw, OOS both halves + maxDD.

## Results
| variant | n_legs | dud-rate | annSh | maxDD | OOS h1/h2 sh |
|--|--|--|--|--|--|
| raw | 456 | **0.447** | **+3.280** | **-9.9%** | 0.43/0.49 |
| ADX>25 (B6) | 294 | 0.459 | +2.600 | -12.3% | 0.29/0.44 |
| ER>=0.30 | 281 | 0.452 | +1.501 | -40.4% | 0.30/0.10 |
| ER>=0.40 | 206 | 0.451 | +1.653 | -42.0% | 0.34/0.08 |
| ER>=0.50 | 124 | 0.460 | +2.026 | -47.6% | 0.56/-0.03 |

## VERDICT: REFUTED
Deciding number: the best ER gate (ER>=0.50) gives annSharpe **+2.026 vs raw +3.280 (lift -1.254)**, its
dud-rate is **0.460 vs raw 0.447 (+0.012 — duds slightly MORE common, not fewer)**, and maxDD blows out
to **-47.6% vs -9.9%**. The gate fails on its own claim: high-ER "clean trend" names are not lower-dud,
and dropping the rest just concentrates the book (fewer legs -> less diversification -> 4-5x worse
drawdown). ER>=0.50 even sign-flips OOS h2 (-0.03). Strictly worse than the already-negative ADX gate.
Same lesson as B6: per-leg trend-quality gates do not cut the dud-rate of an XS-momentum book; the edge
is cross-sectional (rank-relative), not about each leg's own path smoothness. RISK: n=40 rebals thin;
but the dud-rate result is leg-level (456 legs) and unambiguous.
