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

Repo: `/Users/julian_dev/Documents/code/hermes-trader`. The user develops on
branch `python` and fast-forward-merges to `daily-push-v2` (the deploy
branch) after each batch of changes. **Never push directly to other branches
without explicit confirmation.**

## Architecture

A pipeline designed to keep AI token cost proportional to real opportunity:

1. **Scan** — fetch all mids (native + HIP-3 dexes when `enable_hip3=true`),
   evaluate 6 triggers per market (pctMoveSpike, volumeSpike, breakout,
   rangeCompression, trendStrength, momentumBurst). The candle-fetch budget
   is bucketed (default 60 total): top-N crypto by volume + top-M crypto
   by `|24h%|` (movers) + top-K HIP-3 by volume, so HIP-3 tokenized equities
   and low-volume native big-movers each get scanned regardless of where
   they rank against the BTC/ETH volume leaders. `momentumBurst` bypasses
   the composite-score gate so explosive moves always surface. Every
   perception is persisted via `memory.record_perception`.

   Env knobs: `HERMES_MAX_MARKETS=60`, `HERMES_MAX_MARKETS_HIP3=25`,
   `HERMES_MAX_MARKETS_MOVERS=10`, `HERMES_MOVERS_VOL_FLOOR_USD=1000000`.
2. **Pre-research cooldown** — `trading_loop.py` checks the most recent
   trade per coin and skips paid AI research if the coin is still inside its
   `cooldown_min` window. The execute-time `cooldown_gate` remains as the
   authoritative backstop.
3. **TA Filter** — `ta_filter.py` does multi-timeframe validation (1h/4h/1d
   EMA, RSI, ATR, ADX, volume) at zero AI cost. Only CONFIRMED perceptions
   (score ≥ 45) reach AI research; WEAK / REJECTED are dropped. A perception
   whose `momentumBurst` trigger fired bypasses the gate.
4. **AI Research** — deep AI analysis via OpenRouter on triggered candidates.
5. **Execution** — equity-sized orders (`equity_fraction_per_trade × equity ×
   leverage`, defaults to 0.05 × current equity × per-coin-max leverage), SDK
   order signing, an ATR-based backup stop-loss, and DSL dynamic exits.
   Blocked attempts are NOT written to `memory._trades` — only successful
   executions appear there, so `cooldown_gate` keys off real history rather
   than its own rejection log.

## Running

The trading loop is a standalone process (no Hermes command wraps it yet):

```bash
python scripts/trading_loop.py        # continuous scan -> research -> execute
# background: nohup python scripts/trading_loop.py > logs/trading_loop.log 2>&1 &
```

Recommended production start (background daemon):
```bash
nohup python3 scripts/trading_loop.py > logs/trading_loop.log 2>&1 &
```
The `--env prod --daemon` flags are parsed but **informational only** — the script does NOT actually fork or daemonize itself. Use `nohup ... &` or run the loop as a background process from your terminal/task runner. The loop already has its own `while True` with periodic sleeps.

Cadence is `HERMES_SCAN_INTERVAL` (default 60s). Or drive the steps individually
through the MCP `scan` / `research` / `execute` tools.

### Restarting the Trading Loop + Server

Use `scripts/restart.sh` — handles stop (SIGTERM → SIGKILL fallback), verify, background start with logs, and a status readout:

```bash
scripts/restart.sh              # restart both trading loop + FastAPI server
scripts/restart.sh loop         # restart trading loop only
scripts/restart.sh server       # restart FastAPI server only
scripts/restart.sh stop         # stop both, don't start
scripts/restart.sh status       # show what's running
```

Logs land in `logs/trading_loop.log` and `logs/server.log`. The MCP server (`scripts/hermes-mcp-server.py`) is intentionally NOT managed — it's a transient stdio process respawned by Hermes Agent on each tool call. If MCP code is stale: `pkill -f hermes-mcp-server.py` and the next tool call respawns fresh.

**Pitfall:** `python3 scripts/trading_loop.py --env prod --daemon` does NOT daemonize — the `--daemon` flag is parsed but has no effect. The restart script uses `nohup ... &` correctly; only fall back to a manual launch if the script is unavailable.

## Asset-class toggles

`.agent-config.json` carries two boolean flags that control what the scanner
and executor will trade:

