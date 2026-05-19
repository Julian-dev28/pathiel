# Hermes-Trader
> Autonomous multi-market trading agent for Hyperliquid — crypto perps, equity perps (TSLA, NVDA, AAPL), and commodities (NATGAS, SILVER, COPPER). A standalone Python system built with FastAPI and OpenRouter, operated by [Hermes Agent](https://github.com/NousResearch/hermes-agent) through an MCP server.

**What it does:** Scans every Hyperliquid market (500+ perps + spot), fires statistical triggers on price/volume/breakout signals, runs a cheap pre-AI technical analysis filter, and only calls AI on CONFIRMED setups. Executes real trades with DSL-managed dynamic exits — no human in the loop.

---

## The problem it solves

Trading signals appear constantly — 5-minute spikes, hourly trends, daily breakouts. Most systems call expensive AI on every signal, burning tokens on noise. Hermes-Trader solves this by separating cheap statistical analysis from expensive AI reasoning:

1. **Scan** — 500+ markets in parallel with volume pre-filtering and rate-limit-aware batching
2. **TA Filter** — multi-timeframe indicators (EMA, RSI, ATR, ADX, volume) — zero AI cost
3. **AI Research** — only on CONFIRMED signals, plus any fired momentum burst
4. **Execution** — Kelly-sized orders with DSL dynamic stop-loss exits (loss protection → profit locking)
5. **Discovery** — built-in Hyperfeed Discovery replicates Smart Money leaderboards and whale signals

This architecture reduced daily AI costs from $8-$52 to $3-$10 while improving signal quality.

---

## Architecture

```
+---------------------------------------------------------------+
|          hermes-trader — autonomous trading pipeline          |
|                                                               |
|  Scan ➜ TA Filter ➜ AI Research ➜ Risk Gates ➜ DSL Exit ──▶ Execute
|        (cheap)          (expensive)     (11 gates)    (2-phase)
│                       |
│                  Only CONFIRMED
│                  signals proceed
├───────────────────────────────────────────────────────────────┤
│               Hyperfeed Discovery                             |
│  Leaderboard • Smart Money • OI Anomaly • Whale Tracking      |
+---------------------------------------------------------------+
```

### Pipeline

```
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐    ┌──────────┐    ┌──────────┐
│  Perception │───>│  TA Filter   │───>│   AI Research   │───>│  Risk    │───>│  Executor│
│   Scanner   │    │  (TA Filter) │    │ (OpenRouter API)│    │  Gates   │    │ (HL + DSL)│
│ 5m/1h/4h    │    │  EMA/RSI/ATR│    │ Verdict + Price │    │  11 gates│    │ SL/TP    │
│ Volume-N    │    └──────────────┘    └─────────────────┘    └──────────┘    └──────────┘
└─────────────┘
     │
     ├── Hyperfeed Discovery (leaderboard, whale index, OI anomaly)
     │     ↳ smart_money_concentration(), oi_funding_anomaly()
     │     ↳ discovery_get_top_traders(), leaderboard_get_trader_positions()
     └── Rate-Limit Pipeline (1200 weight/min — batch + cache)
```

---

## Key Features

### Rate-Limit-Aware Scan Pipeline
- **Volume pre-filtering**: Top-N markets by 24h notional volume (default 50)
- **Parallel batch scanning**: Workers fan out within batches, sleep between
- **TTL caching**: Candles cached 15 minutes, 4-scan cost ≈ 600 weight (vs. 10,000+ raw)
- **Configurable**: `HERMES_SCAN_INTERVAL`, `HERMES_MAX_MARKETS`, `HERMES_BATCH_SIZE`, `HERMES_BATCH_SLEEP`

### DSL (Dynamic Stop-Loss) Exit Engine
- **Phase 1 — Loss Protection**: Max loss stop, protect threshold
- **Phase 2 — Profit Locking**: Tiered retrace thresholds, trailing floor that only moves up
- **Hard timeout**: Emergency exit after configurable minutes
- **Auto-registration**: Every executed position is registered for DSL tracking

### Hyperfeed Discovery (Native, no MCP)
Replicates the Hyperfeed MCP plugin's data directly from HL API:
- `leaderboard_get_markets(limit)` — top markets by OI + volume
- `market_get_funding_regime()` — LONG_CROWDED / SHORT_CROWDED / NEUTRAL analysis
- `smart_money_concentration()` — identifies assets with whale accumulation
- `oi_funding_anomaly()` — OI spike + negative funding + flat price = accumulation signal
- `discovery_get_top_traders(...)` — trader rankings with win rates
- `market_get_asset_data(asset)` — candles + funding + OI for any coin

---

## Core Modules

| Module | Purpose |
|--------|---------|
| `hermes_trader/agents/perception.py` | Multi-market volume-pre-filtered scanner with parallel batch scanning |
| `hermes_trader/indicators/triggers.py` | Trigger engine — composite scoring across signal types |
| `hermes_trader/agents/ta_filter.py` | Pre-AI technical analysis — multi-TF (1h/4h/1d) EMA, RSI, ATR, ADX, volume confirmation |
| `hermes_trader/agents/research.py` | AI research pipeline — fetches candles, builds context, calls OpenRouter for verdict |
| `hermes_trader/agents/risk_gates.py` | 11 independent risk gates: confidence, notional caps, daily loss, cooldown, correlation, news blackout, etc. |
| `hermes_trader/agents/executor.py` | Kelly sizing + EIP-712 order signing + DSL exit registration |
| `hermes_trader/agents/dsl_exit.py` | Two-phase trailing stop engine — loss protection → profit locking |
| `hermes_trader/agents/hyperfeed.py` | Hyperfeed Discovery API — leaderboard, whale index, smart money signals |
| `hermes_trader/agents/whale_index.py` | Whale detection — OI concentration + funding anomaly signals |
| `hermes_trader/agents/memory.py` | Persistent file-backed state (`.agent-memory.json`, `.agent-config.json`) |
| `hermes_trader/agents/config_store.py` | Config persistence layer |
| `hermes_trader/agents/system_prompt.py` | Dedicated system prompt for the trading agent |
| `hermes_trader/client/hl_client.py` | Hyperliquid REST + WebSocket client (mids, candles, account state) |
| `hermes_trader/client/ws_client.py` | Persistent WebSocket connection for sub-second mids |
| `hermes_trader/client/universe.py` | Volume-ranked market loader with 24h caching |
| `hermes_trader/client/cache.py` | LRU + TTL memoization with in-flight dedup |
| `hermes_trader/client/lock.py` | fcntl lock with stale-PID recovery for scan coalescing |
| `hermes_trader/client/parallel.py` | Concurrency-bounded fan-out for independent API calls |
| `hermes_trader/client/daemon.py` | Long-lived scan scheduler with tick timeouts + graceful shutdown |
| `hermes_trader/client/exchange.py` | Order placement, leverage setting, trigger orders (SL/TP) |
| `hermes_trader/indicators/math.py` | TA indicators: EMA, SMA, ATR, RSI, ADX |
| `hermes_trader/models/types.py` | Shared data type: `Candle` (OHLCV) |
| `hermes_trader/server.py` | FastAPI server — 22 REST routes for frontend/dashboard |

---

## Configuration

There are two places to configure hermes-trader: **`.env.local`** (credentials,
API, runtime/infra — process-level, read at startup) and **`.agent-config.json`**
(trading behaviour and risk — read fresh on every trade, no restart needed). Both
are gitignored.

### `.env.local` — credentials & runtime

Copy `.env.local.example` → `.env.local` and fill in:

```bash
# ── OpenRouter (AI research) ─────────────────────────────────
OPENROUTER_API_KEY=sk-or-...your-key      # required
OPENROUTER_MODEL=x-ai/grok-4.3            # optional — this is the default

# ── Hyperliquid ──────────────────────────────────────────────
HYPERLIQUID_WALLET_ADDRESS=0x...          # required — the signing (agent) wallet
HYPERLIQUID_PRIVATE_KEY=0x...             # required — that wallet's key
# HYPERLIQUID_MASTER_ADDRESS=0x...        # optional — set for an agent-wallet
#                                           setup; the master holds the funds

# ── Scan tuning (optional — defaults shown) ──────────────────
HERMES_SCAN_INTERVAL=60        # seconds between scan cycles
HERMES_MAX_MARKETS=60          # top-N markets scanned by 24h volume
HERMES_BATCH_SIZE=20           # markets per parallel batch
HERMES_BATCH_SLEEP=0.3         # seconds between batches
# HERMES_PORT=8000             # FastAPI server port
```

Keep `HERMES_MAX_MARKETS ≤ HERMES_SCAN_INTERVAL` — see [Rate Limit Math](#rate-limit-math).

### `.agent-config.json` — trading behaviour & risk

The live trading knobs. Read fresh on **every trade**, so edits take effect on the
next cycle — no restart. Keys are read tolerantly: `snake_case` or `camelCase`
both resolve (`max_trade_notional_usd` ≡ `maxTradeNotionalUsd`).

```json
{
  "mode": "LIVE",
  "equity_fraction_per_trade": 0.10,
  "leverage": 10,
  "min_ai_confidence": 0.30,
  "max_concurrent": 10,
  "max_trade_notional_usd": 200,
  "max_total_notional_pct": 10.0,
  "max_daily_loss_usd": -100,
  "min_market_volume_usd": 5000000,
  "cooldown_min": 60,
  "coin_allowlist": [],
  "coin_blocklist": []
}
```

| Key | What it does | Default |
|-----|--------------|---------|
| `mode` | `OFF` = analyse only, no orders · `LIVE` = place real orders | `OFF` |
| `equity_fraction_per_trade` | Fraction of perp equity committed as margin per trade — see [Trade Sizing](#trade-sizing) | `0.01` |
| `leverage` | Leverage applied per coin; **also multiplies position notional** | `5` |
| `min_ai_confidence` | Minimum AI confidence for a LONG/SHORT to execute | `0.8` |
| `max_concurrent` | Max simultaneous open positions | `3` |
| `max_trade_notional_usd` | Hard ceiling on a single trade's notional | `200` |
| `max_total_notional_pct` | Ceiling on combined open notional, as a multiple of equity | `1.0` |
| `max_daily_loss_usd` | Daily-loss kill switch (negative number) | `-100` |
| `min_market_volume_usd` | Skip markets below this 24h volume | `5_000_000` |
| `cooldown_min` | Minutes before re-trading the same coin | `60` |
| `coin_allowlist` | If non-empty, **only** these coins are tradeable | `[]` (all) |
| `coin_blocklist` | Coins that are never traded | `[]` |

Optional nested `dsl_exit` block tunes the trailing-stop engine —
`max_loss_pct` (2.5), `protect_pct` (1.5), `retrace_threshold` (0.30),
`hard_timeout_minutes` (180).

Trigger internals (weights, sigma thresholds, candle interval) live separately in
`hermes_trader/agents/config.py` — edit there to tune the scan itself.

---

## Quick Start

### Prerequisites
- Python 3.11+
- Hyperliquid wallet with private key
- OpenRouter API key ([openrouter.ai](https://openrouter.ai))
- (Optional) Brave Search API key for news

### Setup
```bash
git clone https://github.com/Julian-dev28/hermes-trader
cd hermes-trader

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies (editable, with dev extras: pytest + ruff)
pip install -e ".[dev]"

# Configure
cp .env.local.example .env.local
# Edit .env.local with your keys
```

## Running

### API Server (Optional)
```bash
# Start the FastAPI server (port 8000)
python -m hermes_trader.server

# Or use uvicorn directly:
uvicorn hermes_trader.server:app --host 0.0.0.0 --port 8000
```
The API is available at `http://localhost:8000`. Health check: `GET /` returns `{"service": "Hermes-Trader", "version": "0.3.0", "status": "running"}`.

### Continuous Trading Loop (Recommended)
```bash
# Start the autonomous trading loop (scans every 60s)
python scripts/trading_loop.py

# Or run in background:
nohup python scripts/trading_loop.py > /tmp/hermes-trader.log 2>&1 &
```
Monitor logs: `tail -f /tmp/hermes-trader.log`

**Trading Loop Behavior:**
- Scans top 60 markets every 60 seconds
- Runs the TA filter on each trigger — only CONFIRMED signals (or fired momentum bursts) reach AI research
- Researches qualifying signals with AI (qwen/qwen3-235b-a22b)
- Executes trades that clear all 11 risk gates
- Runs continuously until stopped

---

## Testing

```bash
pytest                          # offline unit tests — fast, no network, CI-safe
pytest -m online                # read-only tests against the live Hyperliquid public API
HERMES_E2E=1 pytest -m live      # real-money e2e: places a tiny order, calls the LLM
```

`online` and `live` tests are deselected by default. The `live` suite spends
real funds (a ~$14 round-trip order plus a billable OpenRouter call) and is
additionally gated behind `HERMES_E2E=1` so it can never run by accident.

---

## MCP Integration

hermes-trader is a standalone Python application; **Hermes Agent operates it through this MCP server** — that is the whole integration boundary. The agent calls the tools below; the trading engine itself has no Hermes-framework dependency.

The MCP server (`scripts/hermes-mcp-server.py`) exposes 100 tools over stdio transport. The 14 primary tools are listed below; the remainder are Hyperliquid data passthroughs (some are placeholders pending SDK wiring).

| Tool | Description |
|------|-------------|
| **Trading Core** | |
| `scan` | Scan all HL markets (volume-filtered), return triggered candidates |
| `research` | Deep AI analysis on a coin with OpenRouter |
| `execute` | Execute trade through risk gates + DSL registration |
| `state` | Get full agent state (mode, equity, positions, trades) |
| `config` | Get/set agent configuration (mode, risk caps, thresholds) |
| **Hyperfeed Discovery** | |
| `leaderboard_get_markets` | Top markets by OI + volume |
| `leaderboard_get_top_traders` | Trader rankings with win rates |
| `leaderboard_get_trader_positions` | Positions for a specific trader |
| `discovery_get_top_traders` | Discovery top traders (alias) |
| `discovery_get_trader_state` | Full trader state from discovery |
| **Market Data** | |
| `market_get_asset_data` | Candles + funding + OI for any coin |
| `market_get_funding_regime` | LONG_CROWDED / SHORT_CROWDED / NEUTRAL |
| `market_list_instruments` | All tradeable instruments |
| `market_get_mids` | Real-time mid prices |

Configure in Hermes Agent's `config.yaml`:
```yaml
mcp_servers:
  hermes-trader:
    command: python3
    args:
      - /path/to/hermes-trader/scripts/hermes-mcp-server.py
    cwd: /path/to/hermes-trader
    timeout: 60
    env:
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY}
```

---

## Operating via Hermes Agent

With the skill loaded and the MCP server registered (see [MCP Integration](#mcp-integration)),
you operate hermes-trader by prompting your Hermes Agent in plain language — the agent
calls the MCP tools for you. Restart your Hermes session first so the skill and MCP
server are picked up.

| Goal | Prompt to give Hermes |
|------|-----------------------|
| **Check state** | *Load the hermes-trader skill and show me its current state — mode, equity, open positions, recent trades.* |
| **Configure** (`OFF` analyzes only, `LIVE` places real orders) | *Set hermes-trader to LIVE mode with a max trade size of $20.* |
| **Scan** | *Scan the markets with hermes-trader and list what triggered, with composite scores.* |
| **Research** | *Research the top candidate and tell me the verdict, side, and confidence.* |
| **Run one full cycle** | *Run a hermes-trader cycle: scan, run the TA filter, research the best candidate, and execute it if the verdict is LONG or SHORT. Tell me what happened.* |
| **Start continuous trading** | *Start the hermes-trader trading loop in the background, then confirm it is running.* |
| **Stop continuous trading** | *Stop the hermes-trader trading loop.* |
| **Monitor (in session)** | *Check hermes-trader's status and tell me if anything changed since the last report.* |

"Start continuous trading" runs `python scripts/trading_loop.py`, which scans ->
TA-filters -> researches -> executes on its own every `HERMES_SCAN_INTERVAL`
seconds, independent of the Hermes session.

For **hands-off monitoring**, resume the hourly status cron job (zero AI cost — it
just runs `status.py`; see [`references/cron-jobs.md`](skills/hermes-trader-agent/references/cron-jobs.md)):
```bash
hermes cron list            # find the "Hermes Trader Hourly Report" job id
hermes cron resume <job-id> # start hourly status delivery
```

---

## Trade Sizing

Every trade's position size comes from one formula in `executor.py`:

```
trade_notional = perp_equity  ×  equity_fraction_per_trade  ×  leverage
```

Both knobs live in `.agent-config.json`:

| Key | Meaning | Example |
|-----|---------|---------|
| `equity_fraction_per_trade` | Fraction of **total perp equity** committed as margin per trade | `0.10` = 10% |
| `leverage` | Leverage applied (also pushed to the exchange via `set_leverage`) | `10` = 10× |

Sizing keys off **total perp equity**, not free margin — so each trade commits a
*fixed* amount and `N` trades scales the account fully in. With
`equity_fraction_per_trade: 0.10`, every trade commits 10% of equity as margin,
so ~10 trades deploys the whole account linearly. (Sizing off *free* margin
instead would decay geometrically and never fully deploy.)

Caps that bound it: `maxConcurrent` (max simultaneous positions — set it ≥ the
number of trades you want open at once), `max_total_notional_pct` (ceiling on
combined open notional as a multiple of equity — at 10× leverage, `10.0` ≈ fully
deployed), and `maxTradeNotionalUsd` (hard ceiling on a single trade's notional).
Config keys are read tolerantly — `snake_case` or `camelCase` both work.

Defaults if the keys are absent: `equity_fraction_per_trade = 0.01`, `leverage = 5`.

---

## Design Decisions

### Why volume pre-filtering?
HL's API rate limit is **1200 weight/minute**. A single candle fetch costs **weight 20**. Scanning all 500+ markets naively requires 10,000+ weight → instant 429. Volume pre-filtering to the top 60 markets keeps a scan at ~1,200 weight. Sustained usage is `1200 × markets ÷ interval` weight/min, so the safe rule is **markets ≤ scan-interval-in-seconds** (the default 60/60 sits right at the limit's edge).

### Why DSL exit engine?
Static SL/TP orders don't adapt to price action. The DSL engine implements a two-phase design: Phase 1 protects your capital (hard stop), Phase 2 locks in profits (trailing floor with tiered retrace thresholds). The floor only moves up — it never gives back locked profit. This pattern is inspired by senpi-skills' DSL dynamic stop-loss engine.

### Why Hyperfeed Discovery?
The HL leaderboard and whale tracking aren't exposed through the public API. This module reconstructs the same data patterns (leaderboard rankings, smart money concentration, OI anomalies) from the raw HL endpoints we already call. No external MCP dependency needed.

### Why pure Python?
Rewritten from TypeScript/Next.js to enable simpler deployment, MCP integration with Hermes Agent, and native testability without a headless browser.

---

## Rate Limit Math

| Operation | Weight | Notes |
|-----------|--------|-------|
| `allMids` | 2 | Real-time prices |
| `metaAndAssetCtxs` | 20 | Universe + volume + OI (perp) |
| `spotMetaAndAssetCtxs` | 20 | Universe + volume + OI (spot) |
| `candleSnapshot` (per coin) | 20 | Plus per-item weight |
| **Total per scan cycle** | ~1,200 | Top 60 markets, one candle fetch each |

With `HERMES_MAX_MARKETS=60` and a 50s candle-cache TTL, each 60s scan fetches fresh candles (~1,200 weight). The cache TTL is deliberately kept just below the scan interval so the scanner never reacts to a stale snapshot — raising it would re-introduce that lag.

---

## Project Structure

```
hermes-trader/
├── hermes_trader/                  # Pure Python agent
│   ├── __init__.py
│   ├── __main__.py                # Entry point
│   ├── server.py                  # FastAPI server — 22 routes
│   ├── agents/                    # Core agent logic
│   │   ├── config.py              # Agent configuration model
│   │   ├── config_store.py        # Config persistence
│   │   ├── executor.py            # Kelly sizing + order execution + DSL registration
│   │   ├── memory.py              # File-backed state
│   │   ├── perception.py          # Volume-filtered parallel scanner
│   │   ├── research.py            # AI research pipeline
│   │   ├── risk_gates.py          # 11 risk gates
│   │   ├── system_prompt.py       # Agent system prompt
│   │   ├── ta_filter.py           # Pre-AI TA filter
│   │   ├── dsl_exit.py            # Two-phase trailing stop engine
│   │   ├── hyperfeed.py           # Discovery API (leaderboard, whale index, etc.)
│   │   └── whale_index.py         # Smart money + OI anomaly signals
│   ├── client/                    # External API clients
│   │   ├── exchange.py            # HL order placement
│   │   ├── hl_client.py           # HL REST + WebSocket client
│   │   ├── ws_client.py           # Persistent WebSocket for real-time mids
│   │   ├── universe.py            # Volume-ranked market loader with caching
│   │   ├── cache.py               # LRU + TTL memoization
│   │   ├── lock.py                # fcntl lock with stale-PID recovery
│   │   ├── parallel.py            # Concurrency-bounded fan-out
│   │   └── daemon.py              # Long-lived scan scheduler
│   ├── indicators/                # TA math
│   │   ├── math.py                # EMA, SMA, ATR, RSI, ADX
│   │   └── triggers.py            # Trigger detection + composite scoring
│   └── models/                    # Shared data types
│       └── types.py               # Candle (OHLCV)
├── scripts/
│   ├── hermes-mcp-server.py       # MCP server (stdio, 100 tools)
│   └── trading_loop.py            # Continuous trading loop
├── skills/hermes-trader-agent/    # Hermes Agent skill
├── tests/                         # pytest suite — offline / online / live e2e
└── docs/
    └── journal-schema.md          # Trade journal schema
```

---

## Built With

- FastAPI — Python web framework
- OpenRouter (Qwen3-235B-A22B) — AI research pipeline
- Hyperliquid Python SDK — perpetual futures DEX
- Brave Search API (optional, for news signals)

It is **operated by** [Hermes Agent](https://github.com/NousResearch/hermes-agent)
through the MCP server — Hermes Agent is not a build dependency; the trading
engine is plain Python.

---

**Note:** Project runs on `python` branch. Never merge to `main`.
