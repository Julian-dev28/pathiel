# 30-day backtest plan for the desk-tick loop (2026-10-25)

What Julien asked: "figure out a good plan for a 30 day backtest with
whatever strategy we are using rn." This is that plan. It names what the
strategy actually is, which parts of it a backtest can replay honestly,
what data exists, how the replay is built, what it measures, and what it
cannot tell us.

## 1. What the strategy is, in rules

The live loop is `.claude/skills/desk-tick/SKILL.md`, run every 15 minutes
for oil (30 for the other desks). Stripped of prose it is:

| step | rule | source of truth |
|---|---|---|
| direction | the desk thesis's call (long/short) and its invalidation line | `services/thesis` (model call) |
| entry | a resting limit at a pullback to the 8h or 24h range edge in the thesis direction, or a breakout retest; never at market above the thesis entry | `scripts/oil_levels.py`, operator rule |
| stop | beyond the structure the entry leans on (the 8h/24h low for a long), never widened | SKILL.md |
| target | the next structure (8h/24h high, then the thesis's targets) | SKILL.md |
| gate | reward/risk at least 1.5R to the first target | `pathiel/agents/bracket.py` (`min_r`) |
| leverage | up to 20x isolated; the stop must sit inside half the distance to liquidation | `check_leverage` in bracket.py, `--lev 20` |
| size | full-port: 90% of equity times leverage, minus what is held | `risk_size_trade`, `place_bracket add=True` |
| top-up | one resting add at the thesis entry while under full size; re-judged each tick | SKILL.md step 5 |
| exit | the venue stop or target fires; or a HOLD becomes CLOSE when a 1h candle closes through the invalidation | SKILL.md step 5 |
| resting order hygiene | cancel a resting entry more than one ATR14 from the mid unless it sits within 0.25 ATR of a range edge | SKILL.md step 5 |
| one slot | one position per account across all desks | `desk_slot.py`, `slot_taken_by` |

Everything in the table except the first row is deterministic code or a
written rule. The first row is the only latent step: a model writes the
thesis from the intel packet. That split decides the design below.

## 2. Deterministic replay, latent held fixed

A backtest that asks a model to re-write 30 days of theses hour by hour
would cost about a dollar per rewrite, would not be reproducible, and would
be contaminated by the model knowing what happened. So:

- **Replay the mechanics, not the thesis.** Build a pure simulator of rows
  2 to 11 that walks 15-minute candles forward with no lookahead and
  consumes a *direction series* as an input.
- **Three direction series, run as three experiments:**
  1. **Journaled theses.** The six oil theses written since 2026-10-18
     (`$PATHIEL_STATE_DIR/strategies/oil/theses/`), each applied from its
     `created_at` to the next. This is the only "real" run and it covers
     seven days, not thirty.
  2. **Always long and always short.** Two runs that hold direction fixed.
     This measures the entry, stop, target and 20x rules on their own: if
     always-long and always-short both bleed, the mechanics bleed.
  3. **Daily analytics sign.** Direction = sign of the 20-day return on the
     daily store (`candle_store`), updated once a day with no lookahead.
     A cheap, deterministic stand-in for "the thesis follows the trend",
     which is what the oil theses have done in practice.

The thesis's own quality is already measured elsewhere: `desk_grade` grades
every expired thesis against what the market did, and `grade-everything`
scores the account's trades against the live call. The backtest does not
duplicate that.

## 3. Data

| series | depth available today | source | enough for 30 days? |
|---|---|---|---|
| xyz:BRENTOIL 15m candles | 5005 bars, back to 2026-09-02 | `candleSnapshot` on the public `/info` endpoint | yes (2880 needed) |
| xyz:BRENTOIL 1h candles | 1081 bars, back to 2026-09-09 | same | yes (720 needed) |
| xyz:BRENTOIL daily | 235 bars, back to 2026-03-04 | `services/strategies/candle_store` (cached) | yes |
| funding (hourly) | `fundingHistory` on `/info` | same endpoint `services/intel/sources/venue.py` already reads | yes |
| fees | taker 0.045% / maker 0.015% on HIP-3 as the account pays today | `get_user_fees` | constant |
| theses | 6 machine theses, 2026-10-18 to 2026-10-23 | journal | partial |
| the loop's own decisions | `/tmp/oil-loop/progress.log`, every tick since 2026-10-23 | this session | calibration only |

Candles are fetched once and cached under `$PATHIEL_STATE_DIR/backtest/`
so every run is reproducible from disk; the fetch is the only network
step and it is read-only.

## 4. Simulator design

`services/strategies/backtest/desk_tick_sim.py`, pure functions, no I/O:

```
simulate(candles_15m, candles_1h, direction_at, params) -> Run
```

- **Clock.** One tick per 15m bar, in order. Levels (8h/24h range, ATR14 on
  1h) are computed from bars strictly before the tick, same as
  `oil_levels.py` does live.
- **Entry.** When flat and the slot is free: if the mid is within 0.25 ATR
  of the range edge on the thesis side, rest a limit at that edge. A
  resting order fills when a later bar's low (long) or high (short) trades
  through it; fill price is the limit, fee is maker.
- **Stop and target.** Stop below the edge by one tick-structure buffer
  (the live desk used 0.10 to 0.60 under the 8h low; the simulator takes
  the parameter and we sweep it). Target at the next structure. Both must
  pass `check_leverage` and the 1.5R gate or the entry is skipped and the
  reason is counted.
- **Fills on the stop.** A stop fills at the stop price when a bar's low
  trades through it, with taker fee and a slippage parameter (default 0.03%,
  roughly one tick of Brent's book) because a stop is a market order.
- **Exit on invalidation.** At each 1h close, if the close is through the
  thesis invalidation, close at that bar's close with taker fee.
- **Top-up.** While in a position under full size and nothing resting, rest
  one add at the original entry level with the position's stop and target;
  fills like any resting order.
- **Resting-order hygiene.** Cancel a resting entry more than one ATR14
  from the mid unless it is within 0.25 ATR of a range edge.
- **Equity.** Start at $64 (the account on 2026-10-24), isolated 20x,
  full-port sizing with the same 10% free-margin floor `place_bracket`
  keeps. Liquidation is checked bar by bar against the venue's maintenance
  rule for the dex; a liquidation ends the run and is reported as such.
- **Funding.** Applied hourly from the fetched history, signed by side.

Nothing in the simulator is new finance: every rule above is already in
`bracket.py`, `oil_levels.py` or the skill, and the simulator imports the
pure helpers (`check_leverage`, the R gate) rather than re-implementing
them, so the backtest and the live tool cannot drift apart.

## 5. What it measures

Per run, written as JSON and one CSV of trades to `/tmp/desk-backtest/`:

- trades, win rate, average R, expectancy per trade in R and in dollars
- equity curve, max drawdown, time in market, liquidation count
- resting-order fill rate (how often a pullback limit actually filled)
- how many setups the 1.5R gate or the 20x rule refused, by reason
- fees and funding paid, as a share of gross P&L

The headline number is **expectancy in R per trade** for the always-long
and always-short runs. If both are negative, the entry/exit mechanics are
the problem regardless of thesis quality. If one is positive, the loop's
edge is directional and the thesis is the whole game.

## 6. Calibration against the live loop

The log since 2026-10-23 holds every real tick decision. Replay the same
window with the journaled-thesis direction series and diff the simulator's
decisions against the log line by line (same resting level, same fill, same
HOLD/CANCEL). Agreement is the proof the simulator models the loop and not
a cousin of it. Known divergences are listed in the report, not hidden.

## 7. Deliverables and tests

| file | role |
|---|---|
| `services/strategies/backtest/desk_tick_sim.py` | pure simulator |
| `services/strategies/backtest/data.py` | candle and funding fetch with disk cache |
| `services/strategies/evals/desk_tick_backtest.py` | runnable eval: fetch, run the three experiments, write report and CSVs, exit 1 if the always-long/always-short runs liquidate |
| `services/strategies/tests/test_desk_tick_sim.py` | gate tests on synthetic candles: a pullback fills and stops, a target fills, the invalidation close exits, the 1.5R gate refuses, the 20x rule refuses, funding and fees are signed right, no lookahead (shifting future bars changes nothing before them) |
| `services/strategies/tests/test_desk_tick_backtest_eval.py` | the eval's pure aggregation on injected runs |
| `.github/workflows/nightly.yml` | adds the eval to the nightly lane next to `verdict_backtest` |

Parameter sweep, reported but not fitted: stop buffer {0.10, 0.25, 0.50}
below the edge, slippage {0.02%, 0.05%}, leverage {10x, 20x}. Thirty days
is one regime (the Hormuz shock and its aftermath), so the sweep is there
to show sensitivity, not to pick a winner.

## 8. What the backtest cannot say

- It cannot grade the thesis writer. Seven days of journaled theses is the
  whole real sample; the other two direction series are stand-ins.
- Headline-driven exits (a ceasefire print inside a bar) are not replayable
  from candles; the simulator exits on the 1h close rule only, which is
  also what the live loop does.
- One instrument, one regime, 30 days. The result is evidence about these
  mechanics in this tape, not a forecast.

## 9. Order of work

1. `data.py` and the cached fetch, with a test that the cache round-trips.
2. `desk_tick_sim.py` with the synthetic-candle tests, one rule at a time.
3. The eval script with the three direction series and the report.
4. The calibration diff against `/tmp/oil-loop/progress.log`.
5. Nightly wiring.

Roughly a day of agent work. Steps 1 to 3 are the deterministic core and
can run in a worktree without touching the live desk.
