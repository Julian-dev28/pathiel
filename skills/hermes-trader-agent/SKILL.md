---
name: hermes-trader-agent
description: Use when operating, maintaining, or debugging hermes-trader — the standalone autonomous Hyperliquid trading system that Hermes Agent drives through its MCP server. Covers the scan/research/execute pipeline, the 11 risk gates, MCP tool wiring, and Hyperliquid order-placement gotchas.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [trading, hyperliquid, mcp, autonomous, quant]
    related_skills: [hyperliquid-agent-wallets]
    homepage: https://github.com/Julian-dev28/hermes-trader
---

# Hermes-Trader Agent

`hermes-trader` is a **standalone Python trading system** for Hyperliquid
perpetual markets — both native crypto perps (BTC, ETH, etc.) **and HIP-3
tokenized-equity / commodity / index perps** (`xyz:NVDA`, `xyz:GOLD`,
`km:US500`, `xyz:CL`, etc.) when the `enable_hip3` flag is on. Hermes Agent
operates it through the **MCP server** registered in `~/.hermes/config.yaml`
(`mcp_servers.hermes-trader`) — that MCP boundary is the integration. The
trading engine itself has no Hermes-framework dependency; it is
Hermes-*operated*, not Hermes-*built*.

Repo: `/Users/julian_dev/Documents/code/hermes-trader`. The user develops on a
working branch (currently `able`) and merges to the deploy branch
(`daily-push-v2` / `main`) after each batch of changes. **Commit/push only when
the user asks, and never push directly to other branches without explicit
confirmation.**

## Architecture

A pipeline designed to keep AI token cost proportional to real opportunity:

1. **Scan** — fetch all mids (native + HIP-3 dexes when `enable_hip3=true`),
   evaluate 6 triggers per market (pctMoveSpike, volumeSpike, breakout,
   rangeCompression, trendStrength, momentumBurst). The candle-fetch budget
   is bucketed (default 45 core markets plus optional sweep): top-N crypto by volume + top-M crypto
   by `|24h%|` (movers) + top-K HIP-3 by volume, so HIP-3 tokenized equities
   and low-volume native big-movers each get scanned regardless of where
   they rank against the BTC/ETH volume leaders. `momentumBurst` bypasses
   the composite-score gate so explosive moves always surface. Every
   perception is persisted via `memory.record_perception`.

   Env knobs: `HERMES_MAX_MARKETS`, `HERMES_MAX_MARKETS_HIP3`,
   `HERMES_MAX_MARKETS_MOVERS`, `HERMES_UNIVERSE_SWEEP`,
   `HERMES_SCAN_WORKERS`, `HERMES_BATCH_SIZE`, `HERMES_BATCH_SLEEP`,
   `HERMES_MOVERS_VOL_FLOOR_USD`, `HERMES_HIP3_MOVERS_FLOOR_USD`.
2. **Pre-research cooldown** — `trading_loop.py` checks the most recent
   trade per coin and skips paid AI research if the coin is still inside its
   `cooldown_min` window. The execute-time `cooldown_gate` remains as the
   authoritative backstop.
3. **TA Filter** — `ta_filter.py` does multi-timeframe validation (1h/4h/1d
   EMA, RSI, ATR, ADX, volume) at zero AI cost. Only CONFIRMED perceptions
   (score ≥ 45) reach AI research; WEAK / REJECTED are dropped. A perception
   whose `momentumBurst` trigger fired bypasses the gate.
4. **AI Research** — deep AI analysis via OpenRouter on triggered candidates.
5. **Execution** — ATR equal-risk sizing when `atr_risk_sizing.enabled=true`
   (current live path), with fraction-based sizing as the explicit fallback.
   Orders are clamped by per-trade notional and leverage caps, normalized to
   Hyperliquid coin precision before gates, signed through the SDK, protected
   by a server-side backup stop-loss, and registered with DSL dynamic exits.
   Blocked attempts are NOT written to `memory._trades` — only successful
   executions appear there, so `cooldown_gate` keys off real history rather
   than its own rejection log.

## Running

Use `scripts/restart.sh` as the canonical process manager for the loop and API
server:

