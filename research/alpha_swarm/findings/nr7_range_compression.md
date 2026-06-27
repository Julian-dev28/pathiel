# C10 nr7_range_compression — MARGINAL (short-side only, front-loaded; long REFUTED)

## Hypothesis
After an NR4/NR7 range-compression bar, a breakout of that bar's high/low runs (volatility
expansion); direction set by the break, regime-gated.

## Exact rule
Daily. NRk = bar i with the smallest (high−low) of the last k bars (confirmed at i close).
Stop-entry break of the NRk high (→long) or low (→short), first trigger within W=3 bars, at
the break LEVEL (lookahead-safe price). Hold {3,5,10}d, stop {8..40}%. Gate both / regime
(long up-only, short down-only). `alpha_lib.summarize` + side-split `mc_null` with the null
pool **regime-matched** (random shorts drawn only from down-regime bars) to strip the tailwind.

## Results — best cell: NR4, regime-gated, hz=5, stop=8%, n=1053, EV +1.31%@12bps
| side | n | obs_mean | regime-matched null | excess | z | p | OOS h1 / h2 |
|--|--|--|--|--|--|--|--|
| long | 433 | −0.17% | −0.80% | +0.63% | 1.28 | 0.10 | **−1.90 / +1.33 (flip)** |
| short | 620 | +2.54% | +0.66% | **+1.88%** | 5.00 | **0.00025** | +4.36 / **+0.43** |

- **Long side REFUTED:** excess not significant (p=0.10) and OOS sign-flips.
- **Short side real but front-loaded:** even after conditioning the null on down-regime
  (random down-regime short earns only +0.66%/5d), the NR4-downbreak short adds **+1.88%
  excess, z=5.0, p=0.00025**. So the compression-timing wrapper genuinely improves a plain
  down-regime short. BUT OOS h2 is only **+0.43%** (vs h1 +4.36%) — the edge is concentrated
  in the first time-half, a decay/period-dependence flag.

## VERDICT
**MARGINAL (short-only).** Deciding number: short NR4-downbreak in BTC-down regime carries
**+1.88% excess over a down-regime-matched random short (p=0.00025)** — a real timing
improvement on the known down-regime short — but the **OOS second half is only +0.43%**, so
it is front-loaded and not cleanly robust. Long side refuted. Tight 8% stop fits (it's a
breakdown continuation, not a squeeze).

**Overlap caveat:** this is a variant/timing-wrapper on the project's existing down-regime
short edge (rally_exhaustion / crash_continue), not an independent factor — its marginal
value is the +1.88% over plain down-regime shorts, which the weak h2 undercuts. Worth a
shadow logger as a short-entry TIMING filter, not a standalone book; revisit when more
down-regime data accrues to test the h2 weakness.
