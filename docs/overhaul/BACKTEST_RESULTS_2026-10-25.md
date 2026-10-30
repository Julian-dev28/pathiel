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

## 2026-10-28: limit vs market target

Operator, before switching the live target from a Hyperliquid "Take Profit
Market" trigger to a resting limit: "actually test it first". Nothing live
changed; `place_bracket` and `bracket.py` are untouched.

**What the sim did until now.** It filled the target on a bare touch, at the
exact target price, maker fee, no slippage: an ideal resting limit. The live
order is a take-profit MARKET (taker fee plus exit slippage), so every
backtest number above priced the winners slightly better than the live
bracket actually exits them.

`Params.target_mode` now picks one of three (default unchanged):

| mode | fills when | price | fee |
|---|---|---|---|
| `touch` (default, the old sim) | a bar touches the target | the target | maker |
| `market` (the live order) | a bar touches the target | target minus exit slip | taker |
| `limit` (proposed) | a bar trades THROUGH the target by one tick (0.01) | the target | maker |

A limit target touched but never traded through is counted
(`Trade.target_missed`); if price then reverses to the stop, the trade takes
the stop. `Params.size_slippage` prices every market exit (stops, market
targets, invalidation and direction-flip closes) off `EXIT_SLIP_CURVE`, the
per-side slip walked on the live Brent book that day: 0.9 bps small, 3.8 bps
at $1M, 12.9 at $3M, 21.7 at $5M, 34.1 at $7M (max of that and the flat 3 bps
`slippage`). Limit targets pay none. Runs below: 20x, 15m clock, size
slippage on, compounded from each starting equity
(`desk_tick_backtest.target_mode_rows`, report key `target_modes`).

30 days (journaled theses cover the last ~10 days; the 50-day window gives
identical journaled rows):

| series | mode | start | trades | targets hit | limit missed | total R | final equity | closed only |
|---|---|---|---|---|---|---|---|---|
| journaled | market | $64 | 13 | 3 | - | +5.45 | $107.95 | $81.38 |
| journaled | limit | $64 | 13 | 3 | 0 | +5.84 | $110.71 | $83.46 |
| journaled | market | $1k | 13 | 3 | - | +5.45 | $1,686.71 | $1,271.51 |
| journaled | limit | $1k | 13 | 3 | 0 | +5.84 | $1,729.89 | $1,304.06 |
| journaled | market | $5k | 13 | 3 | - | +5.45 | $8,433.56 | $6,357.55 |
| journaled | limit | $5k | 13 | 3 | 0 | +5.84 | $8,649.44 | $6,520.29 |
| journaled | market | $20k | 13 | 3 | - | +5.41 | $33,627.24 | $25,349.55 |
| journaled | limit | $20k | 13 | 3 | 0 | +5.80 | $34,480.05 | $25,992.43 |
| journaled | market | $100k | 13 | 3 | - | +3.21 | $136,808.00 | $103,131.30 |
| journaled | limit | $100k | 13 | 3 | 0 | +3.90 | $142,823.20 | $107,665.80 |
| always long (30d) | market | $64 | 45 | 9 | - | +3.30 | $38.09 | $28.71 |
| always long (30d) | limit | $64 | 45 | 9 | 0 | +4.43 | $41.04 | $30.94 |
| always long (50d) | market | $64 | 77 | 14 | - | -8.92 | $6.65 | $5.01 |
| always long (50d) | limit | $64 | 77 | 14 | 0 | -7.20 | $7.48 | $5.64 |

"Final equity" marks the position still open at the window end (the journaled
run ends long, as the live account is); "closed only" counts closed trades.
`touch` rows equal `limit` rows in every run here, so they are left out.

Read:

- **The limit target never missed.** Across 26 target exits (3 journaled, 9
  and 14 always-long) every bar that reached the target also traded at least
  one tick through it, so the conservative limit rule filled each one.
- **The limit is worth about 0.13R per target exit on a small account**:
  the taker-maker fee gap (3 bps) plus the 3 bps exit slip, against a ~0.46%
  stop. On the journaled run that is +0.39R (+5.84R vs +5.45R) and $2.76 on
  $64. On the always-long control, with three times the target exits, it is
  +1.13R (30 days) and +1.72R (50 days).
- **It grows with size.** At $100k the journaled run's exits are millions
  of notional; the limit saves 0.69R and $6,015 (+4.4% of the ending balance),
  against 0.39R at $64.
