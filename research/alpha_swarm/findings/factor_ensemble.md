# A16 factor_ensemble

**Hypothesis:** Combine the surviving Lane-A factors into one vol-weighted market-neutral
book; diversification lifts Sharpe over the best single factor.

**Factors fed in** (skew A3 / low-beta A4 refuted; carry A15 data-blocked): MOM (trailing-30d
return = live XS-momentum), ACCEL (A7 acceleration), RSDD (A13 proximity-to-50d-high, the
ROBUST one). Each: cross-sectional rank, long top-6/short bottom-6, shared non-overlapping grid
(rebal=H), per-rebalance portfolio. COMPOSITE = equal-weight z-rank sum. m=6.

## Results (portfolio per-rebalance, Sharpe-like = mean/std)
### H=7
| factor | n | EV12 | Sharpe | OOS h1 / h2 |
|---|---|---|---|---|
| MOM | 35 | +1.18 | +0.273 | +0.22 / +2.19 |
| ACCEL | 35 | +0.17 | +0.076 | +0.23 / +0.10 |
| RSDD | 35 | +1.01 | +0.234 | +0.47 / +1.59 |
| **COMPOSITE** | 35 | +0.91 | +0.269 | **+0.89 / +0.92** |
| best single = MOM | | | +0.273 | **LIFT = -0.005** |

### H=14
| RSDD (best) | 17 | +3.07 | **+0.536** | +2.51 / +3.71 |
| ACCEL | 17 | -1.97 | -0.315 | (drags composite) |
| COMPOSITE | 17 | +2.00 | +0.295 | **LIFT = -0.241** |

Pairwise corr: **MOM-RSDD +0.69 to +0.74** (heavy overlap), MOM-ACCEL -0.5/-0.17, ACCEL-RSDD -0.3.

## Verdict: **REFUTED** (no diversification lift)
Deciding number: COMPOSITE Sharpe never beats the best single factor — LIFT = **-0.005** at H=7
and **-0.241** at H=14. The surviving factors are not diversifying: MOM and RSDD are +0.7
correlated (RSDD *is* largely the momentum book restated, confirming the A13 overlap risk), and
ACCEL is too unreliable (negative at H=14, dragging the composite down). Actionable: do not build
the ensemble; run the single best market-neutral factor (RSDD / A13). One honest nuance worth
keeping: at H=7 the composite delivered *balanced* OOS halves (+0.89/+0.92) where MOM alone was
lopsided (+0.22/+2.19) — so blending buys OOS *stability* even though it buys no extra Sharpe.
