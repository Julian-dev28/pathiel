# Hermes-Trader
> Autonomous multi-market trading agent for Hyperliquid — crypto perps, equity perps (TSLA, NVDA, AAPL), and commodities (NATGAS, SILVER, COPPER). Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) with FastAPI, OpenRouter, and a pre-AI technical analysis filter that cuts token costs by 80%.

**What it does:** Scans every Hyperliquid market (500+ perps + spot), fires statistical triggers on price/volume/breakout signals, runs a cheap pre-AI technical analysis filter, and only calls AI on CONFIRMED setups. Executes real trades with DSL-managed dynamic exits — no human in the loop.

---

## The problem it solves

Trading signals appear constantly — 5-minute spikes, hourly trends, daily breakouts. Most systems call expensive AI on every signal, burning tokens on noise. Hermes-Trader solves this by separating cheap statistical analysis from expensive AI reasoning:

1. **Scan** — 500+ markets in parallel with volume pre-filtering and rate-limit-aware batching
2. **TA Filter** — multi-timeframe indicators (EMA, RSI, ATR, ADX, volume) — zero AI cost
3. **AI Research** — only on CONFIRMED signals (typically 0-2 per cycle vs. 5+ before)
4. **Execution** — Kelly-sized orders with DSL dynamic stop-loss exits (loss protection → profit locking)
5. **Discovery** — built-in Hyperfeed Discovery replicates Smart Money leaderboards and whale signals

This architecture reduced daily AI costs from $8-$52 to $3-$10 while improving signal quality.

---

## Architecture

```
+---------------------------------------------------------------+
|                  Hermes Agent (LLM)                           |
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
- **Configurable**: `HERMES_MAX_MARKETS`, `HERMES_BATCH_SIZE`, `HERMES_BATCH_SLEEP`

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
| `hermes_agent/agents/perception.py` | Multi-market volume-pre-filtered scanner with parallel batch scanning |
| `hermes_agent/indicators/triggers.py` | Trigger engine — composite scoring across signal types |
| `hermes_agent/agents/ta_filter.py` | Pre-AI technical analysis — multi-TF (1h/4h/1d) EMA, RSI, ATR, ADX, volume confirmation |
| `hermes_agent/agents/research.py` | AI research pipeline — fetches candles, builds context, calls OpenRouter for verdict |
| `hermes_agent/agents/risk_gates.py` | 11 independent risk gates: confidence, notional caps, daily loss, cooldown, correlation, news blackout, etc. |
| `hermes_agent/agents/executor.py` | Kelly sizing + EIP-712 order signing + DSL exit registration |
| `hermes_agent/agents/dsl_exit.py` | Two-phase trailing stop engine — loss protection → profit locking |
| `hermes_agent/agents/hyperfeed.py` | Hyperfeed Discovery API — leaderboard, whale index, smart money signals |
| `hermes_agent/agents/whale_index.py` | Whale detection — OI concentration + funding anomaly signals |
| `hermes_agent/agents/memory.py` | Persistent file-backed state (`.agent-memory.json`, `.agent-config.json`) |
| `hermes_agent/agents/config_store.py` | Config persistence layer |
| `hermes_agent/agents/system_prompt.py` | Dedicated system prompt for the trading agent |
| `hermes_agent/client/hl_client.py` | Hyperliquid REST + WebSocket client (mids, candles, account state) |
| `hermes_agent/client/ws_client.py` | Persistent WebSocket connection for sub-second mids |
| `hermes_agent/client/universe.py` | Volume-ranked market loader with 24h caching |
| `hermes_agent/client/cache.py` | LRU + TTL memoization with in-flight dedup |
| `hermes_agent/client/lock.py` | fcntl lock with stale-PID recovery for scan coalescing |
| `hermes_agent/client/parallel.py` | Concurrency-bounded fan-out for independent API calls |
| `hermes_agent/client/daemon.py` | Long-lived scan scheduler with tick timeouts + graceful shutdown |
| `hermes_agent/client/exchange.py` | Order placement, leverage setting, trigger orders (SL/TP) |
| `hermes_agent/indicators/math.py` | TA indicators: EMA, SMA, ATR, RSI, ADX |
| `hermes_agent/models/types.py` | Shared data type: `Candle` (OHLCV) |
| `hermes_agent/server.py` | FastAPI server — 22 REST routes for frontend/dashboard |

---

## Environment Variables

```bash
# ── OpenRouter ───────────────────────────────────────────────
OPENROUTER_API_KEY=sk-or-...your-key
# Default model (tested working):
OPENROUTER_MODEL=qwen/qwen3-235b-a22b