- **The size-aware slip mostly bites the stops**, which stay market orders in
  every mode: the journaled run falls from +5.84R at $20k to +3.90R at $100k
  in limit mode too.
- **Not modelled:** queue position and depth at the target price. A
  trade-through proves the level cleared, not that a multi-million-dollar
  resting sell was absorbed in that bar; past about $1M of position, a limit
  exit could fill in pieces over a few bars. The 26 exits here are a small
  sample.

Verdict: switch the target to a resting limit. It never missed here, it is
strictly cheaper at every account size (+0.39R on the live-sized account,
+0.69R at $100k), and the stop, the exit that carries the risk, stays a
market order.

## 2026-10-29: closer targets and re-entry (full scenario sweep)

Operator: "test .3% lower targets and then reentry", widened to a full sweep
("DONT MAKE CHANGES JUST GIVE ME RESULTS"). Sim only; nothing live changed.
Runner: `services/strategies/evals/scenario_sweep.py`. New `Params`:
`target_offset_frac` (the take-profit that fraction of price inside the
structural target) and `reentry_after_target` (default True: the sim has
always re-entered under the same call once flat; False waits for the next
thesis, or the next UTC day for the controls).

Grid, 540 combos: offset 0 / 0.1 / 0.2 / 0.3 / 0.5%, re-entry on/off, stop
fixed / breakeven at +1R / trail 1 ATR after +1R, limit/market target,
10/15/20x, stop buffer 0.25 / 0.50 / 0.75. 15m clock, $64 start, size-aware
exit slippage, 8 journaled theses, always-long control over 30 and 49 days.
The journaled 30-day and 49-day runs are identical (the theses start
2026-10-18), so the long window is only informative on the control.

Live config (offset 0, re-entry, fixed, limit, 20x, $0.50 buffer) ranks 21
of 540 on journaled final equity: $123.91, +11.19R, 14 trades, 36% won,
43% max drawdown; control $34.11 (30d) / $6.04 (49d).

Top 5 by journaled final equity (30d):

| # | offset | stop | target | lev | buffer | journaled | control 30d | control 49d | 1h clock |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.5% | fixed | limit | 20x | 0.75 | $199.60, +10.3R, 7 trades, 71% won | $75.28 | $5.48 | $149.02 |
| 2 | 0.5% | fixed | market | 20x | 0.75 | $190.76, +9.9R, 7 | $68.98 | $4.95 | $143.93 |
| 3 | 0.5% | fixed | limit | 20x | 0.50 | $162.86, +13.3R, 12 | $65.03 | $10.25 | $110.79 |
| 4 | 0.5% | fixed | limit | 15x | 0.75 | $157.60, +10.3R, 7 | $85.37 | $13.32 | $126.90 |
| 5 | 0.5% | fixed | market | 20x | 0.50 | $155.66, +12.7R, 12 | $58.41 | $8.89 | $107.94 |

All top-10 rows re-enter after a target. Changing one thing at a time from
the live config (journaled / control 30d / control 49d):

| change | journaled | control 30d | control 49d |
|---|---|---|---|
| none (live) | $123.91 | $34.11 | $6.04 |
| offset 0.3% (the test asked for) | $126.24 | $28.64 | $4.48 |
| offset 0.5% | $162.86 | $65.03 | $10.25 |
| buffer 0.75 | $146.09 | $48.79 | $5.84 |
| offset 0.5% + buffer 0.75 | $199.60 | $75.28 | $5.48 |
| re-entry off | $90.30 | $34.51 | n/a |

Consistent across the journaled run, both control windows and the grid
means: fixed stop beats breakeven and trail; a wider buffer (0.75) beats
0.50 beats 0.25; a 0.5% closer target beats none; limit edges market.
Not consistent: leverage (20x wins on the journaled run, 10x on the losing
control, as leverage only scales the sign of the edge); re-entry (on wins by
$34 on the journaled run, a wash on the control).

Verdict: the 0.3% offset alone does not help (worse on both controls).
Re-entry should stay on. The best combo, offset 0.5% + buffer 0.75, beats
the live config on three of four checks, but its journaled win rests on 7
trades against 14 and less total R (+10.3R vs +11.2R): fewer stop-outs,
not bigger wins. That is not beyond noise. Re-run after 30+ live trades
before changing the live rules; offset 0.5% alone ($162.86, +13.3R, and
better on every control) is the safer single candidate.

