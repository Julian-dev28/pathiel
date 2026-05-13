# Hermes-Trader

> Autonomous multi-market trading agent for Hyperliquid — crypto perps, equity perps (TSLA, NVDA, AAPL, MU, etc.), and commodities (NATGAS, SILVER, COPPER). Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) with Next.js 16, OpenRouter, and a pre-AI technical analysis filter that cuts token costs by 80%.

**What it does:** Scans every Hyperliquid market (230+ perps + spot), fires statistical triggers on price/volume/breakout signals, runs a cheap pre-AI technical analysis filter, and only calls AI on CONFIRMED setups. Executes real trades with SL/TP brackets — no human in the loop.

---

## The problem it solves

Trading signals appear constantly — 5-minute spikes, hourly trends, daily breakouts. Most systems call expensive AI on every signal, burning tokens on noise. Hermes-Trader solves this by separating cheap statistical analysis from expensive AI reasoning:

1. **Scan** — 230+ markets in parallel, fire statistical triggers
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

### Core Components

| Component | Purpose |
|-----------|---------|
| `lib/agent/perception.ts` | Multi-market scanner — triggers: pctMoveSpike, volumeSpike, breakout, rangeCompression, trendStrength |
| `lib/agent/triggers.ts` | Trigger engine — composite scoring across signal types |
| `lib/agent/ta-filter.ts` | Pre-AI technical analysis — multi-TF (1h/4h/1d) EMA, RSI, ATR, ADX, volume confirmation |
| `lib/agent/research.ts` | AI research pipeline — fetches candles, builds context, calls OpenRouter for verdict |
| `lib/agent/risk-gates.ts` | 10 independent risk gates: confidence, notional caps, daily loss, cooldown, correlation, etc. |
| `lib/agent/executor.ts` | EIP-712 order signing + placement on Hyperliquid |
| `lib/agent/memory.ts` | Persistent file-backed state (.agent-memory.json, .agent-config.json) |
| `lib/agent/config.ts` | Agent configuration (live/off mode, thresholds, risk params) |
| `lib/agent/config-store.ts` | Config persistence layer |
| `lib/agent/system-prompt.ts` | Dedicated system prompt for the trading agent |
| `lib/hl-client.ts` | Shared Hyperliquid REST + WSS client |
| `lib/hl-universe.ts` | HL market discovery — auto-detects crypto, equity, commodity perps from meta API |
| `lib/hyperliquid.ts` | Additional HL helpers |
| `lib/openrouter-client.ts` | OpenRouter API client |
| `lib/types.ts` | Shared TypeScript types |

### Scripts

| Script | Purpose |
|--------|---------|
| `scripts/hermes-mcp-server.mjs` | MCP server — exposes scan/research/execute/state/config tools to Hermes Agent |
| `scripts/backtest.mjs` | Historical backtesting utility |
| `scripts/analyze-journal.mjs` | Trade journal analytics |

### Tests

| Path | Purpose |
|------|---------|
| `scripts/__tests__/triggers-unit.test.mjs` | Unit tests for trigger engine |
| `scripts/__tests__/e2e-market-data.test.mjs` | E2E tests for market data fetching |

### Documentation

| Path | Purpose |
|------|---------|
| `docs/journal-schema.md` | Persistent trade journal JSON schema |

### API Routes

#### Agent Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/agent/scan` | Scan all markets, run TA filter, return perceptions |
| POST | `/api/agent/research/:coin` | AI analysis on triggered coin |
| POST | `/api/agent/execute` | Execute trade through risk gates |
| GET | `/api/agent/state` | Full agent state (positions, trades, config) |
| POST | `/api/agent/start` | Start autonomous agent mode |
| POST | `/api/agent/stop` | Stop autonomous agent mode |
| POST | `/api/agent/config` | Get/set agent configuration |
| GET | `/api/agent/trades` | Trade history from journal |
| POST | `/api/agent/session-log` | Session log endpoint |

