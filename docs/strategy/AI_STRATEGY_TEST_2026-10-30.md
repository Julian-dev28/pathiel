# AI-informed strategy test (2026-10-30)

Operator: "is there a more profitable, more ai informed ai powered strategy",
then "do 30 days instead" (Fable at 85% of the weekly quota, so the replay ran
on Opus 5.5). Nothing live changed.

**Answer: no.** Letting the AI pick the entry, stop and target (A) lost money;
sizing by the AI's confidence (B) adds nothing, because the confidence barely
varies. The current rules stay.

## How it was tested

- `services/thesis/evals/replay.py`: one thesis per day for the last 30 days
  (2026-09-30 to 10-29, 16:00Z), written by the real thesis pipeline (prompt,
  validator, repair) on Opus 5.5 from candles that had closed by then, no news.
  30 of 30 long; confidence 0.35 to 0.55. Cost $6.07 for the 30 used here
  ($2.45 + $3.61), plus $6.31 for a first run on the rules fixed in 28eb187.
- `services/strategies/evals/ai_strategy_test.py`: the live rules
  (`desk_tick_sim.live_params()`) against
  - **A, AI levels**: the tick uses the thesis's own entry, stop and first
    target when they pass the 2R and 20x checks, else the normal rules;
  - **B, conviction sizing**: 20x when the thesis confidence is at or above
    a threshold, 15x below it (the operator's floor).
- Both on the 30 replayed theses and on the 10 real journaled ones.

## Results ($64 start, per day = compounding daily rate)

30 replayed theses (Opus, price only, 30 days):

| Variant | Per day | Per week | $64 ends | Trades | Won | Max drawdown |
|---|---|---|---|---|---|---|
| **Live rules** | 2.78% | 21.1% | $145.23 | 38 | 47% | 61% |
| A: AI levels | -3.71% | -23.3% | $20.63 | 64 | 27% | 86% |
| B: 20x at confidence >= 0.55, else 15x | 2.29% | 17.1% | $125.82 | 38 | 47% | 61% |
| B: >= 0.65 (no thesis reached it: all 15x) | 3.31% | 25.6% | $169.64 | 40 | 48% | 54% |
| A + B | -1.13% | -7.7% | $45.49 | 66 | 27% | 68% |

10 real journaled theses (Fable, with news, 2026-10-18 to 10-30):

| Variant | Per day | $64 ends | Trades | Won | Max drawdown |
|---|---|---|---|---|---|
| **Live rules** | 18.59% | $531.36 | 9 | 78% | 29% |
| A: AI levels | 12.95% | $290.13 | 13 | 38% | 42% |
| B (any threshold: all 15x) | 14.55% | $345.35 | 9 | 78% | 23% |
| A + B | 8.41% | $174.35 | 12 | 33% | 34% |

## Reading

- **A loses.** The thesis's own stops sit too close: twice the trades, a
  27-38% win rate. The fixed structure rules (stop 0.75 beyond the 24h edge,
  2R) are what make the desk work.
- **B is just a leverage switch.** Confidence sat between 0.35 and 0.55 on
  every replayed day, so it never sorts good calls from bad ones. Its rows
  equal "everything at 15x": better on the replay (3.31% vs 2.78% a day,
  lower drawdown), worse on the real theses (14.6% vs 18.6%). The earlier
  15x-20x sweep on the controls also favoured 20x. Stay at 20x.
- **A more honest expectation.** Over 30 days of AI theses without news, the
  live rules made about 2.8% a day ($64 to $145) with a 61% worst drawdown.
  The 18.6% a day on the real theses came from 12 days of a strong rally.

## Where AI could still help (untested)

The direction call is where the AI already earns its keep. The next test
worth paying for is the AI saying **when not to trade** (flat on chop or
before a known risk event), measured on the same replay harness, because
drawdown, not entry quality, is the weak spot.
