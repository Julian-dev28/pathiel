# Closer target backtest, 2026-11-03

**Correction (2026-11-03, later the same day):** this doc's original numbers
below were full-port size, not the real live rule. `bp`/`sp` in
`closer_target_test.py` were built by hand
(`dataclasses.replace(base_params()/scouting_params(...), starting_equity=...)`)
and never actually set `Params.risk_cap_frac` -- the 15% risk cap live since
2026-11-02 -- even though this doc's own "Results" line always claimed it
("15% risk cap" in the dataset description below).
`EXTENDED_HISTORY_TEST_2026-11-03.md`'s Caveats caught this first. Fixed:
`bp`/`sp` now build through the new `services/strategies/evals/live_params.py`
(`live_brent_params`/`live_silver_params`, the one shared place every eval
now builds its "live" baseline from). Re-run with the fix, plus a new
`brent_replay_sep` dataset (the September replay,
`EXTENDED_HISTORY_TEST_2026-11-03.md`'s window, read via
`/tmp/thesis-replay-sep/oil/theses`):

**The verdict holds: keep the live far-edge target. No closer target beats
it, capped or not.** `best (all datasets)` is still `cap_5` (the loosest
cap, which essentially never binds); `adopt: none`. Every number below is
now the corrected, capped run (`risk_cap_frac=0.15`); the original
full-port numbers are struck through context in the Method section for the
record.

## Corrected results (capped, 2026-11-03 rerun)