#### Hyperliquid Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/hl/portfolio` | Live portfolio from Hyperliquid |
| GET | `/api/hl/account` | Account info and balances |
| GET | `/api/hl/all-mids` | Current mids for all markets |
| GET | `/api/hl/universe` | Full market universe |
| GET | `/api/hl/price` | Real-time price feed |
| GET | `/api/hl/candles` | OHLCV candlestick data |
| GET | `/api/hl/orderbook` | Orderbook depth |
| POST | `/api/hl/place-order` | Place a new order |
| POST | `/api/hl/cancel-order` | Cancel a pending order |
| POST | `/api/hl/close-position` | Close an open position |

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

- Node.js 20+
- Hyperliquid wallet with private key
- OpenRouter API key (https://openrouter.ai)
- (Optional) Brave Search API key for news

### Setup

```bash
git clone https://github.com/YOUR_HANDLE/hermes-trader
cd hermes-trader
npm install

# Configure
cp .env.local.example .env.local
# Edit .env.local with your keys
```

### Environment Variables

```bash
# ── Brave Search ─────────────────────────────────────────────
BRAVE_API_KEY=BSA...your-key

# ── OpenRouter ───────────────────────────────────────────────
OPENROUTER_API_KEY=sk-or-...your-key
# Optional: override the default Qwen model
# OPENROUTER_MODEL=qwen/qwen3-235b-a22b

# ── Hyperliquid ──────────────────────────────────────────────
HYPERLIQUID_WALLET_ADDRESS=0x...your-wallet-address
HYPERLIQUID_PRIVATE_KEY=0x...your-private-key
# Optional: master account (if using agent wallet setup)
# HYPERLIQUID_MASTER_ADDRESS=0x...your-master-address

# ── Next.js Public (front-end display) ───────────────────────
NEXT_PUBLIC_HL_WALLET=0x...your-wallet-address
# NEXT_PUBLIC_HL_MASTER=0x...your-master-address
```

### Running

```bash
# Terminal 1: Next.js dev server (trading desk dashboard)
npm run dev

# Terminal 2: MCP server (for Hermes Agent integration)
node scripts/hermes-mcp-server.mjs
```

Open [http://localhost:3000](http://localhost:3000) for the trading desk.

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

### Why no DRY/simulated mode?

This agent trades real orders only. The OFF/LIVE toggle controls whether the agent executes — there is no simulated mode. Trade records in memory only contain real executions.

---

## Project Structure

```
hermes-trader/
├── app/                          # Next.js 16 App Router
│   ├── api/agent/                # Trading agent API routes
│   ├── api/hl/                   # Hyperliquid API routes
│   ├── layout.tsx
│   └── page.tsx                  # Trading desk dashboard
├── data/
│   └── trade-journal.json        # Persistent trade history
├── docs/
│   └── journal-schema.md         # Trade journal schema
├── lib/
│   ├── agent/                    # Core agent logic
│   │   ├── config-store.ts       # Config persistence
│   │   ├── config.ts             # Agent config
│   │   ├── executor.ts           # Order execution
│   │   ├── memory.ts             # File-backed state
│   │   ├── perception.ts         # Market scanner
│   │   ├── research.ts           # AI research pipeline
│   │   ├── risk-gates.ts         # 10 risk gates
│   │   ├── system-prompt.ts      # Agent system prompt
│   │   ├── ta-filter.ts          # Pre-AI TA filter
│   │   └── triggers.ts           # Trigger engine
│   ├── hl-client.ts              # Shared HL client
│   ├── hl-universe.ts            # Market discovery
│   ├── hyperliquid.ts            # HL helpers
│   ├── openrouter-client.ts      # OpenRouter client
│   └── types.ts                  # Shared types
├── scripts/
│   ├── hermes-mcp-server.mjs     # MCP server
│   ├── backtest.mjs              # Historical backtesting
│   ├── analyze-journal.mjs       # Trade journal analytics
│   └── __tests__/                # Test suite
├── skills/hermes-trader-agent/   # Hermes Agent skill
└── target/                       # Rust build artifacts (not committed)
```

---

## Built With

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — autonomous AI agent framework
- Next.js 16 App Router
- OpenRouter (Qwen 3.6)
- Hyperliquid API (perpetual futures DEX)
- Brave Search API (optional, for news signals)

---

*Author: [@Julian-dev28](https://github.com/Julian-dev28) — Hermes Agent contributor*
