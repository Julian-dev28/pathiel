# Brent stop buffer, weekend-off and weekend lock test, 2026-11-02

**Correction (2026-11-03).** This file ran at 17:17 on 2026-11-02, about 40
minutes before `Params.risk_cap_frac` (the live 15% risk cap) existed at
all (`loss_fix_test.py`, 17:59, `LOSS_AUDIT_2026-11-02.md`). Every number
below is accurately full-port size FOR ITS OWN TIME -- it was not a bug
when written. It is stale now: `brent_stop_buffer_test.py`'s `bp`/`sp` have
been fixed to build through `live_params.live_brent_params`/
`live_silver_params` (the shared "live" baseline every eval now uses), so
a fresh run matches today's real live rule.

**Re-ran it (2026-11-03) to check: does the cap change any of the three
verdicts below? No.** Full details:

- **A. Wider Brent stop -- verdict unchanged (keep 0.75).** Isolating the
  cap's effect alone (same fresh candles, capped vs. uncapped, controls
  only): the mechanical `rule_pass` list -- candidates that clear ALL
  three of the operator's bars (beats live on most datasets, a plateau,
  stop inside half the way to liquidation) -- narrows from `['pct_0.95']`
  (uncapped) to `[]` (capped). The cap shrinks a wider stop's downside
  less than you'd think relative to its upside here, so it makes the bar
  *harder* to clear, not easier -- the opposite of a risk to the "keep
  0.75" verdict. (Mechanism: `risk_capped_notional` shrinks size once a
  stop's own risk fraction clears 15% of equity, so a wide buffer gets
  capped more often than 0.75 -- but the "Why not 0.90" judgment calls
  below, about round-tripping the actual 11-02 dip and a jagged curve,
  were never about the mechanical gates anyway, so they stand either
  way.) A full re-run with the live oil + metals journals included (`A
  best`: `pct_0.7`, 6 beats / 2 losses of 8, fails "plateau") tells the
  same story: nothing clears `rule_pass`.
- **B. No entries on weekends -- verdict unchanged (do not adopt).** The
  mechanical per-desk `adopt: true` flag this file's code computes for
  Brent's `wk_flat` (weekend-flatten) candidate was ALSO `true` before the
  cap (isolated check, controls only: uncapped geo-mean 1.348 vs. capped
  1.124, both `adopt: true`) -- this is a pre-existing property of
  scoring Brent's weekend candidates on CONTROLS ALONE with no plateau
  check (`families=None`), not something the cap introduced. The doc's own
  "Do not adopt" verdict already overrode that flag with judgment (no
  thesis trade changed, and silver loses outright); that judgment doesn't
  depend on the cap.
- **C. Weekend-only profit lock -- verdict unchanged (do not adopt yet).**
  Every lock/trail candidate here shares the SAME entry stop distance as
  live (only the EXIT moves, via the lock), and `risk_capped_notional`
  sizes once, at entry, off that shared distance -- so live and every
  candidate size identically whether the cap is on or off (same proof as
  `PROFIT_LOCK_TEST_2026-11-01.md`'s correction note). A full re-run
  confirms it: still fails "plateau" on Brent (1R locks beat live, 1.5R/2R
  do not) and "beats live on most datasets" on silver.

Numbers below are the ORIGINAL, uncapped run; not re-published, since none
of the three verdicts moves. Re-run with
`python -m services.strategies.evals.brent_stop_buffer_test
--oil-theses-dir <pathia>/.state/strategies/oil/theses --metals-theses-dir
<pathia>/.state/strategies/metals/theses` (now capped) for current
numbers, or see `CLOSER_TARGET_TEST_2026-11-03.md` for a worked example of
a re-run with the fix.

**Verdict: keep the 0.75 Brent stop, keep trading weekends, no weekend lock
yet.** None of the three clears the operator's rules on what the desk
actually trades. The closest is break-even plus fees after +1R on weekends
only: it never lost on any of 14 datasets, but it never touched a thesis
trade either.

Operator, 2026-11-02:

1. "sure", to "backtest whether a wider Brent stop would have survived this
   dip without losing more over the full history". The long of 42.36 Brent
   from 110.0099 (stop 109.25, target 111.83, liq 107.20) was stopped at
   109.23 at 08:35:15Z for -$33.45. Price then sat around 109.1 to 109.2 and
   the thesis flipped short at 110.20.
2. "maybe we should restrict the trading to market hours sunday night to
   friday eve".
3. "maybe dont run weekends off but maybe stricter rules like dont round
   trip, but during the week let it ride".

## Verdicts

| Question | Verdict | Why, in one line |
|---|---|---|
| A. Wider Brent stop | Keep 0.75 | 0.90 wins this October by refusing entries, never by surviving a stop; today it loses more or skips |
| B. No entries on weekends | Do not adopt | Brent: no thesis trade changes, the controls only say October weekends dipped. Silver: loses on 5 of 7 |
| C. Profit lock on weekends only | Do not adopt yet | Brent 1R locks beat live on 6 of 7 (1 tie) but 1.5R and 2R only tie, so not a plateau; silver 2 of 7 |

All runs: $64 start, live rules otherwise (market entry off the 24h range,
2R gate, target 0.5% inside the 24h edge, limit target, 20x, 15-minute
ticks, Brent with funding and size slippage). Silver runs the live silver
rule, max(1.3 x ATR14_1h, 0.8% of the mid), 20x on a 25x coin.

Datasets: the 30 replayed Brent theses (09-30 to 10-30), always long,
always short and daily sign on that window and on the last 30 days (to
2026-11-02 08:45Z). Silver: the 30 replayed silver theses and the same
controls. **The live Brent and metals journals are not in these numbers**
(see Caveats); one command adds them.

## A. Wider Brent stop

### Today's long, with each buffer

The sim's own setup at the 03:45Z tick (mid 110.00, 24h range 110.14 to
112.39), walked on real 15-minute candles. Dollars at the live 42.36 size,
price only (live added $1.26 of fee and slippage).

