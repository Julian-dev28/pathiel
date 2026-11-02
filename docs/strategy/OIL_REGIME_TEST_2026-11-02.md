# Oil regime test, 2026-11-02

**Verdict: keep live. No regime filter, side override, range-scaled stop
or target clears the bar.** The live rule is not a trend rule: it fades
the 24h range (buys near its own edge, targets the far edge). Lately it
made its money in chop, and it lost in the few straight-line down days.
Over the longest history the venue serves (208 days), the mechanics with
no thesis lose money in every regime mix; the result rests on the thesis
side, not on the regime.

Operator, 2026-11-02: "i'll just wait for you to fix the issues with oil".

| Question | Answer |
|---|---|
| Does the desk only make money in trends? | No. Last 52 days plus the theses: chop +41.0R (101 trades, 47% win), trend up +34.4R (65), trend down -17.0R (16, 1 win) |
| What happened in chop? | The best regime lately: always long +16.7R, 30 replayed theses +7.0R of their +7.0R, journal +5.0R. Over 208 days chop was flat to negative (-5.2R on 111 control trades) |
| And in the breakdown (11-02)? | The day opened as chop. The hourly label turned trend_down at the 02:00Z close; the desk bought at 03:50Z into it. A daily label cannot see that; an hourly one can, but filtering on it costs more than it saves |
| Closest fix | `rstop_max_0.15` (stop buffer max(0.75, 0.15 x 24h range)): beats live on 6 of 8, fails the plateau. It never binds at today's 2.3% range, so it would change nothing now |
| Regime to sit out | None. Sitting out chop cuts the thesis sets in half (replay $414 to $212, journal $894 to $518) |
| Live change | None |

## 1. History

The venue's `candleSnapshot` serves the last 5,000 bars of each interval
(an earlier window returns nothing before them).

| Coin | 1h | 15m | 1d |
|---|---|---|---|
| xyz:BRENTOIL | 5,000 bars, 04-08 to 11-02 12:45Z (208 days) | 5,000, from 09-11 (52 days) | from 03-04 (its listing) |
| xyz:CL (WTI, proxy) | 4,999, from 04-08 | 5,000, from 09-11 | from 01-06 |

Brent funding: 5,001 hourly rows (paged, 500 a call). WTI is a proxy only:
a different contract, run on the same windows, scored separately.

## 2. The regime label

Causal, per UTC day, from 1h bars closed by 00:00Z
(`services/strategies/backtest/regime.py`). In order:

1. 48h efficiency (net move over the sum of hourly moves) >= 0.30: trend in
   the 48h direction (a straight-line run is a trend now; this is how a
   break shows up).
2. |20-day return| >= 4% and 24h range >= 4.5 x ATR14 (1h): trend in the
   20-day direction.
3. Otherwise chop (no 20-day trend, or one whose last day went nowhere).

Thresholds were picked once from where the features fall on Brent's own
history, not from P&L: 4% is about the 20th percentile of |20-day return|
(median 9%: 2026 Brent moved a lot), 0.30 about the 85th of the 48h
efficiency, 4.5 about the 40th of range over ATR (a random walk sits near
5).

Brent, 188 labelled days: **86 chop, 58 trend up, 44 trend down**. 10-16 to
10-21 trend up, the week of 10-26 chop on 6 of 7 days, 11-02 opened chop.

Each threshold shifted -25% and +25%:

| Shifted | Same label as base, -25% / +25% |
|---|---|
| 20-day return 4% | 97% / 96% of days |
| 48h efficiency 0.30 | 92% / 95% |
| Range over ATR 4.5 | 77% / 78% |

In the regime table below, 23 of the 24 Brent cells keep their sign (sum R
over +-0.5) in at least 4 of the 6 shifts; the one that does not is always
long in trend up over 208 days, +2.0R, near zero. The answer does not hinge
on the thresholds.

## 3. Live rules by regime

Live rule: market entry off the 24h range, 2R gate, stop 0.75 beyond the
edge, target 0.5% inside the far edge, limit target, 20x, 15% risk cap,
size slippage and funding. $225 start. Trades tagged by their entry day's
label. R per trade net of fees.

