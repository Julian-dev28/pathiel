# Silver strategy test, 2026-11-04

## Verdict

**Do not adopt anything yet. The live silver journal is too small to
judge.** No variant clears the adopt bar (beat live on BOTH thesis
datasets). The closest candidate, `combo_best2` (the breakdown-continuation
entry plus leverage-fit, see below), fails only one check: it ties live on
the 3-day live journal (one trade, identical in every variant) and wins big
on the 30-day replay (+106% more net return than live). The journal has
exactly one live trade in this window, so "ties" there is not evidence
against the idea, it is an empty sample. Recommend watching `combo_best2`
and `breakout_24h_measured` as the two live journal grows past a handful of
trades; do not ship on this data alone.

## The missed-opportunity rate (the size of the prize)

How often was the silver thesis direction right over the next 24h/72h
while the live rule's own backtest took no trade during that call's
window:

| Dataset | Horizon | Calls | Right | Missed (right, no trade) | Missed / right |
|---|---|---|---|---|---|
| Live journal (11 calls) | 24h | 11 | 7 | 7 | 100% |
| Live journal | 72h | 11 | 9 | 8 | 89% |
| 30-day replay (28 calls) | 24h | 28 | 19 | 18 | 95% |
| 30-day replay | 72h | 28 | 23 | 17 | 74% |

The thesis was right 7 to 9 times out of 11 and traded essentially none of
them in the live journal window; right 19 to 23 times out of 28 in the
replay and missed 17 to 18 of those. This is the prize: the live rule's
"never chase below the thesis entry, 2R to the far 24h edge" refusal is
leaving most of a correct short thesis on the table, exactly as the
operator described (silver fell from about 51.3 to 49.6 while the desk sat
flat).

## Live vs the best 3 variants

Net $ from a $64 start, max drawdown %, trade count. Live rules otherwise
(`live_params.live_silver_params`: per-tick ATR stop with its 0.8% floor,
15% risk cap, 20x, market entry off the 24h range, 2R gate).

| Variant | Journal net$ (dd%, n) | Replay net$ (dd%, n) |
|---|---|---|
| live | -$9.92 (21.4, 1) | $132.92 (26.9, 12) |
| lev_fit | -$9.92 (21.4, 1) | $135.62 (26.9, 13) |
| breakout_24h_measured | -$9.92 (21.4, 1) | $269.33 (26.9, 15) |
| **combo_best2** | **-$9.92 (21.4, 1)** | **$273.91 (26.9, 16)** |