```bash
scripts/restart.sh              # restart both trading loop + FastAPI server
scripts/restart.sh loop         # restart trading loop only
scripts/restart.sh server       # restart FastAPI server only
scripts/restart.sh stop         # stop both, don't start
scripts/restart.sh status       # show what's running
```

Logs land in `logs/trading_loop.log` and `logs/server.log`. The MCP server
(`scripts/hermes-mcp-server.py`) is intentionally NOT managed — it's a transient
stdio process respawned by Hermes Agent on each tool call. If MCP code is stale:
`pkill -f hermes-mcp-server.py` and the next tool call respawns fresh.

Manual foreground launch is only for debugging:

```bash
python scripts/trading_loop.py        # continuous scan -> research -> execute
python -m hermes_trader.server        # API server only
```

The `--env prod --daemon` flags are parsed but **informational only** — the
script does NOT actually fork or daemonize itself. The loop already has its own
`while True` with periodic sleeps; use `scripts/restart.sh` when it needs to
survive the terminal session.

Cadence is `HERMES_SCAN_INTERVAL` (default 60s). Or drive the steps individually
through the MCP `scan` / `research` / `execute` tools.

### Restarting the Trading Loop + Server

`scripts/restart.sh` handles stop (SIGTERM → SIGKILL fallback), verify,
background start with logs, and a status readout. `status` may show the process
group (`screen`/shell/python/`caffeinate`); the invariant is exactly one
`python ... scripts/trading_loop.py` process. If logs show overlapping scan
cadences, check for an orphan with `ps ax | rg "scripts/trading_loop.py"` and
stop the older process before trusting live behavior.

