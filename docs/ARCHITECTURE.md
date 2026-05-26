# hermes-trader — architecture

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
HL market data ──▶ Scanner ──▶ TA filter ──▶ AI research ──▶ Risk gates ──▶ Executor ──▶ HL orders
                                                                              │
                                                                              ▼
                                                                          DSL exit
                                                                          (per-tick)
                                                                              │
                                                                              ▼
                                                                       Auto-close on
                                                                       floor/stop/timeout

Persistent state on disk:  .agent-memory.json  .agent-config.json  .dsl-state.json  session-log.jsonl

Two entry processes:
  scripts/trading_loop.py     — autonomous: scans, decides, executes, exits, repeats
  hermes_trader/server.py     — FastAPI: public dashboard + token-gated operator + JSON API + SSE feed
  scripts/hermes-mcp-server.py — MCP stdio server: exposes 100 tools to Hermes Agent
```

All three share the same on-disk state and the same Python modules under
`hermes_trader/`. The trading loop owns the trade decisions; the server owns
the human-visible surface; the MCP server owns the Hermes Agent integration.

---

## Where the name "Hermes" comes from

The agent layer is [Hermes Agent](https://github.com/NousResearch/hermes-agent)
by Nous Research — a Python-native agentic framework that operates external
systems through MCP (Model Context Protocol) tools. "hermes-trader" is the
**MCP server** + **trading engine** that Hermes Agent operates as one of its
skills. Hermes is the driver; hermes-trader is the car.

Two things follow from this design choice:

1. **The trading engine has zero Hermes-framework dependency.** It runs as a
   plain Python process. The MCP boundary is the only contact surface — that's
   what lets you also operate it through Claude Desktop, Cursor, or any
   MCP-aware client without changing a line of trading code.
2. **Hermes Agent is operational; the engine is autonomous.** The trading loop
   in `scripts/trading_loop.py` runs on its own forever. Hermes Agent is what
   you (a human) use to inspect, configure, and direct the engine — start it,
   stop it, ask "what did you just do," set the mode, etc.

---

## Inspiration from Senpi

[Senpi.ai](https://www.senpi.ai/) is a hosted multi-tenant platform that
deploys AI agents to trade Hyperliquid. Three things specifically influenced
hermes-trader:

| From Senpi | What hermes-trader took |
|---|---|
| **DSL (Dynamic Stop Loss) two-phase exit** | `hermes_trader/agents/dsl_exit.py` is a re-implementation of the same idea: hard stop in phase 1, ratcheting trailing floor with tiered retrace in phase 2, hard timeout as a backstop. |
| **Skill-shaped trading strategies** | The `skills/hermes-trader-agent/` directory mirrors Senpi's per-strategy folder layout (SKILL.md + scripts/ + references/) so a Hermes Agent skill is portable in shape, if not in runtime. |
| **MCP as the integration boundary** | Senpi exposes its proprietary backend through an MCP server; hermes-trader does the same with `scripts/hermes-mcp-server.py` (100 tools). Same pattern, open implementation. |

The crucial difference: **Senpi's runtime and MCP server are closed.** Their
open skills can't execute trades without their proprietary infrastructure.
hermes-trader is the inverse — the **engine + MCP + skills are open**;
deploying a hosted multi-tenant version on top is your business decision.

---

## The trading pipeline

One scan cycle (default 60s, env-tunable via `HERMES_SCAN_INTERVAL`):

```
1. HEARTBEAT
   Pull equity, positions, daily PnL from HL. Persist to .agent-memory.json.
   Append a `loop_heartbeat` event to the session log with a compact
   config snapshot (frac × lev, slots, cap, cooldown, hip3 flag) so
   the feed surfaces what the bot is tuned to do without anyone having
   to open .agent-config.json.

2. DSL MONITOR
   Reconcile DSL trackers with live exchange positions (rehydrate any
   trackers lost on restart; drop any whose coin closed externally).
   For each tracker, check if mark price breached its dynamic floor /
   max-loss / hard-timeout. If yes → market-close via close_position_market
   + deregister tracker + log `dsl_exit` event with realized PnL from
   the actual fill price.

3. SCAN
   perception.scan_once() fetches top-N markets by 24h volume (native
   crypto + HIP-3 dexes when enable_hip3=true), runs each through the
   trigger engine (pct move, volume spike, breakout, range compression,
   trend strength, momentum burst). Every perception is persisted via
   memory.record_perception (previously they were processed but never
   stored — the memory ring buffer ran flat at ~6 perceptions despite
   100+ trades). Log `scan` event with the coin list + scores.

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