- `enable_crypto` (default `true`) — scan native HL perps (BTC, ETH, SOL, ...).
- `enable_hip3` (default `false`) — scan HIP-3 perpDexes (xyz / vntl / km / ...).

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

## Risk Gates (11 independent, no short-circuiting)

Every gate is evaluated; results are collected even when one blocks:
confidence, max_concurrent, per_trade_notional_cap, daily_loss_killswitch,
market_liquidity_floor (with HIP-3 floor split — see below), coin_allowlist /
coin_blocklist, cooldown, opposite_direction_guard, correlation_cap,
equity_risk_cap, news_blackout, market_regime.

Notes on specific gates:
- **market_regime**: blocks counter-trend trades unless `confidence ≥
  counter_regime_min_conf` OR `composite_score ≥ 50` OR `momentumBurst`
  fired. The own-coin-momentum bypass exists because the regime proxy
  (BTC for crypto, SP500 for equity) is slow — a strong individual signal
  should override a stale macro call. Regime is computed from **1h
  candles, 8-bar lookback** (was 4h × 5-bar; too slow for intraday
  rotations).
- **news_blackout**: skipped for tokenized-equity perps (their news
  always mentions earnings/Fed by definition). Crypto + commodity still
  gated.

**Config keys are read as `snake_case` only** — legacy camelCase keys are
silently ignored by the gates.

## Trade Sizing

Per-trade size = `equity_fraction_per_trade × perp_equity × leverage`,
keyed off **main-dex perp equity only** (not the cross-dex aggregated
number — that's a dashboard semantic; sizing must be backed by main free
margin). Each trade commits a fixed fraction, so N trades scales the
account fully in. Bounded by `max_concurrent` (simultaneous positions),
`max_total_notional_pct` (combined-notional ceiling), and
`max_trade_notional_usd` (per-trade ceiling).

Free-margin floor: the executor refuses if `available / equity <
min_available_margin_pct` (default 10%). `available` is computed as
`accountValue - totalMarginUsed` — the same number HL shows as "Available
to Trade". A defensive `equity_unavailable` reason fires when HL returns
`equity=0` (transient outage) instead of sending an unsized order.

For HIP-3 trades the executor runs a per-dex preflight (queries that
specific dex's clearinghouse) and refuses with `hip3_dex_underfunded` if
the target dex has < $1 — HIP-3 dexes are separate clearinghouses and
agent wallets cannot transfer between them.

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

Scanner uses a **bucketed budget** (default 60 candle fetches per scan):
- `HERMES_MAX_MARKETS_HIP3` (25) HIP-3 markets by 24h volume
- `HERMES_MAX_MARKETS_MOVERS` (10) crypto markets by `|24h%|` above a
  `HERMES_MOVERS_VOL_FLOOR_USD` ($1M) floor
- Remainder (25) crypto markets by 24h volume

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
- `min_market_volume_usd` (default 5,000,000) — applies to native crypto
- `min_hip3_volume_usd` (default 500,000) — applies to colon-namespaced markets

Thin HIP-3 (e.g. `hyna:XRP` $33k) still correctly blocks; mid-volume
tokenized equities flow.

**Market regime classifier** (`agents/market_regime.py`) strips the dex
prefix before lookup, so `xyz:NVDA` correctly classifies as `equity` (not
crypto) and uses `EQUITY_PROXY = "xyz:SP500"` for its regime trend.
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

Pitfall: assuming “we scanned everything” when the log simply says “50 markets”. Always check via the MCP market-list tools when the user mentions an asset that was not reported.

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| Dashboard equity ≠ HL UI total | The dashboard reads aggregated (`fetch_account_state(include_hip3=True)`). If the loop is running old code that uses main-only, restart it. |
| Daily PnL inflated after a deposit/transfer | Contribution-aware tracking subtracts spot↔perp transfers + external deposits/withdrawals automatically. If still inflated, baseline may be stale — reset with the snippet in `references/restart-sequence.md`. |
| Executor blocks LONG with "insufficient_free_margin" while HL UI shows plenty | `available` is `accountValue - totalMarginUsed` (matches HL UI). If they differ, the loop is on stale code — restart. |
| Most blocked LONGs are "counter-regime" | Regime proxy is slow; raise `counter_regime_min_conf` floor or rely on the own-coin-momentum bypass (composite_score≥50 or momentumBurst). |
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