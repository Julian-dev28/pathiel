# Daily stop-out cap test, 2026-11-02

**Correction (2026-11-03): verdict holds, confirmed by re-run.** This file
ran at 14:02 on 2026-11-02, before `Params.risk_cap_frac` (the live 15%
risk cap) existed at all (`loss_fix_test.py`, 17:59,
`LOSS_AUDIT_2026-11-02.md`), so its numbers below were accurately
full-port for their own time, not a bug. `stop_cap_test.py`'s `bp`/`sp`/
`fp` now carry `risk_cap_frac=0.15` (via `live_params.live_brent_params`
for Brent; the constant applied directly for silver, which deliberately
keeps a different, non-live stop-rule shape for its own comparison -- see
the file's own comments). Re-ran it with the fix
(`--oil-theses-dir`/`--metals-theses-dir` given): `score [thesis]` --
cap_1/side_1 geo-mean 0.598 (loses on 3 of 5), cap_2/side_2 0.874, cap_3/
side_3 0.988 -- **still no N wins on the thesis datasets**, same as
below. The cap can't change this verdict mechanically either way: it only
scales position SIZE, never which trades fire or when
(`risk_capped_notional` runs strictly after `check_leverage`/
`check_levels` already decided the trade), and the daily-stop-cap feature
itself only gates NEW entries by a stop COUNT, not by price level, so it
never interacts with the cap's sizing math.

Operator, 2026-11-02 ("go"): the silver desk shorted three times into a
rising market. Two shorts were stopped (-$1.82 at 01:05Z, -$2.56 at
05:30Z). A third opened at 05:35Z because a fresh thesis still said short
and the 2R gate passed. Would a daily stop-out cap help?

## Verdict

**Do not adopt.** No N wins on the thesis datasets, which is what the desk
trades.

- **N = 1** loses: $64 became less than with no cap on 3 of 5 thesis sets.
  It cut the Brent journal from $368 to $166 and the silver replay from $174
  to $41. The entries it blocked would have made +8.2R and +13.2R.
- **N = 2 and N = 3** never fire on the Brent theses or the silver replay.
  In 15 to 30 days per thesis set, no UTC day had 2 stops. They only
  fire with the live silver stop (1.3 x the tick's own ATR, which is
  tighter). There N = 2 cut $143 to $63, because the trade after the second
  stop was often the one that worked.
- **Per side is the same as per desk** on every dataset. A thesis almost
  never flips side inside a day after its stops.

On 2026-11-02 itself the sim matches the live desk: two shorts, both
stopped. N = 2 would have blocked the third short. Its result is not known
yet (data ends 06:00Z, one bar after it opened, +0.24R marked).

A cap pays when the side is wrong all day (always short in Brent's October
rally, always long in silver's October fall). The theses pick the side well
enough that the trade after a stop usually works. Same lesson as the
profit lock test (`PROFIT_LOCK_TEST_2026-11-01.md`).

## The rule tested

After N stop exits on the desk in one UTC day, no NEW entry for the rest of
that UTC day. Open positions are untouched. Targets, invalidations and
direction-flip exits do not count. The count resets at 00:00 UTC. N = 1, 2,
3, for the whole desk and per side (N short stops block only shorts).
Everything else is the live rule set (`live_params()`): market entry off the
24h range, 2R gate, target 0.5% inside the 24h edge, limit target, 20x, $64
start, 15-minute ticks. Brent stop 0.75 beyond the 24h edge; silver 1.3 x
ATR14.

## Results

$64 became. "Blocked" is what the entries the cap skipped would have done,
walked on with their own stop and target: wins / losses / still open, and
their R after fees. A plus R means the cap cost that; a minus R means it
saved it.

Thesis datasets:

| Dataset | No cap | N = 1 | N = 2 | N = 3 |
|---|---|---|---|---|
| Brent, live journal (16 theses, 15 days) | $368.16, DD 37%, 11 trades, 64% win | $165.80; blocked 2/1/1, +8.2R | same as no cap (never fired) | same as no cap |
| Brent, 30 replayed theses | $121.78, DD 66%, 38 trades, 45% | $141.63; blocked 3/4/0, -0.7R | never fired | never fired |
| Silver, 30 replayed theses | $173.95, DD 38%, 13 trades, 54% | $41.14; blocked 5/0/0, +13.2R | never fired | never fired |
| Silver replay, live per-tick ATR stop | $142.97, DD 46%, 22 trades, 36% | $56.72; blocked 6/7/0, +6.0R | $63.07; blocked 3/3/0, +6.0R | $127.64; blocked 1/2/0, +0.3R |
| Silver, live journal (4 theses, 2 days) | $49.27, 2 stops, a third short open | $56.69; blocked 0/1/1, -1.0R | $50.01; blocked 0/0/1, +0.2R | never fired |

Days with 2 or more stops under no cap: 0 on both Brent thesis sets and the
silver replay; 4 on the silver replay with the live ATR stop; 1 on the live
silver journal (today).

No-thesis controls:

| Control | No cap | N = 1 | N = 2 | N = 3 |
|---|---|---|---|---|
| Brent always long, 30d | $515.90 | $120.54 | $281.12 | $325.51 |
| Brent always short, 30d | $9.48 | $11.67 | $15.74 | $13.83 |
| Brent daily sign, 30d | $556.44 | $150.45 | $393.83 | $556.44 |
| Brent always long, 50d | $592.21 | $48.00 | $470.57 | $433.82 |
| Brent always short, 50d | $5.84 | $10.78 | $14.48 | $9.13 |
| Brent daily sign, 50d | $33.11 | $35.01 | $62.54 | $58.03 |
| Silver always long | $0.61 | $17.34 | $8.65 | $2.67 |
| Silver always short (= daily sign) | $184.44 | $72.41 | $108.61 | $184.44 |

The cap helps only where the side is wrong or the market chops, and costs
where the side is right.

### 2026-11-02 in the sim

Live metals journal, silver theses only (the 2026-10-23 gold thesis is
dropped), fresh candles to 06:00Z, live per-tick ATR stop:

| | Sim | Live |
|---|---|---|
| Short 1 | in 00:15Z at 49.88, stopped 01:00Z | in 49.98, stopped about 01:05Z, -$1.82 |
| Short 2 | in 01:15Z at 50.17, stopped 05:30Z | in 50.12, stopped 05:30Z, -$2.56 |
| Short 3 | in 05:45Z at 50.48, stop 50.80 (open at the data end) | in 05:35Z |
| N = 1 | blocks short 2 (it lost 1.2R) and short 3 | |
| N = 2 | blocks short 3 only, result open | |

Sim dollars are bigger than live because the sim trades a full $64 desk;
the live silver desk trades a 10% slice.

## Surprise: the live silver stop is not the one we tested

`SILVER_THESIS_TEST_2026-10-31.md` used a fixed 0.41 buffer (1.3 x silver's
mean 1h ATR). The live desk uses 1.3 x the ATR at that tick
(`next_trade.stop_buffer_for`). On 2026-11-02 at 01:00Z that was about 0.21,
half the tested buffer. On the same 30 replayed theses the live rule made
$142.97 (22 trades, 36% win) against $173.95 (13 trades, 54% win) for the
tested buffer. Two stops in one day happen only under the live rule. This is
the likely root of today's run of stops, and is worth its own test (for
example a floor under the ATR buffer). Not changed here.

## Caveats

- **Small samples.** On the thesis sets N = 2 and 3 never fired except on
  the silver ATR replay (6 blocked entries) and today's journal (1, still
  open).
