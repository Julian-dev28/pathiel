# services/strategies/backtest

Pure, offline replay of the desk-tick loop's mechanics — entry, stop,
target, the R gate (`Params.min_r`, 1.5 by default; the live desk runs
2R since 2026-10-30, `desk_tick_sim.live_params()`), the 20x leverage rule, full-port sizing, one
resting top-up, resting-order hygiene, fees and funding — against real
Hyperliquid candles. Built from
`docs/overhaul/BACKTEST_PLAN_2026-10-25.md`; results at
`docs/overhaul/BACKTEST_RESULTS_2026-10-25.md`.

## Files

- `desk_tick_sim.py` — `simulate(candles_15m, candles_1h, direction_at, params) -> Run`.
  Pure, no I/O. Imports `pathiel.agents.bracket.check_leverage`/`check_levels`
  so the backtest and the live tool share the exact same gates.
- `data.py` — fetch + disk cache for candles and funding
  (`fetch_candles_cached`, `fetch_funding_cached`), and the journaled-thesis
  reader (`load_journaled_theses`, READ ONLY against
  `pathia/.state/strategies/oil/theses`).
- `services/strategies/evals/desk_tick_backtest.py` — the runnable eval:
  fetches/reads the cache, runs always-long, always-short, daily-sign and
  journaled-thesis direction series plus a parameter sweep, writes
  `report.json` and one trades CSV per main run, exits 1 if a mechanics-only
  control run (always-long/always-short) liquidates.

## Running it

```
# first run (fetches and caches candles + funding; network, read-only /info)
PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m services.strategies.evals.desk_tick_backtest

# a specific cache/out dir, plus the journaled-thesis + calibration inputs
# (a SEPARATE repo, pathia's own live-desk state — read only, never written
# here; not hardcoded in source, see desk_tick_backtest.py's module docstring)
.venv/bin/python -m services.strategies.evals.desk_tick_backtest \
    --cache-dir /tmp/desk-backtest-cache --out-dir /tmp/desk-backtest \
    --theses-dir /path/to/pathia/.state/strategies/oil/theses \
    --progress-log /tmp/oil-loop/progress.log
```

`PATHIEL_OIL_THESES_DIR` / `PATHIEL_OIL_PROGRESS_LOG` set the same two
paths as environment variables instead. Without either, the journaled-
thesis series and the calibration diff are skipped with a note — the
other three direction series and the sweep still run. Every later run
with the same `--cache-dir` reads the cache; delete the directory (or
point `--cache-dir` at a fresh one) to force a refetch.

## Gate tests

```
PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m pytest -q -p no:cacheprovider \
    services/strategies/tests/test_desk_tick_sim.py \
    services/strategies/tests/test_desk_tick_backtest_eval.py
```

Offline, synthetic candles only, under 2 seconds. The eval's own
live-candle path (`__main__`) is a periodic/nightly run, not a commit gate
(see `.github/workflows/nightly.yml`).

## What's deliberately simplified

- The entry/stop/target are always structural (8h range edge, a dollar
  buffer beyond it, the 24h range edge on the far side) for every direction
  series, including the journaled theses — the thesis's own `entry_px`
  only caps how far the simulator will chase (never worse than the
  thesis's entry), it does not replace the structural level. This keeps
  all three experiments testing the SAME mechanics on the SAME terms.
- A thesis's free-text `invalidation` field is parsed for the first
  "below N" / "above N" number; a second, softer daily-close or headline
  criterion in the same sentence is not replayable from candles (plan
  section 8) and is ignored.
- A direction series flipping sides while a position is open closes it
  immediately (`direction_flip`) — the one-slot rule means a reversed call
  can't sit next to the old position.
- A position still open when the 30-day window ends is marked-to-market in
  `final_equity`/`equity_curve`, not force-closed into a fabricated trade
  row.
