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

---

## Rerun, same day: the stop buffer widened to 0.50 (now the live rule)

Operator, after the first run: "do it, widen the stop buffer and rerun."
Three things changed before this rerun, all in the same commit:

| where | before | after |
|---|---|---|
| `.claude/agents/desk-tick.md` step 5 (the loop's own brackets) | "stop beyond the structure" | stop 0.50 beyond the structure on Brent, about two 1h ATR14; the same two-ATR distance on another desk |
| `services/thesis/prompt.py` (the thesis writer's stop rule) | "just beyond a real structural edge" | beyond by about two 1h ATR14 (0.50 on Brent), not right under it |
| `desk_tick_sim.Params.stop_buffer` (the simulator's default) | 0.25 | 0.50 |

Same 30-day window, same cache, same equity, fees and leverage. The sweep
also gained 0.75 and 1.00 to see whether 0.50 is the knee.

### Headline, before and after

| run | trades | win rate | expectancy R (net of fees and funding) | expectancy $ | final equity from $64 | max DD |
|---|---|---|---|---|---|---|
| always-long, 0.25 | 67 | 11.9% | -0.27 | -$0.71 | $16 | 86.0% |
| **always-long, 0.50** | 47 | 21.3% | **+0.14** | -$0.55 | **$38** | 78.4% |
| always-short, 0.25 | 71 | 1.4% | -1.29 | -$0.89 | $1 | 98.5% |
| always-short, 0.50 | 47 | 6.4% | -0.90 | -$1.34 | $1 | 98.6% |
| daily-sign, 0.25 | 81 | 11.1% | -0.58 | -$0.73 | $5 | 95.7% |
| daily-sign, 0.50 | 57 | 21.1% | -0.28 | -$0.99 | $7 | 95.4% |
| journaled, 0.25 | 7 | 28.6% | +1.58 | +$3.65 | $90 | 19.5% |
| journaled, 0.50 | 6 | 50.0% | +1.86 | +$11.25 | $132 | 26.6% |

Fees, 0.50 run: always-long $16.05 on a gross of -$9.59 (fees are 1.7x
the gross loss), always-short $10.69, daily-sign $9.70, journaled $3.39
(4.8% of gross). Funding stays under 0.1% everywhere. The 20x rule refused
nothing; no run liquidated.

### Read the long control carefully

Positive mean R and negative dollars on the same run is not a
contradiction, it is sequencing under full-port compounding: the early
trades lost at full size, the later wins landed on a smaller stack. The R
figure is the rule's per-trade quality and the one to compare across
settings; the dollar figure is this particular path. Halving the dollar
loss (final equity $16 to $38) is the honest size of the improvement.

### Is 0.50 the knee?

Always-long, slippage 0.02% (leverage does not move R):

| stop buffer | trades | win rate | expectancy R |
|---|---|---|---|
| 0.10 | 85 | 5.9% | -0.23 |
| 0.25 | 67 | 11.9% | -0.23 |
| **0.50** | 47 | 21.3% | **+0.15** |
| 0.75 | 35 | 25.7% | +0.08 |
| 1.00 | 23 | 26.1% | +0.07 |

Yes for longs: 0.50 is the best setting and wider only trades fewer times
for less. Always-short improves monotonically with the buffer (-1.83,
-1.25, -0.88, -0.72, -0.66) and never clears zero, so the short side's
problem is direction on this tape, not the stop. Slippage still costs 0.03
to 0.05R per row at 0.50.

### What this means for the loop

- The live rule is now 0.50 on Brent. The open position's stop is not
  touched: a stop is never widened once placed.
- The mechanics still do not make money on their own. At 0.50 the long
  control is break-even to slightly positive in R and negative in dollars
  after fees; the edge remains the direction call. Fees are the next lever
  (maker entries only, fewer and larger brackets), not leverage and not the
  20x rule.
- The journaled run (6 trades, +1.86R, $64 to $132) is the only series
  that compounds, and it is still one week of one thesis writer.

Machine-readable output of the rerun: `/tmp/desk-backtest-050/report.json`
and `/tmp/desk-backtest-050/trades_<run>.csv`.

## Leverage grid: what setting ends with the most equity

Operator: "can we change the config and tick for the highest pnl and most
equity". Same window, 0.50 buffer, slippage 0.03%, leverage {2, 3, 5, 10, 20}
x min_r {1.5, 2.0, 3.0} x the three series that matter. min_r changed
nothing worth a row (3.0 drops trades from 47 to 38 at the same final
equity), so the table is at min_r 1.5. Script: the session scratchpad
`grid_leverage_minr.py`; output `/tmp/desk-backtest-050/grid_leverage_minr.json`.

| leverage | always-long final / max DD | daily-sign final / max DD | journaled final / max DD |
|---|---|---|---|
| 2x | $67 / 12% | $55 / 24% | $70 / 3% |
| 3x | $68 / 17% | $51 / 33% | $73 / 4% |
| 5x | $69 / 28% | $43 / 50% | $80 / 7% |
| 10x | $63 / 50% | $26 / 76% | $97 / 14% |
| 20x | $38 / 78% | $7 / 95% | $132 / 27% |

Start $64. Expectancy in R is identical down each column (R does not see
leverage); only the path does.

Reading: leverage is a bet on the direction calls being as good as last
week's six. If they are, 20x compounds fastest. If the mechanics have to
carry it, 20x loses 40% of the stack in a month where 5x finishes ahead and
10x finishes flat. 10x keeps three quarters of the journaled upside ($97 of
$132 over $64) and turns the mechanics-only worst case from -40% into
break-even; 5x caps the upside at +25% for a worst case of +8%.

## Hourly clock (2026-10-25, evening): "change the strategy to hourly, keep leverage at 20x"

The loop moved from one tick per 15m bar to one tick per 1h bar. `Params.tick_minutes`
(15 or 60) replays either clock on the same 30 days of xyz:BRENTOIL (2026-09-25 to
2026-10-25), stop buffer 0.50, 20x, slippage 0.03%, starting equity $64. Same six
journaled theses as the morning run. Report: `/tmp/desk-backtest-hourly/report.json`,
trades in `/tmp/desk-backtest-hourly/trades_<run>.csv`.

| run | 15m clock | 1h clock |
|---|---|---|
| journaled theses | 6 trades, 50% win, +1.86R, $64 -> $131.51 | 4 trades, 25% win, +0.55R, $64 -> $69.34 |
| always long (control) | 47 trades, +0.14R, $38.37 | 26 trades, -0.33R, $19.40 |
| always short (control) | 47 trades, -0.90R, $1.22 | 18 trades, -0.38R, $26.53 |
| daily sign | 57 trades, -0.28R, $7.33 | 33 trades, -0.65R, $7.53 |

What the hourly clock changes: about half as many fills (a pullback that touches the
8h low inside an hour and leaves again is seen once, at the hourly open, not four
times), and a stop and target that are judged on the 1h bar's high and low, so a wick
inside the hour that would have stopped the 15m loop still stops the hourly one,
while a fill and a stop in the same hour count as a stop (the conservative read).
On this sample the hourly clock keeps the direction edge (journaled stays positive)
but gives up most of its size: +0.55R on 4 trades against +1.86R on 6. The buffer
sweep at 20x is noisier on the hourly clock (0.10 and 0.75 beat 0.50 on 22 to 29
trades each); nothing in it is large enough to move the live 0.50 rule.

Verdict: the hourly loop is the operator's call for usage and attention, not a
performance choice. On 30 days it costs roughly half the journaled return. The
number to watch is the live journaled R per trade over the next ten trades; under
+0.5R on the hourly clock, go back to 15m (one cron change).

**Reverted the same night.** On seeing the table above the operator went back to the
15-minute clock ("go back to 15 minute clock"). Live rule: tick every 15 minutes,
structure and invalidation on closed 1h candles, 20x. `Params.tick_minutes` stays so
the two clocks keep being compared in the nightly eval.

## 2026-10-28: moving the stop

Operator: "we tested that its not more profitable right?" It had not been. The
simulator now has `Params.stop_mode` ("fixed", "breakeven", "trail"; see the module
docstring) and the eval runs five variants on every direction series, 15m clock, 20x,
the live entry rules and the new no-reserve sizing:

- **fixed**: the stop never moves (the live rule).
- **be_1r / be_2r**: once a finished bar has traded 1R (2R) in favour, the stop
  moves to entry plus the round-trip fee.
- **trail_1atr / trail_2atr**: after 1R, the stop follows the best 15m close minus
  1 (2) ATR14, only tightening.

A moved stop the next bar opens through fills at that open. "Cut" counts trades a
moved stop closed that then reached the original target before the original stop.

Window: 2026-09-28 to 2026-10-28 (30 days), rerun on 50 days (the most 15m history
the venue returns). Journaled theses: 7 (`pathia/.state/strategies/oil/theses`, read
only), so the journaled rows are the same on both windows.

| series | stop | trades | win | avg R | total R | $64 ends at | max DD | moved | cut |
|---|---|---|---|---|---|---|---|---|---|
| journaled | **fixed** | 13 | 31% | **+0.46** | **+6.03** | **$112.74** | 42.3% | 0 | 0 |
| journaled | be_1r | 15 | 20% | +0.08 | +1.19 | $60.01 | 36.4% | 6 | 1 |
| journaled | be_2r | 13 | 23% | +0.10 | +1.35 | $80.84 | 36.4% | 4 | 1 |
| journaled | trail_1atr | 15 | 47% | +0.13 | +1.97 | $68.10 | 36.9% | 6 | 2 |
| journaled | trail_2atr | 14 | 29% | -0.31 | -4.32 | $52.24 | 52.7% | 5 | 2 |
| always_long, 30d | **fixed** | 51 | 18% | **-0.05** | **-2.62** | **$19.13** | 81.7% | 0 | 0 |
| always_long, 30d | be_1r | 57 | 11% | -0.15 | -8.80 | $10.48 | 84.9% | 22 | 4 |
| always_long, 30d | trail_1atr | 67 | 30% | -0.16 | -10.42 | $10.69 | 89.5% | 26 | 9 |
| always_long, 50d | **fixed** | 77 | 18% | **-0.09** | **-7.20** | **$7.48** | 95.0% | 0 | 0 |
| always_long, 50d | be_2r | 85 | 14% | -0.16 | -13.41 | $4.91 | 96.3% | 24 | 2 |
| always_long, 50d | trail_1atr | 107 | 27% | -0.26 | -27.80 | $1.40 | 98.9% | 40 | 12 |

The always-short and daily-sign controls lose under every variant (all end under $4
on 30 days); no variant rescues them. Full rows: `report.json` -> `stop_variants`.

Why fixed wins: this strategy's edge is a few big winners (a 24h-range target is
often 5R to 9R away) paying for many small losers. A breakeven or trailing stop
turns some of those big winners into scratches ("cut" column: 1 to 2 of the 4
journaled winners, 9 to 12 on always-long) while the losers it saves were small
anyway. Trailing doubles the win rate and still loses money: more, smaller wins.

Verdict: keep the fixed stop. On the journaled theses it returns +6.03R against
+1.97R for the best moving variant, and fixed is also best on the always-long
control in both windows. The journaled sample is 13 trades, so this is a direction,
not a proof; the always-long control (51 and 77 trades) agrees. Re-run when the
journal has 30+ trades.

## 2026-10-28: other markets

Operator: "is it replicable with other markets?" `services/strategies/evals/cross_market_backtest.py`
runs the same simulator (15m clock, fixed stop, 1.5R gate, the 20x rule, no-reserve sizing)
on Brent, gold, BTC and the S&P 500 perp (`xyz:SP500`, the most liquid index perp on xyz).
The other markets have no journaled theses, so every market is compared on the oil eval's
mechanics-only controls (always long, always short, daily 20-day trend sign). Oil's
journaled run is shown as the reference row.

- **Leverage**, two modes: *matched* = min(venue max, round(20 x oil ATR% / coin ATR%)),
  with ATR% the mean 1h ATR14 as a share of price over the window; *flat20* = 20x capped
  at the venue max. Venue max on 2026-10-28: Brent 20, gold 25, BTC 40, S&P 500 50.
- **Stop buffer**: the sim's $0.50 is about one 1h ATR on Brent, so each market gets the
  same buffer in ATR terms. A gate test checks that the same chart at 36x the price trades
  identically, so markets are judged on shape, not price level.

30 days (2026-09-28 to 2026-10-28), from $64. R per trade does not depend on leverage
(full-equity sizing), so the matched and flat rows differ only in dollars:

| market | 1h ATR% | lev matched / flat | series | trades | win | total R | $ matched | $ flat | max DD matched |
|---|---|---|---|---|---|---|---|---|---|
| Brent | 0.56% | 20 / 20 | journaled (reference) | 13 | 31% | **+6.03** | **$116.89** | $116.89 | 42.3% |
| Brent | 0.56% | 20 / 20 | always long | 50 | 18% | -1.44 | $22.50 | $22.50 | 78.8% |
| Brent | 0.56% | 20 / 20 | daily sign | 60 | 18% | -23.62 | $3.73 | $3.73 | 96.2% |
| Brent | 0.56% | 20 / 20 | always short | 45 | 7% | -40.48 | $0.88 | $0.88 | 99.0% |
| Gold | 0.24% | 25 / 20 | always long | 59 | 7% | -59.99 | $2.39 | $4.78 | 96.4% |
| Gold | 0.24% | 25 / 20 | daily sign | 58 | 21% | -24.83 | $15.85 | $21.90 | 79.0% |
| Gold | 0.24% | 25 / 20 | always short | 54 | 24% | -13.86 | $26.97 | $33.58 | 72.6% |
| BTC | 0.47% | 24 / 20 | always long | 52 | 12% | -34.63 | $1.19 | $2.51 | 98.7% |
| BTC | 0.47% | 24 / 20 | daily sign | 33 | 15% | -3.65 | $18.74 | $25.34 | 84.5% |
| BTC | 0.47% | 24 / 20 | always short | 36 | 14% | -9.50 | $11.14 | $16.32 | 87.3% |
| S&P 500 | 0.15% | 50 / 20 | always long | 45 | 13% | -44.05 | $2.65 | $19.35 | 97.6% |
| S&P 500 | 0.15% | 50 / 20 | daily sign | 45 | 16% | -35.68 | $4.27 | $23.85 | 95.5% |
| S&P 500 | 0.15% | 50 / 20 | always short | 52 | 13% | -45.06 | $2.17 | $18.48 | 97.0% |

No run liquidated in either window or mode. The 50-day rerun is worse everywhere: every
control in every market ends under $20 from $64. Best: gold always short, -23.84R, $12.83
matched / $19.19 flat. Brent's journaled reference stays at the same 13 trades and +6.03R
(the theses only cover the last 10 days). Full rows: `report.json` -> `rows`.

Two findings:

1. **The mechanics alone lose money everywhere, Brent included.** Range-edge entries,
   a structural stop and a 24h target do not make money without a direction call: the best
   control on 30 days is Brent always long at -1.44R. The only profitable row is Brent with
   the journaled theses. The edge so far is the thesis (direction, invalidation, never
   chasing above its entry), not the market and not the leverage.
2. **Volatility-matched leverage changes only the size of the loss, not the sign.** BTC's
   1h ATR (0.47%) was below Brent's (0.56%) in this window, so matched BTC leverage is 24x.
   Gold and the S&P 500 are calmer, so matching pushes them to the venue cap (25x, 50x),
   which deepens their losses: S&P 500 always long ends at $2.65 matched against $19.35 flat.

Verdict: the method carries over only as far as there is a thesis to trade. Without one,
the backtest says no market works, so nothing here supports turning on a gold, BTC or
index desk on the mechanics. The test would be journaled theses for that market,
replayed the way the Brent ones are. Confidence: the controls (33 to 98 trades per run,
two windows) are a solid negative; Brent's positive rests on 13 trades.
