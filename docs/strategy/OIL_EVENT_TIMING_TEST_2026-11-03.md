# Oil scheduled-event timing test, 2026-11-03

**Verdict: no live change. Do not adopt an event filter yet.** The EIA
report blackout (no new entries 1-2h either side of the Wednesday/holiday-
shifted-Thursday report) beats the live rule on 5 of 9 datasets, ties
exactly on the other 4 (free -- it never actually changes a trade there),
and never loses. That clears two of the three adoption bars. It is blocked
only by the stop-vs-liquidation check, and that check's 0.506 ceiling comes
from a trade the live rule ALREADY takes on one control dataset
(`always_short_replay`) -- the filter never touches stop or target
geometry, so it cannot fix or worsen that number. This is a near-miss, not
a clean pass, and it is flagged below for a decision rather than adopted
on the strength of two out of three legs. The API report filter does not
help (it loses on 4 of 9 datasets) and OPEC+ cannot be judged from only 2
events in the window. Session structure shows the expected, unsurprising
pattern (quieter on weekends, more active in the NYMEX day session) and
gives no new filter idea.

Operator, 2026-11-03: "sure" to testing whether Brent behaves differently
around scheduled events, since the earlier crypto-style "trade only 07:00
to 21:00 UTC" session idea was rejected on 2026-10-30
(`OIL_STRATEGY.md`) for a market that has none of oil's real calendar.

**Mid-run correction:** the default October replay directory
(`/tmp/thesis-replay-30b/oil/theses`) was actively being regenerated while
this test ran (files appearing mid-write, /tmp having been swept earlier).
Per the operator's instruction, this run does NOT use it -- October is
marked "unavailable (regenerating)" throughout, the Sept+Oct chain is
`chain_runs` over the September leg alone (labelled as a stand-in, not a
real two-month chain), and the CLI's `--oil-replay-dir` already takes a
path so a rerun with the regenerated set is one flag away.

## Event study: EIA, API, OPEC, sessions vs. a matched null

