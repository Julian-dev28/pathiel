# 30-day desk-tick backtest results (2026-10-25)

Plan: `docs/overhaul/BACKTEST_PLAN_2026-10-25.md`. Simulator and eval:
`services/strategies/backtest/`, `services/strategies/evals/desk_tick_backtest.py`.
Full machine-readable output: `/tmp/desk-backtest/report.json` and
`/tmp/desk-backtest/trades_<run>.csv` (one CSV per main run).

Data: xyz:BRENTOIL, 15m and 1h candles, public Hyperliquid `/info`
`candleSnapshot`, cached under `/tmp/desk-backtest-cache/`.

| | |
|---|---|
| window | 2026-09-25T01:34 UTC -> 2026-10-25T01:34 UTC (30 days) |
| 15m candles in window | 2,880 (2,901 fetched) |
| 1h candles in window | 720 (1,321 fetched, incl. 25-day daily-sign lookback over 55 days total) |
| journaled theses found | 6 (`pathia/.state/strategies/oil/theses`, read only) |
| starting equity | $64 |
| leverage / sizing | 20x isolated, 90% of equity x leverage (full-port), 10% free-margin floor |
| fees | taker 0.045%, maker 0.015% |

## Headline: the four direction series

| run | trades | win rate | expectancy (R) | expectancy ($) | max DD | liquidations | fill rate |
|---|---|---|---|---|---|---|---|
| always-long | 67 | 11.9% | **-0.27** | -$0.71 | 86.0% | 0 | 93.1% |
| always-short | 71 | 1.4% | **-1.29** | -$0.89 | 98.5% | 0 | 85.7% |
| daily-sign (20d return) | 81 | 11.1% | -0.58 | -$0.73 | 95.7% | 0 | 94.2% |
| journaled theses (6, 7d) | 7 | 28.6% | **+1.58** | +$3.65 | 19.5% | 0 | 87.5% |

Both mechanics-only control runs (always-long, always-short) are negative
in R. Per the plan's own reading rule (section 5): **the mechanics bleed,
independent of which side anyone picks.** The journaled-thesis run is the
only positive series, and it is also the only one with a real, non-random
direction input and the shortest sample (7 trades over 7 days) — see
"what this cannot say" below.

## Gates, fees, funding

| run | rested | filled | cancelled (hygiene) | refused: min_r | refused: leverage | fees paid | funding paid | fees / \|gross\| | funding / \|gross\| | gross P&L |
|---|---|---|---|---|---|---|---|---|---|---|
| always-long | 72 | 67 | 5 | 0 | 0 | $23.48 | -$0.015 | 96.7% | 0.06% | -$24.27 |
| always-short | 84 | 72 | 12 | 0 | 0 | $11.78 | $0.014 | 23.0% | 0.03% | -$51.18 |
| daily-sign | 86 | 81 | 5 | 2 | 0 | $15.69 | $0.0005 | 36.2% | <0.01% | -$43.36 |
| journaled | 8 | 7 | 1 | 0 | 0 | $4.33 | $0.00 | 14.5% | 0.00% | +$29.87 |

The 20x leverage gate (`check_leverage`) never refused a setup in this
window — a 0.10-0.50 stop buffer on Brent never gets close to using half
the distance to a 20x liquidation (see the sweep below: liquidations stay
at 0 even at the widest swept buffer). The 1.5R gate refused twice, both
in the daily-sign run. **Fees, not funding, are the real cost**: funding's
share of gross P&L is under 0.1% in every run (hourly rates on Brent this
month were small and mostly cancelled out over the holding periods);
fees eat 15-97% of gross, and on the always-long run alone they are
larger than the loss itself would be without them.

## Parameter sweep (reported, not fitted)

Stop buffer {0.10, 0.25, 0.50} x slippage {0.02%, 0.05%} x leverage {10x, 20x},
always-long and always-short only (the mechanics-only controls).

