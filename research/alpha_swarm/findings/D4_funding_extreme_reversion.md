# D4 funding_extreme_reversion

**Hypothesis.** Extreme funding = crowded positioning. A funding SPIKE precedes a price
REVERSAL — fade the crowded side. Score as EXCESS over a matched same-side null (the −44% tape
flatters shorts, so the null must carry the down-beta).

**Rule.** Event study, per coin. Signal = z-score of trailing-24h funding vs that coin's own
trailing-30d distribution (lookahead-safe). z ≥ +T → SHORT (fade crowded longs); z ≤ −T → LONG
(fade crowded shorts). Enter at next-day open. Forward h-day hold, stop-width SWEPT {8,15,20,25,40}%.
Null = mc_null.shuffle_label_p against the pool of EVERY bar's same-side h-day forward return.

## Result: the SHORT side is real, the LONG side is dead

Matched-null excess (z-spike events, fade), pooled mean across stops:
| T | h | side | n | excess vs null | null-z | p |
|---|---|---|---|---|---|---|
| 1.5 | 5d | **short** | 102 | **+4.07%** | 4.02 | **0.0002** |
| 2.0 | 5d | **short** | 53 | **+4.74%** | 3.36 | **0.0006** |
| 1.5 | 3d | short | 102 | +2.31% | 2.70 | 0.0028 |
| 2.0 | 3d | short | 53 | +2.14% | 1.80 | 0.035 |
| 1.5/2.0/2.5 | any | long | 107-238 | −0.0003…−0.007 | <0 | 0.50-0.71 (DEAD) |

Short side faded against a random-SHORT pool that already contains the −44% down-beta → the
+4-5% excess is genuine reversal alpha, not beta. (Survivorship makes a SHORT result CONSERVATIVE:
coins that died — the best shorts — are absent, so the edge is if anything understated.)

## Short-side, net of fees + OOS both halves (the gate)
| T | h | stop | n | net@25bps | win | OOS25 h1 / h2 |
|---|---|---|---|---|---|---|
| 2.0 | 5d | 15% | 53 | +4.56% | .68 | **+1.81 / +8.43** ✅ |
| 2.0 | 5d | 20% | 53 | +4.79% | .70 | **+1.36 / +9.63** ✅ |
| 2.0 | 5d | 25% | 53 | +4.41% | .70 | +0.71 / +9.63 |
| 1.5 | 5d | 20% | 102 | +3.56% | .68 | +0.03 / +7.87 |
| 2.0 | 3d | 20% | 53 | +2.11% | .62 | −2.05 / +7.96 ❌ sign-flip |

Stop sweep is FLAT (8%→40% all +0.5-1.5%) → not a tight-stop artifact. The 5-day horizon is
required: h=3d sign-flips across halves (h1 negative), h=5d holds both halves positive.

## VERDICT: ROBUST +EV (SHORT side only)
Deciding numbers: SHORT extreme-positive-funding spikes (z≥2.0 of trailing-24h funding vs own
30d), 5-day hold, 15-20% stop → net +4.6% per event @25bps, win ~69%, BOTH OOS halves positive
(+1.5% / +9%), null **p=0.0006** against a beta-matched random-short pool. This is the strongest,
most significant result in lane D. The LONG side (fade negative funding) is cleanly REFUTED (p≈0.5-0.7).

**Biggest risk:** regime tilt — the second OOS half (deep BTC crash) carries most of the return
(+9% vs +1.5%); the edge pays most when the market is falling, so in a flat/up regime expect the
weaker ~+1.5% end. n=53 at z=2.0 is modest (z=1.5 gives n=102 with a near-zero first half).
This is the funding-data version of a rally-exhaustion / crowded-long-fade short — likely
confirms/feeds the live `rally_exhaustion` and `crash_continue_div_short` cells.
