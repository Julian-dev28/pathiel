# Hermes-Trader

> Autonomous multi-market trading agent for Hyperliquid — crypto perps, equity perps (TSLA, NVDA, AAPL, MU, etc.), and commodities (NATGAS, SILVER, COPPER). Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) with FastAPI, OpenRouter, and a pre-AI technical analysis filter that cuts token costs by 80%.

**What it does:** Scans every Hyperliquid market (500+ perps + spot), fires statistical triggers on price/volume/breakout signals, runs a cheap pre-AI technical analysis filter, and only calls AI on CONFIRMED setups. Executes real trades with SL/TP brackets — no human in the loop.

---

## The problem it solves

Trading signals appear constantly — 5-minute spikes, hourly trends, daily breakouts. Most systems call expensive AI on every signal, burning tokens on noise. Hermes-Trader solves this by separating cheap statistical analysis from expensive AI reasoning:

1. **Scan** — 500+ markets in parallel, fire statistical triggers
2. **TA Filter** — multi-timeframe indicators (EMA, RSI, ATR, ADX, volume) — zero AI cost
3. **AI Research** — only on CONFIRMED signals (typically 0-2 per cycle vs. 5+ before)
4. **Execution** — Kelly-sized orders with auto SL/TP brackets

This architecture reduced daily AI costs from $8-$52 to $3-$10 while improving signal quality.

---

## Architecture

```
+-------------------------------------------------------------+
|                 Hermes Agent (LLM)                          |
|                                                             |
|  Scan --> TA Filter --> AI Research --> Risk Gates --> Execute
|           (cheap)         (expensive)    (10 gates)
|                     ^
|              Only CONFIRMED
|              signals proceed
+-------------------------------------------------------------+
```

### Pipeline

```
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐    ┌──────────┐    ┌──────────┐
│ Perception │───>│  TA Filter   │───>│  AI Research    │───>│ Risk     │───>│ Executor │
│   Scanner  │    │  (TA Filter) │    │ (OpenRouter API) │    │  Gates   │    │ (HL)     │
│ 5m/1h/4h   │    │  EMA/RSI/ATR │    │ Verdict + Price  │    │  10 gates│    │ SL/TP    │
└─────────────┘    └──────────────┘    └─────────────────┘    └──────────┘    └──────────┘
```

### Core Modules

| Module | Purpose |
|--------|---------|
| `hermes_agent/agents/perception.py` | Multi-market scanner — triggers: pctMoveSpike, volumeSpike, breakout, rangeCompression, trendStrength |
| `hermes_agent/indicators/triggers.py` | Trigger engine — composite scoring across signal types |
| `hermes_agent/agents/ta_filter.py` | Pre-AI technical analysis — multi-TF (1h/4h/1d) EMA, RSI, ATR, ADX, volume confirmation |
| `hermes_agent/agents/research.py` | AI research pipeline — fetches candles, builds context, calls OpenRouter for verdict |
| `hermes_agent/agents/risk_gates.py` | 10 independent risk gates: confidence, notional caps, daily loss, cooldown, correlation, etc. |
| `hermes_agent/agents/executor.py` | Kelly sizing + EIP-712 order signing + placement on Hyperliquid |
| `hermes_agent/agents/memory.py` | Persistent file-backed state (`.agent-memory.json`, `.agent-config.json`) |
| `hermes_agent/agents/config_store.py` | Config persistence layer |
| `hermes_agent/agents/system_prompt.py` | Dedicated system prompt for the trading agent |
| `hermes_agent/client/hl_client.py` | Hyperliquid REST client (mids, candles, account state, funding) |
| `hermes_agent/client/universe.py` | HL market discovery — auto-detects crypto, equity, commodity perps from meta API |
| `hermes_agent/client/exchange.py` | Order placement, leverage setting, trigger orders (SL/TP) |
| `hermes_agent/indicators/math.py` | TA indicators: EMA, SMA, ATR, RSI, ADX |
| `hermes_agent/models/` | Data types: `AgentConfig`, `AgentAnalysis`, `AgentTrade`, `Candle`, `HLMarket`, `TriggerHit` |
| `hermes_agent/server.py` | FastAPI server — 26 REST routes for frontend/dashboard + MCP bridge |