| Buffer | Stop | R at entry | What happens | $ |
|---|---|---|---|---|
| 0.75 (live) | 109.25 | 2.44 | stopped, 08:30Z bar | -32.19 |
| 0.80 | 109.20 | 2.29 | stopped, same bar | -34.31 |
| 0.85 | 109.15 | 2.15 | stopped, same bar | -36.43 |
| 0.90 | 109.10 | 2.03 | stopped, same bar | -38.54 |
| 0.8% of mid | 109.12 | 2.08 | stopped, same bar | -37.70 |
| 0.95, 0.9% and wider | 109.05 or lower | 1.92 or less | refused: under 2R | 0 |
| 1.50 | 108.50 | 1.22 | refused: under 2R and past the 20x line | 0 |
| 1.3 x ATR (0.51) | 109.49 | 3.56 | stopped 05:30Z | -22.17 |
| 2.0 x ATR | 109.21 | 2.31 | stopped 08:30Z | -33.88 |

The low after entry was 108.92, 1.08 under the mid. A stop under it needs a
buffer of 1.09, which pays 1.68R and fails the 2R gate. Even a stop that
survived would have closed at the 08:45Z open (109.33, -$28.80) when the
thesis flipped short. **No wider stop survives this dip. 0.80 to 0.90 lose
more; 0.95 and up never take the trade.**

### Over the history

$64 became. Live vs fixed 0.90 (passes the three rules on paper) and 0.9%
of mid (best score, 7 of 7, but one fill at 0.51 of the way to liquidation).

| Dataset | Live 0.75 | Fixed 0.90 | 0.9% of mid |
|---|---|---|---|
| Brent, 30 replayed theses | $121.78 | $159.68 | $158.77 |
| Always long, replay window | $1,057.89 | $1,104.40 | $1,082.19 |
| Always long, last 30 days | $515.90 | $492.13 | $704.26 |
| Always short, replay window | $3.63 | $8.88 | $8.89 |
| Always short, last 30 days | $9.48 | $18.56 | $20.22 |
| Daily sign, replay window | $337.19 | $590.43 | $529.03 |
| Daily sign, last 30 days | $556.44 | $567.06 | $808.81 |

30 replayed theses in detail:

| | Live 0.75 | Fixed 0.90 | 0.9% of mid |
|---|---|---|---|
| Max drawdown | 66% | 51% | 51% |
| Trades | 38 | 21 | 21 |
| Win rate | 45% | 48% | 48% |
| Avg R | +0.18 | +0.33 | +0.32 |
| Worst single loss | -$18.92 (-22.0% of equity) | -$18.32 (-22.6%) | -$18.32 (-23.4%) |
| Closest stop to liquidation, at the fill | 0.47 | 0.47 | 0.47 |

The curve over all 7 datasets (beats live on N of 7, geo-mean of final / live):