`combo_best2` = `breakout_24h_measured` (the best single-concern variant on
replay) + `lev_fit` (the oil final test's winner), merged -- the two
highest-scoring variants from this run's own sweep, decided by the sweep
itself, not picked by hand.

## Variants tested

Every one built on `live_params.live_silver_params()`.

| # | Variant | What it does | Result |
|---|---|---|---|
| 1 | `lev_fit` | `entry_fit_leverage` (20/15/10/8/5x): highest leverage whose stop still clears half the liquidation distance | Small, consistent edge (replay +2% net vs live); never worse |
| 2 | `ai_levels` | Try the thesis's own entry/stop/first target before the structural rule | Loses on the journal (-34% vs -15.5%), wins big on replay (+234% vs +208%) -- the same "wins the big sample, loses the small one" pattern oil's own AI-levels test found; not adopted on oil either |
| 4a | `entry_lookback_8h` | `entry_lookback_h=8` alone | Confirms it does nothing useful on its own: the market-entry rule's chase/break checks never read it. Loses on journal, roughly flat on replay |
| 4b | `pullback_8h` | `pullback_entry="a1"`, `pb_struct_h=8`, `pb_target="thesis"`: on a chase/min_r refusal, buy/sell the mid with a stop beyond the last 8h's structure | Loses on BOTH thesis datasets and all 3 controls. Rejected on oil for the same reason; silver confirms it |
| 3 | `breakout_8h/24h_measured`, `breakout_8h/24h_thesis`, `breakout_24h_r2` | **New**: `pullback_entry="a5"` -- on a chase/min_r refusal, sell/buy the moment a CLOSED 1h bar breaks the prior 8h/24h low/high, stop beyond the last 4h's extreme, target the broken level minus a measured move, the thesis's own target, or a fixed 2R | `breakout_24h_measured` is the best single-concern variant tested: ties journal, +103% net over live on replay, same drawdown as live. The 8h-lookback versions are weaker and inconsistent (one loses on journal) -- no plateau across the 8h/24h grid |
| 5 | `min_r_1.5` | Relax the 2R gate to 1.5R | Loses on both thesis datasets (more entries, lower average quality) |
| 6 | `combo_best2` | `breakout_24h_measured` + `lev_fit`, picked by this run's own scores | Best overall: ties journal, +106% net over live on replay, beats 2 of 3 controls, same drawdown as live, stop never past 46.9% of the way to liquidation (gate: 50%) |

## Adopt bar and why nothing passes

Beat live on BOTH thesis datasets (journal, replay -- "most of 2" is both),
beat live on most controls (2 of 3), max drawdown no worse than live
(within 2 points), a plateau across the breakout lookback grid (8h vs 24h)
where one applies, and the stop inside half the liquidation distance at
the trade's own leverage.

| Variant | Beats thesis (of 2) | Beats controls (of 3) | Max DD | Max stop/liq frac | Plateau | Adopt |
|---|---|---|---|---|---|---|
| lev_fit | 1 | 0 | 97.0% | 0.406 | yes | no |
| ai_levels | 1 | 0 | 97.0% | 0.501 | yes | no (liq gate) |
| breakout_24h_measured | 1 | 0 | 97.4% | 0.498 | no | no (no plateau) |
| breakout_24h_r2 | 1 | 0 | 97.4% | 0.506 | yes | no (liq gate, 0.6%) |
| min_r_1.5 | 0 | 0 | 96.9% | 0.498 | yes | no |
| **combo_best2** | **1** | **2** | **97.4%** | **0.469** | **yes** | **no (one check: thesis)** |

The drawdown figures above (96.9-97.4%) come from the `always_long` control
over the last 30 days, not the thesis datasets -- silver fell hard enough
in that window that ANY always-long account, live rule included, loses
almost everything. It is identical across variants because none of them
change how badly a wrong-direction control performs in a one-way market;
it is not a sign of a bug (checked: `liquidated: false` in every case, a
real compounding drawdown from repeated 15%-of-equity stop-outs, not a
margin call).

`combo_best2` clears every single other check and only fails "beats live
on both thesis datasets" because it TIES live on the journal (identical
single trade) rather than losing to it. With 11 calls and exactly 1 live
trade in this window, the journal does not yet have the sample size to
tell "no different" from "no better" apart. This is the honest reading:
not a disqualifying loss, a data gap.

## Method

