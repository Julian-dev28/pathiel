# W-B5 regime_age_timing

## Hypothesis
BTC up/down regime persistence has a run-length structure, and entry timing by regime AGE (fresh vs
stale) changes XS-momentum / extreme_fade EV.

## Rule
Regime = sign(BTC close - 20d SMA), known at i. Age = consecutive bars in the current regime. Run-length
event study first, then bin XS-book rebals (k=14,H=7,m=6) and extreme_fade-long (ret<-12%, 20% stop, 3d)
by regime sign x {fresh = age<=median, stale = age>median}. EV12 + OOS both halves per cell.

## Results
Run-length: UP runs n=15 median=3 (max 40); DOWN runs n=15 median=5 (max 41). Regimes are short-median,
fat-tailed (a few long trends dominate).

XS-MOMENTUM book (EV12 / OOS h1/h2):
| regime | fresh | stale |
|--|--|--|
| UP | +3.55% (4.92/1.83) n=9 | +0.40% (0.59/0.09) n=8 |
| DOWN | +3.38% (2.29/4.90) n=12 | -0.91% (-2.76/1.86) n=10 |

extreme_fade-long:
| regime | fresh | stale |
|--|--|--|
| UP | -0.52% (1.33/-3.11) n=12 | -8.34% (-9.12/-6.01) n=8 |
| DOWN | +6.60% (12.47/-0.10) n=90 | +4.22% (4.22/4.23) n=89 |

## VERDICT: MARGINAL (XS book) / REFUTED (fade)
Deciding number: the XS book earns **~+3.4% in FRESH regimes vs +0.40% (UP) / -0.91% (DOWN) in stale**,
and both fresh cells are OOS-robust both halves (UP 4.92/1.83, DOWN 2.29/4.90) while stale is flat or
sign-flipping. So momentum pays right after a BTC trend flip and decays as the regime ages — consistent
across UP and DOWN, which is the credible part. Kept MARGINAL not ROBUST because (a) the fresh cells are
tiny (n=9 / n=12), and (b) a fresh BTC pivot is exactly the high-dispersion / turbulent window B15 already
flagged, so this is plausibly turbulence restated, not an independent age effect.
The fade claim is REFUTED: regime AGE adds no robust lift — the operative gate is DOWN-regime (fade
+5.42% there vs -3.65% in UP, matching B13), and within DOWN the steadier OOS bucket is actually STALE
(4.22/4.23 dead-even) while DOWN-fresh sign-flips OOS (12.47/-0.10). So "fresh is better" is false for the
fade. RISK: thin cells; survivor upper bound; age/turbulence confound unresolved.
