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
┌─────────────────────────────────────────────────────────┐
│                    Hermes Agent (LLM)                   │
│                                                         │
│  Scan ──→ TA Filter ──→ AI Research ──→ Risk Gates ─→ Execute
│           (cheap)        (expensive)    (10 gates)
│                     ↑
│              Only CONFIRMED
│              signals proceed
└─────────────────────────────────────────────────────────┘
```

### Components

| Component | Purpose |
|-----------|---------|
| `lib/agent/perception.ts` | Multi-market scanner — triggers: pctMoveSpike, volumeSpike, breakout, rangeCompression, trendStrength |
| `lib/agent/ta-filter.ts` | Pre-AI technical analysis — multi-TF (1h/4h/1d) EMA, RSI, ATR, ADX, volume confirmation |
| `lib/agent/research.ts` | AI research pipeline — fetches candles, builds context, calls OpenRouter for verdict |
| `lib/agent/risk-gates.ts` | 10 independent risk gates: confidence, notional caps, daily loss, cooldown, correlation, etc. |
| `lib/agent/executor.ts` | EIP-712 order signing + placement on Hyperliquid |
| `lib/agent/memory.ts` | Persistent file-backed state (.agent-memory.json, .agent-config.json) |
| `lib/hl-universe.ts` | HL market discovery — auto-detects crypto, equity, commodity perps from meta API |
| `scripts/agent-heartbeat.mjs` | Standalone daemon — scan → TA → AI → execute loop (3-min intervals) |
| `scripts/hermes-mcp-server.mjs` | MCP server — exposes scan/research/execute/state/config tools to Hermes Agent |
| `app/page.tsx` | Trading desk dashboard — equity, positions, verdicts, trade log |
| `app/agent/desk/page.tsx` | Full desk view with session log |

### API Routes

- `POST /api/agent/scan` — scan all markets, run TA filter, return perceptions
- `POST /api/agent/research/:coin` — AI analysis on triggered coin
- `POST /api/agent/execute` — execute trade through risk gates
- `GET /api/agent/state` — full agent state (positions, trades, config)
- `GET /api/hl/portfolio` — live portfolio from Hyperliquid

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
# Hyperliquid
HYPERLIQUID_PRIVATE_KEY=your_wallet_private_key
HYPERLIQUID_MASTER_ADDRESS=your_wallet_address

# OpenRouter (via hermes agent)
OPENROUTER_API_KEY=your_key
OPENROUTER_MODEL=qwen/qwen3.6-plus

# Agent (optional, defaults are sensible)
AGENT_HEARTBEAT_INTERVAL_MS=180000   # 3 minutes
AGENT_MIN_SCORE=80                    # trigger threshold 0-100
AGENT_MAX_AI_PER_CYCLE=2              # max AI calls per scan
```

### Running

```bash
# Terminal 1: Next.js dev server
npm run dev

# Terminal 2: Autonomous agent heartbeat
node scripts/agent-heartbeat.mjs

# Terminal 3: MCP server (for Hermes Agent integration)
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
- Heartbeat daemon setup
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

AI models cost money. Most triggered signals are noise — a 2-sigma price spike in a low-volume market isn't a trade opportunity. The TA filter computes multi-timeframe indicators (EMA crossovers, RSI, ATR, ADX, volume confirmation) in ~50ms of CPU time with zero token cost. Only signals scoring ≥65/100 as "CONFIRMED" proceed to AI analysis.

### Why standalone heartbeat daemon?

The heartbeat runs as `node scripts/agent-heartbeat.mjs`, not inside Next.js. Using `setInterval` inside Next.js serverless functions is unsafe — the function can be killed at any time. The standalone process uses drift-corrected `setTimeout` for precise timing and never crashes on network errors.

### Why no DRY/simulated mode?

This agent trades real orders only. The OFF/LIVE toggle controls whether the agent executes — there is no simulated mode. Trade records in memory only contain real executions.

---

## Built with

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — autonomous AI agent framework
- Next.js 16 App Router
- OpenRouter (Qwen 3.6 Plus)
- Hyperliquid API (perpetual futures DEX)
- Brave Search API (optional, for news signals)

---

*Author: [@Julian-dev28](https://github.com/Julian-dev28) — Hermes Agent contributor*