| Fixed | 0.50 | **0.75** | 0.80 | 0.85 | 0.90 | 0.95 | 1.00 | 1.10 | 1.25 | 1.50 |
|---|---|---|---|---|---|---|---|---|---|---|
| Beats | 2 | live | 1 | 5 | 6 | 6 | 4 | 2 | 3 | 2 |
| Ratio | 0.51 | 1.00 | 0.79 | 1.18 | 1.41 | 1.55 | 1.16 | 0.77 | 1.13 | 0.52 |

| % of mid | 0.6 | 0.7 | 0.8 | 0.85 | 0.9 | 0.95 | 1.0 | 1.1 | 1.2 |
|---|---|---|---|---|---|---|---|---|---|
| Beats | 0 | 5 | 2 | 7 | 7 | 6 | 4 | 2 | 2 |
| Ratio | 1.00 | 1.03 | 0.86 | 1.40 | 1.55 | 1.50 | 1.24 | 0.58 | 0.47 |

ATR stops lose on Brent: 1.0 / 1.3 / 1.6 / 2.0 x ATR14 beat live on 2 of 7,
ratios 0.36 / 0.51 / 0.45 / 0.50. Brent's 1h ATR14 was 0.39 at today's
entry, so 1.3 x ATR is a tighter stop than 0.75. 1.50 took no trade at all
(every entry failed 2R or the 20x line). The percent floor is max(0.75, p% of the mid): at Brent
94 to 114, 0.6% never binds.

### Why not 0.90 even though it passes on paper

Fixed 0.90 and 0.95% of mid clear the three rules (most datasets, neighbours win
too, stop inside half the way to liquidation). They are not adopted because:

- **It never survived a stop on the theses.** On the 30 replayed theses the
  0.90 stop turned 0 live stop-outs into wins. It refused 19 of live's 38
  entries (2R gate). Under the live stop those 19 were 10 losses and 9 wins,
  net +61% of equity. Re-judged on live's own entries, 0.90 does worse
  (summed return on equity -53 points). Its final equity edge comes from the
  different entries it takes later, after refusing one: path, not rule.
- **The curve is jagged.** 0.80 loses on 6 of 7 and 1.10 on 5 of 7, with
  0.85 to 0.95 winning in between. The neighbours pass, the next point out
  flips. Compounding at 20x swings a row 30% on one trade.
- **One month.** The replay window (09-30 to 10-30) and the last 30 days
  (10-03 to 11-02) share 27 days. Seven datasets, one Brent rally.
- **Today it costs.** -$38.54 at 0.90 vs -$32.19 at 0.75.

The 20x line does not separate the variants: at the fill every buffer,
live included, tops out at 0.47 to 0.51 of the way to liquidation. The gate
holds 0.50 at the mid; market-entry slippage adds up to 0.01. On today's
long the live stop used 0.27.

If the live-journal run (below) also puts 0.90 ahead, the live change is
one constant: `services/strategies/next_trade.py:34`
`BRENT_STOP_BUFFER = 0.90`, plus `desk_tick_sim.live_params()`
`stop_buffer=0.90` (a test keeps them equal) and the "0.75" in
`.claude/agents/desk-tick.md`. Not made here.

## B. No new entries on weekends

Window: Friday 16:00 to Sunday 18:00 New York time
(`desk_tick_sim.weekend_closed`, zoneinfo). That is Fri 21:00 to Sun 23:00
UTC in US winter time and Fri 20:00 to Sun 22:00 UTC in US summer time. New
York sets the clock in the weeks the US and UK switch on different dates.
"Hold": no new entry inside it, an open position keeps its stop and
target. "Flatten": also close at market at the window open.

Brent, live 0.75 stop, $64 became:

| Dataset | 24/7 | Hold | Flatten |
|---|---|---|---|
| 30 replayed theses | $121.78 | $121.78 | $121.78 |
| Always long, replay | $1,057.89 | $1,451.67 | $1,636.42 |
| Always long, 30d | $515.90 | $845.38 | $1,004.06 |
| Always short, replay | $3.63 | $2.02 | $1.78 |
| Always short, 30d | $9.48 | $5.27 | $6.13 |
| Daily sign, replay | $337.19 | $462.71 | $582.65 |
| Daily sign, 30d | $556.44 | $911.80 | $1,082.95 |
| Beats live | | 4 of 7 (ratio 1.07) | 4 of 7 (1.18) |

On the 30 theses drawdown (66%), trades (38) and win rate (45%) are the
same in all three: no thesis trade opened or was open in a weekend window.
On top of 0.9% of mid the pattern repeats: weekend-off wins on always long
and daily sign and loses on always short.

Silver, live rule, $64 became:

