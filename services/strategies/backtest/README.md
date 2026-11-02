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
- `portfolio.py` — `simulate_portfolio(desks, starting_equity)`: several desks on one
  clock on top of `desk_tick_sim.simulate_steps`, each sized off its share of the whole
  account's marked equity (live `desk_equity_share`). 100/0 equals the single-desk sim
  byte for byte. See DESK_SPLIT_TEST_2026-11-02.md.
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

## Profit lock (2026-11-01)

Four `Params` fields, off by default (`lock_trigger_r=None`; off keeps every
result byte-identical, see `tests/test_profit_lock.py`):

- `lock_trigger_r`, `lock_r`: once the best price since the fill is
  `lock_trigger_r` R in favour, the stop moves to entry + `lock_r` R (a
  short mirrors). Only tightens; the target stays. Exits as `"lock"`.
- `lock_trail`: after arming, the stop trails `lock_trigger_r - lock_r` R
  behind the best price, never under the lock level.
- `lock_confirm_close` (True): the lock may fill in the bar that set it only
  when that bar closes through it; otherwise from the next bar.

`services/strategies/evals/profit_lock_test.py` runs the grid against the
fixed stop on the Brent theses, the Brent no-thesis controls and the silver
replay. Result and verdict (not adopted):
`docs/strategy/PROFIT_LOCK_TEST_2026-11-01.md`.

## Daily stop-out cap (2026-11-02)

Two `Params` fields, off by default (`daily_stop_cap=None`; off keeps every
result byte-identical, see `tests/test_stop_cap.py`):

- `daily_stop_cap`: after this many `"stop"` exits in one UTC day, no new
  entry or flip for the rest of that day. Resets at 00:00 UTC. Targets,
  locks, invalidations, direction flips and time exits do not count. An
  open position is never touched. Each skipped setup lands in
  `Run.cap_blocked`.
- `cap_per_side`: count and block per side (N short stops block shorts only).

Plus `stop_buffer_atr` (None = off): the entry stop's buffer is that many x
the tick's own ATR14 instead of the fixed `stop_buffer`. 1.3 is the live
non-Brent rule (`next_trade.stop_buffer_for`).

`services/strategies/evals/stop_cap_test.py` runs N = 1, 2, 3 (desk and per
side) against no cap and prices every blocked entry. Result and verdict
(not adopted): `docs/strategy/STOP_CAP_TEST_2026-11-02.md`.

## Stop buffer floor (2026-11-02)

Two `Params` fields, off by default (None; off keeps every result
byte-identical, see `tests/test_silver_stop_floor.py`). Read only when
`stop_buffer_atr` is on, so Brent's fixed buffer never sees them:

- `stop_buffer_floor`: dollars. The buffer is max(stop_buffer_atr x ATR14, floor).
- `stop_buffer_floor_frac`: a fraction of the entry price (0.008 = 0.8%).

A floor only widens. The widened stop goes through the same 20x and min-R
gates; a tick it fails that the bare ATR stop passed is counted in
`Run.floor_blocked` (by gate) and listed in `Run.floor_blocked_rows`.
`Run.floor_widened` counts entries opened with a widened stop.

`services/strategies/evals/silver_stop_floor_test.py` runs the floor grid on
the silver theses, the live metals journal and the silver controls, and
proves Brent unchanged. Result: `docs/strategy/SILVER_STOP_FLOOR_TEST_2026-11-02.md`.

## Brent buffer floor, weekend window, weekend lock (2026-11-02)

Five `Params` fields, off by default (off keeps every result byte-identical,
see `tests/test_brent_stop_buffer.py`):

- `fixed_buffer_floor_frac`: the floor for the fixed-dollar buffer (Brent):
  max(stop_buffer, frac x entry), with the same floor bookkeeping.
- `weekend_off`: "hold" takes no new entry inside `weekend_closed(t)`
  (Friday 16:00 to Sunday 18:00 New York, zoneinfo, so Fri 21:00 to Sun
  23:00 UTC in winter and an hour earlier in summer); "flatten" also closes
  an open position at market on the first tick inside it (`"weekend"` exit).
- `lock_weekend_only`: the profit lock folds in bars and moves the stop
  only inside the window. A stop it moved stays after the reopen.
- `lock_trigger_target_frac`: arm the lock at this share of the way to the
  target instead of `lock_trigger_r`.
- `lock_plus_fees`: the lock level plus the taker round trip, so a
  "break-even" exit nets about zero.

`services/strategies/evals/brent_stop_buffer_test.py` runs the Brent buffer
grid, weekend-off and the weekend-only lock. Result and verdict (none
adopted): `docs/strategy/BRENT_STOP_BUFFER_TEST_2026-11-02.md`.

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

## Loss-audit options (2026-11-02)

Off by default; with them off every earlier eval output is byte-identical
(`tests/test_loss_fix.py`'s golden check). Results and verdicts:
`docs/strategy/LOSS_AUDIT_2026-11-02.md`.

- `risk_cap_frac` — size every entry, top-up and flip so a stop-out (price,
  taker fee both ways, flat slip) costs at most this share of equity
  (`risk_capped_notional`); leverage and the liquidation price unchanged.
- `entry_delay_min`, `side_age_h`, `confirm_1h` — tick-level entry filters
  (`entry_filter`): minutes since the call began (`call_start`), hours the
  side has held across rewrites, the last closed 1h candle's colour.
- `no_break_entry` — no market entry once the mid is past the entry range's
  own edge (a long under the 24h low).

The audit itself (`evals/loss_audit.py`) rebuilds every closed trade from
the wallet's public fills and tags its cause; `evals/loss_fix_test.py`
backtests a fix per cause.

## Desk split (2026-11-02)

`portfolio.py` plus `simulate_steps` and the `size_equity` hook in the
sim (None keeps every result byte-identical). `evals/desk_split_test.py`
runs oil/silver 100/0 to 0/100 with the 15% cap on and off, writes
/tmp/desk-split/. Gate tests: `tests/test_portfolio.py`,
`tests/test_desk_split_eval.py`. Result: docs/strategy/DESK_SPLIT_TEST_2026-11-02.md.