### Adopted live (2026-10-30)

Operator picked combo #1 (0.5% closer target + 0.75 stop buffer, 20x,
limit target, fixed stop, re-entry on: $64 -> $199.60, 7 trades, 71% wins,
30% max drawdown). Where it lives:

| Rule | Code |
|---|---|
| Target 0.5% inside the structure | `pathiel/agents/bracket.py` `TARGET_OFFSET_FRAC`, applied by `place_bracket` on new trades only (a top-up keeps the position's target) |
| Stop 0.75 beyond the structure | `.claude/agents/desk-tick.md` setup rule |
| Backtest twin | `desk_tick_sim.live_params()`; `tests/test_bracket.py` checks the two stay in sync |
| Move an open trade's target | MCP `set_target {coin, tp_px}`: new target first, then the old one is cancelled |

## 2026-10-30: strategy sweep, ranked by profit per day and per week

Operator: "expand on various High ROI strategies, anything im failing to
mention here should be tested ... think the most profit PER DAY, THEN PER
WEEK". Runner: `services/strategies/evals/strategy_sweep.py` (gate tests in
`services/strategies/tests/test_strategy_sweep.py`); new simulator options
in `desk_tick_sim.Params` (gate tests in `test_strategy_variants.py`), every
default unchanged (128 golden runs identical before and after; the sim is
also 10x faster, a pointer instead of a rescan of the 1h bars each tick).

New options: fixed-R targets (`target_r`), a scale-out at N R with optional
breakeven (`partial_*`), a time stop, market entries (`entry_mode`, never
above the thesis entry, can be stopped out on its own bar, no re-entry on
a bar that just closed a trade), entry and target lookbacks, a UTC
trading-hours window.

Data: 9 journaled long theses over 11.83 days (2026-10-18 to 10-30), the
always-long control over 30 and 50 days, $64 start, 15m clock,
size-aware slippage. Per day is the compounding daily rate, per week is
`(1 + day)^7 - 1`.

Stage 1 (one change from live; 43 variants). Better on the theses and both
controls: 2R gate. Worse on all three: partial exits, fixed-R targets,
breakeven and trailing stops, 6h/12h time stops, resting further from the
edge, re-entry off. Mixed: market entry, wider buffers, the 24h entry
range, trading-hour windows, lower leverage.

Stage 2: 1,944 combinations of the mixed and better changes; 1,660 beat
live on both controls; live ranks 121 among them.

| setup | per day | per week | $/day from $64 | $64 ends | trades / won | max dd | control 30d /day | control 50d /day |
|---|---|---|---|---|---|---|---|---|
| live | 11.2% | 110% | $13.58 | $224.63 | 8 / 62% | 30% | +1.01% | -4.88% (98% dd) |
| market entry + 24h range + 2R gate | 18.7% | 233% | $35.82 | $487.66 | 8 / 75% | 29% | +9.48% | +4.67% (87% dd) |
| same at 15x | 14.7% | 160% | $21.85 | $322.49 | 8 / 75% | 22% | +7.51% | +3.81% (81% dd) |
| same at 10x | 10.2% | 97% | $11.66 | $201.93 | 8 / 75% | 16% | +5.60% | +2.69% (70% dd) |

What each part adds (journaled / 50-day control, per day): market entry
alone 12.4% / -4.22%; 24h range alone 7.0% / -0.35%; both 15.0% / +3.42%;
all three 18.7% / +4.67%. Market entry plus the 24h range is the first
rule set that makes the no-thesis control positive over 50 days, and its
first half (Sep 11 to Oct 5, Brent flat to down) goes from -12.2%/day for
live to -0.4%/day. Stress on the winner: double slippage 18.1%/day; the 1h
clock 10.6%/day (it needs the 15m clock).

Caveats: 8 trades on the theses; every thesis was long while Brent rose
about 12%; the 50-day control's worst day for the winner is -57.7% and its
worst week -61.2% at 20x. Per-day rates this high do not hold: they are a
ranking, not a forecast.

Live mapping if adopted (not done): the desk-tick agent takes the trade at
the mid when price sits in the lower part of the 24h range (stop 0.75 under
the 24h low, target 0.5% inside the 24h high, at least 2R, never above the
thesis entry) instead of resting a limit at the 8h low; `place_bracket`'s
gate `DEFAULT_MIN_R` 1.5 -> 2.0.