| Dataset | 24/7 | Hold | Flatten |
|---|---|---|---|
| 30 replayed theses | $253.36, DD 32%, 12 trades, 58% win | $319.14, DD 32%, 11, 64% | $200.65, DD 32%, 11, 64% |
| Always long, replay | $0.72 | $0.72 | $1.43 |
| Always short (= daily sign), replay | $662.68 | $406.48 | $222.32 |
| Always long, 30d | $0.70 | $0.70 | $1.31 |
| Always short (= daily sign), 30d | $479.06 | $293.85 | $160.72 |
| Beats live | | 1 of 7 (ratio 0.78) | 2 of 7 (0.63) |

Brent passes the count (4 of 7) only through the no-thesis controls, and
they say one thing: in October a long opened on a Brent weekend lost and a
short won. Four weekends of one month. Silver is the mirror: its weekend
shorts made money, so weekend-off costs it. Not a rule.

This weekend's closed trades against the window:

| Trade | Entry inside the window? | With weekend-off |
|---|---|---|
| Brent long, Sat 10-31 16:18Z to Sun 11-01 18:20Z, +$7.83 | yes | skipped: $7.83 less |
| Silver short, Mon 11-02 00:51Z, stopped 01:05Z, -$1.82 | no (reopen was Sun 23:00Z) | same |
| Silver short, Mon 11-02 01:18Z, stopped 05:30Z, -$2.56 | no | same |
| Brent long, Mon 11-02 03:50Z, stopped 08:35Z, -$33.45 | no | same |

Weekend-off would have cost $7.83 this weekend and saved nothing. All
three losses came after the Sunday reopen.

## C. Profit lock on weekends only

The lock from `PROFIT_LOCK_TEST_2026-11-01.md` (once the best price is X R
in favour, stop to entry + Y R; fixed or trailing), plus "X% of the way to
the target" triggers and break-even plus fees (the stop to entry plus the
taker round trip once up 1R). It acts only inside the weekend window: a
weekday bar neither arms it nor counts toward its best price. Weekdays run
the live fixed stop. A stop it moved stays after the reopen.

Brent, live 0.75 stop, $64 became:

| Dataset | Live | Break-even + fees at 1R | Lock 0 at 1R | Lock 0.5R at 1R | Trail 1R / 0.5R | Lock 1R at 1.5R | Break-even + fees at 1R, 24/7 |
|---|---|---|---|---|---|---|---|
| 30 replayed theses | $121.78 | $121.78 | $121.78 | $121.78 | $121.78 | $121.78 | $90.79 |
| Always long, replay | $1,057.89 | $1,148.75 | $1,128.14 | $1,256.62 | $1,257.57 | $1,057.89 | $735.22 |
| Always long, 30d | $515.90 | $560.21 | $550.17 | $612.82 | $668.76 | $515.90 | $349.22 |
| Always short, replay | $3.63 | $5.18 | $4.99 | $4.63 | $4.30 | $3.72 | $3.45 |
| Always short, 30d | $9.48 | $11.18 | $10.78 | $9.51 | $7.48 | $8.03 | $14.54 |
| Daily sign, replay | $337.19 | $366.15 | $359.58 | $400.54 | $447.76 | $337.19 | $265.63 |
| Daily sign, 30d | $556.44 | $604.23 | $593.39 | $660.97 | $721.30 | $556.44 | $343.29 |
| Beats live | | 6 of 7 (1.13) | 6 of 7 (1.11) | 6 of 7 (1.14) | 5 of 7 (1.14) | 1 of 7 (0.98) | 1 of 7 (0.82) |

Every 1R trigger beats live on all 6 controls and ties the theses. Triggers
at 1.5R and 2R mostly tie or lose (1 of 7 each), and target-share triggers
at 75% never fire (50%: 5 of 7). The same break-even lock run all week
loses on 6 of 7: limiting it to the weekend is what keeps it from cutting
weekday winners. Drawdown and win rate barely move (daily sign 30d: 49%
drawdown in both).

Silver, live rule: the 30 replayed theses do not change under any lock
($253.36). Always long goes from $0.72 to between $1.22 and $1.68; always
short does not change. Best: 2 of 7 beats, 5 ties, no loss.

Not adopted: on Brent it fails the plateau rule (1R wins, 1.5R and 2R do
not), on silver it fails "most datasets", and on both the thesis sets never
had a weekend position reach +1R, so it is untested where it matters. The
fixed 1R locks (break-even + fees, lock 0, lock 0.5R) never lost on any
dataset (8 wins, 6 ties of 14). They are the ones worth re-running with the
live journal.