# ── Hyperliquid ──────────────────────────────────────────────
HYPERLIQUID_WALLET_ADDRESS=0x...your-wallet-address
HYPERLIQUID_PRIVATE_KEY=0x...your-private-key
# Optional: master account (if using agent wallet setup)
# HYPERLIQUID_MASTER_ADDRESS=0x...your-master-address

# ── Scan Tuning ──────────────────────────────────────────────
HERMES_MAX_MARKETS=50          # Top-N markets to scan by volume
HERMES_BATCH_SIZE=20           # Batch size for parallel scanning
HERMES_BATCH_SLEEP=0.3         # Seconds between scan batches

# ── Brave Search (optional, for news signals) ───────────────
BRAVE_API_KEY=BSA...your-key
```

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
python -m hermes_agent.server

# Or use uvicorn directly:
uvicorn hermes_agent.server:app --host 0.0.0.0 --port 8000
```
The API is available at `http://localhost:8000`. Health check: `GET /` returns `{"service": "Hermes Agent", "version": "0.3.0", "status": "running"}`.

### Continuous Trading Loop (Recommended)
```bash
# Start the autonomous trading loop (scans every 180s)
python scripts/trading_loop.py

# Or run in background:
nohup python scripts/trading_loop.py > /tmp/hermes-trader.log 2>&1 &
```
Monitor logs: `tail -f /tmp/hermes-trader.log`

**Trading Loop Behavior:**
- Scans top 50 markets every 180 seconds
- Researches triggered signals with AI (qwen/qwen3-235b-a22b)
- Executes trades when confidence >= 0.50
- Enforces risk caps (max $200 notional, max 3 concurrent positions)
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

Hermes-Trader's MCP server (`scripts/hermes-mcp-server.py`) exposes 100 tools over stdio transport. The 14 primary tools are listed below; the remainder are Hyperliquid data passthroughs (some are placeholders pending SDK wiring).

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
    command: python
    args:
      - /path/to/hermes-trader/scripts/hermes-mcp-server.py
    timeout: 60
```

---

## Design Decisions

### Why volume pre-filtering?
HL's API rate limit is **1200 weight/minute**. A single candle fetch costs **weight 20**. Scanning all 500+ markets naively requires 10,000+ weight → instant 429. Volume pre-filtering to the top 50 markets reduces this to ~2,000 weight (or ~600 with TTL cache hits).

### Why DSL exit engine?
Static SL/TP orders don't adapt to price action. The DSL engine implements a two-phase design: Phase 1 protects your capital (hard stop), Phase 2 locks in profits (trailing floor with tiered retrace thresholds). The floor only moves up — it never gives back locked profit. This pattern is inspired by senpi-skills' DSL dynamic stop-loss engine.

### Why Hyperfeed Discovery?
The HL leaderboard and whale tracking aren't exposed through the public API. This module reconstructs the same data patterns (leaderboard rankings, smart money concentration, OI anomalies) from the raw HL endpoints we already call. No external MCP dependency needed.

### Why pure Python?
Rewritten from TypeScript/Next.js to enable simpler deployment, direct Hermes Agent integration, and native testability without browser headless.

---

## Rate Limit Math

| Operation | Weight | Notes |
|-----------|--------|-------|
| `allMids` | 2 | Real-time prices |
| `metaAndAssetCtxs` | 20 | Universe + volume + OI (perp) |
| `spotMetaAndAssetCtxs` | 20 | Universe + volume + OI (spot) |
| `candleSnapshot` (per coin) | 20 | Plus per-item weight |
| **Total per scan cycle** | ~600-800 | Top 50 markets with cache |

With `HERMES_MAX_MARKETS=50` and 15-min TTL cache, the first scan per cycle costs ~800 weight. Subsequent scans within the cache window cost ~60 weight (mostly cache hits).

---

## Project Structure

```
hermes-trader/
├── hermes_agent/                  # Pure Python agent
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

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — autonomous AI agent framework
- FastAPI — Python web framework
- OpenRouter (Qwen3-235B-A22B) — AI research pipeline
- Hyperliquid API (perpetual futures DEX)
- Brave Search API (optional, for news signals)

---

**Note:** Project runs on `python` branch. Never merge to `main`.
