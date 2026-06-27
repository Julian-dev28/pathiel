# D5 basis_premium_signal

**Hypothesis.** The `premium` field (perp mark vs oracle/index) is a basis signal distinct from
funding; premium extremes mean-revert (perp rich → SHORT, perp cheap → LONG, expecting convergence).

**Rule.** Same event study + matched same-side null as D4, but signal = z-score of trailing-24h
mean premium vs that coin's own trailing-30d distribution (lookahead-safe). z≥+T → SHORT, z≤−T →
LONG. Next-day-open entry, h-day hold, stop-swept. Premium correlates 0.72-0.89 with the funding rate.

## Result — SHORT side significant, LONG side dead (matched-null excess)
| T | h | side | n | excess vs null | null-z | p |
|---|---|---|---|---|---|---|
| 2.0 | 5d | **short** | 150 | **+3.87%** | 4.48 | **0.0002** |
| 1.5 | 5d | short | 276 | +3.00% | 4.79 | 0.0002 |
| 2.0 | 3d | short | 150 | +1.87% | 2.59 | 0.0052 |
| 1.5/2.0 | any | long | 84-167 | −0.003…−0.015 | <0 | 0.61-0.92 (DEAD) |

## Short side, net of fees + OOS both halves
| T | h | stop | n | net@25bps | win | OOS25 h1 / h2 |
|---|---|---|---|---|---|---|
| 2.0 | 5d | 20% | 150 | +3.67% | .67 | **+1.94 / +6.07** ✅ |
| 2.0 | 5d | 15% | 150 | +3.08% | .65 | +1.32 / +5.51 |
| 1.5 | 5d | 20% | 276 | +2.71% | .64 | −0.26 / +5.81 |

## VERDICT: ROBUST +EV (SHORT side) — but the SAME effect as D4, not independent alpha
Deciding numbers: premium-rich convergence SHORT at z≥2.0 / 5-day hold / 20% stop → net +3.67%
per event @25bps, win 67%, BOTH OOS halves positive (+1.94 / +6.07), null **p=0.0002** vs a
beta-matched random-short pool. Long side cleanly REFUTED (p≈0.6-0.9), exactly like D4.

Because premium and funding are 0.72-0.89 correlated, this is the SAME "perp-overcrowded-long →
reversal short" effect as D4, not a new orthogonal edge. Its practical value is as a BETTER TRIGGER:
the premium z-spike fires ~3× more events (n=150 vs D4's 53 at z=2.0) with comparable per-event EV
AND a cleaner first OOS half (+1.94 vs D4 +1.36). If wiring the crowded-long short, premium is the
higher-coverage entry signal. Same regime-tilt risk (second half +6% vs first +1.9%) and
survivorship-conservative-for-shorts note as D4.
