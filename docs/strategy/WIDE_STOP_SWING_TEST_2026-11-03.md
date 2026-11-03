# Wide-stop swing test, 2026-11-03/04

## Verdict

Not adopted yet, but the leverage-fit idea is the one real lead here: **fit
the leverage to the stop (highest of 20/15/10/8/5x that still clears the
20x line) beats live on both thesis datasets we have** (journal and the
September replay) and on the chained run, with a smaller worst day. The
"fewer entries" idea (`entries_reclaim`: at most one re-entry after a
stop, gated on a 1h bar closing back inside the range) also wins big, but
only on one of the two thesis datasets. Nothing clears the full adopt bar
this run: with only two thesis datasets available (the October replay is
**unavailable -- regenerating** at the time of this run, see Method), the
plateau check can't be satisfied, and `lev_fit`'s realized fills touch
0.506 of the liquidation distance in one control series (3 thousandths
over the 0.500 line, from slippage on the fill, not from an unsafe gate
decision -- see Method). Re-run with `--oil-replay-dir` pointed at the
regenerated October set once it lands, and keep an eye on that one
control series before shipping `lev_fit` live.

The wider-stop-alone ideas (`stop48_*`, `stop24_*`) do not hold up: they
lose on the thesis datasets and, without the leverage fit, push the stop
past half the liquidation distance in several datasets -- exactly the
risk fixed leverage with a wider stop was always going to run into.

## The table: live vs. the best 3 variants

| Dataset | Variant | Net $ (from $64) | Max DD | Trades | Stops |
|---|---|---:|---:|---:|---:|
| journal | live | $181.53 | 56.1% | 14 | 6 |
| journal | lev_fit | **$219.82** | 56.4% | 14 | 6 |
| journal | entries_reclaim | $176.63 | 46.4% | 11 | 4 |
| journal | entries_one | $90.21 | 46.4% | 8 | 3 |
| Sept replay | live | $29.55 | 80.8% | 18 | 11 |
| Sept replay | lev_fit | **$42.29** | 80.8% | 20 | 11 |
| Sept replay | entries_reclaim | **$92.09** | 43.1% | 14 | 7 |
| Sept replay | entries_one | $84.33 | 43.1% | 13 | 7 |
| chain (Sept + journal, Oct skipped*) | live | $83.83 | 80.8% | 32 | - |
| chain (Sept + journal, Oct skipped*) | lev_fit | **$145.25** | 80.8% | 34 | - |
| chain (Sept + journal, Oct skipped*) | entries_reclaim | **$254.15** | 54.3% | 25 | - |
| chain (Sept + journal, Oct skipped*) | entries_one | $118.87 | 46.4% | 21 | - |

\* October's own replay is unavailable (regenerating) at the time of this
run (see Method), so the chain here is Sept + journal only, in
chronological order -- the Sept+Oct chain the brief asked for once
October is back. Live loses money outright on the Sept replay alone
(-54% of $64) -- the same loss the operator's evidence flagged. `lev_fit`
cuts that loss by about 40%; `entries_reclaim` turns it positive.

## Today's regression: 2026-11-03

The live oil journal's thesis of that day (long, entry 104-105.8 across
five rewrites), replayed on real Brent 15m/1h candles for 2026-11-03 UTC
only, same thesis, same price, each rule's own entries:

| Variant | Net $ (from $64) | Return | Trades | Stops |
|---|---:|---:|---:|---:|
| live | $45.68 | -28.6% | 2 | 2 |
| lev_fit | **$55.32** | **-13.6%** | 2 | 2 |
| entries_reclaim | $45.68 | -28.6% | 2 | 2 |
| entries_one | $45.68 | -28.6% | 2 | 2 |