`hermes_trader/agents/dsl_exit.py` — the most consequential single module
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

See `skills/hermes-trader-agent/references/hip3-tokenized-equity-handoff.md`
for the original task brief and the post-implementation audit findings.

---

## Persistence layer — four files

| File | Owner | What it holds | TTL |
|---|---|---|---|
| `.agent-config.json` | operator + UI | live trading knobs: mode, sizing, risk caps, DSL params, regime thresholds | persistent |
| `.agent-memory.json` | trading loop | rolling cache of perceptions, analyses, trades, watchlist, cooldowns, equity history | persistent |
| `.dsl-state.json` | DSL engine | per-position trackers (peak, floor, breach counter, leverage, policy) | persistent |
| `~/.hermes-trader-session-log.jsonl` | every component | append-only event log: heartbeat, scan, ta_skip, research, execute, dsl_exit, error | rolling |

All four are env-overridable for containerized deployment (see `Dockerfile`
and `fly.toml`). On Fly they live under `/data/` on a mounted volume so they
survive rolling deploys.

The session log is the **single source of truth** for everything the dashboard
and `status.py` report. Components write to it; readers tail it. No event bus,
no pubsub, no database — JSONL on disk has been entirely sufficient at one-
user scale.

---

## The MCP server

`scripts/hermes-mcp-server.py` — 100 tools over MCP stdio. The contract that
lets Hermes Agent (and any MCP client) operate the engine.

Tool categories:

| Category | Examples | Count |
|---|---|---|
| Trading core | `scan`, `research`, `execute`, `state`, `config` | 5 |
| Hyperfeed discovery | `leaderboard_get_markets`, `discovery_get_top_traders`, `smart_money_concentration`, `oi_funding_anomaly` | ~10 |
| Market data | `market_get_asset_data`, `market_get_funding_regime`, `market_get_mids`, `market_list_instruments` | ~15 |
| Direct HL passthrough | account state, candles, mids, l2 book, place order, cancel order, etc. | ~70 |

Some of the passthroughs are stubs pending SDK wiring — they exist so the
tool surface is complete from the agent's perspective. Stubs return a
deterministic `{note: "SDK method pending"}` payload, which keeps prompts
consistent and lets you replace them one at a time without changing the
agent's behavior.

The 100-tool surface is intentionally wide because **the MCP server can't be
modified at runtime** without a Hermes restart. Better to expose more than
the agent needs than to have to teach the agent a new tool mid-session.

---

## The web dashboard

`hermes_trader/dashboard.py` — single-file FastAPI extension that adds:

- `GET /` — public dashboard (no auth): how-it-works blurb, equity curve
  (LTTB-decimated, gradient fill), KPIs (equity / today PnL / open / last tick),
  open positions with leveraged ROE matching HL's display, recent closes with
  fees-net PnL, streaming live activity feed via SSE.
- `GET /operator?token=…` — token-gated console: config JSON, in-memory DSL
  trackers, per-position force-close, OFF/LIVE mode toggle.
- `POST /api/dashboard/operator/terminal?token=…` — Hermes terminal endpoint.
  Built-in commands resolve locally (`status`, `pause`, `resume`,
  `close <coin>`, `regime`, `config`, `help`); free-form text falls through
  to **Nous Hermes 3 70B** via OpenRouter, primed with a structured
  world-state snapshot (last 8 real trades from memory, live positions
  with uPnL, recent research verdicts with reasoning, DSL exits with
  reason+PnL, ta_skips) so the chat answers about what the bot is
  actually doing, not in a vacuum.
- `GET /api/feed/stream` — Server-Sent Events tailing the JSONL log.
  Replays last 50 events on connect; heartbeats every 15s to defeat proxy
  idle-kill.
- `GET /api/dashboard/{summary,positions,equity-curve,closed-trades}` —
  JSON endpoints driving the dashboard JS. Reusable for a future Next.js
  frontend or any other consumer.

### UI layer — Tamagotchi-meets-Matrix

Single-file static HTML, no build step. The dashboard intentionally has
personality:

- **Press Start 2P font + NES.css** — pixel-bordered cards with hard 4px
  shadows on every section. Title block (`HERMES-TRADER`) is an emerald-glow
  LCD strip.
