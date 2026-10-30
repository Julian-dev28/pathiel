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

## AI-informed variants (2026-10-30)

Two optional `Params` flags, both off by default (every result above is
unaffected unless a config turns one on — see
`tests/test_ai_variants.py`'s golden check):

- `ai_levels: bool` — when flat, try the thesis's own entry/stop/first
  target before the structural rule (market entry if the mid already
  pays the thesis's price or better, else a resting limit AT the thesis
  entry, never past it). Gated by the same `check_leverage`/`check_levels`
  the structural rule uses; a thesis level that fails either gate falls
  back to the structural rule exactly as if `ai_levels` were off. The
  thesis target gets the same `target_offset_frac` discount the
  structural rule applies.
- `conviction_leverage: Optional[Tuple[float, float, float]]` —
  `(threshold, high_lev, low_lev)`: a new trade's leverage is `high_lev`
  when the thesis's `confidence` is at or above `threshold`, else
  `low_lev`, both floored at 15x (`CONVICTION_LEVERAGE_FLOOR`) regardless
  of the configured value. No confidence to read (a control direction
  series, or a thesis predating the field) falls back to `params.leverage`.

`services/strategies/evals/ai_strategy_test.py` runs `live_params()` vs
`ai_levels` vs `conviction_leverage` (swept at 0.55/0.65/0.75) vs both
together, on a journaled-thesis direction series, using
`strategy_sweep.per_day` for the per-day/per-week numbers:

```
.venv/bin/python -m services.strategies.evals.ai_strategy_test \
    --theses-dir /tmp/thesis-replay/oil/theses --live-theses-dir <journal dir>
```

These variants need more than the 9 real journaled oil theses to test
honestly, which is what `services/thesis/evals/replay.py` is for — see
that module's docstring (replays the real thesis-writing path, one
thesis per day, over a window of history, price data only, writing into
the same journal shape `data.load_journaled_theses` reads).

## What's deliberately simplified

- By default, the entry/stop/target are always structural (8h range
  edge, a dollar buffer beyond it, the 24h range edge on the far side)
  for every direction series, including the journaled theses — the
  thesis's own `entry_px` only caps how far the simulator will chase
  (never worse than the thesis's entry), it does not replace the
  structural level. This keeps all three experiments testing the SAME
  mechanics on the SAME terms. `Params.ai_levels` (above) is the one
  opt-in exception.
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