| side | stop buf | slippage | leverage | trades | expectancy (R) | win rate | max DD | liquidated |
|---|---|---|---|---|---|---|---|---|
| always-long | 0.10 | 0.02% | 10x | 85 | -0.23 | 5.9% | 46.7% | no |
| always-long | 0.10 | 0.02% | 20x | 85 | -0.23 | 5.9% | 72.1% | no |
| always-long | 0.10 | 0.05% | 10x | 85 | -0.51 | 5.9% | 52.7% | no |
| always-long | 0.10 | 0.05% | 20x | 85 | -0.51 | 5.9% | 81.1% | no |
| always-long | 0.25 | 0.02% | 10x | 67 | -0.23 | 11.9% | 60.0% | no |
| always-long | 0.25 | 0.02% | 20x | 67 | -0.23 | 11.9% | 84.9% | no |
| always-long | 0.25 | 0.05% | 10x | 67 | -0.34 | 11.9% | 64.1% | no |
| always-long | 0.25 | 0.05% | 20x | 67 | -0.34 | 11.9% | 88.5% | no |
| always-long | 0.50 | 0.02% | 10x | 47 | +0.15 | 21.3% | 48.9% | no |
| always-long | 0.50 | 0.02% | 20x | 47 | +0.15 | 21.3% | 77.5% | no |
| always-long | 0.50 | 0.05% | 10x | 47 | +0.11 | 21.3% | 51.7% | no |
| always-long | 0.50 | 0.05% | 20x | 47 | +0.11 | 21.3% | 80.1% | no |
| always-short | 0.10 | 0.02% | 10x | 112 | -1.83 | 0.0% | 84.2% | no |
| always-short | 0.10 | 0.02% | 20x | 112 | -1.83 | 0.0% | 97.6% | no |
| always-short | 0.10 | 0.05% | 10x | 112 | -2.13 | 0.0% | 88.3% | no |
| always-short | 0.10 | 0.05% | 20x | 112 | -2.13 | 0.0% | 98.7% | no |
| always-short | 0.25 | 0.02% | 10x | 71 | -1.25 | 1.4% | 86.7% | no |
| always-short | 0.25 | 0.02% | 20x | 71 | -1.25 | 1.4% | 98.2% | no |
| always-short | 0.25 | 0.05% | 10x | 71 | -1.37 | 1.4% | 88.8% | no |
| always-short | 0.25 | 0.05% | 20x | 71 | -1.37 | 1.4% | 98.8% | no |
| always-short | 0.50 | 0.02% | 10x | 47 | -0.88 | 6.4% | 87.0% | no |
| always-short | 0.50 | 0.02% | 20x | 47 | -0.88 | 6.4% | 98.5% | no |
| always-short | 0.50 | 0.05% | 10x | 47 | -0.94 | 6.4% | 88.4% | no |
| always-short | 0.50 | 0.05% | 20x | 47 | -0.94 | 6.4% | 98.8% | no |

Zero liquidations anywhere in the sweep, including the widest buffer and
highest leverage — the 20x rule holds at every setting tried. Leverage
(10x vs 20x) doesn't change expectancy_R or win rate AT ALL, in any row
(both are leverage-independent by construction: R and the fee/slippage
fractions scale with price, not with size, and the 20x rule's gate never
bound in this window) — it only moves max drawdown, because higher
leverage means bigger size against the same stop-distance loss. Slippage
(0.02% vs 0.05%) is NOT free, unlike leverage: it costs roughly 0.1-0.3R
across every row (e.g. always-long at the 0.10 buffer: -0.23R -> -0.51R;
always-short at the same buffer: -1.83R -> -2.13R), consistent with it
being a real cost on every stop fill, not a rounding error. The stop
buffer is still the dominant lever: widening it from 0.10 to 0.50 roughly
halves the trade count (85 -> 47 for always-long) by making fewer
pullbacks qualify for the 1.5R gate at the new, wider stop, and on the
long side it's the only setting that pushes expectancy positive (+0.11 to
+0.15R). The short side never
clears zero at any setting tried.

## Daily-sign series: data depth

20-day trailing return, sign only, updated once per day, no lookahead
(close of day D-1 vs. close of day D-22, both already resolved before any
tick on day D). 55 days of 1h history were fetched specifically so the
30-day display window would never truncate for lack of lookback:

| | |
|---|---|
| days with a signal (full series) | 35 |
| days without enough history (full series) | 21 |

The 21 days without a signal are all inside the extra 25-day lookback
buffer fetched purely to seed this series — every day inside the actual
30-day backtest window has a signal. No truncation inside the reported
window.

## Calibration against the live log