### Scripts

| Script | Purpose |
|--------|---------|
| `scripts/hermes-mcp-server.mjs` | MCP server — exposes scan/research/execute/state/config tools to Hermes Agent |
| `scripts/backtest.mjs` | Historical backtesting utility |
| `scripts/analyze-journal.mjs` | Trade journal analytics |

### Tests

```
test_all.py — 17 module-level tests covering the full pipeline:
  config_store  •  memory  •  system_prompt  •  ta_filter
  risk_gates    •  executor  •  hl_client  •  universe
  exchange      •  indicators/math  •  triggers
  perception    •  research  •  models  •  server  •  HTTP endpoints
```

### Documentation

| Path | Purpose |
|------|---------|
| `docs/journal-schema.md` | Persistent trade journal JSON schema |

### FastAPI Endpoints

#### Agent Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/agent/scan` | Scan all markets, run TA filter, return perceptions |
| POST | `/api/agent/research/{coin}` | AI analysis on triggered coin |
| POST | `/api/agent/execute` | Execute trade through risk gates |
| GET | `/api/agent/state` | Full agent state (positions, trades, config) |
| GET | `/api/agent/config` | Get agent configuration |
| POST | `/api/agent/config` | Set agent configuration |

#### Hyperliquid Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/hl/account` | Account info and balances |
| GET | `/api/hl/all-mids` | Current mids for all markets |
| GET | `/api/hl/universe` | Full market universe (perp + spot) |
| GET | `/api/hl/candles` | OHLCV candlestick data |

### Market Coverage

The universe is fetched live from Hyperliquid's `meta` API. Categories:

| Category | Examples |
|----------|----------|
| **Crypto** | BTC, ETH, SOL, DOGE, WLD, ARB, ... |
| **Equity Perps** | TSLA, NVDA, AAPL, AMZN, GOOGL, MSFT, META, COIN, MSTR, INTC, AMD, NFLX, MU, SNDK, LITE, ARM, PLTR, ... |
| **Commodities** | NATGAS, SILVER, COPPER, GOLD, URNM, CRCL, ... |

New markets added by Hyperliquid are picked up automatically via the meta endpoint.

---

## Quick Start

### Prerequisites