Brent (`xyz:BRENTOIL`) 1h/15m bars, 2026-09-12 to 2026-11-03 (52 days --
the venue's own real history for this HIP-3 market starts there; the
September replay's theses from 08-31 predate real candles and are not
part of the event study). Null = the same minute-of-day, every other
non-adjacent day in the window (`build_null_anchors`). p-values are
`services.review.stats.permutation_p`, 2000 draws
(`research/EVIDENCE_DOCTRINE.md`'s floor). "Stopped" = the live stop
geometry (0.75 beyond the 24h edge) would have traded through in the
window.

### EIA weekly petroleum status report (n=7, -1h/+4h window)

| Metric | Event | Null | p |
|---|---|---|---|
| Move before (%) | 0.56 | 0.35 | 0.17 |
| Move after (%) | 0.63 | 0.58 | 0.84 |
| Range before (%) | 0.93 | 0.72 | 0.32 |
| Range after (%) | 1.59 | 1.31 | 0.51 |
| Reversal rate | 71% | 47% | 0.25 |
| Long stop-out rate | 14% | 15% | 1.00 |
| Short stop-out rate | 29% | 3% | 0.050 |

Nothing clears p<0.05 except the short stop-out rate, and that sits
exactly at the line with only 7 events -- too little power to call it
real, and it does not survive a Bonferroni correction over the 7 metrics
checked here (threshold would be ~0.007). Both chronological halves agree
in sign on every metric except move_after_pct and range_after_pct (see
`report.json`'s `half1`/`half2`), which is itself a reason for caution,
not confirmation.

### API inventory report (n=8, -1h/+4h window)

| Metric | Event | Null | p |
|---|---|---|---|
| Move before (%) | 0.13 | 0.12 | 0.85 |
| Move after (%) | 0.29 | 0.74 | 0.073 |
| Range before (%) | 0.43 | 0.38 | 0.55 |
| Range after (%) | 0.75 | 1.47 | 0.073 |
| Reversal rate | 38% | 53% | 0.50 |
| Long stop-out rate | 0% | 10% | 0.61 |
| Short stop-out rate | 0% | 18% | 0.36 |

If anything, Brent moves and ranges LESS after the API report than an
ordinary hour (p=0.073, not significant, but consistently in that
direction on both move and range) -- the opposite of "the event moves
price". n=8 is small; take this as a lean, not a finding.

### OPEC+ (n=2, day resolution -- no sourced release minute)

| Metric | Event | Null (47 days) | p |
|---|---|---|---|
| Day move (%) | 0.37 | 1.27 | 0.30 |
| Day range (%) | 1.10 | 3.46 | 0.042 |

Two events in the window (2026-09-06, 2026-10-04 -- see `oil_calendar.
OPEC_EVENTS` for the citations). The range result is nominally p<0.05 but
with n=2 it is not a result, it is an artifact of a small sample landing
on two quiet days; do not act on it. More OPEC+ dates would need more
data window, not more code.

### Session structure (mean absolute 1h return)

| Session | n bars | Mean abs. 1h return | Rest of data | p |
|---|---|---|---|---|
| CME/ICE weekend (`weekend_closed`) | 501 | 0.116% | 0.367% | 0.0005 |
| NYMEX/ICE day session (09:00-14:30 ET) | 300 | 0.500% | 0.247% | 0.0005 |
| Asia hours (01:00-07:00 UTC) | 300 | 0.283% | 0.294% | 0.61 |
| Outside all of the above | 579 | 0.341% | 0.266% | 0.0005 |

Exactly what anyone would expect: quiet over the weekend, livelier in the
NYMEX day session, nothing distinctive about Asia hours. This is a sanity
check that the data and the session definitions behave sensibly, not a
new edge -- no filter is proposed from this alone.

## Live-desk filter backtest

Baseline: `live_params.live_brent_params()` (market entry off the 24h
range, stop 0.75 beyond it, limit target 0.5% inside the far edge, 2R
gate, 20x, 15% risk cap, 15-minute ticks). New field:
`desk_tick_sim.Params.event_blackout_windows`, off by default, golden-
identical when unset (`test_event_blackout_sim.py`).

| Variant | Beats live | Loses to live | Geo-mean final/live | Plateau | Stop <= half to liq |
|---|---|---|---|---|---|
| `eia_2_2` (no entry -2h/+2h of EIA) | 5 of 9 | 0 | 1.282 | yes | **no (0.506)** |
| `eia_1_1` | 5 of 9 | 0 | 1.164 | yes | no (0.506) |
| `eia_0.5_0.5` | 5 of 9 | 0 | 1.078 | yes | no (0.506) |
| `wait_eia_1h` (no entry in the 1h after EIA) | 4 of 9 | 0 | 1.121 | n/a (singleton) | no (0.506) |
| `both_1_1` (EIA + API, -1h/+1h) | 3 of 9 | 2 | 1.067 | no | no (0.506) |
| `api_2_2` | 3 of 9 | 3 | 1.016 | no | no (0.506) |
| `api_0.5_0.5` | 1 of 9 | 1 | 0.994 | no | no (0.506) |
| `api_1_1` | 0 of 9 | 4 | 0.916 | no | no (0.506) |
| `wait_api_1h` | 0 of 9 | 0 | 1.000 | n/a (singleton) | no (0.506) |

`rule_pass`: **none.** `0.506` is not new: it is `live`'s own worst stop
(`always_short_replay`), unchanged by every variant above, because the
filter only ever gates WHEN a new entry may open, never the stop or
target distance. No filter tested here can move that number up or down.

Per-dataset detail for the best candidate (`eia_2_2`, net $ = final
equity - $64 start except the journal, which starts from its own live
equity):

| Dataset | Live net | `eia_2_2` net | Live max DD | `eia_2_2` max DD | Trades live / var | Ticks refused |
|---|---|---|---|---|---|---|
| oil_journal (live journal, 14 trades) | +117.53 | +117.53 (tie) | 56.1% | 56.1% | 14 / 14 | 32 |
| oil_replay_sep = chain (Sept alone) | -34.45 | -22.47 | 80.8% | 73.0% | 18 / 16 | 40 |
| always_long_30d | +65.67 | +65.67 (tie) | 80.0% | 80.0% | 32 / 32 | 51 |
| always_short_30d | -47.10 | -47.10 (tie) | 86.8% | 86.8% | 22 / 22 | 16 |
| daily_sign_30d | +87.18 | +87.18 (tie) | 80.0% | 80.0% | 29 / 29 | 55 |
| always_long_replay | -14.49 | **+18.34** | 79.8% | 71.2% | 15 / 12 | 26 |
| always_short_replay | +16.19 | **+51.81** | 64.0% | 49.5% | 18 / 16 | 29 |
| daily_sign_replay | -54.33 | -44.90 | 86.7% | 78.1% | 19 / 15 | 38 |

Four datasets tie byte-for-byte despite 16 to 55 ticks refused each: every
refused tick's signal either did not pass the 2R/structural gates anyway,
or the delayed entry (once the window cleared) landed on the same setup
the baseline would have taken. The other five never lose money or add
drawdown; three turn a loser into less of a loser or into a winner
outright. **The filter is free everywhere it doesn't bind, and helpful
everywhere it does, on every dataset tested** -- which is exactly the
"beats live on most datasets" and "no worse drawdown" legs of the adopt
rule. It is the third leg, the liquidation check, that is blocked, and
only by a number the filter cannot touch.

`api_*` does not share this pattern (`api_1_1` loses on 4 of 9) and is not
recommended in any width tested. `wait_eia_1h`/`wait_api_1h` (wait for the
first closed bar after the report, rather than a symmetric window) were
each tested alone, not as a swept family, so they are reported for
information and are not held to the plateau check.

## What Julien should decide

`eia_1_1`/`eia_2_2` beats live on every dataset it touches and never
loses, but fails the letter of the "stop inside half the way to
liquidation" rule only because of a pre-existing fact about one control
dataset's worst trade that has nothing to do with event timing. Options:
1. Leave it alone (current default: do nothing, which this report does).
2. Adopt `eia_1_1` or `eia_2_2` as a live filter once the liquidation
   check is re-run against ONLY the datasets/trades it can actually
   change (i.e. drop `always_short_replay`'s unrelated 0.506 from the
   gate for this specific candidate), which this report does not do on
   its own authority.
3. Wait for the regenerated October replay and re-run
   (`--oil-replay-dir <durable path>`) before deciding either way.

No live code or config changes here either way -- `event_blackout_windows`
defaults to `None` and nothing in `pathiel/agents/bracket.py`,
`services/strategies/next_trade.py` or any agent/skill was touched.

## Method

- `services/strategies/backtest/oil_calendar.py`: pure timestamp module.
  EIA Wednesday 10:30 ET normal / Thursday 12:00 ET holiday-shifted
  (`us_federal_holidays` computes the US federal calendar by rule; the
  shift rule -- Monday OR Wednesday of that week is a federal holiday --
  reproduces all six of the EIA's own sourced 2026 shifts exactly, see
  the module docstring and `test_oil_calendar.py`). API Tuesday 16:30 ET,
  no holiday rule. OPEC+ hardcoded, sourced list (`OPEC_EVENTS`, each
  entry with its own citation comment, checked by WebSearch 2026-11-04).
  Session structure: NYMEX/ICE day session 09:00-14:30 New York time,
  Asia hours 01:00-07:00 UTC (Singapore/Tokyo, no DST), CME/ICE weekend
  re-exported from `desk_tick_sim.weekend_closed`.
- `services/strategies/evals/oil_event_timing_test.py`: the event study
  (`event_study`, `eia_api_study`, `opec_study`, `session_study`) and the
  filter backtest (`build_filter_variants` + the existing
  `brent_stop_buffer_test.run_dataset`/`score_vs_live`/`verdict` machinery
  and `extended_history_test.chain_runs`/`summarize_chain` for the chain
  leg). `python -m services.strategies.evals.oil_event_timing_test` writes
  `/tmp/oil-events/report.json` and `summary.csv`.
- New field: `desk_tick_sim.Params.event_blackout_windows` (a tuple of
  `[start_ms, end_ms)` UTC windows; a new entry refused while the tick
  falls inside one, never a flip, never an open position), plus
  `in_event_blackout` and a small hook in `simulate_steps`. Off by
  default, additive, placed at the end of `Params` per the other
  concurrent worktrees' edits to the same file.
- Gate tests: `test_oil_calendar.py` (20 tests: the sourced-schedule
  match, DST spring-forward/fall-back edge cases at the 2026-03-08 and
  2026-11-01 transitions, the API/OPEC/session/blackout-window pure
  functions), `test_event_blackout_sim.py` (9 tests: the simulator hook,
  golden-off, boundary conditions, never closes an open position),
  `test_oil_event_timing_eval.py` (17 tests: the eval's own pure helpers).
  Full `services/strategies` suite: 1081 passed, 0 failed, 4 deselected
  (unchanged deselection from before this change).

## Caveats

- EIA n=7, API n=8, OPEC n=2 in this 52-day window: low statistical
  power everywhere except the session-structure check (300-579 bars per
  bucket). None of the headline move/range comparisons clear a corrected
  significance bar; treat the event study as "no detectable edge found
  yet", not "proven absent".
- The API holiday rule is NOT modelled (the module docstring says why);
  none of this window's API Tuesdays fell on a federal holiday, so it
  does not matter here, but a future window spanning one should check by
  hand.
- OPEC+ is day-level only -- no source gives a release minute, so it
  cannot be tested at the same resolution as EIA/API, and 2 events is not
  enough to say anything about it either way.
- The October replay was mid-regeneration during this run and was
  excluded; re-run with `--oil-replay-dir <durable path>` once Julien
  hands over the regenerated set, to get the real two-month chain instead
  of the Sept-alone stand-in used here.
- `max_stop_liq_frac` = 0.506 across every variant comes from
  `always_short_replay`'s own worst live trade, unrelated to event
  timing; see "What Julien should decide" above.
