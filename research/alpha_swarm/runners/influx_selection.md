# Lane 1 — SELECTION: which 5m volume-influx events actually RUN?

Script: `research/alpha_swarm/runners/influx_selection.py` (pure numpy, no sklearn, read-only).
Data: `movers_5m.json` (180 small-cap perps, ~5000 5m bars each). SURVIVOR universe = upper bound.

## Setup
- Event = green 5m candle with vol >= 1.5x trailing-6-bar mean vol. **91,659 events.**
- Label = runner if forward MFE over next 96 bars (8h) >= 10% (base rate **2.07%**) / >= 20% (base **0.33%**).
- Entry replayed at the open of bar i+3 (after 2 follow-through candles are observed, so the
  follow-through features are lookahead-safe). Forward MFE and exits measured from that entry.
- Logistic regression, time-sorted OOS split (fit first half, score second half). AUC is rank-based.
- 13 features, all computed from bars <= the feature-cutoff bar (no lookahead).

## OOS AUC table (composite vs each feature alone)

| feature              | AUC (>=10%) | AUC (>=20%) |
|----------------------|------------:|------------:|
| **COMPOSITE (all)**  |  **0.7782** |  **0.8116** |
| trail_vol            |      0.7786 |      0.7948 |
| influx_move          |      0.6729 |      0.7358 |
| prior_1h_ret         |      0.6090 |      0.6546 |
| compression          |      0.5349 |      0.5629 |
| ft2_vol              |      0.5373 |      0.4787 |
| influx_mag           |      0.5220 |      0.5372 |
| ft2_green            |      0.5223 |      0.5229 |
| consec_green         |      0.5157 |      0.5385 |
| ft3_green            |      0.5204 |      0.5730 |
| body_wick            |      0.5146 |      0.4921 |
| ft3_vol              |      0.4513 |      0.4432 |
| tod_sin              |      0.5110 |      0.5309 |
| tod_cos              |      0.5096 |      0.4319 |

## Which features carry signal
- **trail_vol (coin trailing volatility) is the whole show.** It alone scores AUC 0.7786, equal to the
  full composite (0.7782). Drop it and composite AUC falls to **0.6685.** High-vol coins simply throw
  bigger 8h excursions, so they clear the +10% MFE bar more often. This is a volatility artifact, not a
  timing edge: the same volatility that lifts MFE also lifts the downside.
- The only other features with standalone signal are **influx_move** (the influx candle's own % move, 0.67)
  and **prior_1h_ret** (0.61). Both are extension/momentum proxies, i.e. already-known edges, not new ones.
- **The operator's hypothesized tells carry almost nothing.** influx_mag (vol/trailing, 0.52), the
  follow-through candles (ft2/ft3 vol+color, 0.45-0.57), range-compression/coil (0.53), consecutive-green
  (0.52), body/wick (0.51), time-of-day (0.51) are all at-or-near AUC 0.50. The "big volume bar + green
  follow-through after a coil" pattern does **not** separate runners from non-runners.

## Top-quartile EV lift (tight-floor exit, net 12bps, OOS test half)

The score sorts runner-RATE cleanly and monotonically (deciles, test half):

| decile | runner-rate (>=10%) | tight-floor EV |
|-------:|--------------------:|---------------:|
| D1 (low)  | 0.41% | -0.602% |
| D5        | 0.68% | -0.421% |
| D8        | 2.07% | -0.309% |
| D9        | 3.16% | -0.282% |
| **D10**   | **8.79%** | **-0.278%** |

Top decile runner-rate 8.79% vs base 2.07% = **4.2x lift.** The classifier genuinely predicts which influx
events reach +10% MFE. But:

| bucket (test) | runner-rate | tight-floor EV | win |
|---------------|------------:|---------------:|----:|
| top-quartile  | 5.25% | **-0.293%** | 43.8% |
| rest (bottom 75%) | 0.88% | -0.425% | 40.7% |
| all events    | 1.97% | -0.392% | 41.5% |

**EV is negative in every cell, including the top decile.** Sorting by score moves EV from -0.43% to
-0.29% but never reaches zero. The MFE-prediction selects volatile coins, and the tight-floor exit gets
whipsawed by that same volatility.

## Matched random-entry null (the honest test)
Random entry, same coins, random times, same tight-floor exit:

| comparison | EV |
|------------|---:|
| random-entry null        | -0.171% |
| all-influx vs null (EXCESS)     | **-0.038%** |
| top-quartile vs null (EXCESS)   | **-0.121%** |

The top-quartile EV (-0.293%) is **worse** than a matched random entry (-0.171%). EXCESS is **negative**.
Selecting the highest-score influx events does not beat throwing a dart, because the score concentrates on
high-volatility coins whose tight-floor exits bleed harder than average.

## Monetization attempt (wider "ride" exit, Lane-3 peek)
Swapping the tight floor for a 25% give-back ride exit on the top decile improves it to -0.097% EV (from
-0.278%) but it is **still negative**. No exit tested turns the selected entries +EV.

## VERDICT
**No.** There is no selection score that turns the breakeven 5m influx entry +EV. The classifier hits OOS
AUC 0.78 (>=10%) / 0.81 (>=20%) and lifts top-decile runner-rate 4.2x, but that AUC is a volatility
artifact (trail_vol alone = 0.78; drop it and AUC falls to 0.67), and the top-quartile tight-floor EV
(-0.293%) is WORSE than a matched random-entry null (-0.171%), EXCESS -0.121%. The operator's pattern
features (influx magnitude, follow-through, coil, consec-green, body/wick, time-of-day) all sit at AUC
~0.50 and carry no signal. The MANTA/MEME/CELO charts are survivorship: predicting MFE is easy (pick
volatile coins), monetizing it on the 5m horizon is not.

Caveat: survivor universe is an upper bound, so the real-world number is worse, not better. All-influx EV
here (-0.209%) is more negative than the queue's prior "+0.01%" tight-floor figure because this harness
uses a looser 10%-give-back trail and a delayed i+3 entry; the load-bearing result is the internal
relative comparison (top-quartile vs rest vs matched null), which is self-consistent and negative.
