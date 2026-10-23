# pathiel — architecture

A standalone Python autonomous trading agent for Hyperliquid perpetuals. Trades
crypto majors + memes, single-stock equity perps (TSLA, NVDA, AAPL, …), and
commodity perps (NATGAS, SILVER, COPPER, …) — uniform pipeline, per-asset-class
regime awareness.

This doc is the map: what the pieces are, where they come from, how they fit,
and what the realistic next moves are. It's deliberately opinionated about
*why* each layer exists, not just what it does — the "why" is what's hard to
recover later.

---

## TL;DR for a new contributor

```
Claude ──(MCP: place_bracket / bracket_sweep / close_position)──▶ HL orders

Persistent state on disk:  .agent-config.json  session-log.jsonl  desk journals

Two entry processes:
  pathiel/server.py             — FastAPI: public dashboard (the Desk page, 2026-10-23) + token-gated operator + JSON API
  scripts/pathiel-mcp-server.py — MCP stdio server: exposes 59 tools to Pathiel Agent / Claude — the one trading surface
```

Both share the same on-disk state and the same Python modules under
`pathiel/`. The MCP server owns every trade decision (Claude places orders
through `place_bracket` / `bracket_sweep`, sized against the operator's
saved risk profile); the dashboard server owns the human-visible surface.
There is no scanner, no AI research pass, no risk-gate pipeline and no DSL
exit loop any more — both were retired across two waves:

- **Wave 1 (headless desk, 2026-10-23):** the seven autonomous strategy
  books that placed entries unattended through a trading loop are deleted,
  down to one trader on the wallet.
- **Wave 2 (2026-10-23):** the trading loop itself — the scan → TA-filter →
  AI-research → risk-gates → executor pipeline and its per-tick DSL exit
  engine, diagrammed above in earlier versions of this file — has no caller
  left and is deleted. Claude is the only trader, full stop; there is no
  standing process that scans a market, researches a signal, or manages an
  exit on its own.

See `docs/overhaul/HEADLESS_2026-10-23.md`.

---

## Where the name "Pathiel" comes from