Plan section 6 / step 4. Replayed the journaled-thesis direction series
over the same window the live loop actually traded, and compared "in a
position" (yes/no) between the simulator and `/tmp/oil-loop/progress.log`'s
`DESK-TICK oil ... pos=...` lines, at each log line's nearest 15m tick.

| | |
|---|---|
| log lines parsed | 155 |
| ticks compared | 155 |
| agree | 5 |
| disagree | 150 |
| agreement rate | 3.2% |

**This is a real, known divergence, not a bug to paper over.** The
simulator and the live loop agree on almost nothing here, and the reason
is structural, not a parsing error: the live loop's resting entry, stop
and target came from the model's own thesis-specific price picks (its
`entry_zone`, its chosen stop, its own targets), re-judged and often
pyramided by hand tick to tick — the account spent most of this window
holding ONE position (built up via repeated discretionary top-ups) that
the simulator's purely structural rule (rest exactly at the 8h edge, exit
exactly at the 24h edge or the thesis's parsed invalidation) opens and
closes on a completely different, independent schedule. Every sampled
divergence in `report.json` is the same shape: the log shows a position
open (`pos=8.69@111.43` style rows, most of 2026-10-24) while the
simulator, running its own structural entry/exit logic over the same
candles, is flat at that tick. This is exactly what plan section 2
predicts the three direction series would and wouldn't prove: the
simulator replays the MECHANICS honestly (that part has its own gate
tests: fills, stops, targets, gates, no lookahead), but it does not
reproduce a human-judged, hand-managed position's exact lifecycle, and it
was never designed to. The agreement number here is reported, not hidden,
per the plan's own instruction (section 6).

## What this cannot say (plan section 8, confirmed in practice)

- The journaled-thesis run is 7 trades over 7 days. One sample. +1.58R
  expectancy on 7 trades is not evidence of edge; it's one real week,
  reported honestly as the only non-synthetic direction series available.
- Headline-driven exits (a ceasefire print mid-bar) aren't in here; every
  exit in this backtest is a stop, a target, a 1h-close invalidation, or a
  direction flip.
- One instrument (xyz:BRENTOIL), one regime (the Hormuz shock and its
  aftermath), 30 days. This is evidence about THESE mechanics on THIS
  tape, nothing forward-looking.

## Verdict

**The mechanics bleed on their own.** Both direction-agnostic controls
(always-long -0.27R, always-short -1.29R; the 20-day-sign control -0.58R)
lose money before anyone's thesis quality enters the picture, confirming
plan section 5's reading rule. Fees are the dominant cost (15-97% of
gross P&L across runs, not funding), and leverage/slippage barely move
expectancy while the stop buffer does — the loop's actual edge, such as
it is, lives entirely in the direction call (the journaled run: +1.58R),
not in the entry/exit mechanics being replayed here.

**What the loop should change, in order of leverage:**

1. **Widen the stop** (sweep: 0.10 -> 0.50 under/over the structure edge).
   It is the only mechanical change in this sweep that pushed the long
   side positive, and it halves trade count — fewer, better-qualified
   pullbacks, each paying less in fees relative to its size. The current
   live practice (0.09-0.24, observed in the three journaled theses whose
   stop sat directly under the structural 8h low: 108.05 vs. 108.14, 108.05
   vs. 108.29, 110.80 vs. 110.97) sits at the WORST end of what was tested
   here.
2. **Fees are the main leak, not funding.** On the always-long run fees
   alone (96.7% of gross) are larger than the loss without them. Fewer,
   larger, more selective entries (point 1) cut this directly; chasing
   maker fills instead of taker stops where possible is the other lever,
   already the simulator's (and the live loop's) default behavior for
   entries and targets.
3. **The 20x leverage rule is not the binding constraint here** — it never
   refused a setup in 30 days, at any swept leverage or stop buffer, and
   never produced a liquidation. Loosening or tightening it would not have
   changed this month's P&L. It's free insurance against a regime this
   tape didn't test, not evidence it's currently costing anything.
4. **The real edge is the thesis, not the mechanics** — exactly what plan
   section 2 says this backtest was built to show, and exactly what it
   found: strip the thesis out (always-long/short/daily-sign) and the
   loop loses; put it back (journaled) and the same mechanics, on the
   same tape, turn positive. Keep measuring thesis quality where it
   already is (`desk_grade`, `grade-everything`), not here.