- **Two trending months.** Brent rallied through October and silver fell.
  A choppy month is where a cap could pay. Retest after one.
- **Blocked-entry walk** is prices plus fees, no slippage or funding. It
  walks the skipped entries one at a time (one slot), so it is what that
  day would have looked like without the cap. R and dollars can differ in
  sign because the size follows equity. Final equity carries everything.
- **Silver ATR.** The replay rows use 1.3 x mean ATR except the two rows
  marked "live per-tick ATR stop".

## How to rerun

```
.venv/bin/python -m services.strategies.evals.stop_cap_test \
    --oil-theses-dir <pathia>/.state/strategies/oil/theses \
    --metals-theses-dir <pathia>/.state/strategies/metals/theses \
    --oil-replay-dir /tmp/thesis-replay-30b/oil/theses \
    --silver-theses-dir /tmp/thesis-replay-silver/theses \
    --silver-cache-dir /tmp/thesis-backtest/cache \
    --cache-dir /tmp/stop-cap/cache-0611
```

About 10 seconds. Writes `/tmp/stop-cap/report.json` (every variant, every
dataset, scores, the 2026-11-02 spotlight) and
`/tmp/stop-cap/blocked_entries.csv` (every blocked entry and what it would
have done). A new `--cache-dir` fetches fresh candles.

Sim options: `Params.daily_stop_cap`, `cap_per_side` and `stop_buffer_atr`
in `services/strategies/backtest/desk_tick_sim.py`, all off by default. With
them off, `thesis_backtest` on silver and the full `profit_lock_test` report
are byte-identical to before. Gate tests: `services/strategies/tests/test_stop_cap.py`
and `test_stop_cap_eval.py`.

No live config, order or thesis was touched. No Fable; existing replays and
the live journals only, read only.