The agent layer is [Pathiel Agent](https://github.com/Julian-dev28/pathiel-agent)
by Nous Research — a Python-native agentic framework that operates external
systems through MCP (Model Context Protocol) tools. "pathiel" is the
**MCP server** + **trading engine** that Pathiel Agent operates as one of its
skills. Pathiel is the driver; pathiel is the car.

Two things follow from this design choice:

1. **The trading engine has zero Pathiel-framework dependency.** It runs as a
   plain Python process. The MCP boundary is the only contact surface — that's
   what lets you also operate it through Claude Desktop, Cursor, or any
   MCP-aware client without changing a line of trading code.
2. **Pathiel Agent (or Claude, directly) is the operator and the only
   trader.** There is no standing engine process managing risk or exits any
   more (2026-10-23, wave 2) — every trade decision, entry and exit alike,
   runs through an explicit MCP tool call. Pathiel Agent / Claude is what
   you (a human, through it) use to inspect, configure, and direct the
   system: read a desk brief, place a bracket, sweep stranded orders, close
   a position, ask "what's open," set the mode, etc.

---

## Inspiration from Senpi

[Senpi.ai](https://www.senpi.ai/) is a hosted multi-tenant platform that
deploys AI agents to trade Hyperliquid. Three things specifically influenced
pathiel:

| From Senpi | What pathiel took |
|---|---|
| **DSL (Dynamic Stop Loss) two-phase exit** | `pathiel/agents/dsl_exit.py` is a re-implementation of the same idea: hard stop in phase 1, ratcheting trailing floor with tiered retrace in phase 2, hard timeout as a backstop. |
| **Skill-shaped trading strategies** | The `skills/pathiel-agent/` directory mirrors Senpi's per-strategy folder layout (SKILL.md + scripts/ + references/) so a Pathiel Agent skill is portable in shape, if not in runtime. |
| **MCP as the integration boundary** | Senpi exposes its proprietary backend through an MCP server; pathiel does the same with `scripts/pathiel-mcp-server.py` (59 tools). Same pattern, open implementation. |

The crucial difference: **Senpi's runtime and MCP server are closed.** Their
open skills can't execute trades without their proprietary infrastructure.
pathiel is the inverse — the **engine + MCP + skills are open**;
deploying a hosted multi-tenant version on top is your business decision.

---

## The trading pipeline

One scan cycle (default 60s, env-tunable via `PATHIEL_SCAN_INTERVAL`):

```
1. HEARTBEAT
   Pull equity, positions, daily PnL from HL via
   fetch_account_state(user, include_hip3=True) — equity sums main +
   every HIP-3 dex clearinghouse and positions include all xyz/vntl/km
   coins. Persist to .agent-memory.json. Append a `loop_heartbeat` event
   to the session log with a compact config snapshot (frac × lev, slots,
   cap, cooldown, hip3 flag) so the feed surfaces what the bot is tuned
   to do without anyone having to open .agent-config.json.

2. DSL MONITOR
   Reconcile DSL trackers with live exchange positions (rehydrate any
   trackers lost on restart; drop any whose coin closed externally).
   For each tracker, check if mark price breached its dynamic floor /
   max-loss / hard-timeout. If yes → market-close via close_position_market
   + deregister tracker + log `dsl_exit` event with realized PnL from
   the actual fill price.

3. SCAN
   perception.scan_once() fetches candles for top-N markets by 24h volume,
   runs each through the trigger engine (pct move, volume spike, breakout,
   range compression, trend strength, momentum burst). When enable_hip3=true,
   the candle-fetch budget splits into a crypto bucket
   (PATHIEL_MAX_MARKETS − PATHIEL_MAX_MARKETS_HIP3 slots) + a HIP-3 bucket
   (PATHIEL_MAX_MARKETS_HIP3 slots, default 25), each sorted by 24h
   volume independently — without this split, BTC/ETH/SOL dominate the
   single-list cut and HIP-3 swings (xyz:MU +20%, xyz:CRCL −8%, etc.)
   never surface. Daemon override is min_score=40 (was 75 — too high to
   let any non-burst composite signal through); momentum_burst still
   bypasses the score gate so a >4% / 2-bar move always emits.
   Every perception is persisted via memory.record_perception (previously
   they were processed but never stored — the memory ring buffer ran flat
   at ~6 perceptions despite 100+ trades). Log `scan` event with the
   coin list + scores.

4. PER-TRIGGER
   For each triggered coin:
     a. Pre-research cooldown — skip the paid LLM call if the coin had
        a real trade within `cooldown_min`. The execute-time cooldown
        gate is still authoritative; this just saves tokens on coins
        the gate would block anyway.
     b. TA filter (ta_filter.analyze_perception) — multi-TF EMA/RSI/ATR/ADX
        + volume confirmation. Free statistical gate before any AI cost.
        Result: CONFIRMED / WEAK / REJECTED. Momentum-burst triggers bypass.
     c. AI research (research.research) — fetches candles + news context,
        sends to OpenRouter LLM (Grok-4 or similar), parses verdict
        (LONG / SHORT / PASS) + confidence (0-1) + entry/stop/tp prices.
        memory.record_analysis() persists the result.
     d. Risk gates (risk_gates.eval_all_gates) — 11 independent gates,
        all evaluated (no short-circuit) for telemetry. Listed below.
     e. Executor (executor.maybe_execute) — defensive equity guard first
        (refuse if HL API returned equity=0). If all gates pass: size
        by equity_fraction × equity × leverage, set leverage, place
        IOC order, register DSL tracker, place backup ATR stop on
        exchange. Blocked attempts are NOT written to memory._trades —
        a previous bug had them polluting the trade log with
        size_usd=0 entries, which then tripped the cooldown gate on
        the NEXT scan and caused infinite reject loops.
     f. Log `execute` event with side, executed flag, order_id, blocked_by.
```

### The 11 risk gates

All evaluated; results recorded for telemetry. Trade blocks if any returns
`{pass: False}`. **All config keys are `snake_case`** — legacy camelCase
keys (e.g. `maxConcurrent`) are silently ignored by the gates and only
used by the old MCP-server status display.

| Gate | What it checks |
|---|---|
| `confidence` | AI confidence ≥ `min_ai_confidence` (in-code default 0.8; live config typically 0.25–0.3) |
| `max_concurrent` | Open positions < `max_concurrent` |
| `notional_cap` | Per-trade notional ≤ `max_trade_notional_usd` |
| `daily_loss` | Daily PnL > `max_daily_loss_usd` (kill switch) |
| `liquidity` | Asset-class-aware floor. Crypto: ≥ `min_market_volume_usd` (default 5M). HIP-3 (colon-namespaced): ≥ `min_hip3_volume_usd` (default 500k). Same floor would have wrongly blocked legitimately-liquid tokenized markets like `xyz:CRCL` ($4.7M) and `km:USTECH` ($1.06M). |
| `coin_filter` | Coin not in blocklist; if allowlist set, must be in it |
| `cooldown` | Same-coin cooldown elapsed (`cooldown_min`). Keys off the most-recent REAL trade in memory (blocked attempts no longer pollute this — see fix in pipeline section). |
| `opposite_guard` | No simultaneous opposite-direction position on the same coin |
| `correlation` | Crypto long correlation cap — `max_crypto_long_correlated` (default 2, often 5–8 in live). HIP-3 equity/commodity longs don't count against the crypto cap because the regime classifier strips the dex prefix and routes them to the equity/commodity class. |
| `equity_risk` | Total open notional ≤ `max_total_notional_pct × equity` |
| `market_regime` | Counter-trend trades blocked unless confidence ≥ `counter_regime_min_conf`. Per-asset-class proxy: BTC for crypto, `xyz:SP500` for equity, own ticker for commodities. |
| `news` | No binary news risk in research's news_context (Fed/CPI/earnings/etc.) |

### Why the two-stage AI gating

A single naive flow ("scan → AI → trade") burns LLM tokens on every trigger.
At scale that's $8–$52/day in API costs. The TA filter (`ta_filter.py`) is a
free deterministic check that rejects ~80% of triggers before they reach the
LLM. Result: $3–$10/day in token spend, with no measurable quality drop —
the cheap signals it kills weren't going to clear AI scrutiny anyway.

The exception is `momentumBurst`: a >4% move in 2 bars is always worth
researching, even if the slower TA signals haven't confirmed yet. Speed
matters there; the bypass exists deliberately.

---

## The DSL exit engine

`pathiel/agents/dsl_exit.py` — the most consequential single module
because it owns *when to leave*, which is the half of trading nobody talks
about.

### Two-phase logic per position

```
phase 1 — Loss protection
   Hard stop at `max_loss_pct` (default 2.5%) below entry (long) / above (short).
   Pure capital preservation. No trailing.

phase 2 — Profit locking (triggered once price moves `protect_pct` (default 1.5%) in your favor)
   Trailing floor = entry + (peak − entry) × (1 − retrace).
   `retrace` increases with profit:
       +5%   → give back 30%
       +10%  → give back 40%
       +20%  → give back 50%
       +50%  → give back 60%
   The floor only ratchets one way — never gives back locked profit.

backstop — Hard timeout
   Emergency market-close after `hard_timeout_minutes` (default 180 = 3h)
   regardless of PnL. Prevents indefinite slop on stalled trades.
```

### State persistence

The tracker registry is in-memory by default — useless across restarts.
The engine writes `.dsl-state.json` atomically on every advance (peak moves
up, floor ratchets, new register, deregister). Both the trading loop and the
FastAPI server can `load_state(force=True)` to read the latest, so the
dashboard sees the same floor the executor will act on.

### Reconciliation with the exchange

Every scan tick, `rehydrate_from_exchange(positions)`:
- Synthesizes trackers for any HL position that lacks one (entry = HL's
  `entryPx`, leverage = HL's `position.leverage.value`, time = now).
- Drops trackers for any coin no longer open (closed externally, manually,
  by the backup ATR stop, etc.).

This is the glue that makes the engine robust to restarts, manual interventions,
and external close events.

### Why this matters for marketing

The DSL engine is the difference between "I have a bot" and "I have a strategy."
A static SL/TP gives back all gains on the first retrace. The DSL ratchets
profits and produces the kind of win-rate-plus-positive-skew numbers that
make people stop and ask what you're doing.

---

## HIP-3 — tokenized equities, commodities, and indices

Hyperliquid hosts a second class of perps on separately-deployed dexes
(`xyz`, `km`, `vntl`, `flx`, `hyna`, `abcd`, `cash`, `para`). Markets are
namespaced as `<dex>:<symbol>` — e.g. `xyz:NVDA`, `xyz:SP500`, `xyz:GOLD`,
`km:USOIL`, `km:US500`. Enabled with `"enable_hip3": true` in
`.agent-config.json`. **Restart the loop** after flipping the flag — the
universe is fetched once at startup.

The flag is honored at three entry points so the bot can scan, score, size,
and execute these markets end-to-end:

| Entry point | What changes when `enable_hip3=true` |
|---|---|
| `client/universe.get_universe(include_hip3=True)` | Auto-discovers registered HIP-3 dexes via `/info perpDexs` and merges each dex's `metaAndAssetCtxs` into the unified market list. Each market dict gets `dex: "<name>"` (None for native HL). |
| `client/hl_client.fetch_all_mids(include_hip3=True)` | Adds one HTTP POST per HIP-3 dex (~8 total) so colon-namespaced mids are populated in the scanner. |
| `client/exchange.Info / Exchange(perp_dexs=[""] + hip3)` | Teaches the HL SDK to resolve colon names at order placement. **CRITICAL: the empty string `""` must be prepended** — the SDK treats the list as exclusive. Pass only HIP-3 dexes and BTC/ETH start raising `KeyError` at `update_leverage` / `order`. |
| `client/hl_client.fetch_account_state(user, include_hip3=True)` | Queries each HIP-3 dex's `clearinghouseState` in addition to main, sums `equity` + `total_ntl`, concatenates `asset_positions` (prefixing bare HIP-3 coin names with `<dex>:`), and exposes a per-dex breakdown under `dex_equity`. `available` stays main-only (see "Per-dex equity aggregation" below). |
| `agents/perception.scan_once` | Splits the candle-fetch budget into a crypto bucket + a HIP-3 bucket (`PATHIEL_MAX_MARKETS_HIP3`, default 25 of the 60-slot total) so tokenized-equity markets get sorted independently and aren't crowded out by BTC/ETH/SOL volume. |

### Asset-class routing

`agents/market_regime.classify_asset()` strips the dex prefix before lookup
so `xyz:NVDA` correctly lands in `_EQUITY_COINS` (not crypto) and uses
`EQUITY_PROXY = "xyz:SP500"` for its regime-trend check. Tokenized
commodities (`xyz:GOLD`, `xyz:CL`, `km:USOIL`, etc.) route to `commodity`
and use their own candle stream as the regime proxy.

### Liquidity tier split

Crypto perps and HIP-3 markets have different liquidity profiles — most
`xyz:*` markets are in the $1M–$50M range vs $1B+ for BTC. The
`market_liquidity_floor` gate uses two configurable floors keyed off
whether the coin contains a colon:

- `min_market_volume_usd` (default 5,000,000) — crypto floor
- `min_hip3_volume_usd` (default 500,000) — HIP-3 floor

Thin HIP-3 (e.g. `hyna:XRP` $33k) still correctly blocks; mid-volume
tokenized equities flow.

### Per-dex equity aggregation

HIP-3 dexes are **separate clearinghouses** — main-account USDC does not
back trades on `xyz`/`vntl`/`km` unless transferred in. `fetch_account_state`
takes `include_hip3` precisely so callers can choose between two semantics:

| Caller | `include_hip3` | Why |
|---|---|---|
| Dashboard equity card, portfolio API, trading-loop heartbeat, MCP `state`/`portfolio`/`close`, CLI `account`, dashboard chat (`positions`, `close all`, `kill`, `dump`, LLM context) | `True` | The operator wants to see total tradeable USDC across every dex they hold, and every open position regardless of which clearinghouse it sits on. |
| Executor `maybe_execute` sizing path, research context | `False` | Sizing is keyed off main-dex free margin. If `equity` silently included idle xyz/vntl USDC, `trade_notional = equity × fraction × leverage` would oversize main-dex trades and HL would reject with "insufficient margin". |

The aggregated path:
1. Calls `/info clearinghouseState` for the main dex (unchanged).
2. Calls `list_hip3_dexes()` and issues one `clearinghouseState` POST per
   HIP-3 dex with `{"dex": "<name>"}` (~8 extra POSTs, weight 2 each).
3. Sums `marginSummary.accountValue` → `equity`; sums `totalNtlPos` → `total_ntl`.
4. Walks each dex's `assetPositions` and, if the position's `coin` field is
   bare (HL's response varies — some endpoints return `MU`, others
   `xyz:MU`), prepends `<dex>:`. This keeps DSL tracker keys, dashboard
   joins, and downstream coin-comparison code consistent with what
   perception emits.
5. Exposes `dex_equity = {"": main_equity, "xyz": ..., "vntl": ..., ...}`
   so the dashboard can show where USDC sits.

`available` (used for the executor's free-margin gate) is left as
main-only by design — HIP-3 free margin only backs trades on that dex,
so cross-dex summing would mislead the gate.

The executor still does its per-dex preflight (`executor.maybe_execute`
lines ~94-126) for HIP-3 trades: it queries the *specific* dex's
clearinghouse before sending the order and refuses cleanly with
`hip3_dex_underfunded` rather than letting HL reject with a confusing
"insufficient margin" error.

### The `get_hl_price` gotcha (silent-kill bug, since fixed)

`info.all_mids()` returns ONLY the native HL perp dex. Colon-namespaced
coins need `info.all_mids(dex=<prefix>)`. Before the fix, every HIP-3
trade attempt died at `if mid_price <= 0: return invalid_price_for_X`
before reaching the order endpoint — no log, no exception, just silently
no trade. Both `get_hl_price()` (`client/exchange.py`) and
`fetch_all_mids(include_hip3=True)` (`client/hl_client.py`) now derive
the dex from the prefix when the coin is namespaced.

### Off-hours behavior

HIP-3 equity markets only trade during US equity hours; outside those
hours volume drops to ~zero, so the scanner naturally skips them
(filtered by `min_hip3_volume_usd`). No explicit hours-gate is
implemented — the volume floor handles it.

See `skills/pathiel-agent/references/hip3-tokenized-equity-handoff.md`
for the original task brief and the post-implementation audit findings.

---

## Persistence layer — four files

| File | Owner | What it holds | TTL |
|---|---|---|---|
| `.agent-config.json` | operator + UI | live trading knobs: mode, sizing, risk caps, DSL params, regime thresholds | persistent |
| `.agent-memory.json` | trading loop | rolling cache of perceptions, analyses, trades, watchlist, cooldowns, equity history | persistent |
| `.dsl-state.json` | DSL engine | per-position trackers (peak, floor, breach counter, leverage, policy) | persistent |
| `~/.pathiel-session-log.jsonl` | every component | append-only event log: heartbeat, scan, entry_preflight, ta_skip, research, execute, dsl_exit, error | rolling |

All four are env-overridable for containerized deployment (see `Dockerfile`
and `fly.toml`). On Fly they live under `/data/` on a mounted volume so they
survive rolling deploys.

The session log is the **single source of truth** for everything the dashboard
and `preflight_live.py` report. Components write to it; readers tail it. No event bus,
no pubsub, no database — JSONL on disk has been entirely sufficient at one-
user scale.

---

## The MCP server

`scripts/pathiel-mcp-server.py` — 59 tools over MCP stdio. The contract that
lets Claude (and any MCP client) operate the oil desk.

Tool categories:

| Category | Examples | Count |
|---|---|---|
| Desks | `desk_list`, `desk_brief`, `desk_refresh`, `desk_submit_call`, `desk_track_record`, `desk_grade` | 6 |
| Trading core | `place_bracket`, `bracket_sweep`, `close_position`, `set_leverage`, `cancel_order`, `state`, `config` | 7 |
| Account and risk | `account_overview`, `accounts`, `risk_limits`, `risk_size_trade`, `connected_wallets`, `wallet_account` | 6 |
| Market data | `market_get_asset_data`, `market_get_funding_regime`, `market_list_instruments`, `get_candles`, `get_price`, `get_l2_book`, etc. | 21 |
| Direct HL account/venue passthrough | order history, fills, funding, deposits/withdrawals, rate limits, etc. | 19 |

Full table, regenerated from `TOOLS`: `docs/overhaul/MCP.md`.

There are no stubs left as of the 2026-10-17 shore-up — every tool returns
real data. The 2026-10-23 headless-oil-desk cut removed 22 tools outright
(80 -> 58): the autonomous bot's brain path (`scan`, `research`,
`submit_verdict`, `execute` — Claude now places trades through
`place_bracket`/`bracket_sweep`), the leaderboard/discovery pair, and
staking/validator/vault/referral/rewards/spot/network reads the oil workflow
never used. The same day, `desk_grade` landed (80 -> 59 net): grades every
expired desk thesis's trades against it, no order path.

---

## The web dashboard

**Rebuilt 2026-10-23** for the headless oil desk (docs/overhaul/
DESK_UI_2026-10-23.md has the full keep/delete audit). The autonomous bot
is retired; Claude runs a 15-minute MCP loop
(`.claude/skills/oil-tick/SKILL.md`) and the operator trades their own
wallet by hand from the same thesis. Everything the old Tamagotchi-styled,
multi-tab bot-watching dashboard existed for — scan feed, book panels, the
AI-brain trend tab, trade tape, decision funnel — is gone with the bot.
Three pages are left:

- `GET /` — **the Desk page** (`pathiel/dashboard.py` serves
  `pathiel/templates/oil_desk.html`). Parameterized by desk id (agent
  config `active_desks`, default `["oil"]`): the active desk's current
  call plus the 20x line computed from its own entry/stop
  (`pathiel/agents/bracket.py`), one row per registered wallet with a
  NAKED badge for an uncovered position, candle-derived levels, headlines
  and catalysts, the loop's own progress log, and the track record. Shows
  an onboarding wizard (connect account → risk preset → done) first for a
  brand-new visitor; the same page becomes the Desk once an account is
  connected.
- `GET /desk/{id}` — **Thesis**, served by `services/strategies/api.py`
  (`pathiel/templates/desk.html`), default `oil`: the full reading room —
  call, leverage picker (same 20x line as `/`, plus the probabilistic
  ladder), scenarios, catalysts, bull vs bear, what changes my mind, desk
  school, track record, and a form to submit your own call.
- `GET /account` — **Account**, served by `pathiel/dashboard.py`
  (`pathiel/templates/account.html`): the connected Hyperliquid account
  (or a watch-only address) and the risk preset with a live preview.

Supporting JSON routes: `services/strategies/api.py`'s `/api/desks/...`
(desk briefs, the 20x line, candle levels, the loop log, which desk(s) are
active) and `services/accounts/api.py`'s `/api/accounts/{id}/positions`
(the Wallets section's per-account, per-dex position + stop-coverage read).
`pathiel/dashboard.py` keeps only what nothing else replaced:
`/api/dashboard/account` (a signed-in wallet's own balance, read-only) and
the operator-gated `/api/dashboard/risk` + `/positions`, kept because
`pathiel/metrics.py`'s `/metrics` endpoint and their own dedicated test
suites read them independent of any page.

### UI layer — vanilla, boring on purpose

Plain server-rendered HTML + vanilla JS, no build step, no framework —
deliberately boring (the 2026-10-19 "v4" redesign's own brief: "something a
person with a million dollars would use"), which the Desk rebuild kept.
`static/pathiel.js` (shared formatters, the wallet-connect mount, the
`pollEvery` poll scheduler that never stacks a slow network read behind
itself) and `static/desks.css`/`desks.js` (the desk-card/leverage-radiogroup
styling and behavior, shared between `/` and `/desk/{id}`) are the only
cross-page assets; `oil_desk.js` is the Desk page's own script. One
stylesheet (`static/app.css`, compiled by `scripts/build_static_css.py` —
the gate asserts it is byte-exact with what that script emits), one design
system, no CDN dependency, no emoji (crafted inline SVG instead), tabular
figures for money. `services/wallet_ui`'s React bundle is the one exception
— it renders the wallet-connect modal into a single mount point every page
carries, loaded on demand (never on page load) so a visitor who never opens
it never pays for its ~3 MB.

---

## Why pure Python

Several layers of the codebase do things Python is genuinely best at:

| Layer | Python advantage |
|---|---|
| TA indicators (EMA, RSI, ATR, ADX) | numpy ecosystem; existing battle-tested implementations |
| Backtesting | pandas / vectorized ops |
| LLM research pipeline | first-class Anthropic / OpenAI / OpenRouter SDKs |
| HL trading client | mature `hyperliquid-python-sdk` |
| MCP server | reference Python MCP SDK with stdio transport |
| FastAPI internal API | uvicorn + async stdlib |

TypeScript wins for the browser layer (viem/wagmi/RainbowKit; Next.js for the
hosted product), but the engine has no business being there. Doing both means
two test suites, two CIs, two deploy paths — a solo-founder tax that buys
nothing except matching Senpi's runtime stack.

The repo's `python` branch is also `main`. TypeScript artifacts on older
branches are archived. Don't reanimate them.

---

## What the directory layout reflects

```
pathiel/
├── pathiel/          # the engine — importable as a package
│   ├── agents/             # the strategy logic
│   │   ├── perception.py        # scanner: volume-pre-filtered parallel scan
│   │   ├── ta_filter.py         # multi-TF gate, pre-AI
│   │   ├── research.py          # AI research via OpenRouter
│   │   ├── executor.py          # Kelly sizing + risk gates + place order + DSL register
│   │   ├── dsl_exit.py          # two-phase trailing stop engine + persistence
│   │   ├── risk_gates.py        # 12 independent gates (incl. market_regime)
│   │   ├── market_regime.py     # per-asset-class regime detection (new)
│   │   ├── memory.py            # disk-backed singleton state
│   │   ├── config.py / config_store.py  # config read/write
│   │   ├── book_params.py       # sizing-floor resolver for every entry (stop/leverage/notional)
│   │   ├── data_logger.py       # funding/OI recorder; its dashboard panel was removed 2026-10-23
│   │   ├── shadow_ledger.py     # forward evidence record (thin_short_relax and any future experiment)
│   │   ├── hyperfeed.py         # leaderboard + whale-flow + OI anomaly (Hyperfeed clone)
│   │   ├── whale_index.py       # whale tracking on top of public HL endpoints
│   │   └── system_prompt.py     # the LLM's operating instructions
│   ├── client/             # HL + WS + caching plumbing
│   │   ├── exchange.py          # order placement, leverage, trigger orders
│   │   ├── hl_client.py         # REST: account state, candles, mids, funding
│   │   ├── ws_client.py         # WebSocket for sub-second mids
│   │   ├── universe.py          # volume-ranked market loader
│   │   ├── cache.py / lock.py / parallel.py / daemon.py / __init__.py
│   ├── indicators/         # math
│   │   ├── math.py              # EMA, SMA, ATR, RSI, ADX
│   │   └── triggers.py          # trigger detection + composite scoring
│   ├── models/types.py     # Candle (OHLCV)
│   ├── server.py           # FastAPI: JSON API + routes, session gate, CSP
│   ├── dashboard.py        # the Desk page's HTML + house routes (operator-only)
│   └── session_log.py      # JSONL append-only event log
├── services/               # independent units, contracts at the boundary
│   ├── auth/                    # wallet sign-in (EIP-4361) + sessions + API keys
│   │   ├── siwe.py              # stateless message parse + verify
│   │   ├── store.py             # users/sessions/nonces, SHA-256 only, SQLite
│   │   ├── api_keys.py          # customer keys, owned by the minting wallet
│   │   ├── deps.py / api.py     # cookie-or-Bearer dependency, routes
│   │   └── tests/               # 50 tests, each named for the attack it stops
│   ├── trend_engine/            # 7d HL regime + recorder P&L; its dashboard tab was removed 2026-10-23
│   └── pathiel_data_api/         # market-data product (own deploy unit)
├── scripts/
│   ├── trading_loop.py          # the autonomous loop (long-running)
│   ├── pathiel-mcp-server.py     # MCP stdio server, 59 tools
│   ├── grant_operator.py        # grant/revoke the operator role for a wallet
│   └── backtest.py              # historical-candle backtest
├── skills/pathiel-agent/  # Pathiel Agent skill (operator's manual + helper scripts)
├── tests/                       # offline unit + online + live-e2e
├── research/                    # regime reads + pre-registered hypotheses
├── docs/                        # this file + journal-schema
├── Dockerfile / fly.toml / DEPLOY.md   # one-machine Fly deploy
└── .env.local / .agent-config.json / .agent-memory.json / .dsl-state.json
```

---

## Scaling — honest current state and paths forward

### What works at one-user scale today
- Single wallet, ~10 concurrent positions, multi-asset (crypto + equity + commodity)
- 60s scan cycle over top 60 markets stays under HL's 1200 weight/min rate limit
- JSONL log + 4 JSON state files is sufficient persistence — no DB needed
- Dashboard handles a single instance; SSE feed scales to ~100 simultaneous viewers
  on a free Fly tier

### Where it would break
| Threshold | What breaks | Fix |
|---|---|---|
| 100s of scanned markets | HL rate limit at full scan (each candle fetch = weight 20) | Already mitigated: volume pre-filter + 15min candle cache. To go further, batch-aggregate the meta fetch. |
| Multiple strategies on one wallet | Single `while True` loop, one config, no per-strategy isolation | **Producer/runtime split** — see below |
| Multiple users | Single in-memory state, single wallet env var, no auth | Multi-tenant rewrite (months) |
| Real-time UI updates beyond ~10/s | SSE polling tail at 1Hz | Push from the loop directly into an in-memory pubsub |

### The next high-leverage refactor — producer/runtime split

Today, `executor.maybe_execute()` is called inline from the loop, which is
itself a single `while True` with one config. To run multiple strategies
concurrently you need:

1. **Producer interface**: each strategy is a class/module that yields signals
   `{coin, side, confidence, reasoning, …}` on its own schedule.
2. **Runtime**: hosts N producer instances, owns sizing/gates/orders/DSL,
   routes each producer's signals through its own configured risk profile
   (per-strategy allowlists, fraction, leverage, daily-loss).
3. **HL sub-accounts**: each strategy P&Ls in its own sub-account so one
   blow-up doesn't poison the others.

Senpi does exactly this with their `senpi-trading-runtime`. The skills they
publish are pure producers. Ports cleanly to our codebase if/when you decide
to host multi-strategy.

### The path to a hosted product (Senpi-class)

Roughly in order:

1. **Producer/runtime split** (above) — unblocks everything below
2. **Per-strategy sub-accounts** — clean P&L isolation
3. **Mirror trader strategy** — first-class "copy this HL trader" producer
4. **Telegram bot for alerts + control** — viral mechanic + better operator UX
5. **Web dashboard auth** — multi-tenant viewing
6. **Strategy marketplace UI** — discover + subscribe to others' strategies
7. **Hosted runtime + billing** — sales + ops

Steps 1–4 are weeks of work each, solo-doable. Steps 5–7 are months and
require team. The 90-day plan (in your conversation, not this doc) prioritizes
1–4 + public-wallet marketing in parallel.

### What NOT to scale prematurely

- **Don't add Redis / Postgres** until JSONL + atomic file writes actually
  break. They haven't.
- **Don't rewrite in TypeScript.** The engine has no business in a JS runtime;
  see "Why pure Python" above.
- **Don't add Kafka / message queue.** The session log is the message queue.
- **Don't add a worker farm.** One process per role (server, MCP) has
  been enough; horizontal scaling means an ops surface you don't want yet.

---

## Operating the engine

Two roles, two entry points (the autonomous trading loop this section used
to list as a third is deleted, 2026-10-23, wave 2 — there is no process
left to scan a market or research a signal on its own):

```bash
# 1. The web dashboard + JSON API (port 8000)
python3 -m pathiel.server

# 2. The MCP stdio server (driven by Pathiel Agent / Claude Desktop / Cursor)
python3 scripts/pathiel-mcp-server.py
```

Each loads `.env.local` and reads/writes the same on-disk state. On Fly they
run as two separate `processes` in `fly.toml` sharing a mounted volume; the
MCP server is local-only (stdio doesn't deploy).

Tail what's happening:

```bash
# Last 50 session-log events
curl -s -H "X-Operator-Token: $PATHIEL_OPERATOR_TOKEN" \
  http://localhost:8000/api/agent/session-log

# Browser dashboard
open http://localhost:8000
```

---

## Telemetry-driven debugging

Almost every behavior the engine takes appends to the session log with an
event name + structured payload. Patterns worth knowing:

| Symptom | What to grep in the log |
|---|---|
| "Why didn't it open trade X?" | `grep '"event": "execute"' \| grep <COIN> \| jq .blocked_by` |
| "Did the regime gate fire?" | `grep counter-regime` |
| "Was the close at the right price?" | `grep '"event": "dsl_exit"' \| jq '{coin, fill_px, realized_pnl_pct}'` |
| "When did equity move?" | `grep loop_heartbeat \| jq '{ts, equity}'` |
| "What scan triggered last?" | `grep '"event": "scan"' \| tail -1` |

The dashboard's `/api/feed/stream` is the same log, JSON-decoded and
glyph-formatted. The operator console's tracker view is the same `.dsl-state.json`
the engine writes to. There's one source of truth for each thing.

---

---

## Using pathiel — the operator's manual

Three audiences, three workflows: you-the-human via Pathiel Agent for ad-hoc
operation, you-the-human via the dashboard for live monitoring, and the
trading loop running headless for autonomous execution.

### General use (no Pathiel Agent)

The minimum to get from "I cloned this" to "the bot is trading":

```bash
# 1. Install
git clone https://github.com/Julian-dev28/pathiel
cd pathiel
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure
cp .env.local.example .env.local
# Edit: OPENROUTER_API_KEY, HYPERLIQUID_WALLET_ADDRESS, HYPERLIQUID_PRIVATE_KEY
# Optional: PATHIEL_OPERATOR_TOKEN (for the operator console), BRAVE_API_KEY (news)

# 3. Start in OFF mode first
echo '{"mode":"OFF"}' > .agent-config.json

# 4. Run the dashboard
python3 -m pathiel.server &      # dashboard at http://localhost:8000

# 5. Watch the desk page — equity, open positions, the active desk's thesis
open http://localhost:8000

# 6. When you're satisfied, flip mode to LIVE
#    Either edit .agent-config.json directly, or use /operator?token=… and click "set mode LIVE"
#    Then trade through the MCP server (place_bracket / bracket_sweep) — see README.md
```

The config is read **fresh on every trade** — no restart needed for changes.
Same for risk caps, leverage, allowlists.

### Hands-off operating via Pathiel Agent (the MCP path)

If you have Pathiel Agent installed and the MCP server registered, you operate
the engine in plain English:

```yaml
# ~/.pathiel/config.yaml
mcp_servers:
  pathiel:
    command: python3
    args: [/Users/you/path/to/pathiel/scripts/pathiel-mcp-server.py]
    cwd: /Users/you/path/to/pathiel
    timeout: 60
```

Then in a Pathiel session:

| Intent | Prompt |
|---|---|
| **Check state** | *Load the pathiel skill and show me its current state — mode, equity, open positions, recent trades.* |
| **Tune** | *Set pathiel to LIVE mode with `max_concurrent: 5` and `equity_fraction_per_trade: 0.025`.* |
| **One-shot cycle** | *Run a pathiel scan, research the best candidate, and execute it if the verdict is LONG or SHORT with confidence ≥ 0.7. Tell me what happened.* |
| **Start continuous** | *Start the pathiel trading loop in the background. Confirm it is running.* |
| **Stop continuous** | *Stop the pathiel trading loop. Don't close existing positions.* |
| **Status (in-session)** | *Check pathiel status. Highlight anything that changed since the last report.* |
| **Manual close** | *Close my pathiel position in TSLA. Show me the realized PnL.* |

The skill at `skills/pathiel-agent/` carries the system prompt, the
`feed.py` / `status.py` helper scripts, and reference docs that Pathiel Agent
loads as context for every session. So the agent knows the conventions —
session-log glyphs, gate names, restart ritual — without you having to
re-explain.

### Live monitoring via the dashboard

The dashboard at `/` is **read-only and public-safe** — no token required.
What you see:

- **Hero KPIs**: equity, today's PnL, open positions, last-tick age + scan status pill
- **Equity curve**: LTTB-smoothed line over 24h / 7d / 30d windows
- **Open positions**: leveraged ROE matching HL's display, DSL floor + phase per position
- **Recent closes**: realized PnL net of taker fees, with `~estimated` marker on pre-fill-capture trades
- **Live feed**: SSE stream of every event the engine emits, with hover-tooltips for AI reasoning + full prices

The operator console at `/operator?token=<PATHIEL_OPERATOR_TOKEN>` adds:
- **Config viewer** — current `.agent-config.json` rendered as JSON
- **DSL tracker viewer** — every position's peak/floor/phase/leverage
- **Force-close buttons** — one click per coin to market-close + deregister
- **Mode toggle** — flip OFF ↔ LIVE without editing config files

The operator endpoints check `X-Operator-Token` or `?token=` query param
on every request; missing env var → 503, wrong token → 401.

### Tailing from the terminal (no browser)

```bash
# Last 50 session-log events
curl -s -H "X-Operator-Token: $PATHIEL_OPERATOR_TOKEN" \
  http://localhost:8000/api/agent/session-log
```

---

## The skill scaffolding

`skills/pathiel-agent/` is a self-contained Claude Code skill covering how
to operate the MCP server and the Hyperliquid order-placement gotchas.
2026-10-23 (wave 2): the scan/research/execute engine and its cron-based
hourly status report are retired along with it (`feed.py`, `status.py`,
and the reference docs that were specific to that engine —
`cron-jobs.md`, `restart-sequence.md`, `signal-vs-action-gap.md`,
`trading-mode.md`, `daemon-investigation.md`, `exit-engine.md`,
`short-regime-bias.md`, `hip3-tokenized-equity-handoff.md`).

```
skills/pathiel-agent/
├── SKILL.md                       # how to operate the headless desk:
│                                    place_bracket/bracket_sweep/
│                                    close_position, risk sizing, the
│                                    state files that still exist
├── scripts/
│   └── audit_mcp_server.py        # verifies the MCP server is healthy
└── references/
    ├── hyperliquid-gotchas.md     # tick-size / sig-fig / IOC fill quirks
    ├── mcp-config.md              # ~/.pathiel/config.yaml block
    └── mcp-server.md              # the 59-tool surface
```

The real-time "what is Claude doing" view now is `.claude/skills/
oil-tick/SKILL.md`'s own per-tick log line
(`/tmp/oil-loop/progress.log`) and the dashboard at `http://localhost:8000`
— there is no separate cron-driven status report to wire up.

---

## Backtesting methodology

Deleted 2026-10-23 (wave 2) along with the autonomous AI-verdict pipeline it
replayed: the backtest CLI ran the scan/TA-filter/risk-gates pipeline (all
deleted the same wave) against historical candles. There is no equivalent
for the MCP-submitted path — Claude's calls are one-off tool invocations, not
a replayable strategy function — so there is nothing left to drive a
backtest with. The desk-grading service (`desk_grade` / `services/
strategies/facade.py`) grades what actually happened, not a replayed
what-if.

---

## Risk modeling — Kelly, retrace tiers, daily killswitch (historical)

**This whole section describes `pathiel/agents/executor.py` and `dsl_exit.py`
internals, both deleted 2026-10-23 (wave 2) with the rest of the autonomous
executor.** Left as a record of the design reasoning (half-Kelly sizing, the
retrace-tier trailing-stop schedule, the daily killswitch and its known
equity-spike bug) rather than rewritten — it documents a mechanism that
existed, and that doesn't change retroactively. It does NOT describe how the
live system sizes or exits a trade today: that is `services/risk`
(`risk_size_trade`, a fixed risk-per-trade percent of equity against the
operator's saved profile) and an exact stop/target Claude names on every
`place_bracket` call — there is no Kelly fraction, no retrace tier, and no
daily-PnL killswitch in the current build.

### Half-Kelly sizing (in `executor.kelly_size`)

The classical Kelly criterion says: **fraction of capital to bet** =
`(p × b − q) / b` where:
- p = win probability (= AI confidence)
- q = 1 − p
- b = reward-to-risk ratio (= tp distance / sl distance)

Full Kelly maximizes long-run growth but is brutally volatile (it'll cut
your capital in half from peak with high probability before recovering).
Standard practice is **half-Kelly** — divide the result by 2 — which gives
~75% of full Kelly's expected growth at ~half the volatility.

In the executor:

```python
def kelly_size(confidence, equity, reward_risk_ratio, max_trade_notional):
    p = confidence; q = 1 - p; b = reward_risk_ratio
    f_star = max(0, (p * b - q) / b) if b != 0 else 0
    half_kelly = f_star / 2
    return min(half_kelly * equity, max_trade_notional)
```

**This is the historical implementation.** The *live* sizing in the current
build actually uses a simpler fixed-fraction:
`equity × equity_fraction_per_trade × leverage`. The Kelly function is kept
as a reference implementation and is the model to switch back to when AI
confidence + tp/sl ratios are well-calibrated. The fixed-fraction sizing
is simpler to reason about during the calibration phase.

### Retrace tier theory (in `dsl_exit.ExitPolicy.phase2_tiers`)

Phase 2's trailing floor:

```
floor = entry + (peak − entry) × (1 − retrace_threshold)
```

`retrace_threshold` is **how much of the peak profit you're willing to
give back** before exiting. Lower → tighter trailing → more whipsaw. Higher
→ looser trailing → more drawdown but bigger winners.

The tiered design says: **as a position becomes a bigger winner, tighten
the trail**. Defaults:

| Profit % above entry | Retrace threshold | Floor after pullback from peak |
|---|---|---|
| 1.5% – 5% | 30% | entry + 70% of (peak − entry) |
| 5% – 10% | 40% | entry + 60% of (peak − entry) |
| 10% – 20% | 50% | entry + 50% of (peak − entry) |
| 20% – 50% | 60% | entry + 40% of (peak − entry) |
| 50%+ | (cap at last tier) | entry + 40% of (peak − entry) |

Why tighten with profit? Because mean-reversion probability rises as a move
extends — a position that's +50% is more likely to give back 50% than a
position that's +3% is to give back 50%. The tier schedule reflects this
empirically rather than via a fitted model.

**To tune more aggressively**: shift the entire tier schedule lower (e.g.
20%/30%/40%/50%). You'll close winners faster but with smaller average
realized gain.

**To tune more loosely**: shift higher (e.g. 40%/50%/60%/70%). You'll ride
bigger winners but eat more giveback.

The tier schedule is per-position via `register_position(policy=…)` so
different strategies can use different aggressiveness. The default applies
to all positions opened by the standard executor path.

### Daily killswitch + the equity-spike bug

`max_daily_loss_usd` (default -$100, often tuned tighter) stops new trades
for the rest of the UTC day when realized PnL drops below it. Cap-only —
existing positions still get DSL-managed.

**Known weakness (pending fix):** the heartbeat computes daily PnL from
HL's `accountValue`, which occasionally returns `0` on a transient API
hiccup before recovering. A zero reading mid-day looks like a catastrophic
loss and trips the killswitch on phantom data. The mitigation is to
sanity-check the heartbeat: if equity drops >50% in a single 60s tick AND
no `dsl_exit` event explains it, treat the reading as transient and don't
update daily PnL until the next clean tick. **TODO** in the loop.

### Risk gate composition philosophy

The 12 gates evaluate in parallel (no short-circuit) and the trade blocks
if any returns `pass: False`. Two consequences:

1. **You always see all gate results in the execute event's telemetry,
   even on a block.** This makes "why didn't the trade go through" trivially
   answerable from the session log.
2. **Adding a gate is additive.** Composition is a frozenset; you can't
   accidentally weaken safety by adding more gates, only by removing one.

The 12 gates split into three layers conceptually:

- **Position-level** (confidence, opposite_guard, cooldown) — about *this*
  trade in *this* moment.
- **Portfolio-level** (max_concurrent, notional_cap, equity_risk,
  correlation) — about how this trade fits the rest of your book.
- **Macro-level** (daily_loss, liquidity, coin_filter, market_regime,
  news) — about external conditions independent of your book.

When a strategy fires too much or too little, look at which layer is
binding. The session log's `gate_results` payload tells you exactly.

---

## Specific trade ideas + tuning recipes by market regime

The strategy is designed to work across regimes, but the *parameters* you
want differ by what BTC is doing. These are starting points, not gospel —
backtest before flipping things in `.agent-config.json`.

### Regime: BTC strong uptrend (EMA20 >> EMA50, slope > +1%/5d)

Posture: **trend-follow, accept some giveback.**

```json
{
  "min_ai_confidence": 0.45,         // lower — riding the trend is easier
  "counter_regime_min_conf": 0.85,   // much higher — counter-shorts rarely worth it
  "equity_fraction_per_trade": 0.04, // larger size; correlation works in your favor
  "dsl_exit": {
    "max_loss_pct": 3.0,             // wider stop — trend pulls back further than 2.5%
    "protect_pct": 2.0,              // require more cushion before locking
    "retrace_threshold": 0.45        // wider trail — let winners run further
  }
}
```

Best signal types: **momentum_burst, trend_strength, breakout** (high-beta
crypto + equity perps).

### Regime: BTC strong downtrend

Posture: **trend-follow the bear, tight risk.**

```json
{
  "min_ai_confidence": 0.45,
  "counter_regime_min_conf": 0.85,   // counter-longs only on real conviction
  "equity_fraction_per_trade": 0.025,
  "dsl_exit": {
    "max_loss_pct": 2.0,             // tighter — bear rallies are sharp
    "retrace_threshold": 0.30        // tighter trail
  }
}
```

Best signal types: **breakout (to the downside), volume_spike on selloffs**.

### Regime: chop / neutral (EMA cross near zero, ADX < 20)

Posture: **mean-revert, smaller size, faster exits.**

```json
{
  "min_ai_confidence": 0.65,         // higher bar — fewer trades, better quality
  "counter_regime_min_conf": 0.65,   // symmetric — no directional bias
  "equity_fraction_per_trade": 0.02, // smaller — chop wears you down
  "dsl_exit": {
    "max_loss_pct": 1.8,
    "protect_pct": 1.0,
    "retrace_threshold": 0.25,
    "hard_timeout_minutes": 90       // close faster — chop doesn't pay to hold
  }
}
```

Best signal types: **range_compression, oi_funding_anomaly** (fade
crowded positioning).

### Regime: high-VIX / macro event window (CPI, FOMC, earnings cluster)

Posture: **stand down, or only trade with massive conviction.**

```json
{
  "min_ai_confidence": 0.85,
  "counter_regime_min_conf": 1.01,   // effectively disable counter-trades
  "max_concurrent": 3,
  "max_daily_loss_usd": -15,         // tight killswitch
  "max_total_notional_pct": 3.0      // 3x equity max — way under normal
}
```

The `news_blackout` gate already handles this for explicit FOMC/CPI
keywords. The above is the manual posture for "I know macro vol is high
and I want the bot to be cautious for 24h".

---

## Wallet security

Trading from your **personal main wallet** is wrong on every axis. Do this:

### Recommended setup: agent wallet + master wallet

Hyperliquid supports an **agent wallet** mechanism: a separate keypair you
authorize to *act on behalf of* a master wallet without being able to
*withdraw* from it. The master holds the funds; the agent signs orders.

```bash
# In HL UI:
# 1. Generate or import a fresh wallet for the bot (never reuse a key)
# 2. Master wallet → "Add Agent Wallet" → paste the agent's address
# 3. Agent is now authorized to trade for the master, but can NOT withdraw
```

Then in `.env.local`:

```bash
HYPERLIQUID_MASTER_ADDRESS=0x...    # the wallet that holds funds (read-only here)
HYPERLIQUID_WALLET_ADDRESS=0x...    # the agent wallet (used to sign orders)
HYPERLIQUID_PRIVATE_KEY=0x...       # the AGENT's private key — NOT the master's
```

**The master's private key never touches the bot's filesystem.** If the
agent key leaks (bot machine compromised, key checked in by accident,
malicious dependency), the worst case is the attacker trades your funds
into a loss — they can't drain them.

### Key hygiene checklist

- `.env.local` is gitignored (verify before any first commit).
- Use a separate machine / VM / Fly instance for the bot — not your
  personal laptop where you also browse the web.
- Rotate the agent key every 90 days; HL agent wallets revoke instantly.
- `PATHIEL_OPERATOR_TOKEN` is in `.env.local` (not in `fly.toml`'s env
  block); rotate quarterly or after every "did I share that screen?" moment.
- The dashboard URL is **public**; the operator URL is `?token=…` which is
  in your browser history. Use a long-lived OS keychain entry to retrieve
  it rather than pasting into chat / docs.

### Things that should NEVER be in the repo

```
HYPERLIQUID_PRIVATE_KEY    OPENROUTER_API_KEY    PATHIEL_OPERATOR_TOKEN
BRAVE_API_KEY              .agent-memory.json    .dsl-state.json
```

All of these are in `.gitignore`. If you ever commit one by accident,
**rotate the key immediately** — `git rm --cached` doesn't remove it from
remote history. Treat the key as burned.

### Capital limits

Don't fund the agent wallet with more than you can afford to lose **and
to lose to a bug**. The DSL engine is robust but it's not formally
verified; a single bad `mark_px` reading, an off-by-one in size precision,
a network partition during a close — any of these can lose money faster
than you can intervene. Run with the smallest balance that the strategy
can produce meaningful signal on. The bot should be a research instrument
first, a money-maker second.

---

This is the operator's manual. The pipeline + module map earlier in the
doc is the **map**; this section is the **owner's manual**. Together they
should let you (or anyone) operate the engine confidently without
spelunking through the source.
