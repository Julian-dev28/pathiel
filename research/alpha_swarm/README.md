# alpha_swarm — offline candle alpha-hunt

Durable home for the alpha-hunt swarm (started 2026-06-27). A fleet of read-only
research agents test trading hypotheses against one shared cached candle dataset,
under identical validation gates, and write a verdict per idea. Nothing here trades
or imports into the live loop — it's pure offline research.

## Layout
- `ALPHA-QUEUE.md` — the backlog: ~50 methods in 3 lanes (A factor / B vol-regime /
  C microstructure) + Wave-2 refills. Status legend at the top. Pull from the top of a lane.
- `SWARM-RULES.md` — the contract every agent obeys (read-only, cache-only, lookahead-safe,
  OOS both-halves, slippage sweep, stop-width sweep, survivorship caveat).
- `lib/alpha_lib.py` — shared harness: `load_dataset`, `summarize` (per-tier slippage + OOS
  halves + verdict), `sweep_stop`. Import this; it enforces the gates.
- `lib/mc_null.py` — shuffle-label + block-bootstrap p-value harness. Bolt onto any positive claim.
- `lib/build_dataset.py` — rebuilds `dataset.json` (the candle pull). See below.
- `lib/laneA_common.py`, `lib/laneB2_common.py` — lane helper shims.
- `hypotheses/*.py` — one script per tested idea (the actual backtest).
- `findings/*.md` — one verdict per idea + `_SCOREBOARD_{A,B,C}.md`.

## The dataset (NOT committed — 17MB of candles, regenerate)
`dataset.json` is gitignored (data, not source). Rebuild it:

```bash
.venv/bin/python research/alpha_swarm/lib/build_dataset.py
# or point anywhere: HERMES_ALPHA_DATASET=/path/to/dataset.json
```

It pulls the 40 most-liquid native crypto perps × 1d/1h/5m from Hyperliquid (one fetch,
many readers — agents read cache-only so they never 429-storm the API). **Survivorship
caveat**: it's TODAY's liquid set, so any positive result is an UPPER BOUND.

## Run one hypothesis
The scripts import the harness by name (`import alpha_lib` / `mc_null` / `laneA_common`),
so put `lib/` on the path:
```bash
cd research/alpha_swarm
PYTHONPATH=lib ../../.venv/bin/python hypotheses/<name>.py
```

## Validated / shadow-worthy survivors so far (2026-06-27)
The honest meta-read: candle-space is heavily mined — most +EV prints are the momentum
factor in disguise or a down-beta artifact of the −44% BTC tape. The matched same-side /
same-regime / same-time null (`mc_null.py`) is what separates real signal from beta.

- **extreme_surface** — confirmed both live edges; spawned the live shadow book
  `crash_continue_div_short` (see hermes_trader/agents/).
- **C9 engulfing_reversal_xs** (ROBUST) — XS long bullish-engulf / short bearish-engulf,
  excess +0.6–0.86%/trade over 3 nulls (p≤0.0006), both halves +, survives 50bps. New, non-overlapping.
- **A13 relative_strength_drawdown** (ROBUST, but 0.7-corr w/ live momentum) — long nearest-50d-high /
  short deepest-drawdown. Pending the W-A1 orthogonality verdict before it counts as new capacity.
- **B13 realized_skew_timing** (ROBUST overlay) — neg-market-skew regime arms the live extreme_fade-long
  (EV +5.2%→+7.85%). A filter, not a standalone book.
- **C5 nday_high_breakout** (MARGINAL/SHADOW) — converges with A13's long leg (proximity-to-high).

Promotion path: a validated, non-overlapping edge → forward shadow logger via the unified
shadow ledger (`hermes_trader/agents/shadow_ledger.py`, surveyed by `scripts/shadow_status.py`)
→ operator sign-off before any live flip. Never auto-flip real money.