**Keep-awake (laptop hosts):** `start_loop` now launches a `caffeinate -i -m -w $pid` alongside the loop so the host can't idle/maintenance-sleep mid-run. On a sleeping Mac the whole process freezes — you'll see `[watchdog] no progress for N s — HUNG` re-execs where N is *minutes-to-hours* (that's the sleep gap, NOT a code hang; the watchdog re-execs correctly on wake). During sleep only the **server-side SL/TP brackets** protect open positions. `caffeinate -i` stops idle sleep but a closed lid on battery can still clamshell-sleep → keep on AC for true 24/7. See `references/daemon-investigation.md`.

**Pitfall:** `python3 scripts/trading_loop.py --env prod --daemon` does NOT daemonize — the `--daemon` flag is parsed but has no effect. Use the restart script for persistent operation; only fall back to a manual launch if the script is unavailable.

## Asset-class toggles

`.agent-config.json` carries two boolean flags that control what the scanner
and executor will trade:

- `enable_crypto` (default `true`) — scan native HL perps (BTC, ETH, SOL, ...).
- `enable_hip3` (default `true`) — scan HIP-3 perpDexes (xyz / vntl / km / ...).

Both false = no-op scan (logged loudly). Single-class runs hand the full
candle budget to that class. The executor enforces the same gating at
execute-time so stale perceptions can't sneak through if the flag flips
mid-cycle (`hip3_disabled` / `crypto_disabled` reasons).

## MCP Integration

The server (`scripts/hermes-mcp-server.py`, stdio, 100 tools) is registered in
`~/.hermes/config.yaml`:

```yaml
mcp_servers:
  hermes-trader:
    command: python3
    args:
      - /Users/julian_dev/Documents/code/hermes-trader/scripts/hermes-mcp-server.py
    cwd: /Users/julian_dev/Documents/code/hermes-trader   # recommended
    timeout: 60
    connect_timeout: 30
    env:
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY}
```

Primary tools: `scan`, `research`, `execute`, `state`, `config`. Adding tools and
the audit invariant: see `references/mcp-server.md`. After editing the server,
restart it: `pkill -f hermes-mcp-server.py` (the next call respawns it fresh).

## State Files

Project state — not Hermes memory (all gitignored):
- `.agent-config.json` — mode (OFF/LIVE), risk caps, thresholds
- `.agent-memory.json` — perceptions, analyses, trades, cooldowns
- `~/.hermes-trader-session-log.jsonl` — append-only cycle summaries

## Risk Gates (independent, no short-circuiting)

Every gate is evaluated; results are collected even when one blocks:
confidence, max_concurrent, per_trade_notional_cap, daily_loss_killswitch,
**daily_giveback**, market_liquidity_floor (+ HIP-3 split), short_liquidity,
coin_allowlist / coin_blocklist, cooldown, opposite_direction_guard,
correlation_cap, equity_risk_cap, news_blackout, market_regime.

Notes on specific gates:
- **daily_giveback** (give-back breaker, 2026-06-06): once the day's PnL peaks
  ≥ `daily_giveback_min_peak_usd`, blocks NEW entries if it retraces >
  `daily_giveback_halt_pct` from that peak (existing positions ride their stops;
  resets at UTC roll). Locks green days from round-tripping. Uses the TRUE
  aggregate account PnL (not main-dex-only — that bug spuriously halted). `0` = off.
- **market_regime**: blocks counter-trend trades unless `confidence ≥
  counter_regime_min_conf` OR `composite_score ≥ 50` ("via composite") OR a
  binary momentum/whale trigger fired. Aligned trades clear at the lower
  `aligned_min_conf`. **`block_counter_trend_bypass`** (currently `true`)
  disables ONLY the binary-trigger bypass — the composite≥50 path stays open, so
  strong-momentum counter-trend trades (e.g. an alt long in a down regime via
  composite strength) still pass. **Crowded-squeeze caution** (`crowded_with_min_conf`):
  a with-the-crowd aligned trade (short+`SHORT_CROWDED` / long+`LONG_CROWDED`) no
  longer gets the free "aligned" pass — must clear that conf or it's blocked
  `via:crowded_squeeze` (those are the entries that get squeezed). Regime is
  computed from **1h candles, 8-bar lookback** each scan (fresh, not stale).
- **short_liquidity**: a SEPARATE, deeper 24h-volume floor for SHORTS only
  (`min_short_volume_usd`, $50M) — thin markets squeeze. Distinct from the
  long/general `market_liquidity_floor`.
- **correlation**: `max_crypto_long_correlated` caps simultaneous correlated
  crypto exposure (concentration guard).
- **news_blackout**: skipped for tokenized-equity perps. Crypto + commodity gated.

**Surfacing layer (what reaches research)** — beyond the weighted composite gate,
several weight-0 "surfacing bypasses" bring a coin to the AI even below the gate;
the AI + risk gates then adjudicate:
- `uptrendMomentum` / `downtrendMomentum` — sustained intraday trend (both
  directions; the down side is what lets us short selloffs).
- `dailyMover` — large liquid 24h movers. Raw `daily_move_pct` is carried into
  analysis so TA sidestep can block parabolic PASS upgrades.

**Newer entry-side levers (2026-06, backtest-gated):**
- **`late_chase_relax`** (LIVE) — narrows the runner gate's "late trend-only
  chase; no fresh breakout/burst" block. Admits trend-aligned no-breakout entries
  ONLY on liquid coins (vol ≥ `min_volume_usd`, default $5M) inside the
  `[min_ext_pct, max_ext_pct]` daily-extension band (20–30%) — the one pocket
  backtested +EV/OOS-robust (`scripts/edge_extension.py`). Low-liq and >30% chases
  stay blocked (measured −EV: low-liq 20–30% is −1.27% gross — pump-and-dumps that
  reverse). `shadow_mode` logs `would admit` without trading. This is why we miss
  parabolic low-liquidity runners like TNSR +70% on purpose — they enter our
  universe only after they're already extended, and chasing them loses.
- **`capital_rotation`** (SHADOW) — when a strong fresh candidate is blocked
  purely by capital (book full / notional / margin), evicts the weakest non-winner
  (roe < `protect_winner_roe_pct`, age ≥ `min_hold_minutes`) for it. Wired at both
  the executor stage AND (newer) the pre-research margin preflight. Backtest-validated
  near-inert and held in shadow; flip `shadow_mode:false` only on forward evidence.

**Config keys are read tolerantly** — current gates accept snake_case and the
legacy camelCase form for common knobs. Prefer snake_case in `.agent-config.json`
and MCP writes so diffs stay predictable.

## Trade Sizing

Current live sizing uses `atr_risk_sizing`: target risk is
`equity × risk_per_trade_pct`, converted to notional using the primary stop
distance (`sizing_basis=primary_stop`) and then clamped by
`max_trade_notional_usd`, configured leverage, and the coin's max leverage.
When ATR sizing is disabled, the fallback is
`equity_fraction_per_trade × equity × leverage`.
It is bounded by `max_concurrent`,
`max_total_notional_pct`, and `max_trade_notional_usd`.

**Per-trade notional CLAMPS, not rejects** (`executor.py`): the computed
`trade_notional` is clamped down to `max_trade_notional_usd` so an oversized
candidate is sized down to the cap and can still be taken. The executor then
normalizes to the exact exchange-valid coin size before gates; tiny precision
dust around the cap is tolerated, while real overshoots are still blocked.

Free-margin floor: the executor refuses if `available / equity <
min_available_margin_pct` (config; currently **0.10**).
`available` is `accountValue - totalMarginUsed` (matches HL "Available to
Trade"). A defensive `equity_unavailable` reason fires when HL returns
`equity=0` (transient outage) instead of sending an unsized order.

For HIP-3 trades the executor runs a per-dex preflight (queries that
specific dex's clearinghouse) and refuses with `hip3_dex_underfunded` if
the target dex has < $1 — HIP-3 dexes are separate clearinghouses and
agent wallets cannot transfer between them.

## Exit Engine (DSL + server-side brackets)

Every executed position gets THREE layers of exit, all set at entry:

1. **DSL trailing stop** (`dsl_exit.py`, primary, re-evaluated each 60s tick):
   - **Phase 1 — loss cap:** `max_loss_pct` (current live 2.5% spot) AND
     `max_loss_roe_pct/lev` (current live 15% ROE, whichever is tighter),
     optionally widened to a volatility-scaled `atr_stop` (current live ON:
     1.5× ATR clamped 1.0–2.5%). The wider stop was validated to stop whipsawing
     volatile movers out of trend (the EIGEN/AERO leak).
   - **Phase 2 — profit lock:** engages at `protect_pct` (current live 1.25% for new entries).
     Floor =
     `entry ± peak_range × (1 − retrace_threshold)`, ratchets one-way.
   - **`retrace_threshold` + `phase2_tiers` ladder** — give-back control.
     Current live retrace is a tight **0.10** (banks give-backs early; validated
     live — JUP banked +16%/+12% ROE), loosening via tiers at +8% (0.35) and
     +15% (0.40) so proven runners breathe. `phase2_tiers` is wired from config
     at entry and on state synthesis.
   - **Breakeven ratchet:** once peak ≥ `breakeven_trigger_pct`, floor can't drop
     below `breakeven_lock_pct` — guarantees a winner can't round-trip to flat.
   - **Hard timeout:** `hard_timeout_minutes` (30h) — a scalp/swing horizon, NOT
     a multi-week hold.
2. **Backup stop-loss trigger** (server-side, `place_hl_trigger_order(...,"sl")`
   at `sl_atr_mult`=1.5 ATR): fires on the exchange instantly between 60s ticks,
   and is the ONLY protection while the host sleeps or the loop is restarting.
3. **Take-profit scale-out** (server-side, the "we had it all and gave it back"
   fix): a reduce-only TP trigger banks `tp_scale_fraction` (0.5) of the position
   at `TP_ATR_MULT`=1 ATR past entry — **half locks at target automatically**, the
   rest rides the DSL trail. Validated live (auto-banked ADA half; runner to +66%
   ROE). HL accepts a 100%-SL + 50%-TP reduce-only bracket (150% total) fine.

**Trigger hygiene:** `close_position_market` calls `cancel_open_orders_for_coin`
after a market close to clear the stranded SL/TP bracket — otherwise stale
reduce-only orders accumulate and reject a later reduce-only order ("reduce only
order would increase position"). See `references/exit-engine.md`.

## Unified Accounts

On a Hyperliquid unified account the agent wallet signs orders while the master
account holds funds; `resolve_user_address()` picks `HYPERLIQUID_MASTER_ADDRESS`
first, else `HYPERLIQUID_WALLET_ADDRESS`. Equity reads come from the master.
For agent-wallet setup and the `approveAgent` flow, see the
`hyperliquid-agent-wallets` skill.

## User Rules

- Real orders only — no simulation or dry-run; mode is `OFF` or `LIVE`.
- Full autonomy — do not ask permission for individual trade decisions.
- Token-cost aware — a flat market with 0 triggers = $0 spent = correct behavior.

## Market Coverage & Scan Scope

Scanner uses a **bucketed budget** (default 45 core candle fetches per scan):
- `HERMES_MAX_MARKETS_HIP3` (18) HIP-3 markets by 24h volume
- `HERMES_MAX_MARKETS_MOVERS` (10) crypto markets by `|24h%|` above a
  `HERMES_MOVERS_VOL_FLOOR_USD` ($300k) floor
- Remainder (17) crypto markets by 24h volume

This catches three regimes: high-volume majors, tokenized equities, and
low-volume native-crypto big movers (the IO/SEI/DYDX/GRASS cohort). Without
the movers slot, BTC/ETH/SOL dominate the volume cut and every +10% midcap
rally goes unscanned.

To force coverage of a specific coin not in the buckets:
- Call `research` directly on the symbol via MCP (confirm it's in
  `get_perp_markets` first), or
- Bump `HERMES_MAX_MARKETS_MOVERS` if it's a momentum candidate.

## HIP-3 Tokenized Equity / Commodity Perps

Hyperliquid hosts a separate family of perp dexes for tokenized stocks,
indices, commodities, and FX (`xyz`, `km`, `vntl`, `flx`, `hyna`, `abcd`,
`cash`, `para`). Markets are namespaced as `<dex>:<symbol>` — e.g.
`xyz:NVDA`, `xyz:GOLD`, `xyz:SP500`, `km:US500`, `km:USOIL`.

**Enabling**: set `"enable_hip3": true` in `.agent-config.json` and **restart
the trading loop** (the universe is fetched once at startup). The flag is
threaded through every entry point:

1. `get_universe(include_hip3=True)` — auto-discovers registered HIP-3 dexes
   via `/info perpDexs` and merges each dex's markets into the unified list.
2. `fetch_all_mids(include_hip3=True)` — adds one HTTP POST per HIP-3 dex
   so colon-namespaced mids populate.
3. `get_all_hl_mids(include_hip3=True)` — same for the DSL exit pass; without
   this, HIP-3 trackers receive no mid and peak/floor never advance.
4. `fetch_account_state(user, include_hip3=True)` — aggregates equity +
   `total_ntl` across main + every HIP-3 clearinghouse, concatenates
   `asset_positions` with bare coins prefixed `<dex>:`. Returns
   `dex_equity` (per-dex breakdown) and `queried_dexes` (the dexes that
   actually responded — used by `rehydrate_from_exchange` to skip
   dropping DSL trackers on a timed-out dex).
5. `Info(perp_dexs=[""]+dex_names)` / `Exchange(perp_dexs=...)` — teaches
   the HL SDK to resolve colon names at order-placement time. **CRITICAL**:
   the empty string `""` must be prepended; the SDK treats the list as
   exclusive — pass only HIP-3 dexes and BTC/ETH start raising `KeyError`
   at `update_leverage` / `order`.

**Dashboard vs sizing semantics**: callers that pass `include_hip3=True`
see total aggregated equity (dashboard, heartbeat, portfolio API, CLI).
The executor sizes against `include_hip3=False` (main-only) so free margin
checks aren't fooled by cross-dex idle USDC.

**Liquidity floor split**: HIP-3 markets carry less volume than BTC/ETH
(most `xyz:*` markets sit in the $1M–$50M range vs $1B+ for BTC). The
risk gate uses two floors:
- `min_market_volume_usd` (current live 700,000) — applies to native crypto
- `min_hip3_volume_usd` (current live 700,000) — applies to colon-namespaced markets

Thin HIP-3 (e.g. `hyna:XRP` $33k) still correctly blocks; mid-volume
tokenized equities flow.

**Market regime classifier** (`agents/market_regime.py`) strips the dex
prefix before lookup, so `xyz:NVDA` correctly classifies as `equity` (not
crypto). Equity names are checked against their own 1h trend first; `xyz:SP500`
is only the fallback when that name's own candles are neutral or too thin.
Tokenized commodities (`xyz:GOLD`, `xyz:CL`, `xyz:BRENTOIL`, `km:USOIL`)
classify as `commodity` and use their own candle stream as the proxy.

**Price lookup gotcha**: `info.all_mids()` only returns the native HL perp
dex — colon-namespaced coins need `info.all_mids(dex=<prefix>)`. Both
`get_hl_price()` (`client/exchange.py`) and `fetch_all_mids(include_hip3=True)`
(`client/hl_client.py`) handle this. Outside those helpers, look up the
prefix manually before calling SDK methods.

**Off-hours behavior**: HIP-3 equity markets only trade during US equity
hours; outside those hours volume drops to ~zero, so the scanner naturally
skips them (filtered by `min_hip3_volume_usd`). No explicit hours-gate is
implemented — the volume floor handles it.

See `references/hip3-tokenized-equity-handoff.md` for the original task
brief and the post-implementation audit findings.

**Pitfall:** assuming “we scanned everything” when the log simply says “50 markets”. Always check via the MCP market-list tools when the user mentions an asset that was not reported.

## Funding-regime bias (symmetric — works in either direction)

The funding regime (`market_get_funding_regime`) is the primary signal for
whether the bot should bias toward longs or shorts. Implementation is
**direction-agnostic** so it works the same when the regime flips:

- Trades aligned with the funding regime → normal bar.
- Trades against the funding regime → elevated bar (conf ≥ 0.85 OR
  composite ≥ 60).
- Binary trigger bypasses are no longer a broad force path. With the current
  live config, `block_counter_trend_bypass=true`, a lone slow-burn/momentum
  trigger does not rescue counter-regime trades.

Core logic lives in `risk_gates.py::market_regime_gate`. The funding regime
is read via `hyperfeed.py::market_get_funding_regime` (5-min cache —
funding settles hourly so a longer TTL would still be safe; shorter is
wasteful).

Do **not** solve regime bias by raising `min_ai_confidence` globally — that
kills overall trade volume. Use `counter_regime_min_conf` instead.

Live config (edit `.agent-config.json` directly — the MCP `config` tool
does NOT accept `counter_regime_min_conf` writes):
```json
{
  "min_ai_confidence": 0.67,
  "counter_regime_min_conf": 0.8,
  "leverage": 12,
  "max_trade_notional_usd": 350,
  "atr_risk_sizing": {
    "enabled": true,
    "risk_per_trade_pct": 0.02,
    "sizing_basis": "primary_stop"
  }
}
```

When the user asks "regime?" / "short or long?" / "what's the regime",
always answer with a fresh `market_get_funding_regime` call — do not cache
the answer in session memory across turns.

**Per-class funding overlay**: `market_get_funding_regime()` fetches
`get_universe(include_hip3=True)` and returns `regimes_by_class` for crypto,
equity, and commodity. `market_regime_gate()` looks up the class for the
current coin, so a crypto `SHORT_CROWDED` state no longer leaks onto oil or
tokenized equities. If `regimes_by_class` is missing from an older stub, the
gate falls back to the legacy top-level `regime`.

See `references/short-regime-bias.md` for the full prompt template, code
locations, and pitfalls.

### Testing the regime gate

`tests/test_cleanup.py` has the regime + funding-regime overlay test suite
(`test_market_regime_gate_*` and `test_funding_regime_*`). **Every test
that touches `market_regime_gate` MUST mock BOTH the trend regime and the
funding regime**, or the live API call from inside the gate will hit
production and randomize the result:

```python
from hermes_trader.agents import market_regime, hyperfeed
monkeypatch.setattr(market_regime, "detect_regime", lambda c: "up")
monkeypatch.setattr(hyperfeed, "market_get_funding_regime",
                    lambda: {"regime": "NEUTRAL", "assets": []})
```

For cache-behavior tests, also monkeypatch `_funding_regime_cache` to
`None` and patch `_compute_funding_regime` (not `market_get_funding_regime`
itself — that's the cache wrapper).

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| Dashboard equity ≠ HL UI total | The dashboard reads aggregated (`fetch_account_state(include_hip3=True)`). If the loop is running old code that uses main-only, restart it. |
| Daily PnL inflated after a deposit/transfer | Contribution-aware tracking subtracts spot↔perp transfers + external deposits/withdrawals automatically. If still inflated, baseline may be stale — reset with the snippet in `references/restart-sequence.md`. |
| Executor blocks LONG with "insufficient_free_margin" while HL UI shows plenty | `available` is `accountValue - totalMarginUsed` (matches HL UI). If they differ, the loop is on stale code — restart. |
| Logs show overlapping scan cycles or doubled cadence | There is likely an orphan loop. Run `scripts/restart.sh status` and `ps ax \| rg "scripts/trading_loop.py"`; keep exactly one Python loop process. |
| Most blocked LONGs are "counter-regime" | Regime proxy is slow; raise `counter_regime_min_conf` floor or rely on the own-coin-momentum bypass (composite_score≥50 or momentumBurst). |
| MCP `config` tool dropping a key | FIXED 2026-06-05 and pruned later — the tool exposes the current risk-knob set in snake_case. Removed experiment knobs are intentionally absent. Older builds took a narrow camelCase schema, silently dropped keys, and wrote dup keys; if you see that, the MCP is on stale code → `pkill -f hermes-mcp-server.py`. |
| `[watchdog] no progress for N s — HUNG` re-execs (N = minutes/hours) | NOT a code hang — the host (MacBook) idle/maintenance-slept and froze the process; the watchdog re-execs correctly on wake. Confirm with `pmset -g log \| grep -iE "Sleep\|Wake"`. Fix: `caffeinate` (now auto-launched by `restart.sh`); keep on AC for closed-lid. Positions are held by server-side brackets during sleep. |
| `reduce only order would increase position` reject | Stranded SL/TP trigger orders from a prior closed position. `close_position_market` now auto-cancels them (`cancel_open_orders_for_coin`); if on old code, cancel manually or restart. |
| Day PnL baseline looks stale/reset after a restart | A mid-day restart can re-baseline `startOfDayEquity` to current equity (loses the true SOD) if persisted memory loaded zeroed — would launder a pre-restart drawdown out of the kill-switch. Known issue; verify `dayStartTs` vs UTC midnight before trusting daily PnL. |
| HIP-3 position shows "no DSL" indefinitely | `get_all_hl_mids` must be called with `include_hip3=True` so trackers receive a mid. If a HIP-3 dex query times out, trackers are preserved via `queried_dexes` until the next successful query. |
| Coin lookup fails on HIP-3 (`XYZ:MU` → not found) | Use `_norm_coin()` in MCP handlers — only the symbol uppercases, the lowercase dex prefix stays. |
| `@` coins as noise in scan results | Spot pairs are filtered in `perception.py`; if they appear, the filter regressed. |
| "perception not found" on research | Send the full perception object inline, not just a `perceptionId`. |
| Order rejected on price/size | Hyperliquid `szDecimals` ≠ `pxDecimals` — see `references/hyperliquid-gotchas.md`. |
| MCP tool runs stale code after a fix | The server is a separate process — `pkill -f hermes-mcp-server.py` to respawn. |
| Scan returns 0 triggers | Often correct (quiet market). Lower minScore only to widen deliberately. |
| Scanner fires triggers but zero executes (Signal vs Action Gap) | See `references/signal-vs-action-gap.md`. Scanner is healthy; second-stage filter (TA + AI confidence) is the knob. |

## Bundled Scripts

Runnable tooling shipped with this skill (`scripts/`) — all read-only, no orders
or writes. `audit_mcp_server.py` and `feed.py` are stdlib-only; `status.py` also
does a read-only Hyperliquid query for live equity:

- `scripts/audit_mcp_server.py` — validates MCP tool wiring (`TOOLS` /
  `tool_handlers` / `_STUB_RESPONSES` consistency). Run before and after editing
  the server; parses via `ast` (no import, no execution) and exits non-zero on
  drift.
- `scripts/status.py` — plain-text snapshot showing BOTH the cached state from
  `.agent-memory.json` and LIVE state pulled directly from Hyperliquid. The
  live read uses the repo's `hl_client` and `.env.local` for credentials, so
  drift between the two surfaces a broken loop heartbeat immediately. Falls
  back to cache-only when no wallet env var is set.
- `scripts/feed.py` — human-readable activity feed from the session log. The
  trading loop appends every scan / heartbeat / TA filter / research /
  execute / error event; `feed.py` renders them with timestamps and symbols.
  Examples:
  ```bash
  python3 scripts/feed.py                     # last 20 events
  python3 scripts/feed.py -n 50               # last 50
  python3 scripts/feed.py --since 30m         # last 30 minutes
  python3 scripts/feed.py --follow            # tail -f forever
  python3 scripts/feed.py --filter execute,error  # only those types
  ```
  Event types emitted by the loop: `loop_start`, `loop_stop`, `loop_heartbeat`
  (per-cycle equity/positions sync), `scan` (trigger count + coins), `ta_skip`
  (TA filter dropped a perception), `research` (verdict + confidence),
  `execute` (order outcome), `error`. `status.py` also prints TA verdict counts
  (CONFIRMED / WEAK / REJECTED + avg composite score over the last 30) so you
  immediately see if the statistical gate is over-filtering early signals.

## Visibility / "What is the bot doing right now?"

MCP tools are request/response — there is no streaming progress mid-call.
The right way to watch the trading system is:

1. **Tail the feed:** `python3 scripts/feed.py --follow` in a terminal.
2. **One-off snapshot:** `python3 scripts/status.py` for cached + live state.
3. **Hourly auto-report:** Hermes cron job `8a82eaa567fe` (`hermes-trader-status.sh`)
   runs `status.py` + `feed.py --since 60m` every hour and delivers the
   combined report to the originating chat (no LLM cost — `no_agent=true`).
   - Pause:  `hermes cron pause 8a82eaa567fe`
   - Resume: `hermes cron resume 8a82eaa567fe`
   - Run now: `hermes cron run 8a82eaa567fe`

## Loop Heartbeat (live equity sync)

`trading_loop.py` calls `_sync_account_state()` at the top of every cycle:

1. Resolves the user address via `resolve_user_address()` (master else wallet).
2. Calls `fetch_account_state(user, include_hip3=True)` — aggregated equity
   across main + every HIP-3 dex; returns `queried_dexes` so DSL rehydrate
   can scope its stale check.
3. Fetches net USDC contributions since UTC midnight via
   `fetch_aggregate_contributions_since(user, sod_ts_ms)` — subtracts
   deposits / spot↔perp transfers from the equity diff so daily PnL only
   reflects trading gains.
4. Calls `memory.track_daily_pnl(equity, contributions)` +
   `memory.update_open_positions(...)`.
5. Calls `memory.flush()` so `.agent-memory.json` always reflects current state.
6. Appends a `loop_heartbeat` event with equity / available / daily_pnl /
   positions + the live config snapshot (mode, frac, lev, slots, cap,
   crypto:on/off, hip3:on/off).

If `status.py` shows `cached equity: $0` while LIVE is non-zero, the
heartbeat is broken or the loop hasn't completed one cycle yet.

## Scheduled Operation

An hourly Hermes cron job (`no_agent`, zero LLM cost) runs `status.py` and
delivers the snapshot. It ships paused — `hermes cron resume 8a82eaa567fe` to
start it. See `references/cron-jobs.md`.

## References

- `references/mcp-config.md` — MCP server config and tool list.
- `references/mcp-server.md` — server structure, adding tools, the audit invariant.
- `references/hyperliquid-gotchas.md` — order-placement gotchas (decimals, tick size, $10 min, singletons).
- `references/cron-jobs.md` — Hermes cron wiring for the hourly status report.
- `references/signal-vs-action-gap.md` — "scanner fires, trader stays silent" pattern, including the 2026-05-28 direction-asymmetric gap diagnosis (counter-regime blocking 60 LONGs in 24h).
- `references/restart-sequence.md` — `scripts/restart.sh` usage + baseline-reset snippet.
- `references/trading-mode.md` — execute-first reporting contract when the user is in active trading mode.
- `references/daemon-investigation.md` — historical note on the no-op `--daemon` flag; superseded by `restart.sh`.
- `references/hip3-tokenized-equity-handoff.md` — current HIP-3 production wiring (all 5 entry points, queried_dexes safety, sizing semantics).
- `references/exit-engine.md` — DSL trail + tighter retrace ladder + breakeven, server-side SL/TP brackets, take-profit scale-out, trigger hygiene (the 2026-06-04/05 round-trip-fix overhaul).
- `references/short-regime-bias.md` — regime-aware bias, counter-trend gating, and symmetric up/down trend surfacing.
