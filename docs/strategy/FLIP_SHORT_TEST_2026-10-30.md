# Flip short after a target: backtest and thesis (2026-10-30)

Operator: "upon long close short down to long entry ... dont make any changes
just run a test on roi and run a thesis on it, use fable". Then: "would it
have been more profitable to implement 15x with the flips?"

**Answer: no.** Nothing live changed. Every flip version made less per day
than the live rules (20x, no flips), and the whole desk at 15x with flips
was worse than 20x without them on every series.

## The rule tested

When a long's target fills, the next 15-minute tick sells short at market.
Target: the long's entry price. Stop: a buffer above the 24h high. The flip
trades against the long thesis on purpose, so the thesis invalidation does
not close it. Variants: flip R gate 1 / 1.5 / 2, flip leverage 10 / 15 /
20x, stop buffer 0.75 / 1.5, time stop none / 12h / 24h (the intraday
version). Code: `desk_tick_sim.Params.flip_*` (off by default), runner
`services/strategies/evals/flip_test.py`, gate tests
`services/strategies/tests/test_strategy_variants.py` and `test_flip_test.py`.

## Results

Per day = compounding daily rate from $64. Thesis = 9 journaled long theses
over 11.83 days. Controls = always long over 30 and 50 days; 50a = Sep 11
to Oct 5 (Brent flat to down), 50b = Oct 5 to Oct 30 (up).

| Setup | Thesis per day | $64 ends | Flips | Flip $ | 30d per day | 50d per day | 50a / 50b |
|---|---|---|---|---|---|---|---|
| 20x, no flips (live) | 18.73% | $487.66 | 0 | 0 | 9.52% | 4.68% | -0.37% / 9.98% |
| 15x, no flips | 14.65% | $322.49 | 0 | 0 | 7.54% | 3.81% | -0.40% / 8.20% |
| Best 15x + flips (1R, 0.75 buffer, 24h stop) | 15.27% | $343.57 | 4 | +$22.68 | 7.01% | 3.21% | -0.44% / 6.11% |
| Best 20x flip on the controls (2R, 0.75 buffer) | 18.73% | $487.66 | 0 | 0 | 12.67% | 5.21% | -1.96% / 9.34% |

None of the 54 flip variants beat live on the thesis run and both controls
together. The 20x flip that wins the 30- and 50-day controls never fires on
the thesis run (the 20x liquidation rule refuses its stop), loses in the
flat-to-down half where a short should help (-1.96% vs -0.37% a day), and
rests on 3 control trades.

Today's trade: shorting at 113.26 back to the long's entry (109.46), with
the stop 0.75 over the 114.07 high, pays 2.44R but fails the 20x rule. It
passes at 15x or 10x.

## Thesis (Fable 5.1, $0.54, every number checked against the report)



**Verdict:** Do not adopt. Live runs 18.731%/day on the journaled set and no variant that actually fired a flip beat it; the only setting that lifts control30 (12.668% vs 9.515%) matches journaled only because it refused all 6 candidates.

**Why**
- The best variant (2R, 20x, 0.75 buffer) wins control30 on three flips, two winners, $134.29. That is the whole case, and three trades is not a sample.
- Same variant in the flat-to-down window (control50a), where a short should shine: 0 of 3 won, -$13.69, per-day drops from -0.372% to -1.961%. It also trails in the up window, 9.343% vs 9.98%.
- Winning flips still cost money because they hold the only slot. The 1R/15x/12h version won 7 of 9 flips in control30 (+$77.90) yet cut per-day from 9.515% to 7.677%.
- Cutting to 15x so today's 2.44R flip clears the liquidation rule taxes the long leg: journaled falls from 18.731% to 14.017%, control30 from 9.515% to 8.592%.
- The only run above live on journaled (1R, 15x, 1.5 buffer, 19.839%) got there with flips that lost $61.98; the lift came from skipping a long, and it gives back control50b, 4.59% vs 9.98%.

**Where it would earn**
A two-sided range where the 24h high holds as a ceiling and price retraces all the way to the long's entry inside a day. Winners resolved fast; in the time-stop runs the laggards left on time, not target. On 15m charts: the target fills on a wick (today: 114.07, now 113.2), the next candle closes back inside the range, the 24h high is not above yesterday's, and no long thesis sits above price. Today fails the last check: Fable wants a long at 113.87, stop 111.29, and the flip's target of 109.46 is below that stop.

**Risks**
- Slot cost: one position, so a flip blocks the next long in the uptrend the desk is built for (Brent +12% in 11.83 days).
- Liquidation geometry: a stop above the 24h high at 20x uses over half the distance to liquidation; 5 of 6 journaled candidates were refused on it, as today's would be.
- No drawdown relief: control50 max drawdown stays 87.2% with or without flips.

**What would change the verdict**
- A variant that beats live on journaled (18.731%), control30 (9.515%) and control50 (4.676%) together without worsening control50a (-0.372%). 54 tries have not found one.
- A paper log of 20 or more qualified 2R/20x flips with positive total R and a hit rate above 50%.
- A test where the flip only fires when no long thesis is pending, or does not consume the long slot. If that clears all three controls, adopt it there.
Note: the thesis's "Cutting to 15x" bullet compares one flip-at-15x variant (flip 2R 15x buf 0.75 24h stop), not the whole desk at 15x; the table above has the whole-desk answer.