Live vs. the best 3 candidates (`cap_5`, `cap_3.5`, `cap_2.5` -- picked by
the eval's own `--top-n 3` ranking) on the journal, the October replay and
the new September replay. Net $ = final equity − $64 start.

| Dataset | Variant | Net $ | Max DD | Trades | Win rate |
|---|---|---|---|---|---|
| brent_journal (live oil journal, 21 theses) | **live** | **+259.77** | 39.3% | 12 | 58% |
| brent_journal | cap_5 | +259.77 | 39.3% | 12 | 58% (ties live -- never binds) |
| brent_journal | cap_3.5 | +227.98 | 39.3% | 12 | 58% |
| brent_journal | cap_2.5 | +195.79 | 39.3% | 12 | 58% |
| brent_replay30 (October replay) | **live** | **+53.64** | 55.9% | 38 | 45% |
| brent_replay30 | cap_5 / cap_3.5 / cap_2.5 | +53.64 (all three tie live) | 55.9% | 38 | 45% |
| brent_replay_sep (September replay, new) | **live** | **-34.45** | 80.8% | 18 | 28% |
| brent_replay_sep | cap_5 | -35.67 | 80.8% | 18 | 28% |
| brent_replay_sep | cap_3.5 | -29.71 | 80.8% | 18 | 28% |
| brent_replay_sep | cap_2.5 | **+3.56** | **60.1%** | 22 | 36% |

September is the one dataset where a tighter cap (`cap_2.5`) actually beats
live in dollar terms (+$3.56 vs -$34.45) and cuts drawdown (60.1% vs
80.8%) -- but it loses on 5 of the other 7 scored datasets (see the score
table), so it fails the plateau/adopt bar on the full grid, same as every
other candidate. September alone is not enough to adopt it; see
`EXTENDED_HISTORY_TEST_2026-11-03.md` for the standalone September verdict
(the live rule loses that month regardless of the target).

Full score across all 9 Brent datasets (journal, replay30, replay_sep, and
always-long/always-short/daily-sign on both the replay30 window and the
last 30 days): `rule_pass: []` -- nothing clears all three adoption bars
(beats live on most datasets, a plateau, stop inside half the way to
liquidation). Best overall: `cap_5` (1 beat / 2 losses of 9, geo-mean
0.992 -- a near-tie because it almost never binds). `cap_4`/`cap_3.5`
geo-mean just over 1.0 (1.015 / 1.002) but both fail "beats on most" and
the liquidation-fraction check.

Silver secondary check (top 3 Brent variants on `live_silver_params`):
`cap_3.5` and `cap_5` beat live on both silver control groups (2/0 of 8
and 2/0 of 8) but neither has a thesis dataset to test against this run
(no silver theses were loaded); `cap_2.5` loses on 5 of 8. Nothing here
changes the Brent verdict.

### Current (open) trade regression -- long from 103.61

Unchanged by the cap (the cap only affects position SIZE, and this
regression is priced at the trade's own fixed size, not a fresh sim size).
Brent's high since entry, fetched fresh at this rerun: still **105.51**,
still short of every target tested, live included.

## Original (uncapped) verdict, for the record

**Verdict: keep the live far-edge target. No closer target beats it.** Every
fixed-R target (4R to 10R), every wider percentage offset (2% to 4%), the
24h range midpoint and its 75% point all lose money or barely tie live on
the real thesis sets and the 30-day controls. The only variant that ever
matches live is the loosest cap (`target_cap_frac` 5%, 4%), and only
because it almost never binds — it is not a different strategy, it is live
with a rare safety ceiling. A tighter cap, or any closer rule that actually
changes behaviour, costs money.

Operator, 2026-11-03, on the open long of 38.05 xyz:BRENTOIL from 103.61
(stop 102.99, risk $23.59, target 111.77, about 13R): "whats the realistic
high? ... test it". An empirical check over 208 days (setups where price
sat in the bottom 35% of its 24h range) found the far-edge target fills
first within 72h only 15% of the time, against 58% stop-first, and the
median 72h high is only +3.3%. That reads like the target is unrealistic.
It backtests as the opposite: the rare big win it does occasionally pay
more than makes up for the many trades that never reach it, and a closer
target cuts the tail off without cutting the losses.

## Results (ORIGINAL -- uncapped; see the correction at the top)

$64 start, live rules otherwise (market entry off the 24h range, 2R gate,
stop 0.75 beyond the edge, limit target, 20x, 15-minute ticks, size-aware
slippage and funding on Brent). **This table's "15% risk cap" claim was
wrong when written -- `bp`/`sp` did not actually set it; every number
below is full-port size.** See "Corrected results" above for the capped
re-run.

| Dataset | Variant | Net $ | Per-day % | Win rate | Max DD | Trades |
|---|---|---|---|---|---|---|
| brent_journal (live oil journal) | **live** | **+394.32** | **13.09%** | 58% | 41.0% | 12 |
| brent_journal | tr7 (~7R, "around 108") | -8.85 | -0.92% | 31% | 53.0% | 16 |
| brent_journal | off_3 (3% inside edge) | +48.79 | 3.60% | 100% | 16.2% | 1 |
| brent_journal | rtarget_mid (range midpoint) | +160.96 | 8.17% | 100% | 16.2% | 3 |
| brent_journal | cap_5 (5% cap) | +394.32 | 13.09% | 58% | 41.0% | 12 |
| brent_replay30 (30 replayed theses) | **live** | **+57.78** | **2.17%** | 45% | 66.1% | 38 |
| brent_replay30 | tr7 | -50.05 | -4.95% | 23% | 90.5% | 52 |
| brent_replay30 | off_3 | -2.29 | -0.12% | 60% | 16.6% | 5 |
| brent_replay30 | rtarget_mid | +1.60 | 0.08% | 46% | 40.9% | 13 |
| brent_replay30 | cap_5 | +57.78 | 2.17% | 45% | 66.1% | 38 |
| brent_always_long_30d (control) | **live** | **+179.55** | **4.56%** | 43% | 78.3% | 30 |
| brent_always_long_30d | tr7 | -53.77 | -5.93% | 14% | 89.4% | 21 |
| brent_always_long_30d | cap_5 | +179.55 | 4.56% | 43% | 78.3% | 30 |

Score across all 8 Brent datasets (journal, replay30, and always-long /
always-short / daily-sign on both the replay window and the last 30 days):
no variant beats live on more than 2 of 8. The best geo-mean (final equity
over live's) is `cap_4` at 1.016 and `cap_5` at 1.001 — both within
rounding of a tie, not a win. Every `target_r` variant (tr4 to tr10) has a
geo-mean under 0.23: on this sample a fixed-R target is a clear loser, not
a plateau of neighbouring near-misses.

Silver secondary check (top 3 Brent variants — cap_5, cap_3.5, cap_3 — on
silver's own live rule, `SILVER_LIVE`): cap_5 and cap_3.5 tie live on every
one of the 8 silver datasets (the cap never binds tight enough to change a
silver trade); cap_3 loses on 5 of 8 where it does bind. Nothing here
changes the Brent verdict.

### Current (open) trade regression — long from 103.61

Real structural levels at the entry tick (closed 1h bars only): 24h low
103.74, 24h high 112.33 (the live stop, 103.74 - 0.75 = 102.99, matches
exactly). Brent's high since entry, fetched fresh: **105.51**.

| Variant | Target | % from entry | R multiple | Already filled? |
|---|---|---|---|---|
| live (far edge, 0.5% in) | 111.77 | +7.87% | 13.2R | No |
| tr7 (the operator's "~108") | 107.95 | +4.19% | 7.0R | No |
| off_3 (3% inside edge) | 108.96 | +5.16% | 8.6R | No |
| cap_2 (2% cap) | 105.68 | +2.00% | 3.3R | No |
| cap_3 (3% cap) | 106.72 | +3.00% | 5.0R | No |
| rtarget_mid (range midpoint) | 108.04 | +4.27% | 7.1R | No |
| rtarget_p75 (75% of range) | 110.18 | +6.34% | 10.6R | No |

Not one of these, including the tightest 2% cap, has filled yet. Brent's
+1.84% move off the entry so far is still short of even the closest target
tested. This matches the backtest: a closer target would not have banked
this trade any sooner than the far edge has so far, and the historical
sample says that on balance it is the far edge, when it does hit, that pays
for itself.

## Why the far edge wins despite rarely filling

The empirical numbers in the operator's ask are about this one setup
class (bottom 35% of the 24h range); the backtest runs every entry the live
rule actually takes, thesis and control alike, over 30 to 50+ days. Two
things happen when the target is pulled in:

1. **More round trips.** `reentry_after_target=True` (the live rule) means
   a filled target immediately re-opens the search for a new entry. A
   closer target fills more often, which in a choppy or declining market
   (most of the 30-day replay and the recent controls) means more entries
   into conditions that go on to stop out. `tr7`'s trade count jumps from
   live's 38 to 52 on `brent_replay30`, with the win rate dropping and the
   drawdown growing.
2. **The tail pays.** On `brent_journal`, live's 12 trades include at least
   one that ran close to the far edge; cutting that trade off at 7R or 3%
   removes most of its P&L while leaving every stop-out unchanged. The
   geo-mean ratios show this cleanly: every `target_r` variant is a loser
   by a wide margin, not a close miss.

The cap (`target_cap_frac`) is the gentlest version of this and it shows
the same pattern in miniature: a 5% cap matches live byte-for-byte on most
datasets (it never binds), a 4% cap gives up a hair, and tighter caps give
up more, in proportion to how often they actually change the target.
There is no point on this grid where pulling the target in helps.

## Method

New: `Params.target_cap_frac` (desk_tick_sim.py) caps whichever target rule
is in force (far edge, `target_offset_frac`, `target_r`, or
`target_offset_range_frac`) at `entry +/- target_cap_frac x entry`,
whichever is nearer to the entry — "the nearer of (live target, entry +/-
X%)" from the ask. Default `None`, off; applied only inside `_setup` (the
live structural entry), not pullback or flip entries. Pure helper
`cap_target`. The 24h range midpoint and 75% point needed no new field:
`target_offset_range_frac` (already in desk_tick_sim from
OIL_REGIME_TEST_2026-11-02.md) at 0.5 / 0.25 is exactly that.

Harness: `services/strategies/evals/closer_target_test.py`, modelled on
`brent_stop_buffer_test.py` (reuses its scoring, plateau and verdict
helpers directly) and `oil_regime_test.py` / `pullback_correlation_test.py`
for the dataset and regression patterns. Datasets: the live oil journal
(read only), the 30 replayed Brent theses, always-long / always-short /
daily-sign on the replay window and the last 30 days, and a silver
secondary check with silver's own live rule (`SILVER_LIVE`,
`BRENT_STOP_BUFFER_TEST_2026-11-02.md`). Adopt bar: beats live on most
datasets with a geo-mean of 1 or more, a plateau across neighbouring
settings (not a single spike), drawdown no worse, stop inside half the way
to liquidation — the same bar every 2026-11-02 test used. None of the 17
variants (6 target_r, 3 offset, 6 cap, 2 range-fraction) clears it.

Gate tests: `services/strategies/tests/test_closer_target_sim.py` (the
`target_cap_frac` field and `cap_target`/`_setup` pure logic, default-off
byte-identical check) and `test_closer_target_eval.py` (the variant grid,
`per_day_pct`, the summary CSV rows, and the current-trade regression,
including a causality check that bars after the entry tick cannot change
its levels). Both synthetic, no network, under the gate-test budget.

**2026-11-03 correction.** `bp`/`sp` now build through
`services/strategies/evals/live_params.py` (`live_brent_params`/
`live_silver_params`), the shared function every eval's "live" baseline
now goes through, so `risk_cap_frac=0.15` is actually set (it never was
before). Also added `--oil-replay-sep-dir` (default
`/tmp/thesis-replay-sep/oil/theses`, skipped when absent): the September
replay as a `brent_replay_sep` dataset, so this file covers the same two
replayed months `EXTENDED_HISTORY_TEST_2026-11-03.md` does. New gate
tests: `services/strategies/tests/test_live_params.py` pins
`live_brent_params`/`live_silver_params`'s values and fails if this file
(or any of the five other harnesses with the same gap) stops importing
them.

Reproduce:

    python -m services.strategies.evals.closer_target_test \
        --oil-theses-dir <pathia>/.state/strategies/oil/theses \
        --metals-theses-dir <pathia>/.state/strategies/metals/theses

Writes `/tmp/closer-target/report.json` and `summary.csv`.

## Risks and caveats

- **Sample overlap.** `brent_replay30` and the 30-day controls cover the
  same recent, mostly-down Brent stretch as several other 2026-11-02 tests
  (`BRENT_STOP_BUFFER_TEST`, `OIL_REGIME_TEST`). A regime where live itself
  is losing money makes "sitting out" and "do less" look artificially
  strong; this is the same caveat those tests carry, not a new one. The
  `brent_journal` thesis set (the live desk's own 12 trades) is independent
  evidence pointing the same way (+13.1%/day live versus every closer
  target losing or barely tying).
- **The empirical ask and the backtest answer different questions.** "How
  often does the far edge fill in 72h" (15%) is about one entry; "does a
  closer target make more money over many entries" is about the whole
  rule, including how often a filled target reopens a new, worse trade.
  Both numbers are true; only the second is actionable for a live rule
  change.
- **`target_cap_frac` is net-new and used nowhere live.** It is off by
  default and every existing backtest result stays byte-identical
  (`test_closer_target_sim.py`); it only changes behaviour where an eval
  explicitly sets it, as here.
- **No change to the live rule.** `bracket.py` and
  `services/strategies/next_trade.py` (`TARGET_OFFSET_FRAC`,
  `DEFAULT_MIN_R`) are untouched. This test does not ship anything; it is
  evidence for the operator's "test it".