### The Sat 10-31 Brent long under each lock

40.73 Brent at 20x from 111.48, stop 111.02 (R = 0.46), target 113.66.
Peak on the candles 112.50 (+2.22R; +1.9R on the mid). Closed by hand
Sun 11-01 18:20Z for +$7.83. Walked from the bar after the fill to the
18:15Z bar; a rule that has not exited by then is closed by hand like
live. The whole trade sat inside the weekend window, so the weekend-only
and 24/7 versions are the same here. Net after the sim's fees.

| Rule | Exit | When | Net |
|---|---|---|---|
| Live, closed by hand | - | 11-01 18:20Z | +$7.83 |
| Break-even + fees, any trigger; lock 0 at 1R/1.5R/2R; target share 50% or 75% | not hit | by hand | +$7.83 |
| Lock 0.5R at 1R, 1.5R or 2R | 111.71 | 11-01 18:00Z | +$5.28 |
| Lock 1R at 1.5R or 2R | 111.94 | 11-01 11:00Z | +$14.64 |
| Trail 1R / 0.5R | 111.87 | 10-31 19:00Z | +$11.79 |
| Trail 1.5R / 0 and 2R / 0.5R | 111.81 | 11-01 13:15Z | +$9.35 |
| Trail 1R / 0, 1.5R / 0.5R, 2R / 1R | 112.04 | 11-01 08:00Z | +$18.71 |
| Trail 1.5R / 1R | 112.27 | 11-01 00:45Z | +$28.08 |

The operator's "break-even + fees once up 1R" would have banked the same
+$7.83: price never came back to entry. Only the tighter trails bank more
of the +$35 peak, and those lose across the datasets (trail 1.5R / 1R: 1 of
7 on Brent).

Today's Monday long is outside the window: no lock applies to it.

## Caveats

- **The live journals are not in this run.** The live oil and metals
  journals sit under the old repo's state folder, which this run was not
  allowed to read. The stop-cap test ran the Brent journal (16 theses,
  $368.16 live). It is the only Brent thesis set with weekend trades
  (Sat 10-31). Run the command below with `--oil-theses-dir` and
  `--metals-theses-dir` before acting on A or C.
- **One month, one direction.** Brent rallied through October, silver fell.
  The replay window and the last 30 days overlap by 27 days.
- **The desk sizes by margin, not by risk.** A wider stop loses more per
  stop at the same size: today's 0.90 stop costs $6.35 more than 0.75.
- **Fill-price liquidation ratio.** Measured at the simulated fill after
  slippage; the live `place_bracket` gate checks at its own entry price.
- **Attribution** (the "re-judged on live's entries" numbers) is prices
  plus fees, no slippage or funding, one entry at a time. Final equity is
  the full sim.
- **Today's row** stops at 109.25 exactly; live filled at 109.23.

## How to rerun

```
.venv/bin/python -m services.strategies.evals.brent_stop_buffer_test \
    --oil-theses-dir <pathia>/.state/strategies/oil/theses \
    --metals-theses-dir <pathia>/.state/strategies/metals/theses
```

About 20 seconds. Defaults: Brent replay `/tmp/thesis-replay-30b/oil/theses`,
silver replay `/tmp/thesis-replay-silver/theses` with its cache
`/tmp/thesis-backtest/cache`, fresh candles in `/tmp/brent-buffer/cache`
(Brent to 2026-11-02 08:45Z, silver to 09:00Z). Writes
`/tmp/brent-buffer/report.json` (every study, variant, dataset, score,
verdict and regression row), `/tmp/brent-buffer/summary.csv` (one row per
study, dataset and variant) and `/tmp/brent-buffer/entries.csv` (every live
entry re-judged with each buffer).

Sim options, all off by default, in
`services/strategies/backtest/desk_tick_sim.py`: `fixed_buffer_floor_frac`,
`weekend_off`, `lock_weekend_only`, `lock_trigger_target_frac`,
`lock_plus_fees`, and `weekend_closed()`. With them off, 45 runs of the
existing options (live, lock, trail, cap, ATR, floor, fixed 1.0, session,
flip on Brent and silver) are byte-identical to before (cmp). Gate tests:
`services/strategies/tests/test_brent_stop_buffer.py` (sim, weekend window
with DST edges) and `test_brent_stop_buffer_eval.py` (grids, verdict rules,
today's long on the real 11-02 bars, the 10-31 long, this weekend's trades).

No live config, order or thesis was touched. No LLM calls; replays and
public candles only, read only.
