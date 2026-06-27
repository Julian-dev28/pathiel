# C13 obv_vpt_slope — REFUTED as a distinct factor (subsumed by price momentum)

## Hypothesis
OBV/VPT flow-slope ranking is a cross-sectional flow factor; long high-accumulation / short
distribution coins.

## Exact rule
Daily XS. Score = net-flow-fraction = Σ(sign(ret)·vol)/Σ(vol) over last L days (vol-weighted,
comparable across coins, ∈[−1,1]). Long top-n / short bottom-n, hold H, non-overlapping
rebalances, lookahead-safe (score@i, enter i+1 open, exit i+1+H close). Swept L{10,20} ×
topn{6,8} × H{3,5}. **Control:** a price-MOMENTUM book on the same grid, plus the per-rebalance
**OBV-minus-MOM** spread = the incremental contribution of flow over price momentum.

## Results (EV@12bps, both halves)
| L | topn | H | OBV book | MOM book | OBV−MOM (added) | added h1/h2 |
|--|--|--|--|--|--|--|
| 10 | 6 | 3 | +0.59 | +1.67 | **−1.19** | −0.86 / −1.55 |
| 10 | 8 | 5 | +2.52 (rob.) | +2.84 | **−0.44** | −1.36 / +0.58 |
| 10 | 8 | 3 | +1.60 | +1.31 | +0.17 | −0.41 / +0.79 (flip) |
| 20 | 6 | 3 | +0.28 | +1.94 | **−1.78** | −4.77 / +1.44 |
| 20 | 8 | 5 | +1.55 | +1.46 | −0.03 | −4.90 / +5.48 (flip) |

- OBV-flow long/short is +EV in several cells (it IS a momentum signal), but the **incremental
  OBV-minus-MOM is negative in 6 of 8 cells** (−0.03% to −1.78%/rebal) and the 2 non-negative
  cells **sign-flip across halves** (h1 strongly negative, h2 positive).
- Price momentum dominates OBV-flow head-to-head in almost every cell.

## VERDICT
**REFUTED (as a distinct factor).** Deciding number: the **OBV-minus-MOM incremental is
negative in 6/8 cells** and sign-flips in the rest — flow ranking adds nothing over the price
momentum the live XS book already trades; it's a noisier proxy for the same effect. Not an
independent edge. (The standalone OBV book's apparent +EV is just re-discovering momentum.)