Two stops land inside the window either way (a third long is still open
past the window's end under every variant, matching the operator's "the
third is up" -- `open_position_at_end` is set for all four). `lev_fit`
halves the day's loss: same two stops, smaller size each time because the
fitted leverage (not 20x) sized them. `entries_reclaim`/`entries_one` make
no difference THIS day because the two stops that hit both happened under
**separate thesis rewrites** (the desk wrote five new theses that day), so
"fewer entries per thesis" never had a second entry under the same call
to block. This is the sim's day-level replay off the journaled thesis and
real candles, not a byte-for-byte reconstruction of the exact live fills
(the live desk's three fills in 30 minutes came from its own order timing,
which this causal per-tick replay does not reproduce tick-for-tick); it is
the same rule, the same thesis, the same price action.

## Method

### The grid

No new field was needed for the stop or the target -- `entry_lookback_h`
x `stop_buffer_atr` already gives "beyond the 48h/24h edge plus N x
ATR14_1h", and `target_lookback_h` x `target_offset_frac` already gives
"the far 48h edge, 0.5% inside it" on top of the live 24h/0.5% target.
Two new mechanisms did need fields, both in `desk_tick_sim.Params`,
default off, golden-identical (`test_wide_stop_swing_sim.py`):

- **`entry_fit_leverage`** (a tuple of candidate leverages): the main
  structural entry trades at the highest candidate whose
  `bracket.check_leverage` gate clears its stop, instead of always 20x.
  `_fit_leverage_setup` tries them highest to lowest; the entry/stop/
  target themselves never depend on leverage, so only a "leverage"
  refusal tries the next candidate -- any other refusal (min-R, chase)
  returns at once.
- **`reentry_after_stop`** / **`max_stop_reentries`** / **`reentry_needs_
  1h_reclaim`**: `reentry_after_stop=False` blocks any new entry under
  the SAME call (`Direction.regime_id`, a thesis's own `created_at`)
  after a stop, until a new call starts ("one entry per thesis",
  `entries_one`). `max_stop_reentries=1` instead caps it at one re-entry
  per call; `reentry_needs_1h_reclaim=True` additionally gates that one
  re-entry on a 1h bar that closes STRICTLY AFTER the stop, back inside
  the 24h range the stop broke out of (snapshotted at the stop) --
  `entries_reclaim` is both together.

Grid (one axis at a time, then 4 combined candidates that each pick one
setting per axis -- kept focused per the brief, not a full sweep):
`stop48_atr0.5`, `stop48_atr1.0`, `stop24_atr2.0`, `stop24_atr3.0`,
`lev_fit`, `entries_one`, `entries_reclaim`, `target48`,
`combo_48_0.5_one`, `combo_48_1.0_reclaim`, `combo_24_2.0_one`,
`combo_24_3.0_reclaim`.

### Datasets

Built on `live_params.live_brent_params()` (the shared live baseline: $64,
20x, market entry off the 24h range, 2R gate, 0.75 stop, 0.5% target
offset, limit target, the 15% risk cap, size-aware slippage and funding).

- `brent_journal`: the live oil journal, READ ONLY, 25 theses,
  2026-10-18 through 2026-11-03 22:00Z (this run's data end).
- `brent_replay_sep`: the September replay, 30 theses, 2026-08-31 to
  2026-09-29.
- **`brent_replay30` (the October replay) is unavailable -- regenerating**:
  `/tmp/thesis-replay-30b/oil/theses` exists but is empty (and so are the
  sibling `/tmp/thesis-replay-30` and `/tmp/thesis-replay-fix`) -- macOS's
  periodic `/tmp` cleanup removed the files around 16:00Z. Per the hard
  rule against LLM calls in this task, it was not regenerated here; the
  operator is regenerating it at a durable path. The script already
  skips an empty/missing dataset gracefully (same pattern every other
  eval in this family uses, `if not theses: ... skipped`) rather than
  treating it as a zero/negative result, and every theses directory is a
  CLI flag (`--oil-theses-dir`, `--oil-replay-dir`, `--oil-replay-sep-dir`,
  `--metals-theses-dir`, `--silver-theses-dir`) -- re-run with
  `--oil-replay-dir <the new durable path>` once it lands, no code
  change needed.
- `brent_<always_long|always_short|daily_sign>_replay`: controls on the
  September replay's window.
- `brent_<control>_30d`: the same controls on the last 30 days of fresh
  data.
- `brent_chain`: September replay then the live journal, chained
  oldest-first from one $64 start (`extended_history_test.chain_runs`).
- `silver_<lev_fit, entries_reclaim>`: the top 2 Brent variants by the
  thesis-dataset score, re-run on silver's own live rule
  (`live_silver_params`) and its own datasets (replay, live metals
  journal, controls), as a secondary check. `entries_reclaim` loses more
  than it wins on silver (2 beats, 5 loses of 8); `lev_fit` is flat (1
  beat, 0 loses, geo-mean ~1.0 -- silver's stops are already inside the
  20x line most of the time, so there's little for the fit to do).

### The leverage-aware liquidation check

`brent_stop_buffer_test.risk_numbers` (the shared `summarize`'s
`max_stop_liq_frac`) always checks a trade's stop against `Params.
leverage`, the one fixed default -- correct for every variant except
`entry_fit_leverage`, where different trades open at different
leverages on purpose. `pullback_correlation_test.py` hit this exact gap
for its own per-trade leverage (`pb_fit_leverage`) and fixed it the same
way this file does: re-check each trade at its OWN leverage
(`Trade.leverage`), not the Params default (`max_liq_frac`, `run_dataset`,
`summarize_chain` in `wide_stop_swing_test.py`; `brent_stop_buffer_test.py`
itself is untouched).

Even after that fix, `lev_fit` touches 0.506 on `brent_always_short_
replay` -- 0.006 over the 0.500 line. This is not an unsafe GATE decision:
every entry's leverage was chosen so the price the gate checked (the
pre-slippage mid) cleared 0.500. The stored trade's `entry_px` is the
post-slippage FILL (0.03% worse on a market order), and re-checking the
realized fill against the same leverage nudges the ratio a few thousandths
over. `brent_stop_buffer_test.risk_numbers` has always measured every
OTHER variant off the fill price too (it is the only price a closed Trade
carries); `entry_fit_leverage` is simply the first variant where that
gap-vs-gate difference lands close enough to 0.5 to cross it. Worth a
one-line note on `lev_fit`, not a reason to call the gate itself unsafe.

### Adopt bar

Beats live on most of the available thesis datasets (journal, Sept;
October would join these when present) AND on the chain; a plateau
across neighbouring settings (a lone spike does not count -- with only
two thesis datasets and 12 variants split 1-beat/1-lose almost everywhere,
nothing plateaus this run); max drawdown no worse than live's worst; the
stop inside half the way to liquidation. None of the 12 variants clear
all four this run -- `lev_fit` and `entries_reclaim` are the two that get
closest (both beat live on the chain and on at least one of the two
thesis sets; `lev_fit` beats BOTH).

### Reproduce

```
python -m services.strategies.evals.wide_stop_swing_test \
    --oil-theses-dir <pathia>/.state/strategies/oil/theses \
    --metals-theses-dir <pathia>/.state/strategies/metals/theses
```

Writes `/tmp/wide-swing/report.json` and `/tmp/wide-swing/summary.csv`.
Reads candles through the eval disk cache and the venue's public `/info`;
never touches the live account, the live config or the journal. No LLM
calls.