| Dataset | Final $ | Trades | Win | Trend up: n, R | Trend down: n, R | Chop: n, R |
|---|---|---|---|---|---|---|
| Always long, 208d (1h ticks) | 73 | 90 | 29% | 32, +2.0 | 26, +18.7 | 32, -5.8 |
| Always short, 208d (1h) | 2 | 114 | 23% | 44, -12.6 | 23, +0.8 | 47, +3.1 |
| Daily sign, 208d (1h) | 10 | 92 | 28% | 36, -4.9 | 24, +3.8 | 32, -2.5 |
| Always long, 52d | 1,777 | 41 | 42% | 18, +11.8 | 0 | 23, +16.7 |
| Always short, 52d | 46 | 43 | 23% | 13, +3.5 | 9, -10.3 | 21, +6.7 |
| Daily sign, 52d | 196 | 48 | 38% | 18, +10.5 | 7, -6.7 | 23, +5.5 |
| 30 replayed theses | 414 | 38 | 45% | 11, -0.0 | 0 | 27, +7.0 |
| Live journal, 18 theses | 894 | 12 | 58% | 5, +8.6 | 0 | 7, +5.0 |

Pooled:

| | Trend up | Trend down | Chop |
|---|---|---|---|
| 208d controls: trades, R, R/trade | 112, -15.5, -0.14 | 73, +23.3, +0.32 | 111, -5.2, -0.05 |
| 52d + theses: trades, R, R/trade | 65, +34.4, +0.53 | 16, -17.0, -1.06 | 101, +41.0, +0.41 |

Read plainly:

- **The regime that pays changes with the market.** Over 208 days the best
  regime was trend down (longs bought the 24h low in a falling, swinging
  war market and caught the bounces: +18.7R). Over the last 52 days it was
  chop, and trend down was the worst (16 trades, 1 win).
- **No thesis, no edge.** Over 208 days every control loses with the live
  mechanics: always long -67%, always short -99%, daily sign -95%. Same as
  the 10-28 cross-market finding: the edge is the thesis.
- **Chop is not the problem.** It is where the recent money came from, in
  the controls and in both thesis sets.
- **The thesis sets never traded a trend-down day.** All their trades fell
  on chop or trend-up days, so they cannot tell us how the theses do in a
  down regime. Today is the first.

WTI, same windows (proxy): over 208 days trend down again carried the
controls (+20.7R long, +16.0R short, +14.4R daily sign), trend up lost.

### 11-02, hour by hour

| Close (Z) | 20d return | 48h efficiency | Range / ATR | Label |
|---|---|---|---|---|
| 00:00 | +8.1% | 0.26 | 3.4 | chop |
| 01:00 | +8.2% | 0.29 | 3.2 | chop |
| 02:00 | +8.5% | 0.32 | 4.5 | trend down |
| 03:00 | +7.6% | 0.44 | 5.7 | trend down |
| 12:00 | +6.3% | 0.45 | 5.5 | trend down |

The live long filled at 03:50Z, nearly two hours after the hourly label
turned.
The 20-day trend was still up 7%, so a rule built on the 20-day return
(veto, override) sees nothing wrong with a long.

## 4. Fixes vs live