- Python 3.12+
- Hyperliquid wallet with private key
- OpenRouter API key ([openrouter.ai](https://openrouter.ai))
- (Optional) Brave Search API key for news

### Setup

```bash
git clone https://github.com/YOUR_HANDLE/hermes-trader
cd hermes-trader

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.local.example .env.local
# Edit .env.local with your keys
```

### Environment Variables

```bash
# ── OpenRouter ───────────────────────────────────────────────
OPENROUTER_API_KEY=sk-or-...your-key
# Optional: override the default Qwen model
# OPENROUTER_MODEL=qwen/qwen3.6-35b-a3b

# ── Hyperliquid ──────────────────────────────────────────────
HYPERLIQUID_WALLET_ADDRESS=0x...your-wallet-address
HYPERLIQUID_PRIVATE_KEY=0x...your-private-key
# Optional: master account (if using agent wallet setup)
# HYPERLIQUID_MASTER_ADDRESS=0x...your-master-address

# ── Brave Search (optional, for news signals) ───────────────
BRAVE_API_KEY=BSA...your-key
```

### Running

```bash
# Start the FastAPI server (port 8000)
python -m hermes_agent.server

# Or use uvicorn directly:
uvicorn hermes_agent.server:app --host 0.0.0.0 --port 8000
```

The API is available at `http://localhost:8000`. Health check: `GET /` returns `{"service": "Hermes Agent", "version": "0.2.0", "status": "running"}`.

---

## MCP Integration

Hermes-Trader exposes an MCP server at `scripts/hermes-mcp-server.mjs` with tools:

| Tool | Description |
|------|-------------|
| `scan` | Scan all HL markets, return triggered candidates |
| `research` | Deep AI analysis on a coin |
| `execute` | Execute trade from prior analysis |
| `state` | Get full agent state |
| `config` | Get/set agent configuration |

Configure in Hermes Agent's `config.yaml`:

```yaml
mcp_servers:
  hermes-trader:
    command: node
    args:
      - /path/to/hermes-trader/scripts/hermes-mcp-server.mjs
    timeout: 60
```

See `skills/hermes-trader-agent/SKILL.md` for full usage guide.

---

## Skills

This project includes a Hermes Agent skill in `skills/hermes-trader-agent/` that provides:

- Architecture overview and patterns
- Risk gate configuration
- MCP tool usage
- Common pitfalls and debugging tips

To use as a reusable skill in your own Hermes Agent project:

```bash
# Symlink or copy to your Hermes skills directory
ln -s /path/to/hermes-trader/skills/hermes-trader-agent ~/.hermes/skills/
```

Or load it directly:

```
skill_view(name='hermes-trader-agent')
```

---

## Design Decisions

### Why pre-AI TA filter?

AI models cost money. Most triggered signals are noise — a 2-sigma price spike in a low-volume market isn't a trade opportunity. The TA filter computes multi-timeframe indicators (EMA crossovers, RSI, ATR, ADX, volume confirmation) in ~50ms of CPU time with zero token cost. Only signals scoring >=65/100 as "CONFIRMED" proceed to AI analysis.

### Why pure Python?

The project was rewritten from TypeScript/Next.js to pure Python for:
- Simpler deployment (no Node.js build step, no Next.js overhead)
- Better testability (pytest-native modules, no browser headless needed)
- Direct integration with the Hermes Agent Python framework
- Leaner dependencies and faster cold-start for the FastAPI server

### Why no DRY/simulated mode?

This agent trades real orders only. The OFF/LIVE toggle controls whether the agent executes — there is no simulated mode. Trade records in memory only contain real executions.

---

## Project Structure

```
hermes-trader/
├── hermes_agent/                  # Pure Python agent (3674 LOC)
│   ├── __init__.py
│   ├── __main__.py                # Entry point
│   ├── server.py                  # FastAPI server — 26 routes
│   ├── agents/                    # Core agent logic
│   │   ├── config.py              # Agent configuration model
│   │   ├── config_store.py        # Config persistence
│   │   ├── executor.py            # Kelly sizing + order execution
│   │   ├── memory.py              # File-backed state
│   │   ├── perception.py          # Market scanner
│   │   ├── research.py            # AI research pipeline
│   │   ├── risk_gates.py          # 10 risk gates
│   │   ├── system_prompt.py       # Agent system prompt
│   │   └── ta_filter.py           # Pre-AI TA filter
│   ├── client/                    # External API clients
│   │   ├── exchange.py            # HL order placement
│   │   ├── hl_client.py           # HL REST client (mids, candles)
│   │   └── universe.py            # Market discovery + caching
│   ├── indicators/                # TA math
│   │   ├── math.py                # EMA, SMA, ATR, RSI, ADX
│   │   └── triggers.py            # Trigger detection + composite scoring
│   └── models/                    # Data types
│       ├── analysis.py            # AgentAnalysis, AgentTrade, WatchlistEntry
│       ├── hl.py                  # HLMeta, HLOrderResponse
│       ├── perception.py          # TriggerHit, Perception
│       └── types.py               # AgentConfig, AgentVerdict, Candle, HLMarket
├── scripts/
│   ├── hermes-mcp-server.mjs     # MCP server (Node.js bridge)
│   ├── backtest.mjs              # Historical backtesting
│   └── analyze-journal.mjs       # Trade journal analytics
├── skills/hermes-trader-agent/   # Hermes Agent skill
├── test_all.py                   # 17-module test suite
└── docs/
    └── journal-schema.md         # Trade journal schema
```

---

## Built With

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — autonomous AI agent framework
- FastAPI — Python web framework
- OpenRouter (Qwen 3.6)
- Hyperliquid API (perpetual futures DEX)
- Brave Search API (optional, for news signals)

---

*Author: [@Julian-dev28](https://github.com/Julian-dev28) — Hermes Agent contributor*
