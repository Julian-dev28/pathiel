# W-C5 entropy_on_engulf — REFUTED (no lift, wrong sign)

## Hypothesis
Restricting the engulf-short edge to LOW permutation-entropy (predictable) names lifts EV /
cuts duds.

## Rule
Base = bearish-engulf SHORT (W-C2 real leg), hz=1, stop 0.40. At decision bar i compute
PE (m=3, delay=1) on the coin's last 30 daily returns (lookahead-safe). Bucket by PE
median + terciles; measure EV / win / dud-rate / OOS halves; permutation p on the
low-minus-high EV gap; low-PE bucket excess vs random-short null.

## Results
| bucket | n | EV@0 | EV@25 | win | dud | h1 | h2 |
|--|--|--|--|--|--|--|--|
| BASE all | 727 | +1.74 | +1.49 | .59 | .402 | +2.84 | +0.39 |
| LOW-PE ≤med | 365 | +1.47 | +1.22 | .60 | **.395** | +1.91 | +0.76 |
| HIGH-PE >med | 362 | **+2.01** | +1.76 | .58 | .409 | +3.08 | +0.69 |
| LOW-PE tercile | 250 | +1.29 | +1.04 | .59 | .404 | +1.12 | +1.21 |
| HIGH-PE tercile | 268 | **+2.38** | +2.13 | .60 | .392 | +3.47 | +1.02 |

- low-vs-high EV gap = **−0.545%** (wrong sign — high-PE is better), permutation **p=0.83**.
- Dud-rate is flat across buckets (.395 vs .409) → no dud cut.
- (Aside: the low-PE tercile is the most OOS-balanced (1.12/1.21) but at the LOWEST EV, and
  the gap test is insignificant, so this is not a real stability gain to wire on.)

## VERDICT
**REFUTED.** Deciding number: the low-minus-high-PE EV gap is **−0.55% with p=0.83** —
the filter neither lifts EV nor cuts duds, and if anything points the wrong way. The
engulf-short edge stands on its own (low-PE bucket still beats the random-short null by
+1.24%, p=0.0013, but so does high-PE, more). Do NOT bolt the entropy filter onto the wire.