- **Matrix-rain sidebar** — the live activity feed sits in a sticky 440px
  right column with CRT scanline overlay, fade-in row animation, and
  brighter glow on the newest entry (the "head" of the rain).
- **White-rabbit habitat** — a hand-rolled 16×16 inline-SVG pixel rabbit
  sits at the top of the matrix sidebar, bouncing on a spinning ⚙ wheel.
  An NES speech balloon next to it cycles through 24 law-of-attraction
  affirmations every 7s.
- **Reactive header pet** — separate emoji widget that swaps on the
  current `status` × `daily_pnl_pct` (scanning → 👀, executing → ⚡ shake,
  profitable → 🤑/😎, losing → 😰 [pixel-SVG sprite] / 😱). Live trading
  events animate the rabbit too: `execute` → yellow celebrate + ⚡ burst,
  `dsl_exit` profit → green victory wiggle + 💰, `dsl_exit` loss → red
  defeat shake + 💀.
- **Heartbeat config insight line** — every heartbeat in the feed shows
  the compact live config alongside the equity/PnL/open line:
  `♥ perp=$X avail=$Y daily=±$Z open=N  ⚙ 5.0%×40x slots=20 cap=40x cool=60m hip3:on`.
- **Currency + language selectors** — Intl.NumberFormat with USD-base FX
  rates from open.er-api.com (15 currencies); 10 languages with a static
  i18n dict applied via `data-i18n` attributes. Both persist in localStorage.
- **Discreet mode** (`👁` toggle) — flips every $ amount to `•••` while
  leaving every % visible. For screenshots / public sharing without
  disclosing capital size.
- **Operator-mode toggle** (`🔒 op` / `🔓 op` button) — prompts for the
  `HERMES_OPERATOR_TOKEN`, stashes it in localStorage, reloads with
  `?token=`. No more hand-editing the URL to unlock the terminal.
- **Hermes terminal modal** — **Cmd+K** (Ctrl+K) opens a NES-styled
  black console with an emerald prompt. Operator-token gated. Esc closes.

Design choice worth knowing: still **static HTML + Tailwind CDN +
Chart.js CDN + NES.css CDN + vanilla JS**. No build step, no bundler, no
SPA. This stays the right choice at this scale because:
- Anyone can fork and modify in 5 minutes
- No npm dependency surface to maintain
- Server-side trivially deployable as one Python process
- Looks indie-but-real, which is right for a public trading wallet

Move to a real framework (Lit, Alpine, HTMX, or Svelte) once the
dashboard grows multi-page, auth-multi-tenant, or marketplace-y — not
before.

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
hermes-trader/
├── hermes_trader/          # the engine — importable as a package
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
│   │   ├── hyperfeed.py         # leaderboard + smart money + OI anomaly (Hyperfeed clone)
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
│   ├── server.py           # FastAPI: JSON API + dashboard routes
│   ├── dashboard.py        # public + operator HTML + SSE feed
│   └── session_log.py      # JSONL append-only event log
├── scripts/
│   ├── trading_loop.py          # the autonomous loop (long-running)
│   ├── hermes-mcp-server.py     # MCP stdio server, 100 tools
│   └── backtest.py              # historical-candle backtest
├── skills/hermes-trader-agent/  # Hermes Agent skill (operator's manual + helper scripts)
├── tests/                       # offline unit + online + live-e2e
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
- **Don't add a worker farm.** One process per role (loop, server, MCP) has
  been enough; horizontal scaling means an ops surface you don't want yet.

---

## Operating the engine

Three roles, three entry points:

```bash
# 1. The trading loop — autonomous
python3 scripts/trading_loop.py

# 2. The web dashboard + JSON API (port 8000)
python3 -m hermes_trader.server

# 3. The MCP stdio server (driven by Hermes Agent / Claude Desktop / Cursor)
python3 scripts/hermes-mcp-server.py
```

