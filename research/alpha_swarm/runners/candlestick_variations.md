# candlestick_variations — do candle SHAPES preceding the +50/100% runs lift the runner rate?

**Agent:** candlestick_variations (MANTA swarm). Read-only, cache-only (`movers_dataset.json`, 160 native
perps, 1h, ~2000 bars each, 50 coins with a >=50%/48h run, 11 with >=100%).

## Method (lookahead-safe)
- Scan every bar `i` with >=48 forward bars across all 160 coins (**304,101 scannable bars**).
- Decide each pattern on bars `[..i]`; **enter at `open[i+1]`**. Forward horizon **H=48 bars (48h)**.
- Outcome: forward **MFE = max(high[i+1..i+48]) / entry − 1**. `RUNNER50 = MFE>=50%`, `RUNNER100 = MFE>=100%`.
- **Base rate** (unconditional per-bar): runner50 = **0.472%**, runner100 = **0.086%**, median MFE = 3.65%.
- **Null:** matched random-bar bootstrap (draw k random bars 2000×) → empirical one-sided p on the lift.
- **EV:** asymmetric long policy, init stop 25%, two exits tested: ride-to-horizon-close AND chandelier
  trail (12/18/30% off peak). Per-coin 48-bar cooldown so the same window isn't counted twice.
  Slippage swept {0,6,12,25,50}bps, OOS split into first/second time halves.

## Pattern screen — runner-rate lift vs base (the headline)
| pattern | n fires | runner50% | lift× | null p | runner100% | FP% | med MFE% |
|---|--:|--:|--:|--:|--:|--:|--:|
| first_strong_bar (early big up-bar, +5–20% off base) | 415 | **1.20** | **2.55** | 0.048 | 0.482 | 98.80 | 5.00 |
| staircase_4 (4 higher closes + higher lows) | 7,321 | 0.98 | 2.08 | <0.001 | 0.301 | 99.02 | 3.47 |
| range_expansion_3 (3 bars each wider than last, up) | 7,024 | 0.93 | 1.96 | <0.001 | 0.228 | 99.07 | 3.82 |
| gap_and_go (open > prior high, holds green) | 6,805 | 0.87 | 1.84 | <0.001 | 0.176 | 99.13 | 3.40 |
| coil_then_go (NR3 tight cluster → thrust bar) | 2,331 | 0.86 | 1.82 | 0.007 | 0.215 | 99.14 | 3.88 |
| staircase_3 (3 higher closes + higher lows) | 18,310 | 0.79 | 1.67 | <0.001 | 0.218 | 99.21 | 3.44 |
| no_upper_wick_thrust (close at high, ≥0.5 ATR) | 33,919 | 0.61 | 1.29 | <0.001 | 0.130 | 99.39 | 3.47 |

**Real finding:** the runs are NOT random with respect to preceding candle structure. Every "buyers-in-control"
shape lifts the runner-50 probability above base, and the lift is significant vs a matched random-bar null
(p<0.001 for the staircase / expansion / gap / thrust family; p=0.007 coil; p=0.048 first-strong-bar, n thin).
The strongest are **first_strong_bar (2.55×)** and **staircase_4 (2.08×)** — i.e. a strong early up-bar while
still near the base, and a clean multi-bar staircase, roughly double the runner odds. More consecutive higher
closes = bigger lift (staircase_4 > staircase_3). The weakest is the single no-upper-wick thrust (1.29×) — one
strong bar in isolation barely beats base.

## But the lift does not survive as a tradeable entry
A 2× lift sits on a **0.47% base**, so conditioned runner-50 precision is still ~1% → **~99% false-positive
rate**, and the typical favorable excursion is small (median MFE ~3.5%). Tested under the asymmetric thesis
(let the rare runner pay), net EV at realistic cost:

**Chandelier trail 18% (init stop 25%) — representative:**
| pattern | n | EV slip0 | EV slip12 | EV slip25 | OOS (h1 / h2) | win% | max win% |
|---|--:|--:|--:|--:|:--|--:|--:|
| coil_then_go | 1,715 | +0.48 | +0.36 | +0.23 | **+1.34 / −0.62** | 48.9 | 106 |
| first_strong_bar | 374 | +0.41 | +0.29 | +0.16 | **+0.71 / −0.12** | 41.2 | 138 |
| range_expansion_3 | 3,286 | −0.20 | −0.32 | −0.45 | +0.74 / −1.38 | 47.0 | 166 |
| staircase_4 | 2,857 | −0.09 | −0.21 | −0.34 | +0.57 / −1.00 | 44.9 | 170 |
| staircase_3 | 4,322 | −0.09 | −0.21 | −0.34 | +0.55 / −0.98 | 45.3 | 178 |
| gap_and_go | 3,012 | −0.27 | −0.39 | −0.52 | +0.51 / −1.31 | 45.2 | 178 |
| no_upper_wick_thrust | 5,504 | −0.27 | −0.39 | −0.52 | +0.38 / −1.16 | 44.4 | 189 |

(Ride-to-horizon-close exit is the same story, slightly worse.) **Every single pattern's OOS halves
sign-flip: first half positive, second half negative.** Per `alpha_lib` rule 3 that is the definition of
noise, not alpha. The two that look best on aggregate (coil_then_go, first_strong_bar) are positive only
because the first time-half carries them; the second half is negative for both. The asymmetric payoff does
NOT rescue it: even before costs (slip0) five of seven are flat-to-negative, because the >=100% runners are
too rare under the condition (runner100 ~0.2–0.5%) and most "wins" are tiny clips.

## Verdict per pattern
- **first_strong_bar (early big up-bar)** — biggest lift (2.55×) and best forward MFE (5.0% median). REAL
  signal-direction but **NOT tradeable**: n=415, p=0.048 (borderline), OOS sign-flips (+0.71 / −0.12). Best
  candidate if anything, but fails the robustness gate. NO-SHIP.
- **staircase_4 / staircase_3** — real, highly significant lift (2.08× / 1.67×), monotone in bar count.
  **NOT tradeable**: −EV at 12bps, OOS sign-flips. NO-SHIP.
- **range_expansion_3** — real lift (1.96×, p<0.001). −EV at cost, OOS sign-flips. NO-SHIP.
- **gap_and_go** — real lift (1.84×). −EV even at slip0, OOS sign-flips. NO-SHIP.
- **coil_then_go (NR3→thrust)** — lift 1.82× (p=0.007); the only one positive at slip25 on aggregate, but
  OOS sign-flips hard (+1.34 / −0.62) and n is modest. Tempting, but fails OOS. NO-SHIP.
- **no_upper_wick_thrust** — weakest lift (1.29×); −EV, OOS sign-flips. NO-SHIP.

## Bottom line
Candle shapes preceding the runs are **statistically real but economically dead**. They roughly double the
runner probability vs random (a genuine, p<0.001 excess for the staircase/expansion family), confirming the
runs have a buyers-in-control fingerprint — but the lift is on a 0.47% base, precision stays ~1% (~99% FP),
and **no pattern survives OOS both-halves at any slippage tier under either a fixed or trailing asymmetric
exit** (universal first-half-positive / second-half-negative sign-flip). This matches the swarm's standing
"candle space SATURATED" verdict: shape alone is not enough; the continuation tell is not in the candles.

**OVERALL VERDICT: NO-SHIP for all 7 variations.** Honest lift exists; tradeable edge does not.

Scripts: `scratchpad/candlestick_variations.py`, `scratchpad/cs_trail.py`.
Results: `scratchpad/candlestick_variations_results.json`.
