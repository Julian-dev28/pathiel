# Scorecard test: does showing the thesis its own win/loss record help? (2026-10-31)

Operator: "does the thesis consider wins / losses? what we did right? what
could be improved?" It did not. Built the feedback (services/thesis/
scorecard.py, wired behind a flag, default OFF) and tested it on a 30-day
oil replay before touching live behaviour, per the standing rule.

## Question

Does adding a SCORECARD section (the desk's last N graded calls: hit/miss,
return, which scenario bucket actually happened vs. the probability given
to it, calibration summary) to the thesis prompt improve calibration or
money performance, or is it noise/harm?

## Setup

- Desk: oil (primary xyz:BRENTOIL). Model: Opus, both arms. Price data only
  (no intel), cold start every day, no prior thesis carried forward.
- Window: 2026-09-30 to 2026-10-29, 16:00Z, 30 days. Pinned with
  `--end-day 2026-10-30` so both arms replay the identical set of days.
- **Control**: `/tmp/thesis-replay-30b/oil/theses` (pre-existing, not
  rewritten; current prompt rules, no scorecard).
- **Treatment**: `/tmp/thesis-replay-scorecard/oil/theses`, built with:
  `.venv/bin/python -m services.thesis.evals.replay --desk oil --days 30
  --as-of-hour 16 --model opus --scorecard --end-day 2026-10-30 --out-dir
  /tmp/thesis-replay-scorecard/oil/theses`. SCORECARD built from the
  replay's own earlier, now-closed days only (no lookahead).
- Calibration scored by grading every thesis in both arms with
  `services.strategies.grader.grade_thesis`, point-in-time as of
  2026-10-31 (the 6 most recent days per arm have not reached their full
  7-day horizon yet; graded as mid-flight reads, same as any other
  point-in-time grade).
- Money scored by `services.strategies.evals.thesis_backtest` (`--coin
  xyz:BRENTOIL`, $64 start, live 20x rules), same candle cache for both
  arms so the market data is identical.

## Results

**Calibration** (grader, as of 2026-10-31):

| Arm | n | direction graded | hit rate | mean Brier | mean ret/thesis |
|---|---|---|---|---|---|
| Control (no scorecard) | 30 | 30 | 96.7% (29/30) | 0.681 | +3.53% |
| Treatment (scorecard ON) | 30 | 28 (2 flat) | 92.9% (26/28) | 0.706 | +3.02% |

**Money** (`thesis_backtest.py`, $64 start, 20x live rules, same window):

| Series | $end | %/day | trades | win rate | total R | max dd |
|---|---|---|---|---|---|---|
| Control ai_theses | $138.77 | 2.54% | 37 | 46% | 7.67 | 61% |
| Treatment ai_theses | $89.27 | 1.08% | 45 | 44% | 5.96 | 77% |
| always_long (shared control) | $898.25 | 8.94% | 23 | 56% | 25.60 | 66% |
| always_short (shared control) | $3.47 | -9.01% | 25 | 12% | -11.19 | 98% |
| daily_sign (shared control) | $336.80 | 5.53% | 28 | 54% | 20.65 | 84% |

(always_long / always_short / daily_sign are identical in both arms, as
they should be: same candles, no theses involved. Confirms both runs
scored against the same market data.)

What differed day to day: the two arms wrote the same long side on 28 of
30 days. The two exceptions: 2026-09-30 (control long, treatment short,
scorecard was "n=0: no graded theses yet" that day, so the flip is
ordinary model variance, not the scorecard's doing) and two validator
double-rejections in the treatment run (2026-10-02, 2026-10-23) that
degraded to a flat fallback where control wrote a long both days. Both of
those days sat inside a strong uptrend (always_long alone turned $64 into
$898 over the window), so losing the position on them cost more than the
flat call itself shows.

## Verdict: NOT BETTER. Keep the flag OFF.

Calibration is flat to slightly worse (mean Brier 0.706 vs 0.681, hit rate
92.9% vs 96.7%, though the treatment number rests on 2 fewer graded days).
Money is clearly worse: less than two thirds the final equity ($89 vs
$139), under half the daily rate (1.08% vs 2.54%), a thinner win rate, a
lower total R, and a materially worse drawdown (77% vs 61%) despite MORE
trades (45 vs 37). Nothing here says "calibrate confidence against the
record" made the desk worse on purpose; the gap is small-sample noise
(28-30 days, one side flip, two validator misses) amplified by a strongly
trending window where any missed long day compounds hard. It is not
evidence the idea is wrong, only that this one 30-day run gives no reason
to flip the switch.

## Flag to flip if adopted

`PATHIEL_THESIS_SCORECARD=1` (read by `services.thesis.scorecard.
scorecard_enabled()`, consumed in `services/strategies/pipeline.py`'s
`refresh()` via `_build_scorecard_text()`). Unset today: the live oil desk
runs exactly as before this change. Flipping it needs no restart beyond
the next scheduled `desk_refresh` (the env var is read fresh every call).

## Token usage of this eval

| Run | LLM calls | input tokens | output tokens | cost |
|---|---|---|---|---|
| Smoke (2 days, `--scorecard`) | 4 | 42,313 | 9,777 | $0.5378 |
| Full treatment (30 days, `--scorecard`) | 55 | 614,122 | 142,269 | $7.0013 |
| **Total this eval** | **59** | **656,435** | **152,046** | **$7.5391** |

All Opus, as instructed (never Fable). The control run's theses already
existed (not rewritten, no new spend this session); its own original run
cost $3.613 across 18 written days, for reference.

One honest flag on the call-count cap: nearly every day in BOTH arms
needed a validator repair round (desk-wide grounding false positive on
"24h"/"8h" style range labels being misread as bare numbers, e.g.
`scenario 'X' mentions 24, outside its own implied price range`) -- 15 of
18 written control days, similarly in treatment. That is a pre-existing
oil-desk validator sensitivity, unrelated to this feature, and it is why a
cold 30-day run costs ~1.8 calls/day (~55) rather than the ~1/day the
"~35 total" guidance assumed. Worth a separate look, not fixed here.