Each loads `.env.local` and reads/writes the same on-disk state. On Fly they
run as two separate `processes` in `fly.toml` sharing a mounted volume; the
MCP server is local-only (stdio doesn't deploy).

Tail what the engine is doing:

```bash
# Live feed in terminal
python3 skills/hermes-trader-agent/scripts/feed.py --follow

# Last 50 events with stats
python3 skills/hermes-trader-agent/scripts/status.py

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

## Using hermes-trader — the operator's manual

Three audiences, three workflows: you-the-human via Hermes Agent for ad-hoc
operation, you-the-human via the dashboard for live monitoring, and the
trading loop running headless for autonomous execution.

### General use (no Hermes Agent)

The minimum to get from "I cloned this" to "the bot is trading":

```bash
# 1. Install
git clone https://github.com/Julian-dev28/hermes-trader
cd hermes-trader
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure
cp .env.local.example .env.local
# Edit: OPENROUTER_API_KEY, HYPERLIQUID_WALLET_ADDRESS, HYPERLIQUID_PRIVATE_KEY
# Optional: HERMES_OPERATOR_TOKEN (for the operator console), BRAVE_API_KEY (news)

# 3. Start in OFF mode first — verify the engine reads market data without trading
echo '{"mode":"OFF","minAiConfidence":0.8,"max_concurrent":3}' > .agent-config.json

# 4. Run the trading loop and the dashboard
python3 scripts/trading_loop.py &     # scans every 60s
python3 -m hermes_trader.server &      # dashboard at http://localhost:8000

# 5. Watch one cycle — the dashboard's live feed shows scans + research verdicts
open http://localhost:8000

# 6. When you're satisfied, flip mode to LIVE
#    Either edit .agent-config.json directly, or use /operator?token=… and click "set mode LIVE"
```

The config is read **fresh on every trade** — no restart needed for changes.
Same for risk caps, leverage, allowlists.

### Hands-off operating via Hermes Agent (the MCP path)

If you have Hermes Agent installed and the MCP server registered, you operate
the engine in plain English:

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  hermes-trader:
    command: python3
    args: [/Users/you/path/to/hermes-trader/scripts/hermes-mcp-server.py]
    cwd: /Users/you/path/to/hermes-trader
    timeout: 60
```

Then in a Hermes session:

| Intent | Prompt |
|---|---|
| **Check state** | *Load the hermes-trader skill and show me its current state — mode, equity, open positions, recent trades.* |
| **Tune** | *Set hermes-trader to LIVE mode with `max_concurrent: 5` and `equity_fraction_per_trade: 0.025`.* |
| **One-shot cycle** | *Run a hermes-trader scan, research the best candidate, and execute it if the verdict is LONG or SHORT with confidence ≥ 0.7. Tell me what happened.* |
| **Start continuous** | *Start the hermes-trader trading loop in the background. Confirm it is running.* |
| **Stop continuous** | *Stop the hermes-trader trading loop. Don't close existing positions.* |
| **Status (in-session)** | *Check hermes-trader status. Highlight anything that changed since the last report.* |
| **Manual close** | *Close my hermes-trader position in TSLA. Show me the realized PnL.* |

The skill at `skills/hermes-trader-agent/` carries the system prompt, the
`feed.py` / `status.py` helper scripts, and reference docs that Hermes Agent
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

The operator console at `/operator?token=<HERMES_OPERATOR_TOKEN>` adds:
- **Config viewer** — current `.agent-config.json` rendered as JSON
- **DSL tracker viewer** — every position's peak/floor/phase/leverage
- **Force-close buttons** — one click per coin to market-close + deregister
- **Mode toggle** — flip OFF ↔ LIVE without editing config files

The operator endpoints check `X-Operator-Token` or `?token=` query param
on every request; missing env var → 503, wrong token → 401.

### Tailing from the terminal (no browser)

```bash
# Live feed, same format as the dashboard
python3 skills/hermes-trader-agent/scripts/feed.py --follow

# Last hour of activity (one-shot, for cron / piping to Slack)
python3 skills/hermes-trader-agent/scripts/feed.py --since 1h

# Compact status block — equity, positions, recent closes, win rate
python3 skills/hermes-trader-agent/scripts/status.py
```

---

## The skill scaffolding + cron for hands-off monitoring

`skills/hermes-trader-agent/` is a self-contained Hermes-Agent skill: a
folder that Hermes loads as a *capability* for an agent session. The
layout intentionally mirrors Senpi's skill format so the directory pattern is
portable, even though the runtime semantics differ.

```
skills/hermes-trader-agent/
├── SKILL.md                       # the system prompt — what this agent IS,
│                                    what tools it has, how to phrase
│                                    decisions, what to ask the user before
│                                    risky actions
├── scripts/
│   ├── feed.py                    # live activity tail (CLI + cron-friendly)
│   ├── status.py                  # compact status block
│   └── audit_mcp_server.py        # verifies the MCP server is healthy
└── references/
    ├── cron-jobs.md               # how to wire the hourly status job
    ├── hyperliquid-gotchas.md     # tick-size / sig-fig / IOC fill quirks
    ├── mcp-config.md              # Hermes ~/.hermes/config.yaml block
    ├── mcp-server.md              # the 100-tool surface
    ├── restart-sequence.md        # the canonical pkill+restart ritual
    ├── signal-vs-action-gap.md    # debugging "scanner fires, no trade"
    ├── trading-mode.md            # execute-first reporting contract
    └── daemon-investigation.md    # the --daemon flag is informational only
```

### Hands-off monitoring via Hermes cron

Hermes Agent supports scheduled jobs (`hermes cron list/create/resume/pause`).
The skill includes a recommended **hourly status report** job that runs
`status.py` and posts the output to whichever channel you have configured
(Telegram, Slack, email, or just stdout in your terminal).

```bash
# One-time setup
hermes cron create hermes-trader-hourly \
  --interval "0 * * * *" \
  --command "python3 /path/to/hermes-trader/skills/hermes-trader-agent/scripts/status.py"

# Status of all jobs
hermes cron list

# Pause / resume
hermes cron pause  <job-id>
hermes cron resume <job-id>
```

The status report is **zero AI cost** — it's a pure CLI script reading the
session log + memory file. The whole point is to give you ambient awareness
without paying for an LLM call.

For full AI-summarized reports (Hermes-driven "tell me what changed in the
last hour and flag anomalies"), set up a separate Hermes cron that prompts
the agent — that does cost OpenRouter tokens but produces a much richer
report.

---

## Backtesting methodology

`scripts/backtest.py` runs the current strategy against historical HL candles.
The point is to make tuning empirical rather than vibes-driven.

```bash
# Basic run — last 30 days of BTC + ETH 4h candles
python3 scripts/backtest.py --coins BTC,ETH --interval 4h --days 30

# Walk-forward: optimize on first half, validate on second
python3 scripts/backtest.py --walk-forward 0.5

# Test a specific config (overrides .agent-config.json for the run only)
HERMES_BACKTEST_CONFIG='{"min_ai_confidence":0.7,"counter_regime_min_conf":0.85}' \
  python3 scripts/backtest.py --coins BTC --days 60
```

### What the backtest does and doesn't model

| Modeled | Approximated | Not modeled |
|---|---|---|
| Trigger detection (same code as live) | Slippage (configurable bps drag) | Order-book depth |
| TA filter (same code) | HL taker fees | Funding payments mid-trade |
| Risk gates (same code, sees historical equity) | LLM verdicts (replayed from memory if available, else mocked) | News-blackout (no historical news index) |
| DSL exit logic (same code) | Fill probability for IOC orders | Liquidations |
| Sizing math (same code) | | Cross-margin interactions |

The biggest gap: **the AI research call** doesn't replay historically (the LLM
might give a different verdict today than the day-of). The backtest defaults
to "all verdicts pass" or "use cached verdicts from memory", which over-states
results. Treat backtest numbers as an **upper bound**, not a forecast.

### Walk-forward validation

To avoid overfitting parameters to a specific window, split the period in
half: optimize the config on the first half, then run the optimized config
**unchanged** on the second half. A strategy that wins the in-sample period
but loses out-of-sample is overfit; ditch the change.

Three params worth walk-forwarding:
- `counter_regime_min_conf` (0.5 / 0.7 / 0.85 / 1.0)
- `min_ai_confidence` (0.3 / 0.5 / 0.7)
- `dsl_exit.max_loss_pct` (1.5 / 2.5 / 4.0)

Don't walk-forward more than 3 params simultaneously — combinatorial
explosion, and most "wins" become noise.

---

## Risk modeling — Kelly, retrace tiers, daily killswitch

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
- `HERMES_OPERATOR_TOKEN` is in `.env.local` (not in `fly.toml`'s env
  block); rotate quarterly or after every "did I share that screen?" moment.
- The dashboard URL is **public**; the operator URL is `?token=…` which is
  in your browser history. Use a long-lived OS keychain entry to retrieve
  it rather than pasting into chat / docs.

### Things that should NEVER be in the repo

```
HYPERLIQUID_PRIVATE_KEY    OPENROUTER_API_KEY    HERMES_OPERATOR_TOKEN
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