One pinned candle snapshot (`services.strategies.backtest.snapshot`):
xyz:SILVER 15m/1h/1d candles and hourly funding, fetched into the SAME
stamped directory the oil final test uses
(`~/Documents/code/pathiel-replays/candles/20261103T224029Z/`, merge-
appended by coin so both coins' files sit side by side). No eval in this
run touched the network.

| Dataset | Requested start | Effective start | End |
|---|---|---|---|
| Snapshot (15m) | -- | 2026-09-12 17:45Z | 2026-11-03 23:15Z |
| Snapshot (1h / 1d / funding) | -- | 2026-08-21 00:00Z | 2026-11-04 00:00Z |
| Journal | 2026-10-31 08:38Z | unaffected | 2026-11-03 23:15Z |
| 30-day replay | 2026-10-01 16:00Z | unaffected | 2026-10-31 16:00Z |

The requested and effective starts match for both datasets: the snapshot's
15m history reaches back far enough to cover both windows whole. No
clamping needed.

Datasets: the live metals journal (READ ONLY,
`~/Documents/code/pathia/.state/strategies/metals/theses`, 12 theses --
`stop_cap_test.same_market` drops the one GOLD thesis the desk logged
before it moved to silver, leaving 11), the 30 replayed silver theses
(`~/Documents/code/pathiel-replays/metals-silver30/theses`, durable copy,
10-01 to 10-30), and always_long/always_short/daily_sign over the
snapshot's last 30 days. Venue max leverage for xyz:SILVER fetched live:
25x (the desk trades up to 20x, per `scouting_params`).

Variants, every one built on `live_params.live_silver_params()` (the real
live rule: per-tick ATR stop, 0.8% floor, 15% risk cap): `live`, `lev_fit`
(`entry_fit_leverage=(20,15,10,8,5)`), `ai_levels`, `entry_lookback_8h`,
`pullback_8h` (`pullback_entry="a1"`, `pb_struct_h=8`, `pb_target=
"thesis"`), `breakout_8h_measured`/`breakout_24h_measured`/
`breakout_8h_thesis`/`breakout_24h_thesis` (`pullback_entry="a5"`, new this
test -- see below), `breakout_24h_r2` (`pb_target_r=2.0`), `min_r_1.5`, and
`combo_best2` (the top 2 by this run's own thesis-dataset score, merged --
decided by the run, reported in `report.json`'s `top2_for_combo`).

Adopt bar: beat live on BOTH thesis datasets, beat live on most controls
(2 of 3), max drawdown no worse than live (within 2 points), a plateau
where a grid applies (the breakout lookback 8h vs 24h), and the stop
inside half the liquidation distance at the trade's own leverage
(`final_strategy_test.corrected_max_stop_liq_frac` for the
`entry_fit_leverage` variants, same fix the oil final test uses).

This bar borrows two principles from `research/EVIDENCE_DOCTRINE.md`
(the `research/` repo's stricter statistical standard, built for a
different infra -- matched-null draws, Bonferroni across cells -- that
this desk-tick harness does not have): never "beats zero" (the
always_long/short/daily_sign controls stand in for the doctrine's matched
null -- a variant must beat live on the account's OWN no-thesis benchmark,
not just drift upward with the market), and both splits must agree (the
doctrine's "both OOS halves positive" is this test's "beats both thesis
datasets" -- which is exactly the check `combo_best2` ties rather than
fails, on a journal too small to split any further).

### New: the breakdown/breakout continuation entry (`pullback_entry="a5"`)

Added to `desk_tick_sim.Params`/`pullback_setup` this test, additive, off
by default (`PULLBACK_MODES` gained `"a5"`, `PB_TARGETS` gained
`"measured"`). On a live refusal (chase or min_r -- exactly the "never
chase below the thesis entry, needs 2R to the far edge" refusal that left
silver's short thesis untraded): sell a short thesis the moment a CLOSED
1h bar's close breaks the prior `pb_breakout_lookback_h` hours' low (mirror
for a long and the prior high). Stop beyond the last `pb_breakout_stop_h`
hours' extreme, using the SAME buffer formula every other mode already
uses (the live ATR rule with its floor on silver). Target: the existing
`pb_target` "far"/"thesis" rule, the new "measured" (the broken level
projected out by the height of the range that broke), or a fixed R via the
new `pb_target_r` field (pullback-only, so a fixed-R test here cannot also
change what the live rule's OWN entries target when they still fire --
unlike the pre-existing global `target_r`, which `pb_target_r` takes
priority over when both are set).

Code: `services/strategies/backtest/desk_tick_sim.py` (the new `a5` mode,
`pb_breakout_lookback_h`, `pb_breakout_stop_h`, `pb_target_r` fields, every
default byte-identical -- gate-tested in `test_pullback_sim.py`, 8 new
tests), `services/strategies/evals/silver_strategy_test.py` (this eval;
writes `/tmp/silver-strategy/report.json` and `summary.csv`), gate-tested
in `test_silver_strategy_test_eval.py` (17 tests, synthetic data, no
network). Re-run:

```
python -m services.strategies.backtest.snapshot --coin xyz:SILVER \
    --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261103T224029Z   # only if a fresher snapshot is wanted
python -m services.strategies.evals.silver_strategy_test \
    --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261103T224029Z
```
