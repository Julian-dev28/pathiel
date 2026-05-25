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
   Append a `loop_heartbeat` event to the session log.

2. DSL MONITOR
   Reconcile DSL trackers with live exchange positions (rehydrate any
   trackers lost on restart; drop any whose coin closed externally).
   For each tracker, check if mark price breached its dynamic floor /
   max-loss / hard-timeout. If yes → market-close via close_position_market
   + deregister tracker + log `dsl_exit` event with realized PnL from
   the actual fill price.

3. SCAN
   perception.scan_once() fetches top-N markets by 24h volume, runs each
   through the trigger engine (pct move, volume spike, breakout, range
   compression, trend strength, momentum burst). Returns triggers above
   composite_score threshold. Log `scan` event with the coin list.

4. PER-TRIGGER
   For each triggered coin:
     a. TA filter (ta_filter.analyze_perception) — multi-TF EMA/RSI/ATR/ADX
        + volume confirmation. Free statistical gate before any AI cost.
        Result: CONFIRMED / WEAK / REJECTED. Momentum-burst triggers bypass.
     b. AI research (research.research) — fetches candles + news context,
        sends to OpenRouter LLM (Grok-4 or similar), parses verdict
        (LONG / SHORT / PASS) + confidence (0-1) + entry/stop/tp prices.
     c. Risk gates (risk_gates.eval_all_gates) — 12 independent gates,
        all evaluated (no short-circuit) for telemetry. Listed below.
     d. Executor (executor.maybe_execute) — if all gates pass:
        Kelly-sized notional, set leverage, place IOC order, register
        DSL tracker with leverage, place backup ATR stop on exchange.
     e. Log `execute` event with side, executed flag, order_id, blocked_by.
```

### The 12 risk gates

All evaluated; results recorded for telemetry. Trade blocks if any returns
`{pass: False}`.

| Gate | What it checks |
|---|---|
| `confidence` | AI confidence ≥ `min_ai_confidence` (default 0.8, often 0.3 in live) |
| `max_concurrent` | Open positions < `max_concurrent` |
| `notional_cap` | Per-trade notional ≤ `max_trade_notional_usd` |
| `daily_loss` | Daily PnL > `max_daily_loss_usd` (kill switch) |
| `liquidity` | Coin 24h volume ≥ `min_market_volume_usd` |
| `coin_filter` | Coin not in blocklist; if allowlist set, must be in it |
| `cooldown` | Same-coin cooldown elapsed (`cooldown_min`) |
| `opposite_guard` | No simultaneous opposite-direction position on the same coin |
| `correlation` | Crypto long correlation cap (max 2 concurrent crypto longs) |
| `equity_risk` | Total open notional ≤ `max_total_notional_pct × equity` |
| `market_regime` | **(new)** Counter-trend trades blocked unless conf ≥ `counter_regime_min_conf`. Per-asset-class proxy: BTC for crypto, NVDA for equity, own ticker for commodities. |
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
- `GET /api/feed/stream` — Server-Sent Events tailing the JSONL log.
  Replays last 50 events on connect; heartbeats every 15s to defeat proxy
  idle-kill.
- `GET /api/dashboard/{summary,positions,equity-curve,closed-trades}` —
  JSON endpoints driving the dashboard JS. Reusable for a future Next.js
  frontend or any other consumer.

Design choice worth knowing: the dashboard is **static HTML + Tailwind CDN +
Chart.js CDN + HTMX/vanilla JS**. No build step, no bundler, no SPA. Total
JS surface ~250 lines. This was the right choice at this scale because:
- Anyone can fork and modify in 5 minutes
- No npm dependency surface to maintain
- Server-side trivially deployable as one Python process
- Looks indie-but-real, which is right for a public trading wallet

Move to Next.js once the product has auth + marketplace + multi-tenant —
not before.

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

## What this doc deliberately does NOT cover

- Specific trade ideas, parameter tuning recipes, market-condition strategies
- Detailed risk modeling, Kelly sizing math, retrace tier theory
- The cron job + skill scaffolding for hands-off monitoring
- Backtesting methodology + walk-forward validation
- Wallet security (use a fresh agent wallet with limited funds; master-wallet
  reads only)

Those belong in their own docs (or, for now, in the code + commit history).
This doc is the **map**. If you're lost, start at the pipeline diagram.
