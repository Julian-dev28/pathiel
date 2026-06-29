# Volume-influx books: is excluding HIP-3 (xyz:*) markets correct?

The volume-influx books exclude all `:` coins (HIP-3 tokenized stocks/commodities like `xyz:CL`
oil, `xyz:SP500`). Operator flagged thin HIP-3 markets as "easy to game" with a volume trigger.
Test: does the operator's immediate volume-influx LONG perform WORSE on HIP-3 than on crypto
(validating the exclusion), or is it actually fine?

## Method (lookahead-safe, identical engine on both universes)

- ENTRY = a GREEN 5m candle whose volume `>= 1.5x the PREVIOUS candle's volume` -> long next bar open.
- EXIT = tight profit-floor (`gb=0.10`), hard stop `0.15`, max-hold 96 bars (8h). Net 12bps round-trip.
- Decide on complete bar `i`, fill `i+1` open. Forward 96-bar (8h) MFE for run-capture.
- OOS = time-sorted both halves. EXCESS = strategy EV minus a matched random-entry null
  (same coins, same per-coin count, same exit).
- Absolute `$-notional` floor on the influx candle (`close*vol`), the lever that just turned the
  CRYPTO version OOS-positive, swept at {25k, 50k, 100k, 250k}.
- Data: `scratchpad/hip3_5m.json` (96 xyz HIP-3 coins fetched, 83 with full ~5000-bar history;
  13 newly-listed had no candles) vs `scratchpad/movers_5m.json` (176 main-perp crypto movers).
- Scripts: `influx_hip3_validation.py` (engine), `build_hip3_5m.py` (fetch).

## Result — main table (immediate 1.5x-prev LONG, tight-floor, net 12bps)

### CRYPTO (176 main-perp movers) — matched-random null EV +0.023%

| variant       | n     | EV       | win | run10 | run20 | OOS h1/h2        | verdict   |
|---------------|-------|----------|-----|-------|-------|------------------|-----------|
| no-floor      | 16354 | +0.037%  | 67% | 2%    | 0%    | +0.160 / -0.086  | mixed     |
| $-floor 25k   | 4597  | +0.030%  | 68% | 3%    | 1%    | +0.109 / -0.049  | mixed     |
| $-floor 50k   | 3187  | +0.079%  | 70% | 3%    | 1%    | +0.140 / +0.017  | +EV both  |
| $-floor 100k  | 2073  | +0.074%  | 69% | 4%    | 1%    | +0.119 / +0.029  | +EV both  |
| $-floor 250k  | 1182  | +0.114%  | 70% | 4%    | 0%    | +0.193 / +0.035  | +EV both  |

Reproduces the known crypto finding: barely +EV no-floor (excess over null +0.014%), and the
`$-floor` rescues it to +EV in BOTH OOS halves from 50k up.

### HIP-3 (83 xyz tokenized stocks/commodities) — matched-random null EV -0.054%

| variant       | n    | EV       | win | run10 | run20 | OOS h1/h2        | verdict |
|---------------|------|----------|-----|-------|-------|------------------|---------|
| no-floor      | 6608 | -0.065%  | 54% | 1%    | 0%    | +0.064 / -0.194  | mixed (h2 deep neg) |
| $-floor 25k   | 3767 | -0.032%  | 59% | 1%    | 0%    | +0.115 / -0.178  | mixed   |
| $-floor 50k   | 3008 | -0.064%  | 59% | 1%    | 0%    | +0.106 / -0.234  | mixed   |
| $-floor 100k  | 2273 | -0.059%  | 58% | 1%    | 0%    | +0.134 / -0.252  | mixed   |
| $-floor 250k  | 1430 | -0.045%  | 60% | 2%    | 0%    | +0.112 / -0.202  | mixed   |

HIP-3 is `-EV at EVERY floor`. Excess over its own null is -0.011% (worse than random entry on
the same coins). The `$-floor` that flipped crypto to +EV does `nothing` here: the EV stays
negative and the OOS second half stays deeply negative (-0.18 to -0.25%) at every floor.

## Gameability — what actually breaks on HIP-3

The operator's "easy to game / spiky" intuition is directionally right that the edge dies, but
the mechanism is the opposite of fat tails. Side-by-side (no-floor events):

| metric                                  | CRYPTO  | HIP-3   |
|-----------------------------------------|---------|---------|
| immediate-reversal frac (next bar red & < influx open) | 20.4% | 17.1% |
| return std                              | 1.84%   | 1.60%   |
| p05 / p95 of trade return               | -3.69 / +1.74% | -2.84 / +1.42% |
| worst-decile mean return                | -4.12%  | -3.58%  |
| median forward 8h MFE                    | +1.48%  | +0.70%  |
| influx candles that never run even 2% (mfe<2%) | 61% | `81%` |
| win rate                                 | 67%     | 54%     |

HIP-3 is `lower` variance with `tighter` tails and `fewer` immediate single-bar reversals. It is
not spikier. What kills it is `no follow-through`: 81% of HIP-3 volume-influx candles never run
even 2% (vs 61% crypto), and median 8h MFE is half of crypto's (0.70% vs 1.48%). The volume
spike on a tokenized stock/commodity carries no directional information on the 5m perp tape, so
the tight-floor exit clips microscopic winners while the occasional loser still runs to the stop
-> negative EV. The signal does not transfer because HIP-3 price is driven by the off-exchange
underlying (RTH equity/commodity flow, gaps over closed hours), not by its own perp volume.

## Verdict

`YES — excluding HIP-3 from the volume-influx books is CORRECT.` The immediate volume-influx LONG
is -EV on HIP-3 at every state (no-floor -0.065%, never positive through a 250k `$-floor`, excess
-0.011% vs random, OOS second half -0.18 to -0.25%), while the same engine is +EV-both-halves on
crypto from a 50k floor up. The volume trigger has zero follow-through on HIP-3 (81% never run 2%);
the dollar-floor that rescues crypto does not rescue HIP-3.

## Caveat

Survivor universe: only the 83 xyz coins currently listed with ~17 days of 5m history; the 13
newly-listed (no candles) are exactly the thinnest/most-gameable names and are excluded, so this
is an upper bound that likely flatters HIP-3. The conclusion (exclude) holds a fortiori. ~17-day
window is short; result is direction-of-sign robust across both OOS halves and all five $-floors.
