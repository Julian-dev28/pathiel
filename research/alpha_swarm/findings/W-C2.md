# W-C2 engulf_leg_decomp — the edge is the SHORT leg only; long leg REFUTED

## Hypothesis
C9's symmetric L/S book hides an asymmetry: one leg is real, the other is beta/noise.

## Rule
Spec from W-C1 (hz=1, classic full-body engulf, no gap, no vol filter, stop 0.40).
Split into long-bullish-engulf-ONLY and short-bearish-engulf-ONLY. Score each as EXCESS
over a matched SAME-SIDE null: random-LONG / random-SHORT (controls the −44% down-tape
beta) and bigbar-LONG / bigbar-SHORT (strict same-direction range-expansion continuation).

## Results
| leg | n | EV@0 | EV@25 | win | OOS h1 | OOS h2 | vs random same-side | vs bigbar same-side |
|--|--|--|--|--|--|--|--|--|
| **LONG** (bull engulf) | 668 | −0.02% | −0.27% | .46 | **−0.81** | +0.54 | +0.20% z=0.62 **p=0.27** | **−0.32%** p=0.83 |
| **SHORT** (bear engulf) | 808 | **+1.47%** | +1.22% | .57 | **+1.68** | **+1.02** | **+1.25% z=4.45 p=0.00012** | **+1.32% z=4.96 p=0.00012** |

## Read
- **LONG leg is DEAD.** EV ~0, OOS sign-flips (h1 −0.81 / h2 +0.54), no excess over
  random-long (p=0.27), and NEGATIVE vs the bigbar-long continuation null. Bullish engulf
  carries no long edge — C9's symmetric framing was averaging a strong short with a dead
  long and diluting the real signal.
- **SHORT leg is the WHOLE edge** and it is REAL, not down-beta: a random short over the
  same bars made only +0.22%, but the bearish-engulf short made +1.47% → excess **+1.25%**
  over the same-side tape null (p=0.00012), +1.32% over the strict bigbar-short null. Both
  OOS halves strongly + (+1.68 / +1.02). Survives to +0.97% at 50bps.

## VERDICT
**Asymmetric — SHORT leg ROBUST, LONG leg REFUTED.** Deciding number: bearish-engulf
short excess **+1.25%/trade over matched random-short (p=0.00012), both halves +**, while
the bullish-engulf long is +0.20% (p=0.27) and sign-flips. **Wire C9 SHORT-ONLY**
(bearish full-body engulf → next-open short, 1-day hold), not as a symmetric L/S book.

**Biggest risk:** the cache is a −44% down-tape. The same-side null controls for generic
short beta (so the +1.25% is real conditional timing alpha), but we cannot confirm the
excess holds in an UP regime from this sample → forward shadow must tag BTC regime and
watch the short-leg excess specifically when BTC trends up.