Bar (the other tests' rule): beats live on most of the 8 Brent datasets
with a geo-mean of 1 or more; a plateau (all 6 threshold shifts, or each
grid neighbour, also pass); mean max drawdown no worse; stops inside half
the way to liquidation. **Plus one guard this history needs:** live loses
money on 5 of the 8 datasets, so not trading at all already "beats live on
5 of 8". A fix has to beat live on more datasets than sitting out does.

| Rule | Beats / loses of 8 | Geo-mean | Mean DD (live 82.6%) | Theses W/L | Neighbours that pass | WTI W/L of 6 | 11-02 long (live -$34.98) |
|---|---|---|---|---|---|---|---|
| no_chop, day | 4 / 4 | 1.02 | 74.4% | 0 / 2 | 1 of 6 | 5 / 1 | skipped (chop) |
| no_chop, hour | 4 / 4 | 0.88 | 78.1% | 0 / 2 | 0 of 6 | 1 / 5 | -34.98 |
| with_trend, day | 3 / 1 | 1.46 | 81.6% | 0 / 0 | 1 of 6 | 2 / 4 | -34.98 |
| with_trend, hour | 3 / 5 | 1.69 | 77.9% | 1 / 1 | 0 of 6 | 4 / 2 | skipped (against) |
| trend_only, day | 4 / 4 | 1.57 | 69.7% | 0 / 2 | 1 of 6 | 5 / 1 | skipped (chop) |
| trend_only, hour | 3 / 5 | 1.55 | 59.8% | 0 / 2 | 0 of 6 | 1 / 5 | skipped (against) |
| veto_20d, day | 2 / 1 | 1.28 | 80.8% | 0 / 0 | 0 of 6 | 1 / 3 | -34.98 |
| veto_20d, hour | 4 / 2 | 1.41 | 79.9% | 1 / 0 | 1 of 6 | 3 / 2 | -34.98 |
| override, day | 2 / 4 | 0.77 | 87.9% | 0 / 2 | 0 of 6 | 1 / 3 | -34.98 |
| override, hour | 5 / 2 | 1.35 | 80.5% | 1 / 0 | 4 of 6 | 3 / 3 | -34.98 |
| stop max(0.75, 0.10 x range) | 2 / 1 | 1.12 | 82.4% | 0 / 0 | 1 of 1 | 3 / 0 | -34.98 |
| **stop max(0.75, 0.15 x range)** | **6 / 1** | 1.23 | 81.6% | 1 / 0 | **1 of 2** | 5 / 0 | -34.98 |
| stop max(0.75, 0.20 x range) | 5 / 2 | 1.49 | 80.4% | 1 / 0 | 2 of 2 | 4 / 2 | -34.98 |
| stop max(0.75, 0.40 x range) | 5 / 3 | 2.97 | 33.4% | 0 / 2 | 2 of 2 | 5 / 1 | -34.80 |
| stop 0.35 x range alone | 5 / 3 | 1.66 | 69.9% | 0 / 2 | 0 of 1 | 3 / 3 | -34.93 |
| target 0.20 x range inside | 3 / 5 | 1.05 | 82.8% | 0 / 2 | 0 of 2 | 2 / 4 | -34.98 |
| target 0.30 x range inside | 3 / 5 | 1.06 | 81.4% | 0 / 2 | 0 of 1 | 6 / 0 | -34.98 |
| no_break_entry | 4 / 4 | 0.77 | 80.0% | 0 / 2 | none | 2 / 3 | 04:00 buy, flip, -18.83 |
| weekend lock, break-even + fees at 1R | 5 / 1 | 1.17 | 82.0% | 0 / 0 | none | 0 / 6 | -34.98 |
| weekend lock 0.5R at 1R | 5 / 1 | 1.11 | 82.4% | 0 / 0 | none | 0 / 6 | -34.98 |

Rules: no_chop = no new entry on a chop label; with_trend = none against
a trend label; trend_only = both; veto_20d = none against the 20-day trend
unless the 48h run is a straight line the entry's way; override = take the
20-day side instead of a call that fights it (same escape). "day" reads
the label at 00:00Z, "hour" at the last 1h close. The full grid (every
threshold shift, stop k 0.10 to 0.60, stop-alone k 0.20 to 0.35, target
0.10 to 0.30) is in `/tmp/oil-regime/variants.csv`.

What each one does:

- **Regime filters.** Each wins only on the 208-day controls, where live
  loses and trading less loses less. On the theses they cut winners: no
  rule beats live on both thesis sets, and no_chop / trend_only lose on
  both. Only override by the hour holds on 4 of the 6 threshold shifts;
  every other rule on at most 1.
- **with_trend by the hour** is the one that skips the 11-02 long. It also
  skips $258 of the journal's $669 gain and halves always long over 52 days
  ($1,777 to $936). 3 of 8.
- **override by the hour** comes closest of the regime rules (5 of 8, 4 of
  6 shifts), but 5 of 8 is what sitting out scores, WTI splits 3 / 3, and it
  never touches the 11-02 long (that long was with the 20-day trend).
- **stop max(0.75, 0.15 x range)** clears every rule but the plateau: its
  lower neighbour (0.10) ties on 5 datasets and wins only 2, its upper
  neighbour (0.20) wins 5, no more than sitting out. It only acts when the
  24h range is over 5.0 (4.5% of the price); Brent's range is 2.3% now, so
  it would change no trade today. Month by month it is mixed (always long,
  208 days: better in May and June, worse in August and September). Its
  thesis win is $0.35.
- **Wider multiples (0.35 to 0.60)** look best on paper (geo up to 3,
  drawdown down to 0%) because they stop trading: 0.40 takes 1 to 3
  trades in 208 days. Sitting out, not a fix.
- **Range-scaled targets and stop-alone** lose on most sets.

### no_break_entry and the weekend lock, by regime

Pooled over the 8 Brent sets, change vs live:

| Rule | Trend up | Trend down | Chop |
|---|---|---|---|
| no_break_entry | -13 trades, -31.9R | -1, +1.4R | -23, -4.3R |
| Weekend lock, break-even + fees at 1R | +9, +1.9R | +4, +5.7R | +13, -0.1R |
| Weekend lock 0.5R at 1R | +11, +1.6R | +4, +2.8R | +16, -0.8R |

- **no_break_entry** loses in trend up and chop, the two regimes the desk
  trades most; it helps only in trend down (+1.4R). On the
  theses it loses both (replay $414 to $241, journal $894 to $590). On
  11-02 it halves the loss (buys 04:00 inside the new range, out on the
  08:45 flip, -$18.83), the same as the loss audit found.
- **The weekend lock** gains a little in trend down, nothing in chop, and
  never touches a thesis trade (no weekend thesis trade reached +1R). It
  loses on all 6 WTI sets. Same answer as the buffer test: no.

## 5. Sample size

| What | Size |
|---|---|
| Labelled Brent days | 188: 86 chop, 58 up, 44 down |
| 208-day runs | 3 controls, hourly ticks, 90 to 114 trades each |
| 52-day runs | 3 controls, 15-minute ticks, 41 to 48 trades each |
| Thesis sets | 30 replayed (09-30 to 10-30), 38 sim trades; 18 journal theses (10-18 to now), 12 sim trades; none on a trend-down day |
| 52-day window regimes | 30 chop, 19 up, 3 down days |
| Real oil fills | 8 since 10-20 |
| WTI proxy | 6 sets, same windows |

Every thesis-set number is one month of one Brent rally. Trend down in the
recent window is 3 days and 16 trades.

## 6. Caveats

- **Hourly ticks on the 208-day runs.** There are no 15m candles before
  09-11. On the 52-day overlap, hourly ticks made $595 vs $1,777 (always
  long), $63 vs $196 (daily sign), $62 vs $46 (always short). Read the
  208-day levels loosely; the comparisons between rules use the same ticks.
- **The liquidation line at the fill.** The sim gates every stop at 0.5 of
  the way to liquidation at the mid; market slippage moves the fill, so live
  itself reaches 0.509 at the fill. The rule allows 0.515 (every variant
  sits at 0.50 to 0.512).
- **The sim trades more than the desk** (every flat 15-minute tick), as in
  the split test.
- **The labeller is causal** (gate test: the label for day D does not move
  when any bar after D's open changes), and the sim reads it at the day's
  open or the last closed hour.

## 7. What would change the call

- **A thesis set with down days.** The journal from 11-02 on is the first
  one. Rerun this weekly; if the theses lose in trend-down labels while
  with_trend by the hour keeps skipping them, test that filter on the
  journal alone.
- **A pre-registered one-sided rule.** with_trend by the day gained +13.4R
  pooled by skipping shorts in trend-up labels (63 fewer trades) and lost
  -14.7R by skipping longs in trend-down labels (30 fewer). "No shorts in a
  20-day uptrend" keeps the first half. It is read off this data, in a year
  of large swings (median |20-day move| 9%), so it needs a fresh window
  before it counts. It matters now: the 08:40Z short thesis was written in
  a 20-day uptrend (+7.9% at the 08:00 close).
- **Wide ranges again.** If Brent's 24h range goes back over 4.5%, the
  range-scaled stop (0.15) starts to act; rerun before it matters.

## 8. How it was run

```
python3 -m services.strategies.evals.oil_regime_test \
    --oil-theses-dir ~/Documents/code/pathia/.state/strategies/oil/theses
```

About 2.5 minutes. Writes `/tmp/oil-regime/report.json` (every dataset,
variant, regime split, score, verdict, the 11-02 rows, monthly returns),
`regime_days.csv` (features and labels per day, base and every shift),
`live_trades.csv` (each live trade with its regime), `variants.csv` (one row
per dataset and variant), `progress.log`. Candles and funding cached in
`/tmp/oil-regime/cache` (public `/info`, read only).

Code:

- `services/strategies/backtest/regime.py`: the labeller (`features_at`,
  `label_series`, `regime_blocks`, `against_20d`). Gate tests
  `services/strategies/tests/test_regime.py` (each label on a built shape,
  warm-up, causality, the fast series equal to the rescan float for float,
  the shifts, the filter rules).
- `desk_tick_sim.Params`: `regime_filter`, `regime_cfg`, `regime_refresh`,
  `range_buffer_k`, `range_buffer_floor`, `target_offset_range_frac`, all
  off by default. With them off, `brent_stop_buffer_test` and
  `desk_split_test` outputs are byte-identical before and after (cmp on
  every CSV, and the reports less their timestamps). Gate tests
  `test_oil_regime_sim.py`.
- `services/strategies/evals/oil_regime_test.py`: the runs, the scoring
  and the bar. Gate tests `test_oil_regime_eval.py`, including the 11-02
  long on its real bars: the hourly with-trend filter refuses it, the day
  label does not.

No live config, order, thesis or agent file was touched. No LLM calls.
