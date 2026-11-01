# Profit lock test, 2026-11-01

Operator, 2026-11-01: a Brent long opened Sat 2026-10-31 16:17Z at 111.48
(40.73 Brent, 20x, stop 111.02, target 113.66) reached +1.9R on the mid
(112.36, +$35.84 unrealized), drifted back, and closed +$7.83 net. "we were
up like 35$ and we round tripped" ... "yeah backtest it".

## Verdict

**Do not adopt.** Keep the fixed stop. On the three thesis datasets (what the
desk actually trades) a profit lock beat the fixed stop in 1 of 48 tries
(16 variants x 3 datasets). Of the 92 exits the lock caused on thesis
trades, 90 were trades that would have gone on to fill their target. It
never once saved a thesis trade from its stop.
This matches the 2026-10-28 result for breakeven and trailing stops
(`OIL_STRATEGY.md`).

If a lock is wanted anyway, the cheapest one is **trigger 2R, lock 0.5R**
(fixed, not trailing): it never fired on either Brent thesis set, it cost
30% on silver, and it helped on the no-thesis controls. Not recommended now.

## The rule tested

Once the best price since the fill (a long's highest high) is TRIGGER R in
favour (R = entry to stop), move the stop to entry + LOCK R. Mirror for a
short. It only tightens; the target stays. Grid: TRIGGER 1, 1.5, 2 and LOCK
0, 0.5, 1 with LOCK under TRIGGER (8 pairs), each as a fixed lock and as a
trail (the stop stays TRIGGER minus LOCK R behind the best price, never
under the lock). Everything else is the live rule set (`live_params()`):
market entry off the 24h range, 2R gate, target 0.5% inside the 24h edge,
limit target, 20x, $64 start, 15-minute bars.

## Results

Thesis datasets. "Lock exits" splits every exit the lock caused by where the
same position would have closed with its original stop and target.

| Dataset | Variant | $64 became | Max DD | Trades | Win | Avg R | Lock exits: cut a winner / saved a stop / saved a give-back / cut a runner | R gained on lock exits |
|---|---|---|---|---|---|---|---|---|
| Brent, live journal (15 theses) | fixed stop | $509.33 | 29% | 9 | 78% | +1.82 | - | - |
| | 1R, lock 0 | $389.03 | 29% | 9 | 67% | +1.59 | 1 / 0 / 0 / 0 | -2.1 |
| | 1.5R, lock 1R | $324.12 | 32% | 9 | 78% | +1.45 | 2 / 0 / 0 / 0 | -3.3 |
| | 2R, lock 0.5R | $509.33 | 29% | 9 | 78% | +1.82 | never fired | 0 |
| Brent, 30 replayed theses | fixed stop | $121.78 | 66% | 38 | 45% | +0.18 | - | - |
| | 1R, lock 0 | $118.88 | 66% | 39 | 44% | +0.14 | 1 / 0 / 0 / 0 | -3.2 |
| | 1.5R, lock 1R | $89.18 | 66% | 39 | 46% | +0.09 | 3 / 0 / 0 / 0 | -5.2 |
| | 2R, lock 0.5R | $121.78 | 66% | 38 | 45% | +0.18 | never fired | 0 |
| Silver, 30 replayed theses | fixed stop | $173.95 | 38% | 13 | 54% | +0.81 | - | - |
| | 1R, lock 0 | $111.95 | 38% | 13 | 46% | +0.57 | 1 / 0 / 0 / 0 | -3.1 |
| | 1.5R, lock 1R | $72.72 | 44% | 13 | 54% | +0.23 | 3 / 0 / 0 / 0 | -7.5 |
| | 2R, lock 0.5R | $121.85 | 38% | 13 | 54% | +0.61 | 1 / 0 / 0 / 0 | -2.6 |

The one thesis win: 1.5R, lock 0.5R on the Brent replay, $127.50 vs $121.78.
The best trailing variant (2R, lock 0.5R, trailing) never fired on Brent and
took silver to $93.11.

No-thesis controls, $64 became:

| Control | Fixed stop | 1R, lock 0 | 1.5R, lock 1R | 2R, lock 0.5R | 2R, lock 0.5R, trailing |
|---|---|---|---|---|---|
| Brent always long, 30d | $717.32 | $499.46 | $219.64 | $717.32 | $717.32 |
| Brent always short, 30d | $8.41 | $11.98 | $11.76 | $13.69 | $17.81 |
| Brent daily sign, 30d | $773.68 | $630.19 | $236.90 | $773.68 | $773.68 |
| Brent always long, 50d | $823.42 | $166.11 | $99.17 | $581.35 | $1,118.92 |
| Brent always short, 50d | $5.18 | $9.18 | $6.96 | $7.34 | $9.88 |
| Brent daily sign, 50d | $46.04 | $45.10 | $19.19 | $76.12 | $185.86 |
| Silver always short / daily sign | $184.44 | $127.07 | $139.87 | $258.68 | $234.18 |
| Silver always long | $0.61 | $1.31 | $0.62 | $1.25 | $1.44 |

The lock saves stops when the side is wrong (always short in Brent's October
rally) or the market chops (the 50-day daily sign). The theses pick the side
well enough that a trade at +1R to +1.5R mostly keeps going.

### The live trade itself

Replayed on real 15-minute candles from the bar after the fill, with the
live levels and size (fees in, no slippage):

| Rule | Exit | When | Net |
|---|---|---|---|
| What happened | closed | 2026-11-01 | +$7.83 |
| Fixed stop, never closed | still open at 111.57 | data end 11-01 23:45Z | -$0.42 marked |
| 1.5R, lock 1R | 111.94 | 11-01 11:00Z | +$14.64 |
| 2R, lock 0.5R | 111.71 | 11-01 18:00Z | +$5.28 |
| 1.5R, lock 1R, trailing | 112.27 | 11-01 00:45Z | +$28.08 |

On this one trade the lock would have helped. Across 60 thesis trades it costs
more than it saves.

## Caveats

- **Small samples.** 9, 38 and 13 thesis trades; the variants in the table
  changed at most 3 of them (the tightest trails up to 6). One extra target or one saved stop moves a row a lot.
- **Two trending regimes.** Brent rallied through October and silver fell
  19%. A fixed stop and a far target pay most in a trend. In a range the lock
  should do better (the 50-day daily-sign control hints at it). Retest after
  a choppy month.
- **Intrabar order.** 15-minute candles do not say whether the high or the
  low came first. The sim is conservative: each bar's stop and target are
  judged first, against the stop as it stood at the open; the lock is set
  from that bar's high (low) after that; it fills in the same bar only when
  the bar closes through it (price touched the trigger, then finished below
  the lock, so it crossed the lock after the trigger). Otherwise it applies
  from the next bar, and a bar that opens through it fills at the open.
  Turning the same-bar fill off (what a 15-minute tick with no price
  watcher could do) changed almost nothing.
- **Trade prints, not mid.** Candle highs are trades. The live trade peaked
  at 112.50 on the candles (+2.22R) against 112.36 on the mid (+1.9R), so a
  2R trigger fires in the replay where the live mid never got there.
- **Attribution is prices only** and walks each position on alone. The
  final equity numbers carry fees, funding (Brent), size slippage (Brent)
  and the re-entries a lock exit frees up.

## How to rerun

```
.venv/bin/python -m services.strategies.evals.profit_lock_test \
    --oil-theses-dir <pathia>/.state/strategies/oil/theses \
    --oil-replay-dir /tmp/thesis-replay-30b/oil/theses \
    --silver-theses-dir /tmp/thesis-replay-silver/theses \
    --silver-cache-dir /tmp/thesis-backtest/cache
```

About 15 seconds. Writes `/tmp/profit-lock/report.json` (every variant,
every dataset, scores) and `/tmp/profit-lock/lock_exits.csv` (every lock
exit with its counterfactual). Brent candles were fetched fresh on
2026-11-02 into `/tmp/profit-lock/cache` (data to 2026-11-01 23:45Z).
The sim option is `Params.lock_trigger_r` / `lock_r` / `lock_trail` /
`lock_confirm_close` in `services/strategies/backtest/desk_tick_sim.py`, off
by default. With it off, `thesis_backtest` on silver reproduces
`SILVER_THESIS_TEST_2026-10-31.md` byte for byte.
Gate tests: `services/strategies/tests/test_profit_lock.py` and
`test_profit_lock_eval.py`.

No live config, order or thesis was touched. No Fable; existing Opus replays
and the live journal only, read only.
